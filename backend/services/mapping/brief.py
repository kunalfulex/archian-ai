"""Deterministic mapping brief for LLM field wiring (not raw user prompt)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.planning.plan_builder import build_mapping_relationship_context_block
from services.planning.schemas import PlanEntityRef, normalize_connector_type


def _entity_label(ref: PlanEntityRef) -> str:
    return ref.step_entity_label() or (ref.entity_key or "").strip()


def compose_mapping_brief(
    *,
    source: PlanEntityRef,
    destination: PlanEntityRef,
    mapping_target: PlanEntityRef,
    migration_intent: Optional[Dict[str, Any]] = None,
    recovery_field_hints: Optional[str] = None,
    extra_instruction: Optional[str] = None,
) -> str:
    """
    Build a concise, plan-scoped instruction for field mapping LLMs.
    Endpoints come from plan_contract — not the original pipeline sentence.
    """
    lines: list[str] = []

    src_ct = normalize_connector_type(source.connector_type or "unknown")
    dest_ct = normalize_connector_type(destination.connector_type or "unknown")
    src_conn = (source.display_name or "").strip()
    dest_conn = (destination.display_name or "").strip()
    src_entity = _entity_label(source)
    target_entity = _entity_label(mapping_target) or _entity_label(destination)

    if src_entity or dest_conn or target_entity:
        src_part = f"{src_conn} / `{src_entity}`" if src_conn and src_entity else (src_entity or src_conn or "source")
        dest_part = f"{dest_conn} / `{target_entity}`" if dest_conn and target_entity else (target_entity or dest_conn or "destination")
        lines.append(f"Map extracted fields from {src_part} to {dest_part}.")

    intent_block = build_mapping_relationship_context_block(migration_intent)
    if intent_block:
        lines.append(intent_block)

    if target_entity:
        lines.append(
            f"CRITICAL: Map ONLY to destination entity `{target_entity}`. "
            "Do not map source fields to any other base object."
        )

    if src_ct == "salesforce" and dest_ct == "salesforce" and src_entity and target_entity:
        if src_entity.lower() == target_entity.lower():
            lines.append(
                f"Cross-org migration: source and destination both use `{target_entity}`."
            )

    recovery = (recovery_field_hints or "").strip()
    if recovery:
        lines.append(f"Additional field mapping guidance: {recovery}")

    extra = (extra_instruction or "").strip()
    if extra and extra not in "\n".join(lines):
        lines.append(extra)

    return "\n\n".join(line for line in lines if line).strip()
