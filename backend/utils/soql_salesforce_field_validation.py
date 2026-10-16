"""
Validate SOQL SELECT field references against synced salesforce_object_fields,
including relationship paths (Account.Name, Foo__r.Bar__c, Account.Owner.Name).
"""
from __future__ import annotations

import re
from typing import Any, List, Optional, Set, Tuple
from uuid import UUID

from db.connection import database


def relationship_token_for_lookup_field(
    field_api_name: str,
    relationship_name: Optional[str],
) -> Optional[str]:
    """
    SOQL relationship name for a lookup field (segment before the dot), from describe or heuristics.
    """
    if relationship_name and str(relationship_name).strip():
        return str(relationship_name).strip()
    fn = (field_api_name or "").strip()
    if fn.endswith("__c"):
        return fn[:-3] + "__r"
    if fn.endswith("Id") and len(fn) > 2:
        return fn[:-2]
    return None


async def _canonical_field_api_name(
    user_id: str,
    org_id: str,
    object_api_name: str,
    field_api_name: str,
) -> Optional[str]:
    row = await database.fetch_one(
        """
        SELECT field_api_name FROM salesforce_object_fields
        WHERE user_id = :user_id AND org_id = :org_id
          AND object_api_name = :obj
          AND LOWER(field_api_name) = LOWER(:field)
        LIMIT 1
        """,
        {
            "user_id": user_id,
            "org_id": org_id,
            "obj": object_api_name,
            "field": field_api_name.strip(),
        },
    )
    if not row:
        return None
    return str(dict(row).get("field_api_name") or "").strip() or None


