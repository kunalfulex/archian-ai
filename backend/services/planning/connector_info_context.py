"""Rich connector context for schema/info chat (profiles, schema graphs, entity semantics)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from db.connection import database
from services.labeling.catalog_helpers import fetch_semantics_map, primary_from_semantics
from services.planning.label_match import match_entity_in_prompt, normalize_phrase
from services.planning.resource_catalog import ConnectorProfileSummary, _profile_summary_for_connector

logger = logging.getLogger(__name__)


@dataclass
class ResolvedConnector:
    connector_type: str
    display_name: str
    instance_id: str
    connector_ref: Dict[str, Any]


def _parse_json(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return None
    return None


async def resolve_salesforce_connector(
    user_id: str,
    source_name: Optional[str] = None,
) -> Optional[ResolvedConnector]:
    from services.connectors.salesforce_ref import (
        enriched_salesforce_connector_ref,
        resolve_salesforce_token_by_display_name,
    )

    uid = str(user_id)
    token = None
    if (source_name or "").strip():
        token = await resolve_salesforce_token_by_display_name(uid, source_name or "")
    if not token:
        row = await database.fetch_one(
            """
            SELECT id::text AS connector_id, org_id::text AS org_id,
                   COALESCE(org_display_name, instance_url, 'Salesforce') AS display_name
            FROM salesforce_tokens
            WHERE user_id = :user_id
            ORDER BY is_default DESC NULLS LAST, issued_at DESC
            LIMIT 1
            """,
            {"user_id": uid},
        )
        token = dict(row) if row else None
    if not token:
        return None
    return ResolvedConnector(
        connector_type="salesforce",
        display_name=str(token.get("display_name") or source_name or "Salesforce"),
        instance_id=str(token["connector_id"]),
        connector_ref=enriched_salesforce_connector_ref(token),
    )


async def resolve_sql_connector(
    user_id: str,
    source_name: Optional[str] = None,
) -> Optional[ResolvedConnector]:
    uid = str(user_id)
    name = (source_name or "").strip()
    row = None
    if name:
        row = await database.fetch_one(
            """
            SELECT id::text AS connector_id, name
            FROM sql_connectors
            WHERE user_id = :user_id AND is_active = true
              AND lower(trim(name)) = lower(trim(:name))
            LIMIT 1
            """,
            {"user_id": uid, "name": name},
        )
    if not row:
        row = await database.fetch_one(
            """
            SELECT sc.id::text AS connector_id, sc.name
            FROM sql_connectors sc
            WHERE sc.user_id = :user_id AND sc.is_active = true
              AND EXISTS (
                SELECT 1 FROM sql_extracted_schemas ses
                WHERE ses.connector_id = sc.id AND ses.user_id = sc.user_id
              )
            ORDER BY sc.created_at DESC
            LIMIT 1
            """,
            {"user_id": uid},
        )
    if not row:
        return None
    r = dict(row)
    cid = str(r["connector_id"])
    return ResolvedConnector(
        connector_type="sql",
        display_name=str(r.get("name") or name or "SQL"),
        instance_id=cid,
        connector_ref={"connector_id": cid},
    )


async def resolve_google_sheets_connector(
    user_id: str,
    *,
    connection_id: Optional[str] = None,
    spreadsheet_id: Optional[str] = None,
    spreadsheet_name: Optional[str] = None,
) -> Optional[ResolvedConnector]:
    uid = str(user_id)
    row = None
    if connection_id:
        row = await database.fetch_one(
            """
            SELECT id::text AS connection_id,
                   COALESCE(connection_name, google_account_email, 'Google Sheets') AS display_name
            FROM google_sheets_connections
            WHERE user_id = :user_id AND id::text = :cid AND is_active = true
            LIMIT 1
            """,
            {"user_id": uid, "cid": str(connection_id)},
        )
    elif spreadsheet_id:
        row = await database.fetch_one(
            """
            SELECT gsc.id::text AS connection_id,
                   COALESCE(gsc.connection_name, gsc.google_account_email, 'Google Sheets') AS display_name
            FROM gsheets_extracted_schemas ges
            JOIN google_sheets_connections gsc ON ges.google_sheets_connection_id = gsc.id
            WHERE ges.user_id = :user_id AND ges.spreadsheet_id = :sid
            ORDER BY ges.updated_at DESC NULLS LAST
            LIMIT 1
            """,
            {"user_id": uid, "sid": spreadsheet_id},
        )
    elif spreadsheet_name:
        row = await database.fetch_one(
            """
            SELECT gsc.id::text AS connection_id,
                   COALESCE(gsc.connection_name, gsc.google_account_email, 'Google Sheets') AS display_name
            FROM user_google_spreadsheets ugs
            JOIN google_sheets_connections gsc ON ugs.google_sheets_connection_id = gsc.id
            WHERE ugs.user_id = :user_id
              AND lower(trim(ugs.spreadsheet_name)) = lower(trim(:name))
            LIMIT 1
            """,
            {"user_id": uid, "name": spreadsheet_name},
        )
    if not row:
        return None
    r = dict(row)
    cid = str(r["connection_id"])
    return ResolvedConnector(
        connector_type="google_sheets",
        display_name=str(r.get("display_name") or spreadsheet_name or "Google Sheets"),
        instance_id=cid,
        connector_ref={"connection_id": cid},
    )


async def load_schema_graph_data(
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Cached schema graph first; build synced graph if none stored."""
    from services.connector_metadata import store
    from services.connector_metadata.graph_builder import build_schema_graph

    uid = str(user_id)
    try:
        sched = await store.get_schedule(uid, connector_type, connector_ref)
        if sched and sched.get("active_graph_id"):
            cached = await store.get_graph(str(sched["active_graph_id"]), uid)
            if cached:
                data = _parse_json(cached.get("graph_data"))
                if isinstance(data, dict) and data.get("nodes") is not None:
                    return data

        latest = await store.get_latest_graph(uid, connector_type, connector_ref)
        if latest:
            data = _parse_json(latest.get("graph_data"))
            if isinstance(data, dict) and data.get("nodes") is not None:
                return data

        graph = await build_schema_graph(
            user_id=uid,
            connector_type=connector_type,
            connector_ref=connector_ref,
            graph_scope="synced",
            include_deleted=False,
        )
        return graph.to_dict()
    except Exception as exc:
        logger.debug("schema graph load skipped: %s", exc)
        return None


