"""
Display / compiler groups for flat field_mappings rows (split vs combine).

Mirrors edisonai-frontend/src/utils/fieldMappingDisplayGroups.js.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Tuple

GroupKind = Literal["split", "combine", "single"]


def build_field_mapping_display_groups(
    field_mappings: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Returns list of {kind, rows, row_indices} where row_indices are positions in the input list.
    """
    rows = list(field_mappings or [])
    if not rows:
        return []

    def source_key(r: Dict[str, Any]) -> str:
        return str(r.get("extracted_field_name") or "").strip()

    def dest_key(r: Dict[str, Any]) -> str:
        o = str(r.get("salesforce_object") or "").strip()
        f = str(r.get("salesforce_field") or "").strip()
        return f"{o}\x00{f}".lower()

    with_index: List[Tuple[int, Dict[str, Any]]] = [(i, dict(r)) for i, r in enumerate(rows)]

    by_source: Dict[str, List[Tuple[int, Dict[str, Any]]]] = {}
    by_dest: Dict[str, List[Tuple[int, Dict[str, Any]]]] = {}
    for i, r in with_index:
        sk = source_key(r)
        if sk not in by_source:
            by_source[sk] = []
        by_source[sk].append((i, r))
        dk = dest_key(r)
        if dk and dk != "\x00":
            if dk not in by_dest:
                by_dest[dk] = []
            by_dest[dk].append((i, r))

    for lst in by_source.values():
        lst.sort(key=lambda x: x[0])
    for lst in by_dest.values():
        lst.sort(key=lambda x: x[0])

    used: set[int] = set()
    groups: List[Dict[str, Any]] = []

    for i, r in with_index:
        if i in used:
            continue
        sk = source_key(r)
        split_candidates = by_source.get(sk, [(i, r)]) if sk else [(i, r)]
        if sk and len(split_candidates) >= 2:
            for idx, _ in split_candidates:
                used.add(idx)
            groups.append(
                {
                    "kind": "split",
                    "rows": [dict(x[1]) for x in split_candidates],
                    "row_indices": [x[0] for x in split_candidates],
                }
            )
            continue

        dk = dest_key(r)
        combine_candidates = by_dest.get(dk, []) if dk and dk != "\x00" else []
        if len(combine_candidates) >= 2:
            for idx, _ in combine_candidates:
                used.add(idx)
            groups.append(
                {
                    "kind": "combine",
                    "rows": [dict(x[1]) for x in combine_candidates],
                    "row_indices": [x[0] for x in combine_candidates],
                }
            )
            continue

        used.add(i)
        groups.append({"kind": "single", "rows": [dict(r)], "row_indices": [i]})

    return groups
