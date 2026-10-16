"""Job-scoped mapping readiness (lineage + schema compatibility)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

ROW_ONLY_DATA_OPS = frozenset({"filter", "dedupe", "validate"})


def schemas_compatible(
    parent_schema: Optional[Sequence[str]],
    target_schema: Optional[Sequence[str]],
) -> bool:
    """True when column sets match (order-insensitive). Empty schemas → assume compatible."""
    if not parent_schema or not target_schema:
        return True
    return set(parent_schema) == set(target_schema)


def is_row_only_operations(operations: Optional[Sequence[str]]) -> bool:
    ops = {str(o).strip().lower() for o in (operations or []) if str(o).strip()}
    if not ops:
        return True
    return ops.issubset(ROW_ONLY_DATA_OPS)


def goal_data_ops_schema_changing(data_operations: Optional[Sequence[str]]) -> bool:
    ops = {str(o).strip().lower() for o in (data_operations or []) if str(o).strip()}
    if not ops:
        return False
    return bool(ops - ROW_ONLY_DATA_OPS)


def _schema_from_extraction_row(row: Optional[Dict[str, Any]]) -> List[str]:
    if not row:
        return []
    meta = row.get("metadata") or {}
    if isinstance(meta, dict):
        schema = meta.get("source_schema")
        if isinstance(schema, list) and schema:
            return [str(c) for c in schema if str(c).strip()]
    data = row.get("extracted_data") or []
    if not isinstance(data, list) or not data:
        return []
    keys: set[str] = set()
    for rec in data[:50]:
        if isinstance(rec, dict):
            keys.update(str(k) for k in rec.keys())
    return sorted(keys)


async def mapping_ready_for_job(
    user_id: str,
    conversation_id: Optional[str],
    job_id: str,
    *,
    mapping_config_on_job: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    True when mapping exists for ``job_id`` or a lineage ancestor with compatible schema.
    """
    jid = (job_id or "").strip()
    if not jid:
        return False
    if isinstance(mapping_config_on_job, dict) and mapping_config_on_job:
        return True

    from services.migration.context_repository import get_context
    from services.migration.mapping_lineage import find_lineage_mapping_context

    if conversation_id:
        ctx = await get_context(
            user_id=str(user_id),
            conversation_id=str(conversation_id),
            extraction_job_id=jid,
        )
        if ctx and ctx.mapping:
            return True

        found = await find_lineage_mapping_context(
            user_id=str(user_id),
            conversation_id=str(conversation_id),
            target_job_id=jid,
        )
        if not found:
            return False

        stored_ctx, mapping_job_id = found
        if not stored_ctx.mapping:
            return False
        if str(mapping_job_id) == jid:
            return True

        from services.extraction_results_service import ExtractionResultsService

        parent_row = await ExtractionResultsService.get_extraction_result(
            mapping_job_id,
            str(user_id),
            prefer_lineage_latest=False,
        )
        target_row = await ExtractionResultsService.get_extraction_result(
            jid,
            str(user_id),
            prefer_lineage_latest=False,
        )
        return schemas_compatible(
            _schema_from_extraction_row(parent_row),
            _schema_from_extraction_row(target_row),
        )

    return False
