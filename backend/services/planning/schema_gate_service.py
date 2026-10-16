"""Salesforce schema gate: compare org field catalogs before map/migrate."""
from __future__ import annotations

from typing import Any, AsyncIterable, Dict, List, Optional, Set

import httpx


async def _describe_custom_fields(
    *,
    instance_url: str,
    access_token: str,
    object_api_name: str,
) -> Set[str]:
    url = f"{instance_url.rstrip('/')}/services/data/v59.0/sobjects/{object_api_name}/describe"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {access_token}"})
    if resp.status_code != 200:
        return set()
    names: Set[str] = set()
    for f in resp.json().get("fields") or []:
        name = f.get("name")
        if name and f.get("custom"):
            names.add(name)
    return names


async def _relevant_custom_field_names(
    *,
    user_id: str,
    job_id: Optional[str],
    prompt: str,
    conversation_history: Optional[list],
) -> Set[str]:
    """Custom fields in scope for this migration (extraction columns + saved mapping)."""
    names: Set[str] = set()
    if job_id:
        from services.extraction_results_service import ExtractionResultsService

        row = await ExtractionResultsService.get_extraction_result(job_id, str(user_id))
        data = (row or {}).get("extracted_data") or []
        if isinstance(data, list) and data and isinstance(data[0], dict):
            for col in data[0].keys():
                if str(col).endswith("__c"):
                    names.add(str(col))
    from services.planning.schema_gates import extraction_or_mapping_has_custom_fields

    if conversation_history:
        for msg in reversed(conversation_history):
            meta = (msg or {}).get("metadata") or {}
            mapping = meta.get("mapping") or meta.get("field_mapping") or {}
            if isinstance(mapping, dict):
                for entry in mapping.values():
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("is_custom") or str(entry.get("field") or "").endswith("__c"):
                        field = str(entry.get("field") or "")
                        if field:
                            names.add(field)
                if names:
                    break
    return names


