"""Validate field mappings against destination schema (omit unmapped source fields)."""
from __future__ import annotations

import enum
import re
from typing import Any, Dict, List, Optional, Set, Tuple


class MappingValidationPhase(enum.Enum):
    """AUTO_MAP: structural + semantic (LLM auto-map). MIGRATE: structural only (user-confirmed)."""

    AUTO_MAP = "auto_map"
    MIGRATE = "migrate"


def _norm_tokens(text: str) -> Set[str]:
    raw = re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()
    stop = {"the", "a", "an", "field", "column", "value", "data"}
    return {t for t in raw.split() if t and t not in stop}


def source_field_matches_destination_column(source_field: str, dest_column: str) -> bool:
    """
    True when a destination column plausibly corresponds to the source field.

    Prevents mapping e.g. phone → FirstName when no Phone column exists in the sheet.
    """
    src = (source_field or "").strip()
    col = (dest_column or "").strip()
    if not src or not col:
        return False

    src_compact = re.sub(r"[^a-z0-9]", "", src.lower())
    col_compact = re.sub(r"[^a-z0-9]", "", col.lower())
    if src_compact and col_compact and (src_compact in col_compact or col_compact in src_compact):
        return True

    src_tokens = _norm_tokens(src.replace("_", " "))
    col_tokens = _norm_tokens(col.replace("_", " "))
    if src_tokens & col_tokens:
        return True

    alias_groups = (
        frozenset({"phone", "mobile", "tel", "telephone", "cell", "fax"}),
        frozenset({"email", "mail"}),
        frozenset({"firstname", "fname", "given"}),
        frozenset({"lastname", "lname", "surname", "family"}),
        frozenset({"company", "organization", "org", "account"}),
        frozenset({"street", "address", "line1", "line2"}),
        frozenset({"city", "town"}),
        frozenset({"state", "province", "region"}),
        frozenset({"zip", "postal", "postcode"}),
        frozenset({"country", "nation"}),
    )

    for group in alias_groups:
        src_hit = bool(src_tokens & group) or src_compact in group
        col_hit = bool(col_tokens & group) or col_compact in group
        if src_hit and col_hit:
            return True

    return False


def _sheet_columns_index(
    sheets_structure: Dict[str, Any],
) -> Dict[str, Dict[str, str]]:
    """sheet_name_lower → {column_lower: canonical_column_name}."""
    out: Dict[str, Dict[str, str]] = {}
    for sheet_name, meta in (sheets_structure or {}).items():
        if not isinstance(meta, dict):
            continue
        cols = meta.get("columns") or {}
        if isinstance(cols, dict):
            col_names = list(cols.keys())
        elif isinstance(cols, list):
            col_names = [c.get("name") if isinstance(c, dict) else str(c) for c in cols]
        else:
            col_names = []
        sheet_key = (sheet_name or "").strip().lower()
        out[sheet_key] = {}
        for name in col_names:
            if not name:
                continue
            out[sheet_key][str(name).strip().lower()] = str(name).strip()
    return out


