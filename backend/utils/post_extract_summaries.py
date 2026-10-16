"""Human-readable summaries of post-extract rule deltas for SSE / UI."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _cond_sentence(cond: Dict[str, Any]) -> Optional[str]:
    if not isinstance(cond, dict):
        return None
    field = cond.get("field") or cond.get("source_field")
    op = (cond.get("operator") or "").strip().lower()
    if op == "valid_email" and field:
        return f"Keep rows where {field} is a valid email."
    if op and field:
        return f"Filter {field} ({op})."
    return None


def _transformation_rules_list(delta: Dict[str, Any]) -> List[Dict[str, Any]]:
    tr = delta.get("transformation_rules")
    if isinstance(tr, list):
        return [r for r in tr if isinstance(r, dict)]
    if isinstance(tr, dict) and isinstance(tr.get("fields"), list):
        return [r for r in tr["fields"] if isinstance(r, dict)]
    return []


def _merge_source_labels(r: Dict[str, Any]) -> tuple[List[str], str]:
    srcs = r.get("source_fields")
    if srcs is None:
        srcs = r.get("source_field") or []
    if isinstance(srcs, str):
        srcs = [srcs]
    if not isinstance(srcs, list):
        srcs = []
    labels = [str(s).strip() for s in srcs if str(s).strip()]
    out_f = (r.get("output_field") or r.get("target_field") or "").strip()
    return labels, out_f


def _nested_tf_sources(r: Dict[str, Any], t_raw: Dict[str, Any]) -> tuple[str, str]:
    sf = r.get("source_field") or r.get("field")
    of = r.get("output_field") or ""
    if isinstance(sf, str) and sf.strip():
        sf_s = sf.strip()
    else:
        ns = t_raw.get("source_field") or t_raw.get("field")
        sf_s = ns.strip() if isinstance(ns, str) and ns.strip() else ""
    if isinstance(of, str) and of.strip():
        of_s = of.strip()
    else:
        no = t_raw.get("output_field")
        of_s = no.strip() if isinstance(no, str) and no.strip() else sf_s
    return sf_s, of_s


def _phrases_from_transform_rules(raw_rules: List[Dict[str, Any]]) -> List[str]:
    """Natural-language fragments from transformation_rules (LLM list or merged fields)."""
    phrases: List[str] = []
    drop: List[str] = []

    for r in raw_rules:
        act = (r.get("action") or "").strip().lower().replace("-", "_")

        if act == "remove_fields" and isinstance(r.get("fields"), list):
            for f in r["fields"]:
                if isinstance(f, str) and f.strip():
                    drop.append(f.strip())
            continue

        if act in ("remove", "remove_field", "exclude", "drop_field", "delete_column"):
            bulk = r.get("fields")
            if isinstance(bulk, list):
                for f in bulk:
                    if isinstance(f, str) and f.strip():
                        drop.append(f.strip())
            else:
                sf = r.get("source_field")
                if isinstance(sf, str) and sf.strip():
                    drop.append(sf.strip())
            continue

        if act in ("merge", "combine"):
            labels, out_f = _merge_source_labels(r)
            if labels and out_f:
                phrases.append(f"Combined {' and '.join(labels)} into {out_f}.")
            elif labels:
                phrases.append(f"Merged columns {' and '.join(labels)}.")
            continue

        if act.startswith("split"):
            sf = r.get("source_field") or r.get("field")
            outs = r.get("output_fields") or r.get("target_fields") or []
            if isinstance(sf, str) and sf.strip() and isinstance(outs, list) and outs:
                names = ", ".join(str(o).strip() for o in outs if str(o).strip())
                if names:
                    phrases.append(f"Split {sf.strip()} into {names}.")
            continue

        if act == "add_prefix":
            fld = r.get("field") or r.get("source_field")
            pv = r.get("prefix") if r.get("prefix") is not None else r.get("value")
            if isinstance(fld, str) and fld.strip():
                phrases.append(f"Prefixed {fld.strip()} with {pv!r}.")
            continue

        if act == "add_suffix":
            fld = r.get("field") or r.get("source_field")
            sv = r.get("suffix") if r.get("suffix") is not None else r.get("value")
            if isinstance(fld, str) and fld.strip():
                phrases.append(f"Added suffix {sv!r} to {fld.strip()}.")
            continue

        if act == "prepend":
            fld = r.get("source_field") or r.get("field")
            pv = r.get("value")
            if isinstance(fld, str) and fld.strip():
                phrases.append(f"Prefixed {fld.strip()} with {pv!r}.")
            continue

        if act == "append":
            fld = r.get("source_field") or r.get("field")
            pv = r.get("value")
            if isinstance(fld, str) and fld.strip():
                phrases.append(f"Added suffix {pv!r} to {fld.strip()}.")
            continue

        if act == "regex_replace":
            fld = r.get("source_field") or r.get("field")
            if isinstance(fld, str) and fld.strip():
                phrases.append(f"Reformatted values in {fld.strip()} (pattern replace).")
            continue

        if act in ("remove_invalid", "remove_invalid_record", "remove_invalid_records"):
            fld = r.get("source_field") or r.get("field") or "Email"
            if isinstance(fld, str) and fld.strip():
                phrases.append(f"Keep rows with valid {fld.strip()}.")
            continue

        if act == "transform":
            t_raw = r.get("transformation")
            if isinstance(t_raw, dict):
                sf_s, of_s = _nested_tf_sources(r, t_raw)
                if isinstance(t_raw.get("pattern"), str) and str(t_raw.get("pattern") or "").strip():
                    if sf_s:
                        phrases.append(f"Reformatted values in {sf_s}.")
                    continue
                if "prepend" in t_raw:
                    pv = t_raw.get("prepend")
                    tgt = sf_s or "field"
                    phrases.append(f"Prefixed {tgt} with {pv!r}.")
                    continue
                if "append" in t_raw:
                    av = t_raw.get("append")
                    tgt = sf_s or "field"
                    phrases.append(f"Added suffix {av!r} to {tgt}.")
                    continue
                ty = t_raw.get("type") or t_raw.get("op") or t_raw.get("name")
                if ty is not None and str(ty).strip():
                    tn = str(ty).strip().lower().replace("-", "_")
                    if tn in ("prepend", "prefix", "add_prefix"):
                        pv = t_raw.get("value") if t_raw.get("value") is not None else t_raw.get("prefix")
                        tgt = sf_s or "field"
                        phrases.append(f"Prefixed {tgt} with {pv!r}.")
                        continue
                    if tn in ("append", "suffix", "add_suffix"):
                        pv = t_raw.get("value") if t_raw.get("value") is not None else t_raw.get("suffix")
                        tgt = sf_s or "field"
                        phrases.append(f"Added suffix {pv!r} to {tgt}.")
                        continue
            continue

    if drop:
        cols = ", ".join(sorted(set(drop)))
        phrases.insert(0, f"Removed columns: {cols}.")

    return phrases


def _ordering_phrase(orules: Any) -> Optional[str]:
    if not isinstance(orules, dict):
        return None
    sorts = orules.get("sort")
    lim = orules.get("limit")
    if not isinstance(sorts, list) or not sorts:
        if lim is not None:
            try:
                n = int(lim)
                return f"Limit to {n} rows." if n else None
            except (TypeError, ValueError):
                return None
        return None
    bits = []
    for s in sorts:
        if not isinstance(s, dict):
            continue
        f = (s.get("field") or s.get("column") or "").strip()
        d = (s.get("direction") or "asc").strip().lower()
        if f:
            bits.append(f"{f} ({d})")
    if not bits:
        return None
    tail = ""
    if lim is not None:
        try:
            n = int(lim)
            if n:
                tail = f"; limit {n}"
        except (TypeError, ValueError):
            pass
    return f"Sort by {', '.join(bits)}{tail}."


def summarize_operation_delta(operation_id: str, delta: Optional[Dict[str, Any]]) -> str:
    """Short natural-language line describing what this delta requested."""
    if not delta:
        return "No changes proposed for this step."

    op = (operation_id or "").strip().lower()
    phrases: List[str] = []

    raw_tr = _transformation_rules_list(delta)
    phrases.extend(_phrases_from_transform_rules(raw_tr))

    if op == "filter":
        fr = delta.get("filter_rules")
        if isinstance(fr, dict):
            for c in fr.get("conditions") or []:
                s = _cond_sentence(c if isinstance(c, dict) else {})
                if s:
                    phrases.append(s)

        ma = delta.get("merge_actions") if isinstance(delta.get("merge_actions"), dict) else {}
        rmi = ma.get("remove_fields") if isinstance(ma.get("remove_fields"), list) else []
        top_rf = delta.get("remove_fields") if isinstance(delta.get("remove_fields"), list) else []
        extras = []
        for lst in (rmi, top_rf):
            for f in lst:
                if isinstance(f, str) and f.strip():
                    extras.append(f.strip())
        if extras:
            cols = ", ".join(sorted(set(extras)))
            phrases.append(f"Dropped columns: {cols}.")

        srcs = ma.get("source_fields") if isinstance(ma.get("source_fields"), list) else None
        tgt = ma.get("target_field") or ma.get("output_field")
        if srcs and isinstance(tgt, str) and tgt.strip():
            labels = [str(s).strip() for s in srcs if str(s).strip()]
            if labels:
                phrases.append(f"Combined {' and '.join(labels)} into {tgt.strip()}.")

    if op in ("dedupe", "deduplication"):
        dr = delta.get("deduplication_rules")
        if isinstance(dr, dict) and dr.get("enabled"):
            phrases.append("Removed duplicate rows (per configured fields).")

    if op in ("validate", "validation", "cleanup_values"):
        dc = delta.get("data_cleanup_rules")
        if isinstance(dc, dict) and dc:
            phrases.append("Validated and cleaned field values.")

    orules = delta.get("ordering_rules")
    ophr = _ordering_phrase(orules)
    if ophr:
        phrases.append(ophr)

    if not phrases:
        return "Updated extraction rules for this step."

    seen = set()
    out_phrases = []
    for p in phrases:
        if p not in seen:
            seen.add(p)
            out_phrases.append(p)
    return " ".join(out_phrases)