async def compare_salesforce_orgs_schema(
    *,
    user_id: str,
    source_name: str,
    destination_name: str,
    prompt: str,
    conversation_history: Optional[list] = None,
    job_id: Optional[str] = None,
    limit_to_custom_fields: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    # When limit_to_custom_fields is set (including empty), only those fields can be flagged missing.
    """
    Compare custom fields on a source object vs destination org.
    Returns object_api_name, fields_only_on_source, fields_on_both, needs_clarification, message.
    """
    from handlers.salesforce_handler import (
        detect_salesforce_org_from_prompt,
        determine_salesforce_object_and_fields,
    )
    from utils.salesforceapimigration import get_valid_salesforce_token

    conversation_history = conversation_history or []
    org_src = await detect_salesforce_org_from_prompt(
        prompt, user_id, conversation_history, source_name=source_name
    )
    org_dest = await detect_salesforce_org_from_prompt(
        prompt, user_id, conversation_history, source_name=destination_name
    )
    if not org_src or not org_src.get("found") or not org_dest or not org_dest.get("found"):
        return {
            "ok": False,
            "needs_clarification": True,
            "message": "Could not resolve source or destination Salesforce org.",
        }

    object_result = await determine_salesforce_object_and_fields(
        prompt,
        user_id,
        conversation_history,
        org_id=org_src.get("org_id"),
    )
    if object_result.get("needs_clarification"):
        return {
            "ok": False,
            "needs_clarification": True,
            "message": object_result.get("clarification_message")
            or "Which object and fields should we compare?",
        }
    object_api_name = object_result.get("object_api_name")
    if not object_api_name:
        return {
            "ok": False,
            "needs_clarification": True,
            "message": "Specify the Salesforce object to assess (e.g. Contact).",
        }

    src_token = await get_valid_salesforce_token(
        user_id=str(user_id),
        org_id=org_src.get("org_id"),
        connected_app_id=org_src.get("connected_app_id"),
    )
    dest_token = await get_valid_salesforce_token(
        user_id=str(user_id),
        org_id=org_dest.get("org_id"),
        connected_app_id=org_dest.get("connected_app_id"),
    )
    if not src_token or not dest_token:
        return {"ok": False, "message": "Salesforce connection unavailable."}

    src_fields = await _describe_custom_fields(
        instance_url=src_token["instance_url"],
        access_token=src_token["access_token"],
        object_api_name=object_api_name,
    )
    dest_fields = await _describe_custom_fields(
        instance_url=dest_token["instance_url"],
        access_token=dest_token["access_token"],
        object_api_name=object_api_name,
    )
    missing_on_dest = sorted(src_fields - dest_fields)
    if limit_to_custom_fields is not None:
        missing_on_dest = sorted(
            f for f in missing_on_dest if f in limit_to_custom_fields
        )
    elif job_id is not None:
        missing_on_dest = []
    return {
        "ok": True,
        "object_api_name": object_api_name,
        "source_name": source_name,
        "destination_name": destination_name,
        "fields_missing_on_destination": missing_on_dest,
        "fields_on_both": sorted(src_fields & dest_fields),
        "deploy_recommended": bool(missing_on_dest),
    }


async def stream_schema_gate_phase(
    *,
    user_id: str,
    source_name: str,
    destination_name: str,
    prompt: str,
    conversation_history: Optional[list],
    gate_phase: str,
    job_id: Optional[str] = None,
) -> AsyncIterable[str]:
    """SSE lines for assess or propose schema gate steps."""
    import json

    limit_fields = await _relevant_custom_field_names(
        user_id=user_id,
        job_id=job_id,
        prompt=prompt,
        conversation_history=conversation_history,
    )
    if gate_phase == "propose_schema_delta":
        prior = {}
        if job_id:
            from services.planning.schema_gates import compute_schema_delta_from_job

            prior = await compute_schema_delta_from_job(job_id, user_id)
        result = await compare_salesforce_orgs_schema(
            user_id=user_id,
            source_name=source_name,
            destination_name=destination_name,
            prompt=prompt,
            conversation_history=conversation_history,
            job_id=job_id,
            limit_to_custom_fields=limit_fields,
        )
        if not result.get("ok"):
            yield f"data: {json.dumps({'status': 'clarification', 'message': result.get('message')})}\n\n"
            return
        missing = result.get("fields_missing_on_destination") or []
        obj = result.get("object_api_name") or "Object"
        lines = [
            f"**Schema review — {obj}**",
            f"Source: **{source_name}** → Destination: **{destination_name}**",
            "",
        ]
        if missing:
            lines.append(f"Custom fields to create on destination ({len(missing)}):")
            for f in missing[:25]:
                lines.append(f"- `{f}`")
            if len(missing) > 25:
                lines.append(f"- …and {len(missing) - 25} more")
            lines.append("")
            lines.append(
                "_Mapping will proceed; deploy missing fields from the Connections page or ask to deploy metadata in chat._"
            )
        else:
            lines.append("No missing custom fields detected on the destination object.")
        if prior.get("source_columns"):
            lines.append("")
            lines.append(f"Extracted columns ({len(prior['source_columns'])}): {', '.join(prior['source_columns'][:12])}")
        text = "\n".join(lines)
        for line in text.split("\n"):
            yield line + "\n"
        yield (
            f"data: {json.dumps({'event_type': 'agent_step_completed', 'action': 'schema_gate_proposal', 'object_api_name': obj, 'fields_missing_on_destination': missing, 'deploy_recommended': bool(missing)})}\n\n"
        )
        return

    yield f"**Assessing schema for {destination_name}...**\n\n"
    result = await compare_salesforce_orgs_schema(
        user_id=user_id,
        source_name=source_name,
        destination_name=destination_name,
        prompt=prompt,
        conversation_history=conversation_history,
        job_id=job_id,
        limit_to_custom_fields=limit_fields,
    )
    if not result.get("ok"):
        yield f"data: {json.dumps({'status': 'clarification', 'message': result.get('message')})}\n\n"
        return
    missing = result.get("fields_missing_on_destination") or []
    obj = result.get("object_api_name") or "Object"
    if missing:
        yield f"Found **{len(missing)}** custom field(s) on **{obj}** in {source_name} that are not on {destination_name}.\n"
    else:
        yield f"Destination **{destination_name}** already has matching custom fields on **{obj}**.\n"
    yield (
        f"data: {json.dumps({'action': 'schema_gate_assessed', 'object_api_name': obj, 'fields_missing_on_destination': missing})}\n\n"
    )
