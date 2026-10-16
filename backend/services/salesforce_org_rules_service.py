"""
Cache Salesforce validation rules (Tooling) and duplicate/matching rules on salesforce_objects.

- validation_rules_json: Tooling ValidationRule + Metadata
- duplicate_rules_json: SOQL summaries + Metadata API retrieve (DuplicateRule + MatchingRule XML files);
  plus metadata_parsed (XML → JSON) for each file path.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from uuid import UUID

import httpx

from db.connection import database
from utils.salesforce_metadata_api import metadata_retrieve_unpackaged_types
from utils.salesforce_metadata_xml import enrich_duplicate_rules_json

logger = logging.getLogger(__name__)

SF_API = "v59.0"
ORG_RULES_CONCURRENCY = 5


def _soql_string_literal(value: str) -> str:
    return "'" + (value or "").replace("\\", "\\\\").replace("'", "\\'") + "'"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def _sf_query_pages(
    instance_url: str,
    access_token: str,
    soql: str,
    *,
    tooling: bool = False,
) -> List[Dict[str, Any]]:
    base = (instance_url or "").rstrip("/")
    path_prefix = f"/services/data/{SF_API}/{'tooling/' if tooling else ''}query"
    url = f"{base}{path_prefix}?q={quote(soql)}"
    headers = {"Authorization": f"Bearer {access_token}"}
    records: List[Dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=120.0) as client:
        while url:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Salesforce query failed ({'tooling' if tooling else 'data'}) "
                    f"{resp.status_code}: {resp.text[:800]}"
                )
            data = resp.json()
            records.extend(data.get("records") or [])
            if data.get("done", True):
                break
            nxt = data.get("nextRecordsUrl")
            if not nxt:
                break
            url = base + nxt if nxt.startswith("/") else nxt

    return records


async def _fetch_validation_rule_detail(
    instance_url: str,
    access_token: str,
    rule_id: str,
) -> Dict[str, Any]:
    """Fetch one ValidationRule row including Metadata (Tooling API allows at most one row)."""
    rid = (rule_id or "").strip()
    if not rid:
        raise ValueError("ValidationRule Id is required")
    q = (
        "SELECT Id, FullName, ValidationName, Active, Metadata "
        "FROM ValidationRule "
        f"WHERE Id = {_soql_string_literal(rid)}"
    )
    rows = await _sf_query_pages(instance_url, access_token, q, tooling=True)
    if not rows:
        raise RuntimeError(f"ValidationRule {rid} not found")
    return rows[0]


async def fetch_validation_rules_json(
    instance_url: str,
    access_token: str,
    object_api_name: str,
) -> Dict[str, Any]:
    obj = (object_api_name or "").strip()
    list_q = (
        "SELECT Id, ValidationName, Active "
        "FROM ValidationRule "
        f"WHERE EntityDefinition.QualifiedApiName = {_soql_string_literal(obj)}"
    )
    summaries = await _sf_query_pages(instance_url, access_token, list_q, tooling=True)
    rule_ids = [str(r["Id"]) for r in summaries if r.get("Id")]
    if rule_ids:
        rules = await asyncio.gather(
            *[
                _fetch_validation_rule_detail(instance_url, access_token, rid)
                for rid in rule_ids
            ]
        )
        rules = list(rules)
    else:
        rules = []
    return {
        "synced_at": _now_iso(),
        "object_api_name": obj,
        "rules": rules,
    }


def _metadata_member_names(object_api_name: str, rows: List[Dict[str, Any]]) -> List[str]:
    """Build Metadata API fullNames: ObjectName.DeveloperName"""
    obj = (object_api_name or "").strip()
    seen: set = set()
    out: List[str] = []
    for r in rows or []:
        dev = (r.get("DeveloperName") or "").strip()
        if not dev:
            continue
        fn = f"{obj}.{dev}"
        if fn not in seen:
            seen.add(fn)
            out.append(fn)
    return out


async def fetch_duplicate_rules_bundle_json(
    instance_url: str,
    access_token: str,
    object_api_name: str,
) -> Dict[str, Any]:
    """
    REST SOQL: DuplicateRule + MatchingRule headers for the object.
    Metadata API: retrieve DuplicateRule + MatchingRule XML (full definitions) in one job.
    """
    obj = (object_api_name or "").strip()
    fetch_errors: List[str] = []

    dup_soql = (
        "SELECT Id, DeveloperName, MasterLabel, IsActive, SobjectType, Language "
        "FROM DuplicateRule "
        f"WHERE SobjectType = {_soql_string_literal(obj)}"
    )
    try:
        duplicate_rules = await _sf_query_pages(
            instance_url, access_token, dup_soql, tooling=False
        )
    except Exception as e:
        fetch_errors.append(f"DuplicateRule: {e}")
        duplicate_rules = []

    mr_soql = (
        "SELECT Id, DeveloperName, MasterLabel, SobjectType, RuleStatus, "
        "MatchEngine, BooleanFilter, Description, Language "
        "FROM MatchingRule "
        f"WHERE SobjectType = {_soql_string_literal(obj)}"
    )
    try:
        matching_rules = await _sf_query_pages(
            instance_url, access_token, mr_soql, tooling=False
        )
    except Exception as e:
        fetch_errors.append(f"MatchingRule: {e}")
        matching_rules = []

    dup_members = _metadata_member_names(obj, duplicate_rules)
    mr_members = _metadata_member_names(obj, matching_rules)

    types_members: List[Tuple[str, List[str]]] = []
    if dup_members:
        types_members.append(("DuplicateRule", dup_members))
    if mr_members:
        types_members.append(("MatchingRule", mr_members))

    metadata_files: Dict[str, str] = {}
    metadata_retrieve_error: Optional[str] = None

    if types_members:
        try:
            ok, files, err = await metadata_retrieve_unpackaged_types(
                access_token,
                instance_url,
                types_members,
                max_wait_seconds=180.0,
            )
            if ok and files is not None:
                metadata_files = files
            else:
                metadata_retrieve_error = err or "Metadata retrieve failed"
                if err:
                    fetch_errors.append(f"Metadata retrieve: {err}")
        except Exception as e:
            metadata_retrieve_error = str(e)
            fetch_errors.append(f"Metadata retrieve: {e}")

    bundle = {
        "synced_at": _now_iso(),
        "object_api_name": obj,
        "duplicate_rules": duplicate_rules,
        "matching_rules": matching_rules,
        "metadata_source": "metadata_api",
        "metadata_files": metadata_files,
        "metadata_retrieve_error": metadata_retrieve_error,
        "fetch_errors": fetch_errors,
    }
    return enrich_duplicate_rules_json(bundle)


async def persist_org_rules_for_object(
    user_id: UUID,
    org_id: str,
    object_api_name: str,
    validation_payload: Dict[str, Any],
    duplicate_payload: Dict[str, Any],
) -> None:
    vj = json.dumps(validation_payload, default=str)
    dj = json.dumps(duplicate_payload, default=str)
    await database.execute(
        """
        UPDATE salesforce_objects
        SET validation_rules_json = CAST(:v AS jsonb),
            duplicate_rules_json = CAST(:d AS jsonb)
        WHERE user_id = :user_id AND org_id = :org_id AND object_api_name = :object_api_name
        """,
        values={
            "v": vj,
            "d": dj,
            "user_id": str(user_id),
            "org_id": org_id,
            "object_api_name": object_api_name,
        },
    )


async def _sync_one_object_org_rules(
    user_id: UUID,
    org_id: str,
    instance_url: str,
    access_token: str,
    object_api_name: str,
) -> None:
    v_json = await fetch_validation_rules_json(instance_url, access_token, object_api_name)
    d_json = await fetch_duplicate_rules_bundle_json(instance_url, access_token, object_api_name)
    await persist_org_rules_for_object(user_id, org_id, object_api_name, v_json, d_json)


async def sync_org_rules_after_schema_sync(
    user_id: UUID,
    org_id: str,
    instance_url: str,
    access_token: str,
) -> None:
    """
    After objects and fields are stored, fetch validation + duplicate/matching snapshots
    for each object row and UPDATE salesforce_objects JSON columns.
    Failures are logged per object; do not raise (schema sync already succeeded).
    """
    rows = await database.fetch_all(
        """
        SELECT object_api_name FROM salesforce_objects
        WHERE user_id = :user_id AND org_id = :org_id
        ORDER BY object_api_name
        """,
        values={"user_id": str(user_id), "org_id": org_id},
    )
    names = [r["object_api_name"] for r in (rows or [])]
    if not names:
        logger.info("[org rules] No salesforce_objects rows for org %s; skipping", org_id)
        return

    sem = asyncio.Semaphore(ORG_RULES_CONCURRENCY)

    async def _run(name: str) -> None:
        async with sem:
            try:
                await _sync_one_object_org_rules(
                    user_id, org_id, instance_url, access_token, name
                )
            except Exception as e:
                detail = str(e).strip() or type(e).__name__
                logger.warning(
                    "[org rules] Failed for %s / %s: %s",
                    org_id,
                    name,
                    detail,
                    exc_info=True,
                )

    await asyncio.gather(*[_run(n) for n in names])


async def sync_org_rules_for_objects(
    user_id: UUID,
    org_id: str,
    instance_url: str,
    access_token: str,
    object_api_names: List[str],
) -> None:
    """Sync validation + duplicate rules for a subset of objects (drift-only path)."""
    names = [n.strip() for n in object_api_names if n and str(n).strip()]
    if not names:
        return

    sem = asyncio.Semaphore(ORG_RULES_CONCURRENCY)

    async def _run(name: str) -> None:
        async with sem:
            try:
                await _sync_one_object_org_rules(
                    user_id, org_id, instance_url, access_token, name
                )
            except Exception as e:
                detail = str(e).strip() or type(e).__name__
                logger.warning(
                    "[org rules] Failed for %s / %s: %s",
                    org_id,
                    name,
                    detail,
                    exc_info=True,
                )

    await asyncio.gather(*[_run(n) for n in names])
    logger.info(
        "[org rules] Completed sync for org %s (%d objects)", org_id, len(names)
    )


def _coerce_json_mapping(val: Any) -> Optional[Dict[str, Any]]:
    if val is None:
        return None
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _parse_tooling_metadata(metadata: Any) -> Dict[str, Any]:
    if metadata is None:
        return {}
    if isinstance(metadata, dict):
        return metadata
    if isinstance(metadata, str):
        try:
            parsed = json.loads(metadata)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _truncate_text(text: str, max_len: int) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    if len(t) <= max_len:
        return t
    return t[: max_len - 1].rstrip() + "…"


def _normalize_duplicate_filter_items(items: Any) -> List[Dict[str, Any]]:
    if not items:
        return []
    if isinstance(items, dict):
        return [items]
    if isinstance(items, list):
        return [x for x in items if isinstance(x, dict)]
    return []


def _duplicate_rule_inner_from_metadata(
    duplicate_payload: Dict[str, Any], developer_name: str
) -> Optional[Dict[str, Any]]:
    """Resolve DuplicateRule dict from metadata_parsed (keys like duplicateRules/Obj.DevName.duplicateRule)."""
    if not developer_name:
        return None
    mp = duplicate_payload.get("metadata_parsed") or {}
    if not isinstance(mp, dict):
        return None
    needle = f"{developer_name}.duplicateRule"
    for path, parsed in mp.items():
        if not isinstance(path, str) or not isinstance(parsed, dict):
            continue
        if needle not in path:
            continue
        inner = parsed.get("DuplicateRule")
        if isinstance(inner, dict):
            return inner
        if "alertText" in parsed or "duplicateRuleFilter" in parsed:
            return parsed
    return None


def _format_duplicate_rule_filter(inner: Dict[str, Any]) -> str:
    """Plain-text duplicate rule filter only (duplicateRuleFilterItems); matching rule name omitted from UI."""
    chunks: List[str] = []
    drf = inner.get("duplicateRuleFilter")
    if isinstance(drf, dict):
        items = _normalize_duplicate_filter_items(drf.get("duplicateRuleFilterItems"))
        parts: List[str] = []
        for it in items:
            field = (it.get("field") or "").strip()
            op = (it.get("operation") or "").strip()
            val = (it.get("value") or "").strip()
            table = (it.get("table") or "").strip()
            if not field or not op:
                continue
            val_part = f" {val}" if val else ""
            if table:
                parts.append(f"{table}.{field} {op}{val_part}")
            else:
                parts.append(f"{field} {op}{val_part}")
        bf = drf.get("booleanFilter")
        if parts:
            if isinstance(bf, str) and bf.strip() and len(parts) > 1:
                bf_u = bf.upper()
                if " AND " in bf_u and " OR " not in bf_u:
                    joiner = " AND "
                else:
                    joiner = " OR "
                chunks.append(joiner.join(parts))
            else:
                chunks.append(" ; ".join(parts))

    return " ; ".join(chunks) if chunks else ""


def _build_duplicate_rule_summary(
    label: str,
    active: bool,
    duplicate_payload: Dict[str, Any],
    developer_name: str,
    object_label: str,
) -> str:
    if not active:
        return f'"{label}" is turned off.'
    inner = _duplicate_rule_inner_from_metadata(duplicate_payload, developer_name or "")
    if not inner:
        return (
            f'"{label}" looks for possible duplicate records when you create or edit '
            f'{duplicate_payload.get("object_api_name") or object_label or "records"}.'
        )

    alert = (inner.get("alertText") or "").strip()
    desc = (inner.get("description") or "").strip()
    if isinstance(alert, str) and alert.lower() in ("", "null"):
        alert = ""
    # description can be xsi:nil stored as empty
    rule_body = _format_duplicate_rule_filter(inner)
    if not rule_body:
        ml = (inner.get("masterLabel") or "").strip()
        rule_body = ml or "duplicate detection"

    rule_body = _truncate_text(rule_body, 220)
    msg = alert or desc
    if msg:
        return f'"{label}": {rule_body} — {msg}'
    return f'"{label}": {rule_body}'


def build_org_rules_display_for_object(
    object_api_name: str,
    validation_payload: Any,
    duplicate_payload: Any,
) -> Dict[str, Any]:
    """
    Normalize cached validation_rules_json + duplicate_rules_json into stable display JSON for mapping UI.

    Returns:
        {
          "object_api_name": str,
          "validation": [{ id, validation_name, full_name, active, error_message,
                           error_condition_formula, description, summary }, ...],
          "duplicate": [{ id, master_label, developer_name, active, summary }, ...],
          "matching": [{ id, master_label, developer_name, rule_status, match_engine, summary }, ...],
          "has_any": bool,
        }
    """
    obj = (object_api_name or "").strip()
    vp = _coerce_json_mapping(validation_payload) or {}
    dp = _coerce_json_mapping(duplicate_payload) or {}

    validation_out: List[Dict[str, Any]] = []
    for r in vp.get("rules") or []:
        if not isinstance(r, dict):
            continue
        name = (r.get("ValidationName") or r.get("FullName") or "Validation rule").strip()
        meta = _parse_tooling_metadata(r.get("Metadata"))
        msg = (meta.get("errorMessage") or meta.get("description") or "").strip()
        formula = (meta.get("errorConditionFormula") or "").strip()
        active = r.get("Active", True) is not False

        if not active:
            summary = f'"{name}" is turned off, so it does not run.'
        elif msg and formula:
            # Compact: rule name, condition, error message (no "when" / Salesforce boilerplate)
            summary = f'"{name}": {_truncate_text(formula, 220)} — {msg}'
        elif msg:
            summary = f'"{name}": {msg}'
        elif formula:
            summary = f'"{name}": {_truncate_text(formula, 220)}'
        else:
            summary = f'"{name}" (no condition or message stored).'

        validation_out.append(
            {
                "id": r.get("Id"),
                "full_name": r.get("FullName"),
                "validation_name": r.get("ValidationName"),
                "active": active,
                "error_message": msg,
                "error_condition_formula": formula,
                "description": (meta.get("description") or "").strip(),
                "summary": summary,
            }
        )

    duplicate_out: List[Dict[str, Any]] = []
    for d in dp.get("duplicate_rules") or []:
        if not isinstance(d, dict):
            continue
        label = (d.get("MasterLabel") or d.get("DeveloperName") or "Duplicate rule").strip()
        active = d.get("IsActive", True) is not False
        dev = (d.get("DeveloperName") or "").strip()
        summary = _build_duplicate_rule_summary(label, active, dp, dev, obj)
        duplicate_out.append(
            {
                "id": d.get("Id"),
                "master_label": d.get("MasterLabel"),
                "developer_name": d.get("DeveloperName"),
                "active": active,
                "summary": summary,
            }
        )

    matching_out: List[Dict[str, Any]] = []
    for m in dp.get("matching_rules") or []:
        if not isinstance(m, dict):
            continue
        label = (m.get("MasterLabel") or m.get("DeveloperName") or "Matching rule").strip()
        status = (m.get("RuleStatus") or "").strip()
        engine = (m.get("MatchEngine") or "").strip()
        status_l = status.lower()
        eng_suffix = f" using {engine}" if engine else ""
        if status_l == "inactive" or status == "Inactive":
            summary = f'"{label}" matching is inactive{eng_suffix}.'
        else:
            summary = (
                f'"{label}" defines how fields are compared to find duplicates{eng_suffix}.'
            )
        matching_out.append(
            {
                "id": m.get("Id"),
                "master_label": m.get("MasterLabel"),
                "developer_name": m.get("DeveloperName"),
                "rule_status": status or None,
                "match_engine": engine or None,
                "summary": summary,
            }
        )

    has_any = bool(validation_out or duplicate_out or matching_out)
    return {
        "object_api_name": obj,
        "validation": validation_out,
        "duplicate": duplicate_out,
        "matching": matching_out,
        "has_any": has_any,
    }