def _format_profile_section(
    *,
    display_name: str,
    connector_type: str,
    profile: Optional[ConnectorProfileSummary],
) -> List[str]:
    lines = [f"\n🏷️ **Connector profile — {display_name}** ({connector_type})\n"]
    if not profile:
        lines.append("_No business profile yet — run connector labeling to enrich descriptions._\n")
        return lines
    if profile.user_description:
        lines.append(f"- **Your note:** {profile.user_description}\n")
    if profile.synopsis:
        lines.append(f"- **Purpose:** {profile.synopsis}\n")
    if profile.business_domains:
        lines.append(f"- **Business domains:** {', '.join(profile.business_domains[:8])}\n")
    if profile.system_roles:
        lines.append(f"- **System roles:** {', '.join(profile.system_roles[:6])}\n")
    if profile.synonyms:
        lines.append(f"- **Also known as:** {', '.join(profile.synonyms[:6])}\n")
    if profile.entity_total_count:
        labeled = profile.entity_labeled_count or 0
        lines.append(
            f"- **Labeled entities:** {labeled}/{profile.entity_total_count}"
            f" ({profile.profile_status or 'pending'})\n"
        )
    return lines


def _node_label_map(graph_data: Optional[Dict[str, Any]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for node in (graph_data or {}).get("nodes") or []:
        if not isinstance(node, dict):
            continue
        nid = str(node.get("id") or "").strip()
        if nid:
            out[nid] = str(node.get("label") or nid)
    return out


def _format_entity_inventory(
    semantics_map: Dict[str, Dict[str, Any]],
    graph_data: Optional[Dict[str, Any]],
    *,
    focus_keys: Optional[Sequence[str]] = None,
    max_entities: int = 35,
) -> List[str]:
    labels = _node_label_map(graph_data)
    keys = list(focus_keys or [])
    if not keys:
        keys = sorted(set(list(semantics_map.keys()) + list(labels.keys())))

    lines = ["\n📚 **Entity catalog** (learned labels + schema inventory)\n"]
    shown = 0
    for key in keys:
        if shown >= max_entities:
            lines.append(f"_…and {len(keys) - shown} more entities_\n")
            break
        sem = semantics_map.get(key) or {}
        primary = primary_from_semantics(sem)
        label = labels.get(key, key)
        category = primary.get("category") or ""
        desc = (primary.get("description") or "")[:160]
        domains = primary.get("domains") or []
        line = f"- **{label}** (`{key}`)"
        if category:
            line += f" — {category}"
        if desc:
            line += f": {desc}"
        if domains:
            line += f" _[{', '.join(domains[:3])}]_"
        lines.append(line + "\n")
        shown += 1

    if shown == 0:
        nodes = (graph_data or {}).get("nodes") or []
        for node in nodes[:max_entities]:
            if not isinstance(node, dict):
                continue
            nid = str(node.get("id") or "")
            if not nid:
                continue
            ntype = node.get("node_type") or "entity"
            lines.append(f"- **{node.get('label') or nid}** (`{nid}`, {ntype})\n")
            shown += 1
        if shown == 0:
            lines.append("_No synced entities found yet._\n")
    return lines


def _format_relationship_summary(
    graph_data: Optional[Dict[str, Any]],
    *,
    focus_keys: Optional[Sequence[str]] = None,
    max_edges: int = 20,
) -> List[str]:
    edges = (graph_data or {}).get("edges") or []
    if not edges:
        return []
    labels = _node_label_map(graph_data)
    focus = {k for k in (focus_keys or []) if k}
    lines = ["\n🔗 **Relationships** (lookups / foreign keys)\n"]
    shown = 0
    for edge in edges:
        if shown >= max_edges:
            lines.append(f"_…and {len(edges) - shown} more relationships_\n")
            break
        if not isinstance(edge, dict):
            continue
        src = str(edge.get("source_id") or "")
        tgt = str(edge.get("target_id") or "")
        if focus and src not in focus and tgt not in focus:
            continue
        rel = edge.get("label") or edge.get("edge_type") or "relates to"
        lines.append(
            f"- **{labels.get(src, src)}** → **{labels.get(tgt, tgt)}** via `{rel}`\n"
        )
        shown += 1
    if shown == 0 and focus:
        for edge in edges[:max_edges]:
            if not isinstance(edge, dict):
                continue
            src = str(edge.get("source_id") or "")
            tgt = str(edge.get("target_id") or "")
            rel = edge.get("label") or edge.get("edge_type") or "relates to"
            lines.append(
                f"- **{labels.get(src, src)}** → **{labels.get(tgt, tgt)}** via `{rel}`\n"
            )
            shown += 1
    return lines if shown else []


def pick_relevant_entity_keys(
    prompt: Optional[str],
    semantics_map: Dict[str, Dict[str, Any]],
    graph_data: Optional[Dict[str, Any]],
    *,
    limit: int = 3,
) -> List[str]:
    """Deterministic entity focus from prompt + learned semantics (no extra LLM)."""
    entity_summaries = [
        {"entity_key": key, "semantics": sem}
        for key, sem in (semantics_map or {}).items()
    ]
    hit = match_entity_in_prompt(prompt or "", entity_summaries)
    if hit:
        return [hit]

    pl = normalize_phrase(prompt)
    if not pl:
        return []

    labels = _node_label_map(graph_data)
    scored: List[Tuple[float, str]] = []
    for key, label in labels.items():
        score = 0.0
        if normalize_phrase(label) in pl or normalize_phrase(key) in pl:
            score += 80
        sem = semantics_map.get(key) or {}
        primary = primary_from_semantics(sem)
        cat = normalize_phrase(primary.get("category") or "")
        if cat and cat in pl:
            score += 60
        for syn in primary.get("synonyms") or []:
            if normalize_phrase(str(syn)) in pl:
                score += 50
        if score > 0:
            scored.append((score, key))

    ranked = sorted(scored, key=lambda x: x[0], reverse=True)
    return [key for _, key in ranked[:limit]]


async def build_connector_info_enrichment(
    user_id: str,
    connector: ResolvedConnector,
    prompt: Optional[str] = None,
    *,
    focus_entity_keys: Optional[Sequence[str]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Build profile + schema graph + entity semantics block for info chat system prompts.

    Returns (formatted_text, metadata) where metadata includes semantics_map, graph_data,
    and focus_entity_keys for downstream entity selection.
    """
    profile = await _profile_summary_for_connector(
        str(user_id),
        connector.connector_type,
        connector.instance_id,
    )
    semantics_map = await fetch_semantics_map(
        user_id=str(user_id),
        connector_type=connector.connector_type,
        connector_instance_id=connector.instance_id,
    )
    graph_data = await load_schema_graph_data(
        str(user_id),
        connector.connector_type,
        connector.connector_ref,
    )

    focus = list(focus_entity_keys or [])
    if not focus and prompt:
        focus = pick_relevant_entity_keys(prompt, semantics_map, graph_data)

    parts: List[str] = []
    parts.extend(
        _format_profile_section(
            display_name=connector.display_name,
            connector_type=connector.connector_type,
            profile=profile,
        )
    )

    summary = (graph_data or {}).get("summary") or {}
    if summary:
        parts.append(
            f"\n🗺️ **Schema graph:** {summary.get('node_count', 0)} entities, "
            f"{summary.get('edge_count', 0)} relationships"
            f" (scope: {summary.get('scope', 'synced')})\n"
        )

    parts.extend(
        _format_entity_inventory(
            semantics_map,
            graph_data,
            focus_keys=focus or None,
        )
    )
    parts.extend(
        _format_relationship_summary(graph_data, focus_keys=focus or None)
    )

    meta = {
        "semantics_map": semantics_map,
        "graph_data": graph_data,
        "focus_entity_keys": focus,
        "profile": profile,
    }
    return "".join(parts), meta