def _resolve_sheet_and_column(
    entry: Dict[str, Any],
    *,
    sheet_index: Dict[str, Dict[str, str]],
    default_sheet: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    sheet = (entry.get("sheet") or default_sheet or "").strip()
    column = (entry.get("column") or "").strip()
    if not sheet or not column:
        return None, None
    sheet_cols = sheet_index.get(sheet.lower())
    if not sheet_cols:
        return None, None
    canonical = sheet_cols.get(column.lower())
    if not canonical:
        return None, None
    return sheet, canonical


def _valid_mapping_entry(
    source_field: str,
    entry: Dict[str, Any],
    *,
    sheet_index: Dict[str, Dict[str, str]],
    default_sheet: Optional[str],
    phase: MappingValidationPhase = MappingValidationPhase.AUTO_MAP,
) -> bool:
    sheet, column = _resolve_sheet_and_column(
        entry, sheet_index=sheet_index, default_sheet=default_sheet
    )
    if not sheet or not column:
        return False
    if phase == MappingValidationPhase.MIGRATE:
        return True
    return source_field_matches_destination_column(source_field, column)


def filter_google_sheets_mapping_to_destination_schema(
    mapping: Dict[str, Any],
    sheets_structure: Dict[str, Any],
    *,
    default_sheet: Optional[str] = None,
    phase: MappingValidationPhase = MappingValidationPhase.AUTO_MAP,
) -> Dict[str, Any]:
    """
    Drop source fields that have no valid destination column in the target sheet.

    - Column must exist on the sheet (case-insensitive).
    - AUTO_MAP: column must plausibly match the source field name.
    - MIGRATE: structural check only (honor explicit user column picks).
    """
    if not mapping or not sheets_structure:
        return dict(mapping or {})

    sheet_index = _sheet_columns_index(sheets_structure)
    if not default_sheet and sheet_index:
        default_sheet = next(iter(sheets_structure.keys()), None)

    filtered: Dict[str, Any] = {}
    for source_field, info in mapping.items():
        if isinstance(info, list):
            kept = [
                item
                for item in info
                if isinstance(item, dict)
                and _valid_mapping_entry(
                    source_field,
                    item,
                    sheet_index=sheet_index,
                    default_sheet=default_sheet,
                    phase=phase,
                )
            ]
            if kept:
                filtered[source_field] = kept
            elif info:
                print(
                    f"⚠️ [MAP_GSHEETS] Omitted '{source_field}' — no matching destination column"
                )
            continue

        if not isinstance(info, dict):
            continue

        if _valid_mapping_entry(
            source_field,
            info,
            sheet_index=sheet_index,
            default_sheet=default_sheet,
            phase=phase,
        ):
            sheet, column = _resolve_sheet_and_column(
                info, sheet_index=sheet_index, default_sheet=default_sheet
            )
            normalized = dict(info)
            if sheet:
                normalized["sheet"] = sheet
            if column:
                normalized["column"] = column
            filtered[source_field] = normalized
        else:
            col = info.get("column")
            print(
                f"⚠️ [MAP_GSHEETS] Omitted '{source_field}' → '{col}' "
                "(column missing or no plausible match in destination)"
            )

    return filtered


def _all_destination_columns(sheet_index: Dict[str, Dict[str, str]]) -> Set[str]:
    cols: Set[str] = set()
    for per_sheet in sheet_index.values():
        cols.update(str(c).lower() for c in per_sheet.values())
    return cols


def _resolve_gsheets_mapping_source_key(
    key: str,
    entry: Dict[str, Any],
    *,
    flat_data: Dict[str, Any],
    source_keys_lower: Dict[str, str],
    used_sources: Set[str],
    dest_columns_lower: Set[str],
) -> Optional[str]:
    """Map a mapping dict key to a real extraction source field name."""
    if not key or not isinstance(entry, dict):
        return None

    if key in flat_data and key not in used_sources:
        return key

    key_lower = key.lower()
    if key_lower in source_keys_lower and source_keys_lower[key_lower] not in used_sources:
        return source_keys_lower[key_lower]

    column = (entry.get("column") or "").strip()
    col_lower = column.lower()
    key_looks_like_dest = key_lower in dest_columns_lower or (
        col_lower and key_lower == col_lower
    )

    if key_looks_like_dest:
        candidates: List[str] = []
        for sk in flat_data.keys():
            if sk in used_sources:
                continue
            if source_field_matches_destination_column(sk, column or key):
                candidates.append(sk)
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            # Prefer exact token overlap (e.g. phone → Mobile Number over Contact Number)
            def score(src: str) -> int:
                src_compact = re.sub(r"[^a-z0-9]", "", src.lower())
                col_compact = re.sub(r"[^a-z0-9]", "", (column or key).lower())
                if src_compact and col_compact and src_compact == col_compact:
                    return 100
                if src_compact in col_compact or col_compact in src_compact:
                    return 50
                return 0

            candidates.sort(key=score, reverse=True)
            return candidates[0]
        return None

    if key in flat_data and key not in used_sources:
        return key
    return key if key not in used_sources else None


def normalize_gsheets_mapping_source_keys(
    mapping: Dict[str, Any],
    flat_data: Dict[str, Any],
    sheets_structure: Dict[str, Any],
    *,
    default_sheet: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Re-key Google Sheets mappings so dict keys are extraction source fields, not destination column labels.

    LLMs sometimes return {"Email": {"column": "Email"}} instead of {"email": {"column": "Email"}}.
  """
    if not mapping or not isinstance(flat_data, dict):
        return dict(mapping or {})

    sheet_index = _sheet_columns_index(sheets_structure or {})
    if not default_sheet and sheet_index:
        default_sheet = next(iter((sheets_structure or {}).keys()), None)
    dest_columns_lower = _all_destination_columns(sheet_index)
    source_keys_lower = {str(k).lower(): k for k in flat_data.keys()}

    used_sources: Set[str] = set()
    normalized: Dict[str, Any] = {}

    for key, info in mapping.items():
        if isinstance(info, list):
            resolved = _resolve_gsheets_mapping_source_key(
                key,
                info[0] if info and isinstance(info[0], dict) else {},
                flat_data=flat_data,
                source_keys_lower=source_keys_lower,
                used_sources=used_sources,
                dest_columns_lower=dest_columns_lower,
            )
            if resolved:
                used_sources.add(resolved)
                normalized[resolved] = info
            elif key.lower() in dest_columns_lower:
                print(f"⚠️ [MAP_GSHEETS] Dropped destination-as-source key '{key}' (split mapping)")
            continue

        if not isinstance(info, dict):
            continue

        resolved = _resolve_gsheets_mapping_source_key(
            key,
            info,
            flat_data=flat_data,
            source_keys_lower=source_keys_lower,
            used_sources=used_sources,
            dest_columns_lower=dest_columns_lower,
        )
        if not resolved:
            if key.lower() in dest_columns_lower or (
                info.get("column") and str(key).lower() == str(info.get("column")).lower()
            ):
                print(
                    f"⚠️ [MAP_GSHEETS] Dropped destination-as-source key '{key}' "
                    f"→ '{info.get('column')}' (no matching extraction field)"
                )
            continue

        if resolved != key:
            print(f"🔧 [MAP_GSHEETS] Re-keyed mapping '{key}' → source field '{resolved}'")

        used_sources.add(resolved)
        entry = dict(info)
        sheet, column = _resolve_sheet_and_column(
            entry, sheet_index=sheet_index, default_sheet=default_sheet
        )
        if sheet:
            entry["sheet"] = sheet
        if column:
            entry["column"] = column
        normalized[resolved] = entry

    return normalized


def _salesforce_object_fields(objects_structure: Dict[str, Any], object_api: str) -> Set[str]:
    meta = (objects_structure or {}).get(object_api) or {}
    fields = meta.get("fields") or {}
    if isinstance(fields, dict):
        return {str(k) for k in fields.keys() if k}
    return set()


def _salesforce_entry_exists(
    entry: Dict[str, Any],
    objects_structure: Dict[str, Any],
) -> Tuple[bool, Optional[str], Optional[str]]:
    obj = (entry.get("object") or "").strip()
    field = (entry.get("field") or "").strip()
    if not obj or not field:
        return False, obj or None, field or None
    if obj not in (objects_structure or {}):
        return False, obj, field
    if field not in _salesforce_object_fields(objects_structure, obj):
        return False, obj, field
    return True, obj, field


def filter_salesforce_mapping_to_destination_schema(
    mapping: Dict[str, Any],
    objects_structure: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Drop mappings whose object.field is not in the synced org schema (salesforce_object_fields).

    LLM-only prompt rules are not enough — hallucinated custom fields are removed here.
    """
    if not mapping:
        return {}
    if not objects_structure:
        return dict(mapping)

    filtered: Dict[str, Any] = {}
    for source_field, info in mapping.items():
        if isinstance(info, list):
            kept: List[Dict[str, Any]] = []
            for item in info:
                if not isinstance(item, dict):
                    continue
                ok, obj, field = _salesforce_entry_exists(item, objects_structure)
                if ok:
                    kept.append(item)
                else:
                    print(
                        f"⚠️ [MAP_SF] Omitted '{source_field}' → {obj}.{field} "
                        "(not in destination org schema)"
                    )
            if kept:
                filtered[source_field] = kept
            continue

        if not isinstance(info, dict):
            continue

        ok, obj, field = _salesforce_entry_exists(info, objects_structure)
        if ok:
            filtered[source_field] = info
        else:
            print(
                f"⚠️ [MAP_SF] Omitted '{source_field}' → {obj}.{field} "
                "(not in destination org schema)"
            )

    return filtered


def _sql_table_columns_index(
    tables_structure: Dict[str, Any],
) -> Dict[str, Dict[str, str]]:
    """table_name_lower → {column_lower: canonical_column_name}."""
    out: Dict[str, Dict[str, str]] = {}
    for table_name, meta in (tables_structure or {}).items():
        if not isinstance(meta, dict):
            continue
        cols = meta.get("columns") or {}
        if isinstance(cols, dict):
            col_names = list(cols.keys())
        elif isinstance(cols, list):
            col_names = [
                c.get("name") if isinstance(c, dict) else str(c) for c in cols
            ]
        else:
            col_names = []
        table_key = (table_name or "").strip().lower()
        out[table_key] = {}
        for name in col_names:
            if not name:
                continue
            out[table_key][str(name).strip().lower()] = str(name).strip()
    return out


def _resolve_sql_table_and_column(
    entry: Dict[str, Any],
    *,
    table_index: Dict[str, Dict[str, str]],
    primary_table: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    table = (entry.get("table") or primary_table or "").strip()
    column = (entry.get("column") or "").strip()
    if not table or not column:
        return None, None
    table_cols = table_index.get(table.lower())
    if not table_cols:
        return None, None
    canonical = table_cols.get(column.lower())
    if not canonical:
        return None, None
    return table, canonical


def filter_sql_mapping_to_destination_schema(
    mapping: Dict[str, Any],
    tables_structure: Dict[str, Any],
    *,
    primary_table: Optional[str] = None,
    phase: MappingValidationPhase = MappingValidationPhase.AUTO_MAP,
) -> Dict[str, Any]:
    """
    Drop mappings whose table/column is not in sql_extracted_schemas for the destination DB.

    AUTO_MAP: structural + semantic plausibility. MIGRATE: structural only.
    """
    if not mapping:
        return {}
    if not tables_structure:
        return dict(mapping)

    table_index = _sql_table_columns_index(tables_structure)
    if not primary_table and table_index:
        primary_table = next(iter(tables_structure.keys()), None)

    filtered: Dict[str, Any] = {}
    for source_field, info in mapping.items():
        if isinstance(info, list):
            kept = []
            for item in info:
                if not isinstance(item, dict):
                    continue
                table, column = _resolve_sql_table_and_column(
                    item,
                    table_index=table_index,
                    primary_table=primary_table,
                )
                semantic_ok = (
                    phase == MappingValidationPhase.MIGRATE
                    or source_field_matches_destination_column(source_field, column)
                )
                if table and column and semantic_ok:
                    normalized = dict(item)
                    normalized["table"] = table
                    normalized["column"] = column
                    kept.append(normalized)
                elif item.get("column"):
                    print(
                        f"⚠️ [MAP_SQL] Omitted '{source_field}' → "
                        f"{item.get('table') or primary_table}.{item.get('column')} "
                        "(column missing or no plausible match in destination)"
                    )
            if kept:
                filtered[source_field] = kept
            continue

        if not isinstance(info, dict):
            continue

        table, column = _resolve_sql_table_and_column(
            info,
            table_index=table_index,
            primary_table=primary_table,
        )
        semantic_ok = (
            phase == MappingValidationPhase.MIGRATE
            or source_field_matches_destination_column(source_field, column)
        )
        if table and column and semantic_ok:
            normalized = dict(info)
            normalized["table"] = table
            normalized["column"] = column
            filtered[source_field] = normalized
        else:
            print(
                f"⚠️ [MAP_SQL] Omitted '{source_field}' → "
                f"{info.get('table') or primary_table}.{info.get('column')} "
                "(column missing or no plausible match in destination)"
            )

    return filtered
