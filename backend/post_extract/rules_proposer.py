"""
LLM rules-only proposer for post-extract plugins (no source re-extract).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

import logging

from post_extract.debug import log_snapshot, post_extract_debug_enabled
from utils.data_quality_analyzer import convert_quality_report_to_extraction_rules

logger = logging.getLogger("post_extract.rules_proposer")


def _normalize_schema_token(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _prompt_suggests_row_presence_filter_not_column_drop(prompt: str) -> bool:
    """True when user language targets dropping ROWS (missing values), not removing columns."""
    pl = (prompt or "").strip().lower()
    if not pl:
        return False
    # Explicit column / field removal — trust transformation_rules for schema edits.
    if re.search(r"\b(remove|drop|delete)\s+(the\s+)?(column|field)\b", pl):
        return False
    if re.search(r"\b(remove|drop|delete|exclude)\b.{0,140}\b(records?|rows?)\b", pl):
        return True
    if re.search(r"\b(records?|rows?)\b", pl) and re.search(
        r"\b(without|missing|lacking|empty|blank|null)\b",
        pl,
    ):
        return True
    return False


def _schema_fields_hinted_for_presence(prompt: str, schema: List[str]) -> set[str]:
    """Map phrases like 'without account name' to schema field names (best-effort)."""
    pl = (prompt or "").strip().lower()
    out: set[str] = set()
    if not pl or not schema:
        return out

    def consider_phrase(phrase_raw: str) -> None:
        raw = (phrase_raw or "").strip().strip(".,")
        if len(raw) < 2:
            return
        pn = _normalize_schema_token(raw)
        if len(pn) < 2:
            return
        for sf in schema:
            if not isinstance(sf, str) or not sf.strip():
                continue
            sn = _normalize_schema_token(sf)
            if len(sn) < 2:
                continue
            if pn == sn or pn in sn or sn in pn:
                out.add(sf.strip())

    for m in re.finditer(
        r"\b(?:without|missing)\s+(.+?)(?=\.|,|\band\b|\bor\b|$)",
        pl,
        flags=re.DOTALL,
    ):
        consider_phrase(m.group(1))
    for m in re.finditer(
        r"\b(?:with)\s+(?:no|empty|blank)\s+(.+?)(?=\.|,|\band\b|\bor\b|$)",
        pl,
        flags=re.DOTALL,
    ):
        consider_phrase(m.group(1))
    return out


def _is_column_removal_transform_rule(rule: Dict[str, Any]) -> bool:
    if not isinstance(rule, dict):
        return False
    act = (rule.get("action") or "").strip().lower().replace("-", "_")
    return act in (
        "remove",
        "remove_fields",
        "remove_field",
        "exclude",
        "drop_field",
        "drop_column",
        "delete_column",
    )


def _removal_rule_target_fields(rule: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    flds = rule.get("fields")
    if isinstance(flds, list):
        out.extend(str(f).strip() for f in flds if isinstance(f, str) and str(f).strip())
    for key in ("source_field", "field"):
        v = rule.get(key)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    return out


def sanitize_transform_delta_row_vs_column_language(
    prompt: str,
    schema: List[str],
    parsed: Dict[str, Any],
    *,
    operation_id: str,
) -> Dict[str, Any]:
    """
    Models often mis-read 'remove records without AccountName' as 'drop the AccountName column'.

    Strip column-removal rules whose targets match fields the user indicated must be present
    (e.g. phrase after 'without'), leaving real transforms intact.
    """
    op = (operation_id or "").strip().lower()
    if op not in ("transform", "transformation"):
        return parsed
    if not _prompt_suggests_row_presence_filter_not_column_drop(prompt):
        return parsed

    hinted = _schema_fields_hinted_for_presence(prompt, schema)
    hinted_norm = {_normalize_schema_token(h) for h in hinted if _normalize_schema_token(h)}

    tr = parsed.get("transformation_rules")
    if tr is None:
        return parsed

    rules_list: List[Any]
    tr_dict: Optional[Dict[str, Any]] = None
    if isinstance(tr, list):
        rules_list = list(tr)
    elif isinstance(tr, dict) and isinstance(tr.get("fields"), list):
        tr_dict = dict(tr)
        rules_list = list(tr["fields"])
    else:
        return parsed

    kept: List[Any] = []
    for rule in rules_list:
        if not isinstance(rule, dict):
            kept.append(rule)
            continue
        if not _is_column_removal_transform_rule(rule):
            kept.append(rule)
            continue
        targets_norm = {_normalize_schema_token(t) for t in _removal_rule_target_fields(rule)}
        targets_norm.discard("")
        if not targets_norm:
            kept.append(rule)
            continue
        strip_rule = False
        if hinted_norm and (targets_norm & hinted_norm):
            strip_rule = True
        if strip_rule:
            continue
        kept.append(rule)

    out = dict(parsed)
    if not kept:
        out.pop("transformation_rules", None)
    elif isinstance(tr, list):
        out["transformation_rules"] = kept
    elif tr_dict is not None:
        out["transformation_rules"] = {**tr_dict, "fields": kept}
    else:
        out["transformation_rules"] = kept
    return out


def _resolve_email_field(schema: List[str]) -> str:
    for field in schema or []:
        if isinstance(field, str) and "email" in field.lower():
            return field.strip()
    return "Email"


def _delta_is_meaningful(op: str, delta: Optional[Dict[str, Any]]) -> bool:
    """True when parsed LLM/heuristic output contains rules for this operation."""
    if not isinstance(delta, dict) or not delta:
        return False
    key = (op or "").strip().lower()
    if key in ("transform", "transformation"):
        tr = delta.get("transformation_rules")
        if isinstance(tr, dict) and isinstance(tr.get("fields"), list):
            return bool(tr["fields"])
        if isinstance(tr, list):
            return bool(tr)
        fields = delta.get("fields")
        return isinstance(fields, list) and bool(fields)
    if key == "filter":
        fr = delta.get("filter_rules")
        if isinstance(fr, dict):
            return bool(fr.get("conditions"))
        return bool(fr)
    if key in ("dedupe", "deduplication"):
        dr = delta.get("deduplication_rules")
        return isinstance(dr, dict) and bool(dr)
    if key in ("validate", "validation", "cleanup_values"):
        dc = delta.get("data_cleanup_rules")
        return isinstance(dc, dict) and bool(dc)
    return bool(delta)


def heuristic_rules_for_prompt(
    prompt: str, source_schema: List[str]
) -> Optional[Dict[str, Any]]:
    """
    Deterministic filter fallback for common email prompts when filter LLM returns {}.
    """
    pl = (prompt or "").strip().lower()
    if not pl:
        return None

    wants_row_action = any(
        k in pl
        for k in (
            "remove",
            "drop",
            "delete",
            "exclude",
            "filter",
            "without",
            "invalid",
            "bad ",
        )
    )
    if "email" in pl and wants_row_action and (
        "invalid" in pl or "valid email" in pl or "bad email" in pl
    ):
        email_field = _resolve_email_field(source_schema)
        return {
            "filter_rules": {
                "conditions": [
                    {"field": email_field, "operator": "valid_email"},
                ],
                "logic": "AND",
            }
        }
    return None


def _filter_transform_context(
    existing_rules: Optional[Dict[str, Any]],
    source_schema: List[str],
) -> tuple[str, List[str], List[str]]:
    """Prompt block + post-transform schema + merged-away fields for filter proposals."""
    if not existing_rules:
        return "", list(source_schema or []), []
    from utils.extraction_rules_validate import (
        fields_removed_by_transform,
        predict_post_transform_schema,
    )

    tr = existing_rules.get("transformation_rules")
    if not tr:
        return "", list(source_schema or []), []
    post_schema = predict_post_transform_schema(source_schema, tr)
    removed = fields_removed_by_transform(tr)
    lines = [
        "Prior transform step (already merged into this job):",
        f"Columns available AFTER transform — filter ONLY on these: {json.dumps(post_schema)}",
    ]
    if removed:
        lines.append(
            f"Columns REMOVED by merge/exclude — do NOT filter on these: {json.dumps(removed)}"
        )
    lines.append(
        "Only add filter conditions for intents the user explicitly requested in the current message. "
        "Do not add is_not_empty on merged source columns. "
        "Example: 'remove invalid emails' → valid_email on Email only."
    )
    return "\n".join(lines) + "\n", post_schema, removed


def _sanitize_filter_delta_for_transform(
    delta: Dict[str, Any],
    source_schema: List[str],
    existing_rules: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not delta.get("filter_rules") or not existing_rules:
        return delta
    from utils.extraction_rules_validate import align_filter_rules_with_transform

    aligned = align_filter_rules_with_transform(
        delta["filter_rules"],
        source_schema,
        existing_rules.get("transformation_rules"),
    )
    out = dict(delta)
    if aligned is not None:
        out["filter_rules"] = aligned
    return out


async def propose_rules_delta(
    operation_id: str,
    prompt: str,
    sample_rows: List[Dict[str, Any]],
    source_schema: List[str],
    *,
    data_quality_report: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    existing_rules: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    op = (operation_id or "").strip().lower()

    if data_quality_report and op in ("filter", "validate", "validation", "cleanup_values"):
        qr_rules = convert_quality_report_to_extraction_rules(data_quality_report)
        if post_extract_debug_enabled():
            log_snapshot(
                f"propose_rules_delta.quality_report_hint op={op}",
                {
                    "qr_keys": list(qr_rules.keys()) if isinstance(qr_rules, dict) else None,
                    "short_qr": qr_rules,
                },
            )
        if op == "filter" and qr_rules.get("filter_rules"):
            from utils.extraction_rules_validate import sanitize_filter_rules_dict

            out = {
                "filter_rules": sanitize_filter_rules_dict(qr_rules["filter_rules"])
                or {"conditions": [], "logic": "AND"}
            }
            if post_extract_debug_enabled():
                log_snapshot("propose_rules_delta.result_from_quality_report filter", out)
            return out
        if op in ("validate", "validation", "cleanup_values") and qr_rules.get("data_cleanup_rules"):
            from utils.extraction_rules_validate import sanitize_data_cleanup_rules

            out = {"data_cleanup_rules": sanitize_data_cleanup_rules(qr_rules["data_cleanup_rules"])}
            if post_extract_debug_enabled():
                log_snapshot("propose_rules_delta.result_from_quality_report validate", out)
            return out
        if op == "dedupe" and qr_rules.get("deduplication_rules"):
            out = {"deduplication_rules": qr_rules["deduplication_rules"]}
            if post_extract_debug_enabled():
                log_snapshot("propose_rules_delta.result_from_quality_report dedupe", out)
            return out

    if post_extract_debug_enabled():
        log_snapshot(
            f"propose_rules_delta.llm_path op={op}",
            {
                "prompt_excerpt": (prompt or "")[:600],
                "schema_fields": (source_schema or [])[:80],
                "sample_row_keys": [sorted(r.keys()) for r in (sample_rows[:3] or []) if isinstance(r, dict)],
            },
        )

    from openai import OpenAI
    from services.architect_agent import async_openai_call

    bucket_hint = {
        "transform": (
            "transformation_rules only (fields array). "
            "CRITICAL — ROW vs COLUMN: If the user asks to remove/drop/exclude RECORDS or ROWS "
            "that are missing a value, have no name, are 'without' a field, or have blank/empty "
            "values, that is FILTERING (handled by the filter operation). Return {{}} for this "
            "operation unless they also asked for an unrelated column change (merge, regex, etc.). "
            "Never use remove/exclude/remove_fields on a column just to drop rows missing that column — "
            "that removes the COLUMN from every row and breaks later filters. "
            "Only use remove/exclude/remove_fields when the user explicitly wants to delete/hide/stop "
            "outputting a COLUMN or FIELD from the dataset. "
            "Otherwise use merge/combine, split_2/split_3, prepend/append (action transform + transformation), "
            "or regex_replace / nested transform with pattern+replacement. "
            "Use ONLY column names from 'Current schema fields' — never invent fields absent from that list "
            "(e.g. do not reference FirstName if the schema only has FullName). "
            "For split: action split_2 (or split_3), source_field = existing column to split, "
            "output_fields = new column names, split_char default space. "
            "For merge/combine: source_fields list + output_field (or target_field) for the result column. "
            "Do not put row filtering in transformation_rules."
        ),
        "transformation": "transformation_rules only",
        "filter": (
            'filter_rules (conditions + logic) and optional ordering_rules { "sort": [{"field":"Name","direction":"asc"}] } '
            'for sort only — do NOT include "limit" unless the user explicitly asks to cap rows (e.g. top 10, first 50). '
            'For valid email use operator "valid_email" (not is_valid). Use exact field names from "Columns available AFTER transform". '
            'Only propose filter conditions for what the user explicitly asked in the current message — do not add extra data-quality guards. '
            'When a prior transform merged/combined columns, those source columns are gone before filter runs — never use is_not_empty on them. '
            'When the user says remove/drop ROWS or RECORDS "without" a value, "missing" a field, or blank Account Name / '
            'similar, add a condition with operator "is_not_empty" on that schema field ONLY if that column still exists after transform. '
            'Do not rely on transform exclude/remove for that intent. '
            'For membership use operator "in" or "not_in" (aliases: is_in, nin, is_not_in) with value as a JSON array. '
            'For regex use operator "regex_matches" with string "value" as pattern; optional "case_insensitive": true. '
            'For calendar dates on string fields use "date_between" with value [start,end], or "date_on_or_after" / "date_on_or_before" with a single boundary (ISO-like strings). '
            "IMPORTANT: Never use filter_rules (especially regex_matches on a field) to \"select\" rows before fixing a specific bad cell value — use transformation_rules only. "
            "regex_matches on the old malformed value will drop every row after the value is corrected."
        ),
        "dedupe": "deduplication_rules only (enabled, fields, strategy)",
        "deduplication": "deduplication_rules only",
        "validate": "data_cleanup_rules only. Supported per-field keys: type (email|phone|date), trim (true), format (e.g. e164 for phone), default_region (string, default US), normalize_country (alpha2), input_formats (list of strftime for date), to_iso (bool for date output as YYYY-MM-DD), normalize_case (title|upper|lower), regex_replace {pattern, replacement, max_count}, handle_empty (skip), fill_missing.",
        "validation": "data_cleanup_rules only. Supported per-field keys: type (email|phone|date), trim (true), format (e.g. e164 for phone), default_region (string, default US), normalize_country (alpha2), input_formats (list of strftime for date), to_iso (bool for date output as YYYY-MM-DD), normalize_case (title|upper|lower), regex_replace {pattern, replacement, max_count}, handle_empty (skip), fill_missing.",
    }.get(op, "only the rule buckets needed for the user request")

    system = (
        "You propose extraction rules as JSON. Return ONLY valid JSON with rule buckets. "
        "Do not rewrite data rows. Use the current schema field names exactly."
    )
    learning_block = ""
    if user_id:
        try:
            from services.pipeline_learning.service import PipelineLearningService

            if PipelineLearningService.enabled():
                learning_block = await PipelineLearningService.get_retrieval_context(
                    user_id=str(user_id),
                    query_text=f"{op} {prompt}",
                    stage="data_ops",
                    chunk_type="data_ops_recipe",
                )
                if learning_block:
                    learning_block = learning_block + "\n\n"
        except Exception:
            learning_block = ""

    transform_context = ""
    if op == "filter":
        transform_context, _, _ = _filter_transform_context(existing_rules, source_schema)

    user = f"""{learning_block}{transform_context}Operation: {op}
