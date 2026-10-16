"""
Normalize connector field mappings into base vs lookup destination slots.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple

Scope = Literal["base", "lookup"]


@dataclass(frozen=True)
class DestSlot:
    container: str
    field: str

    def key(self) -> Tuple[str, str]:
        return (self.container.strip(), self.field.strip())


@dataclass
class NormalizedEntry:
    source_key: str
    scope: Scope
    base_container: str
    dest_container: str
    dest_field: str
    lookup_of: Optional[str]
    is_list_mapping: bool


def adapter_keys(destination_type: str) -> Tuple[str, str]:
    dest = (destination_type or "").lower()
    if dest in ("google_sheets", "gsheets"):
        return "column", "sheet"
    if dest in ("sql", "database"):
        return "column", "table"
    return "field", "object"


def resolve_base_container(
    *,
    base_container: Optional[str] = None,
    base_objects: Optional[List[str]] = None,
    document_type: Optional[str] = None,
    mapping: Optional[Dict[str, Any]] = None,
    destination_type: str = "salesforce",
) -> str:
    """Primary migration entity (Contact, Sheet1, users table)."""
    if base_container and str(base_container).strip():
        return str(base_container).strip()
    if base_objects:
        for obj in base_objects:
            if obj and str(obj).strip():
                return str(obj).strip()
    if document_type and str(document_type).strip():
        return str(document_type).strip()

    field_key, container_key = adapter_keys(destination_type)
    counts: Dict[str, int] = {}
    for _src, info in (mapping or {}).items():
        if isinstance(info, dict) and not info.get("lookup_of"):
            container = (info.get(container_key) or "").strip()
            if container:
                counts[container] = counts.get(container, 0) + 1
    if counts:
        return max(counts, key=counts.get)
    return ""


def _entry_from_dict(
    source_key: str,
    info: Dict[str, Any],
    *,
    base_container: str,
    field_key: str,
    container_key: str,
) -> NormalizedEntry:
    lookup_of = (info.get("lookup_of") or "").strip() or None
    dest_container = (info.get(container_key) or "").strip() or base_container
    dest_field = (info.get(field_key) or "").strip()
    scope: Scope = "lookup" if lookup_of else "base"
    return NormalizedEntry(
        source_key=source_key,
        scope=scope,
        base_container=base_container,
        dest_container=dest_container,
        dest_field=dest_field,
        lookup_of=lookup_of,
        is_list_mapping=False,
    )


def normalize_mapping_entries(
    mapping: Dict[str, Any],
    *,
    destination_type: str,
    base_container: str,
) -> List[NormalizedEntry]:
    """Expand mapping dict into normalized rows (split mappings → multiple entries)."""
    if not mapping or not base_container:
        return []

    field_key, container_key = adapter_keys(destination_type)
    base = str(base_container).strip()
    out: List[NormalizedEntry] = []

    for source_key, info in mapping.items():
        if not source_key or info is None:
            continue
        if isinstance(info, list):
            for item in info:
                if isinstance(item, dict):
                    e = _entry_from_dict(
                        source_key, item, base_container=base, field_key=field_key, container_key=container_key
                    )
                    e.is_list_mapping = True
                    out.append(e)
            continue
        if isinstance(info, dict):
            out.append(
                _entry_from_dict(
                    source_key, info, base_container=base, field_key=field_key, container_key=container_key
                )
            )
    return out


def entries_for_source(entries: List[NormalizedEntry], source_key: str) -> List[NormalizedEntry]:
    return [e for e in entries if e.source_key == source_key]


def base_entries(entries: List[NormalizedEntry]) -> List[NormalizedEntry]:
    return [e for e in entries if e.scope == "base"]


def lookup_entries(entries: List[NormalizedEntry]) -> List[NormalizedEntry]:
    return [e for e in entries if e.scope == "lookup"]


def dest_slot_for_entry(entry: NormalizedEntry) -> Optional[DestSlot]:
    if not entry.dest_field:
        return None
    return DestSlot(entry.dest_container, entry.dest_field)