async def build_salesforce_mapping_select_items(
    user_id: str,
    org_id: str,
    from_object: str,
    mappings: List[Any],
) -> Tuple[List[str], Optional[str]]:
    """
    Build SOQL SELECT list items for a mapping save.

    IMPORTANT: SOQL does NOT support arbitrary field aliases for non-aggregate expressions. We therefore:
    - For direct fields: select the canonical field API name (must match extracted_field_name semantics).
    - For lookup rows (lookup_of): select relationshipName.field (no alias). Downstream extraction
      normalizes relationship results to flattened keys like AccountName / AccountPhone.

    Returns (items, error_message).
    """
    from_obj = (from_object or "").strip()
    if not from_obj:
        return [], "Could not determine the Salesforce object in the query's FROM clause."

    items: List[str] = []

    for idx, m in enumerate(mappings):
        row_n = idx + 1
        extracted = (getattr(m, "extracted_field_name", None) or "").strip()
        obj = (getattr(m, "salesforce_object", None) or "").strip()
        fld = (getattr(m, "salesforce_field", None) or "").strip()
        lookup_raw = getattr(m, "lookup_of", None)
        lookup_of = (lookup_raw or "").strip() if lookup_raw else ""

        if not extracted or not obj or not fld:
            return [], f"Mapping row {row_n}: source field, destination target, and destination field are required."

        if lookup_of:
            parts = [p.strip() for p in lookup_of.split(".") if p.strip()]
            if len(parts) != 2:
                return (
                    [],
                    f"Mapping row {row_n}: lookup_of must look like BaseObject.LookupFieldApiName "
                    f"(e.g. Contact.AccountId); got '{lookup_of}'.",
                )
            base_obj, lookup_field_name = parts[0], parts[1]
            if base_obj.lower() != from_obj.lower():
                return (
                    [],
                    f"Mapping row {row_n}: lookup_of must start with the same object as the SOQL query "
                    f"('{from_obj}'), not '{base_obj}'.",
                )

            row = await database.fetch_one(
                """
                SELECT field_api_name, relationship_name, lookup_object_api_name
                FROM salesforce_object_fields
                WHERE user_id = :user_id AND org_id = :org_id
                  AND object_api_name = :base_obj
                  AND field_api_name = :lookup_field
                  AND is_lookup = true
                  AND lookup_object_api_name IS NOT NULL
                LIMIT 1
                """,
                {
                    "user_id": user_id,
                    "org_id": org_id,
                    "base_obj": base_obj,
                    "lookup_field": lookup_field_name,
                },
            )
            if not row:
                return (
                    [],
                    f"Mapping row {row_n}: cannot resolve lookup path — no synced lookup field "
                    f"'{lookup_field_name}' on '{base_obj}'. Open Connections and sync this Salesforce org.",
                )

            rd = dict(row)
            rel_tok = relationship_token_for_lookup_field(
                str(rd.get("field_api_name") or lookup_field_name),
                rd.get("relationship_name"),
            )
            if not rel_tok:
                return (
                    [],
                    f"Mapping row {row_n}: cannot resolve the SOQL relationship name for lookup field "
                    f"'{lookup_field_name}' on '{base_obj}'. Re-sync Salesforce field metadata.",
                )

            target_meta = str(rd.get("lookup_object_api_name") or "").strip()
            if target_meta.lower() != obj.lower():
                return (
                    [],
                    f"Mapping row {row_n}: lookup field '{lookup_field_name}' points to '{target_meta}', "
                    f"but the mapping targets '{obj}'. Fix the destination object or lookup_of.",
                )

            canon_field = await _canonical_field_api_name(user_id, org_id, obj, fld)
            if not canon_field:
                return (
                    [],
                    f"Mapping row {row_n}: field '{fld}' is not available on '{obj}' for this org "
                    "(sync metadata or check the field API name).",
                )

            path = f"{rel_tok}.{canon_field}"
            # No alias here: SOQL does not allow aliasing plain relationship fields.
            items.append(path)
            continue

        # Direct field on the query's base object
        if obj.lower() != from_obj.lower():
            return (
                [],
                f"Mapping row {row_n}: destination object '{obj}' does not match the SOQL base object "
                f"'{from_obj}'. For fields on a related object, set lookup_of (e.g. Contact.AccountId).",
            )

        canon = await _canonical_field_api_name(user_id, org_id, from_obj, fld)
        if not canon:
            return (
                [],
                f"Mapping row {row_n}: field '{fld}' is not available on '{from_obj}' for this org.",
            )

        # No alias for plain fields either. Require that the source key matches the actual SOQL column name.
        # (For lookup rows, the source key is produced later by record-flattening.)
        if extracted.lower() != canon.lower():
            return (
                [],
                f"Mapping row {row_n}: source field '{extracted}' must match the Salesforce field API name "
                f"'{canon}' for direct fields (SOQL doesn't support aliasing regular fields).",
            )
        items.append(canon)

    return items, None


def filter_soql_candidates_for_validation(candidates: List[str]) -> List[str]:
    """
    field_candidates_from_select_clause emits both 'Account.Name' and 'Name'.
    Skip bare segments that are only the suffix of a dotted path on the same base
    (e.g. drop standalone 'Name' when 'Account.Name' is present).
    """
    if not candidates:
        return []
    cleaned: List[str] = []
    seen: Set[str] = set()
    for c in candidates:
        t = (c or "").strip()
        if not t or t.lower() in seen:
            continue
        seen.add(t.lower())
        if "." in t:
            cleaned.append(t)
            continue
        suffix = "." + t
        if any(
            "." in x and x.lower().endswith(suffix.lower()) for x in candidates if x
        ):
            continue
        cleaned.append(t)
    return cleaned


def _field_exists_local(
    fields_by_lower: dict, field_name: str
) -> bool:
    fn = (field_name or "").strip()
    if not fn:
        return False
    if fn.lower() == "id":
        return True
    return fn.lower() in fields_by_lower


async def _load_object_field_map(
    user_id: str,
    org_id: str,
    object_api_name: str,
) -> Optional[dict]:
    """lower(field_api_name) -> canonical field_api_name for one object; None if no synced rows."""
    rows = await database.fetch_all(
        """
        SELECT field_api_name FROM salesforce_object_fields
        WHERE user_id = :user_id AND org_id = :org_id AND object_api_name = :obj
        """,
        {"user_id": user_id, "org_id": org_id, "obj": object_api_name},
    )
    if not rows:
        return None
    out = {}
    for r in rows or []:
        fn = str(dict(r).get("field_api_name") or "")
        if fn:
            out[fn.lower()] = fn
    out.setdefault("id", "Id")
    return out


