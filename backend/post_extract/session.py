"""
Post-extract session: load extraction_results, merge rules, apply pipeline, save version.
"""
from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from services.extraction_results_service import ExtractionResultsService
from utils.extraction_rule_pipeline import apply_extraction_rules, extraction_rules_from_ai_record
from utils.extraction_rules_flags import extraction_rules_imply_non_identity_processing
from utils.rule_merge import merge_extraction_rules_baseline, merge_rules_for_operation

RULE_METADATA_KEYS = frozenset(
    {
        "data_cleanup_rules",
        "transformation_rules",
        "filter_rules",
        "deduplication_rules",
        "ordering_rules",
        # Persisted rules embedding mistakenly copied schema onto row[0]; strip when reloading.
        "source_schema",
        "mapping_context_summary",
        "document_type",
        "document_description",
        "object_name",
    }
)

# Only embed rule buckets on row 0 — never mirror structural keys like ``source_schema`` as fake columns.
_ROW_EMBEDDED_RULE_KEYS = frozenset(
    {
        "filter_rules",
        "deduplication_rules",
        "transformation_rules",
        "data_cleanup_rules",
        "ordering_rules",
    }
)


def _strip_rule_metadata(record: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in record.items() if k not in RULE_METADATA_KEYS}


def _rules_from_records(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not records:
        return {}
    first = records[0] if isinstance(records[0], dict) else {}
    return extraction_rules_from_ai_record(first)


def _embed_rules_in_records(
    records: List[Dict[str, Any]], rules: Dict[str, Any]
) -> List[Dict[str, Any]]:
    if not records:
        return records
    out = [_strip_rule_metadata(r) if isinstance(r, dict) else r for r in records]
    meta = copy.deepcopy(rules) if rules else {}
    if out and isinstance(out[0], dict):
        row_meta = {k: meta[k] for k in _ROW_EMBEDDED_RULE_KEYS if meta.get(k)}
        out[0] = {**out[0], **row_meta}
    return out


@dataclass
class OperationStep:
    operation_id: str
    prompt: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OperationPlan:
    parent_job_id: str
    steps: List[OperationStep]
    source_version: Optional[int] = None


@dataclass
class PostExtractSession:
    user_id: str
    conversation_id: Optional[str]
    job_id: str
    parent_job_id: Optional[str]
    source_type: str
    source_name: Optional[str]
    source_identifier: Optional[str]
    extraction_version: int
    records: List[Dict[str, Any]]
    extraction_rules: Dict[str, Any]
    source_schema: List[str]
    document_type: Optional[str] = None
    document_description: Optional[str] = None
    extraction_goals: Optional[str] = None
    operations_applied: List[str] = field(default_factory=list)
    pipeline_transform_before_filter: bool = True

    @classmethod
    async def load(
        cls,
        job_id: str,
        user_id: str,
        *,
        version: Optional[int] = None,
        conversation_id: Optional[str] = None,
        extraction_rules_override: Optional[Dict[str, Any]] = None,
    ) -> "PostExtractSession":
        row = await ExtractionResultsService.get_extraction_result(
            job_id, user_id, version=version
        )
        if not row:
            raise ValueError(f"Extraction not found for job_id={job_id}")

        raw = row.get("extracted_data") or []
        if not isinstance(raw, list):
            raw = []

        rules = _rules_from_records(raw)
        if extraction_rules_override and isinstance(extraction_rules_override, dict):
            rules = merge_extraction_rules_baseline(rules, extraction_rules_override)

        clean = [_strip_rule_metadata(r) for r in raw if isinstance(r, dict)]
        schema: List[str] = []
        if clean:
            schema = sorted({k for r in clean[:50] for k in r.keys()})

        return cls(
            user_id=str(user_id),
            conversation_id=conversation_id or row.get("conversation_id"),
            job_id=row["job_id"],
            parent_job_id=row["job_id"],
            source_type=row.get("source_type") or "unknown",
            source_name=row.get("source_name"),
            source_identifier=row.get("source_identifier"),
            extraction_version=int(row.get("extraction_version") or 1),
            records=clean,
            extraction_rules=rules,
            source_schema=schema,
            document_type=row.get("document_type"),
            document_description=row.get("document_description"),
            extraction_goals=row.get("extraction_goals"),
        )

    def sample_rows(self, n: int = 5) -> List[Dict[str, Any]]:
        return self.records[:n] if self.records else []

    def merge_rules(self, delta: Dict[str, Any], operation_id: str) -> None:
        self.extraction_rules = merge_rules_for_operation(
            self.extraction_rules, delta, operation_id
        )
        if self.source_schema:
            self.extraction_rules["source_schema"] = list(self.source_schema)
        self._prune_incompatible_transformation_rules()

    def _prune_incompatible_transformation_rules(self) -> None:
        """Drop keep_only / merge / split rules that reference columns absent from current data."""
        from utils.extraction_rules_validate import resolve_field_in_schema

        schema = self.source_schema or []
        if not schema:
            return
        tr = self.extraction_rules.get("transformation_rules")
        if not isinstance(tr, dict):
            return
        fields = tr.get("fields")
        if not isinstance(fields, list):
            return
        kept: List[Dict[str, Any]] = []
        for rule in fields:
            if not isinstance(rule, dict):
                continue
            action = (rule.get("action") or "").strip().lower()
            if action == "keep_only":
                cols = rule.get("fields") or []
                if cols and not any(
                    resolve_field_in_schema(str(c), schema)
                    for c in cols
                    if isinstance(c, str) and str(c).strip()
                ):
                    continue
            if action == "merge":
                srcs = rule.get("source_fields") or rule.get("source_field") or []
                if isinstance(srcs, str):
                    srcs = [srcs]
                if srcs and not any(
                    resolve_field_in_schema(str(s), schema)
                    for s in srcs
                    if isinstance(s, str) and str(s).strip()
                ):
                    continue
            if action.startswith("split"):
                sf = rule.get("source_field") or rule.get("field")
                if isinstance(sf, str) and sf.strip() and not resolve_field_in_schema(sf.strip(), schema):
                    continue
            kept.append(rule)
        tr["fields"] = kept
        self.extraction_rules["transformation_rules"] = tr

    def apply(self) -> List[Dict[str, Any]]:
        self._prune_incompatible_transformation_rules()
        rules = dict(self.extraction_rules or {})
        if self.source_schema:
            rules.setdefault("source_schema", self.source_schema)
        applied = apply_extraction_rules(
            self.records,
            rules,
            source_schema=self.source_schema,
            transform_before_filter=self.pipeline_transform_before_filter,
            apply_ordering_limit=False,
        )
        self.records = applied
        if applied:
            keys = sorted({k for r in applied[:50] if isinstance(r, dict) for k in r.keys()})
            self.source_schema = keys
        return applied

    async def _inherit_migration_context_if_row_only(self, new_job_id: str) -> None:
        """Copy parent migration context onto row-only post-extract versions."""
        if not self.conversation_id or not self.parent_job_id:
            return
        from services.migration.mapping_readiness import (
            is_row_only_operations,
            schemas_compatible,
        )

        if not is_row_only_operations(self.operations_applied):
            return

        from services.migration.context_repository import get_context, upsert_context
        from services.migration.mapping_readiness import _schema_from_extraction_row

        parent_ctx = await get_context(
            user_id=str(self.user_id),
            conversation_id=str(self.conversation_id),
            extraction_job_id=str(self.parent_job_id),
        )
        if not parent_ctx or not parent_ctx.mapping:
            return

        parent_row = await ExtractionResultsService.get_extraction_result(
            self.parent_job_id,
            self.user_id,
            prefer_lineage_latest=False,
        )
        child_row = await ExtractionResultsService.get_extraction_result(
            new_job_id,
            self.user_id,
            prefer_lineage_latest=False,
        )
        if not schemas_compatible(
            _schema_from_extraction_row(parent_row),
            _schema_from_extraction_row(child_row),
        ):
            return

        inherited = parent_ctx.__class__(
            user_id=str(self.user_id),
            extraction_job_id=str(new_job_id),
            mapping=dict(parent_ctx.mapping),
            document_type=parent_ctx.document_type,
            destination_object_api_name=parent_ctx.destination_object_api_name,
            destination=parent_ctx.destination,
            destination_name=parent_ctx.destination_name,
            document_description=parent_ctx.document_description,
            conversation_id=str(self.conversation_id),
            mapping_id=parent_ctx.mapping_id,
            mapping_version=parent_ctx.mapping_version,
            is_transformed_data=parent_ctx.is_transformed_data,
            transformation_rules=parent_ctx.transformation_rules,
            migration_rules=parent_ctx.migration_rules,
            connector_id=parent_ctx.connector_id,
            source_connector_id=parent_ctx.source_connector_id,
            sql_query=parent_ctx.sql_query,
            table_name=parent_ctx.table_name,
            spreadsheet_id=parent_ctx.spreadsheet_id,
            spreadsheet_name=parent_ctx.spreadsheet_name,
            sheet_name=parent_ctx.sheet_name,
            operation=parent_ctx.operation,
            source_type=parent_ctx.source_type,
            extracted_schema_type=parent_ctx.extracted_schema_type,
        )
        await upsert_context(inherited)

    async def save(self) -> Dict[str, Any]:
        new_job_id = str(uuid.uuid4())
        payload = _embed_rules_in_records(self.records, self.extraction_rules)
        has_cleanup = bool(self.extraction_rules.get("data_cleanup_rules"))
        has_transform = extraction_rules_imply_non_identity_processing(self.extraction_rules)

        extraction_metadata: Dict[str, Any] = {}
        rules_snapshot = dict(self.extraction_rules or {})
        if rules_snapshot:
            extraction_metadata["extraction_rules"] = rules_snapshot
        if self.source_schema:
            extraction_metadata["source_schema"] = list(self.source_schema)
        if self.source_type == "sql" and self.parent_job_id:
            parent_row = await ExtractionResultsService.get_extraction_result(
                self.parent_job_id, self.user_id, prefer_lineage_latest=False
            )
            if parent_row:
                parent_meta = parent_row.get("extraction_metadata") or {}
                if isinstance(parent_meta, str):
                    import json as _json

                    try:
                        parent_meta = _json.loads(parent_meta)
                    except (_json.JSONDecodeError, TypeError):
                        parent_meta = {}
                if isinstance(parent_meta, dict):
                    for key in ("sql_query", "query", "table_name", "source_connector_id"):
                        if parent_meta.get(key) and key not in extraction_metadata:
                            extraction_metadata[key] = parent_meta[key]
        elif self.source_type == "salesforce" and self.parent_job_id:
            parent_row = await ExtractionResultsService.get_extraction_result(
                self.parent_job_id, self.user_id, prefer_lineage_latest=False
            )
            if parent_row:
                parent_meta = parent_row.get("extraction_metadata") or {}
                if isinstance(parent_meta, str):
                    import json as _json

                    try:
                        parent_meta = _json.loads(parent_meta)
                    except (_json.JSONDecodeError, TypeError):
                        parent_meta = {}
                if isinstance(parent_meta, dict):
                    for key in ("soql", "sql_query", "object_api_name"):
                        if parent_meta.get(key) and key not in extraction_metadata:
                            extraction_metadata[key] = parent_meta[key]

        await ExtractionResultsService.save_extraction_result(
            job_id=new_job_id,
            user_id=self.user_id,
            extracted_data=payload,
            source_type=self.source_type,
            extraction_goals=self.extraction_goals,
            document_type=self.document_type,
            document_description=self.document_description,
            source_name=self.source_name,
            source_identifier=self.source_identifier,
            parent_job_id=self.parent_job_id,
            conversation_id=self.conversation_id,
            has_cleanup_rules=has_cleanup,
            has_transformation_rules=has_transform,
            extraction_metadata=extraction_metadata or None,
        )

        saved = await ExtractionResultsService.get_extraction_result(new_job_id, self.user_id)
        new_version = int((saved or {}).get("extraction_version") or 1)
        await self._inherit_migration_context_if_row_only(new_job_id)
        return {
            "job_id": new_job_id,
            "parent_job_id": self.parent_job_id,
            "extraction_version": new_version,
            "record_count": len(payload),
            "operations_applied": list(self.operations_applied),
        }