User request: {prompt}
Current schema fields: {json.dumps(source_schema[:80])}
Sample rows (up to 5): {json.dumps(sample_rows[:5], default=str)}
Return JSON with {bucket_hint}.
Use exact names from Current schema fields for source_field / source_fields.
Merge example: {{"action": "merge", "source_fields": ["A", "B"], "output_field": "Combined", "separator": " "}}
Split example: {{"action": "split_2", "source_field": "FullName", "output_fields": ["FirstName", "LastName"], "split_char": " "}}
"""

    from services.llm import tasks as llm_tasks

    response = await async_openai_call(
        None,
        task=llm_tasks.RULES_PROPOSER,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=1500,
        temperature=0.1,
    )
    text = (response.choices[0].message.content or "").strip()
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if json_match:
        text = json_match.group(1)
    else:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            if post_extract_debug_enabled():
                log_snapshot(
                    f"propose_rules_delta.llm_parsed op={op}",
                    {
                        "model_raw_excerpt": text[:2500],
                        "parsed_keys": list(parsed.keys()),
                        "parsed": parsed,
                    },
                )
            from utils.extraction_rules_validate import (
                sanitize_data_cleanup_rules,
                sanitize_filter_rules_dict,
                sanitize_ordering_rules,
            )

            if isinstance(parsed.get("filter_rules"), dict):
                parsed = dict(parsed)
                parsed["filter_rules"] = sanitize_filter_rules_dict(parsed["filter_rules"]) or {
                    "conditions": [],
                    "logic": "AND",
                }
                parsed = _sanitize_filter_delta_for_transform(
                    parsed, source_schema, existing_rules
                )
            if isinstance(parsed.get("data_cleanup_rules"), dict):
                parsed = dict(parsed)
                parsed["data_cleanup_rules"] = sanitize_data_cleanup_rules(parsed["data_cleanup_rules"])
            if isinstance(parsed.get("ordering_rules"), dict):
                parsed = dict(parsed)
                orules = sanitize_ordering_rules(parsed["ordering_rules"])
                if orules:
                    parsed["ordering_rules"] = orules
                else:
                    parsed.pop("ordering_rules", None)
            from utils.rule_merge import normalize_data_ops_delta_shape

            pre_norm = dict(parsed)
            parsed = normalize_data_ops_delta_shape(parsed)
            if post_extract_debug_enabled():
                log_snapshot(
                    f"propose_rules_delta.normalized op={op}",
                    {
                        "input_shape": {
                            "transformation_rules": type(pre_norm.get("transformation_rules")).__name__
                            if "transformation_rules" in pre_norm
                            else None,
                            "filter_rules": type(pre_norm.get("filter_rules")).__name__
                            if "filter_rules" in pre_norm
                            else None,
                            "deduplication_rules": type(pre_norm.get("deduplication_rules")).__name__
                            if "deduplication_rules" in pre_norm
                            else None,
                            "data_cleanup_rules": type(pre_norm.get("data_cleanup_rules")).__name__
                            if "data_cleanup_rules" in pre_norm
                            else None,
                            "ordering_rules": type(pre_norm.get("ordering_rules")).__name__
                            if "ordering_rules" in pre_norm
                            else None,
                        },
                        "output_keys": sorted(list(parsed.keys())),
                        "coerced_count": len(set(parsed.keys()) - set(pre_norm.keys()))
                        + len(set(pre_norm.keys()) - set(parsed.keys())),
                    },
                )
            if op in ("transform", "transformation"):
                parsed = sanitize_transform_delta_row_vs_column_language(
                    prompt,
                    source_schema or [],
                    parsed,
                    operation_id=op,
                )
                from utils.extraction_rules_validate import align_transform_rules_with_schema

                tr = parsed.get("transformation_rules")
                if tr is not None:
                    aligned_tr = align_transform_rules_with_schema(tr, source_schema or [])
                    parsed = dict(parsed)
                    if isinstance(aligned_tr, list) and aligned_tr:
                        parsed["transformation_rules"] = {"fields": aligned_tr}
                    elif isinstance(aligned_tr, dict) and aligned_tr.get("fields"):
                        parsed["transformation_rules"] = aligned_tr
                    else:
                        parsed.pop("transformation_rules", None)
            if _delta_is_meaningful(op, parsed):
                return parsed
    except json.JSONDecodeError as exc:
        if post_extract_debug_enabled():
            logger.info(
                "[POST_EXTRACT_DEBUG] propose_rules_delta JSON parse failed op=%s err=%s excerpt=%s",
                op,
                exc,
                text[:1200],
            )
        pass
    if post_extract_debug_enabled():
        log_snapshot(f"propose_rules_delta.empty_return op={op}", {"model_raw_excerpt": text[:2500]})

    if op == "filter":
        heuristic = heuristic_rules_for_prompt(prompt, source_schema)
        if heuristic:
            if post_extract_debug_enabled():
                log_snapshot(f"propose_rules_delta.heuristic_fallback op={op}", heuristic)
            return _sanitize_filter_delta_for_transform(
                heuristic, source_schema, existing_rules
            )

    return {}