async def _find_related_object_for_segment(
    user_id: str,
    org_id: str,
    current_object: str,
    segment: str,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Resolve relationship segment (e.g. Account, Foo__r) to referenced object API name.
    Returns (target_object, error_detail) — error_detail set if not found.
    """
    seg = (segment or "").strip()
    if not seg:
        return None, "empty relationship segment"

    rows = await database.fetch_all(
        """
        SELECT field_api_name, relationship_name, lookup_object_api_name
        FROM salesforce_object_fields
        WHERE user_id = :user_id AND org_id = :org_id
          AND object_api_name = :obj
          AND is_lookup = true
          AND lookup_object_api_name IS NOT NULL
        """,
        {"user_id": user_id, "org_id": org_id, "obj": current_object},
    )
    seg_l = seg.lower()

    # 1) Exact relationship_name match (preferred after sync)
    for r in rows or []:
        d = dict(r)
        rn = d.get("relationship_name")
        lo = d.get("lookup_object_api_name")
        if rn and str(rn).strip() and str(rn).lower() == seg_l:
            return str(lo), None

    # 2) Legacy / NULL relationship_name heuristics
    for r in rows or []:
        d = dict(r)
        rn = d.get("relationship_name")
        if rn and str(rn).strip():
            continue
        fn = (d.get("field_api_name") or "").strip()
        lo = d.get("lookup_object_api_name")
        if not fn or not lo:
            continue
        # Custom: Foo__r <-> Foo__c
        if seg.endswith("__r") and fn == seg[:-3] + "__c":
            return str(lo), None
        # Standard: Owner <-> OwnerId, Account <-> AccountId, etc.
        if fn.lower() == (seg + "Id").lower():
            return str(lo), None

    return None, f"relationship '{seg}' on {current_object}"


async def validate_soql_field_paths_against_synced_metadata(
    user_id: UUID,
    org_id: str,
    base_object: str,
    candidates: List[str],
) -> List[str]:
    """
    Return a list of human-readable errors for invalid field references (empty if OK).
    """
    uid = str(user_id)
    missing: List[str] = []
    seen: Set[str] = set()

    filtered = filter_soql_candidates_for_validation(candidates)

    for cand in filtered:
        raw = (cand or "").strip()
        if not raw:
            continue
        if re.match(r"^select\s+", raw, re.IGNORECASE):
            continue
        if "(" in raw and ")" in raw and not raw.startswith("("):
            # Likely function(expr) — skip deep validation
            continue

        key = raw.lower()
        if key in seen:
            continue
        seen.add(key)

        if "." not in raw:
            fmap = await _load_object_field_map(uid, org_id, base_object)
            if not fmap:
                missing.append(
                    f"{raw} (no field metadata for base object '{base_object}')"
                )
                continue
            if not _field_exists_local(fmap, raw):
                missing.append(f"{raw} on {base_object}")
            continue

        parts = [p.strip() for p in raw.split(".") if p.strip()]
        if len(parts) < 2:
            missing.append(raw)
            continue

        current_obj = base_object
        for i in range(len(parts) - 1):
            rel = parts[i]
            next_obj, err = await _find_related_object_for_segment(
                uid, org_id, current_obj, rel
            )
            if not next_obj:
                missing.append(f"{raw} ({err or 'unknown relationship'})")
                current_obj = None
                break
            current_obj = next_obj

        if current_obj is None:
            continue

        final_field = parts[-1]
        fmap = await _load_object_field_map(uid, org_id, current_obj)
        if not fmap:
            missing.append(
                f"{raw} (no field metadata for related object '{current_obj}')"
            )
            continue
        if not _field_exists_local(fmap, final_field):
            missing.append(f"{final_field} on {current_obj} (in path {raw})")

    return missing
