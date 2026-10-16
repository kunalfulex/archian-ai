"""
Parse Salesforce Metadata API XML (DuplicateRule, MatchingRule, etc.) into JSON-serializable dicts.
Uses ElementTree; strips metadata namespaces for stable keys.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Union

_NS_METADATA = "http://soap.sforce.com/2006/04/metadata"
_XSI = "{http://www.w3.org/2001/XMLSchema-instance}"


def _local_tag(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _is_nil_element(el: ET.Element) -> bool:
    return el.attrib.get(f"{_XSI}nil") == "true" or el.attrib.get("nil") == "true"


def _element_to_json(el: ET.Element) -> Union[None, str, Dict[str, Any], List[Any]]:
    """
    Recursively convert a Metadata XML element to JSON-friendly data.
    Repeated sibling tags become a list.
    """
    if _is_nil_element(el):
        return None

    children = list(el)
    if not children:
        text = (el.text or "").strip()
        return text if text else None

    # Group by local tag name
    buckets: Dict[str, List[ET.Element]] = {}
    for c in children:
        name = _local_tag(c.tag)
        buckets.setdefault(name, []).append(c)

    out: Dict[str, Any] = {}
    for name, elems in buckets.items():
        values = [_element_to_json(e) for e in elems]
        if len(values) == 1:
            out[name] = values[0]
        else:
            out[name] = values
    return out


def parse_metadata_xml_string(xml_text: str) -> Optional[Union[Dict[str, Any], str]]:
    """
    Parse a single metadata XML document to a dict (root element name is not duplicated;
    we return the inner structure under the root's local name).
    Returns None for empty/whitespace input.
    """
    if not (xml_text or "").strip():
        return None
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as e:
        return {"_parse_error": str(e), "_raw_preview": xml_text[:500]}

    if _is_nil_element(root) and not list(root):
        return None

    name = _local_tag(root.tag)
    inner = _element_to_json(root)
    # Root with only text (unusual)
    if not isinstance(inner, dict):
        return {name: inner}
    return {name: inner}


def parse_metadata_files_to_json(metadata_files: Dict[str, str]) -> Dict[str, Any]:
    """
    Parse a path -> XML string map (from Metadata retrieve) into path -> JSON structure.
    Keys are duplicateRules/... and matchingRules/... paths.
    """
    out: Dict[str, Any] = {}
    for path, xml_content in (metadata_files or {}).items():
        parsed = parse_metadata_xml_string(xml_content)
        out[path] = parsed
    return out


def enrich_duplicate_rules_json(duplicate_rules_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add `metadata_parsed` from `metadata_files` on the duplicate_rules_json blob.
    Mutates a copy and returns it (safe to persist).
    """
    payload = dict(duplicate_rules_payload or {})
    files = payload.get("metadata_files") or {}
    if isinstance(files, dict) and files:
        payload["metadata_parsed"] = parse_metadata_files_to_json(files)
    else:
        payload["metadata_parsed"] = {}
    return payload
