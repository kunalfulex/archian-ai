"""Small helpers for extraction_results flags — keep free of post_extract imports."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def extraction_rules_imply_non_identity_processing(extraction_rules: Optional[Dict[str, Any]]) -> bool:
    """
    True when stored extraction_rules indicate row/column processing beyond identity mapping.

    Used for has_transformation_rules so identity-only \"keep\" scaffolding does not force
    the frontend transformation-preview lane on reload.
    """
    if not extraction_rules or not isinstance(extraction_rules, dict):
        return False
    fr = extraction_rules.get("filter_rules")
    if isinstance(fr, dict) and (fr.get("conditions") or []):
        return True
    dr = extraction_rules.get("deduplication_rules")
    if isinstance(dr, dict) and dr.get("enabled"):
        return True
    dcr = extraction_rules.get("data_cleanup_rules")
    if isinstance(dcr, dict) and dcr:
        return True
    tr = extraction_rules.get("transformation_rules")
    fields: List[Dict[str, Any]] = []
    if isinstance(tr, dict):
        raw = tr.get("fields")
        if isinstance(raw, list):
            fields = [r for r in raw if isinstance(r, dict)]
    elif isinstance(tr, list):
        fields = [r for r in tr if isinstance(r, dict)]
    for r in fields:
        act = (r.get("action") or "keep").strip().lower().replace("-", "_")
        if act not in ("keep", ""):
            return True
    return False
