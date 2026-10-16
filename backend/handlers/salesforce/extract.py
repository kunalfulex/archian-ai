"""Salesforce extract streams, jobs, and SOQL generation."""

import asyncio
import json
import re
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
from uuid import UUID, uuid4

import httpx

from utils.salesforceapimigration import (
    get_valid_salesforce_token,
)
from utils.salesforce_bulk_api import (
    create_bulk_query_job,
    wait_for_bulk_query_job_completion,
    get_bulk_query_results,
)
from utils.extraction_rule_pipeline import extraction_rules_from_ai_record
from utils.extraction_rules_flags import extraction_rules_imply_non_identity_processing
from utils.extraction_sse_payload import extraction_setup_progress_sse_line
from utils.extraction_sse_stream import stream_extraction_job_events_until_terminal
from services.llm import tasks as llm_tasks
from utils.salesforce_extract_debug import SfExtractTimer, approx_token_count, log_sf_extract
from handlers.salesforce.common import (
    _build_invalid_field_clarification,
    _clarification_stream_chunks,
    _clean_salesforce_records,
    _dedupe_preserve_order,
    _extract_field_names_from_clarification,
    _fetch_salesforce_records,
    _fetch_salesforce_records_with_bulk_api,
    _is_field_related_clarification,
    _parse_invalid_field_from_soql_error,
    _parse_soql_from_prompt,
    _remove_field_from_soql,
    _salesforce_clarification_response,
    _sanitize_soql_query,
    _validate_soql_fields,
)
from handlers.salesforce.map import (
    build_salesforce_context_for_prompt,
    detect_salesforce_org_from_prompt,
)

def _sf_extract_abort_payload(
    reason: str,
    *,
    job_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    message: Optional[str] = None,
    **extra: Any,
) -> None:
    log_sf_extract(
        "extract_abort",
        level="warning",
        always=True,
        reason=reason,
        job_id=job_id,
        conversation_id=conversation_id,
        message=(message or "")[:400],
        **extra,
    )


def _sf_persisted_extraction_metadata(
    *,
    migration_intent: Optional[Dict[str, Any]] = None,
    object_result: Optional[Dict[str, Any]] = None,
    destination: Optional[str] = None,
    destination_name: Optional[str] = None,
    org_info: Optional[Dict[str, Any]] = None,
    soql: Optional[str] = None,
    extraction_rules: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from services.intent.extraction_metadata import build_persisted_extraction_metadata

    field_scope = object_result.get("extract_field_scope") if object_result else {}
    related_keys = None
    if isinstance(field_scope, dict):
        related_keys = field_scope.get("parent_entity_keys")
    extra: Dict[str, Any] = {
        "source_type": "salesforce",
        "source_name": (org_info or {}).get("connection_name"),
        "org_id": (org_info or {}).get("org_id"),
    }
    if soql and str(soql).strip():
        extra["soql"] = str(soql).strip()
        extra["sql_query"] = str(soql).strip()
    if extraction_rules and isinstance(extraction_rules, dict):
        extra["extraction_rules"] = extraction_rules
    return build_persisted_extraction_metadata(
        migration_intent=migration_intent,
        extract_field_scope=field_scope if isinstance(field_scope, dict) else None,
        related_entity_keys=related_keys,
        source_entity_key=object_result.get("object_api_name") if object_result else None,
        field_hints=object_result.get("selected_fields") if object_result else None,
        destination=destination,
        destination_name=destination_name,
        extra=extra,
    )


async def handle_salesforce_data_extraction_stream(
    prompt: str,
    user_id: str,
    conversation_history: Optional[list] = None,
    destination: str = "salesforce",
    destination_name: Optional[str] = None,
    source_name: Optional[str] = None,
    conversation_id: Optional[str] = None,
    parent_job_id: Optional[str] = None,
    source_entity_key: Optional[str] = None,
    related_entity_keys: Optional[list] = None,
    extract_field_scope: Optional[Dict[str, Any]] = None,
    migration_intent: Optional[Dict[str, Any]] = None,
    extract_contract: Optional[Dict[str, Any]] = None,
):
    """
    Handle Salesforce data extraction requests.
    Emits job_id early so frontend can cancel before slow setup completes.
    """
    if extract_contract and not source_entity_key:
        source_entity_key = extract_contract.get("primary_object") or source_entity_key
    if extract_contract and isinstance(extract_field_scope, dict):
        extract_field_scope = dict(extract_field_scope)
    elif extract_contract and extract_contract.get("field_scope"):
        extract_field_scope = dict(extract_contract.get("field_scope") or {})

    async def generate():
        from utils.job_stores import extraction_jobs

        try:
            conversation_history_local = conversation_history or []
            log_sf_extract(
                "extract_start",
                conversation_id=conversation_id,
                user_id=str(user_id),
                source_name=source_name,
                destination=destination,
                destination_name=destination_name,
                prompt_preview=(prompt or "")[:120],
            )

            # Create job immediately so frontend can cancel early (before slow org/object/SOQL setup)
            job_id = str(uuid4())
            await extraction_jobs.set(job_id, {
                "status": "starting",
                "conversation_id": conversation_id,
                "user_id": str(user_id),
                "source_type": "salesforce",
            })
            yield f"data: {json.dumps({'event_type': 'extraction_job_created', 'job_id': job_id})}\n\n"

            await extraction_jobs.update(
                job_id,
                {"status": "starting", "progress": 0, "message": "Finding your Salesforce org…"},
            )
            yield extraction_setup_progress_sse_line(
                message="Finding your Salesforce org…",
                phase="resolve_org",
                source_type="salesforce",
                job_id=job_id,
                progress=0,
            )

            # 1. Check for active intent_state
            from handlers.classification_handler import get_active_intent_state
            intent_state = await get_active_intent_state(
                conversation_id, user_id, conversation_history_local, source_type="salesforce"
            )
            source_name_from_intent = intent_state.get("source_name") if (intent_state and intent_state.get("active_source") == "salesforce") else None
            final_source_name = source_name if source_name else source_name_from_intent

            org_result = await detect_salesforce_org_from_prompt(
                prompt, user_id, conversation_history_local, intent_state=intent_state, source_name=final_source_name
            )
            if not org_result:
                _sf_extract_abort_payload(
                    "org_not_detected",
                    job_id=job_id,
                    conversation_id=conversation_id,
                )
                yield f"**Error:** I could not detect any Salesforce connection for your account. Please connect your Salesforce org first.\n"
                return
            if not org_result.get("found"):
                _sf_extract_abort_payload(
                    "org_not_found",
                    job_id=job_id,
                    conversation_id=conversation_id,
                    message=org_result.get("message"),
                )
                yield f"**Error:** {org_result.get('message') or 'Please tell me which Salesforce org to use.'}\n"
                return
            if not org_result.get("is_connected"):
                conn = org_result.get("connection_name") or "your Salesforce org"
                _sf_extract_abort_payload(
                    "org_not_connected",
                    job_id=job_id,
                    conversation_id=conversation_id,
                    connection_name=conn,
                )
                yield f"**Error:** Looks like {conn} is not currently connected. Please reconnect it from the Connections page.\n"
                return

            log_sf_extract(
                "org_resolved",
                job_id=job_id,
                connection_name=org_result.get("connection_name"),
                org_id=org_result.get("org_id"),
            )

            conn_display = org_result.get("connection_name") or "your org"
            await extraction_jobs.update(
                job_id,
                {
                    "message": f"Using {conn_display} — choosing object and fields…",
                    "connection_name": conn_display,
                    "org_id": org_result.get("org_id"),
                },
            )
            yield extraction_setup_progress_sse_line(
                message=f"Using {conn_display} — choosing object and fields…",
                phase="resolve_object",
                source_type="salesforce",
                job_id=job_id,
                progress=0,
            )

            # 2. Determine object and fields
            from services.planning.extract_field_scope import resolve_extract_field_scope

            resolved_field_scope = resolve_extract_field_scope(
                extract_field_scope,
                source_type="salesforce",
                destination=destination,
                source_name=final_source_name or source_name,
                destination_name=destination_name,
                related_entity_keys=related_entity_keys,
            )
            from services.planning.extract_contract_gates import (
                extract_contract_is_authoritative,
                sf_object_result_from_extract_contract,
            )

            with SfExtractTimer("object_and_fields", job_id=job_id, org_id=org_result.get("org_id")):
                if extract_contract_is_authoritative(extract_contract):
                    object_result = await sf_object_result_from_extract_contract(
                        extract_contract,
                        user_id=str(user_id),
                        org_id=org_result.get("org_id"),
                    )
                    log_sf_extract(
                        "object_fields_from_contract",
                        job_id=job_id,
                        object_api_name=object_result.get("object_api_name"),
                        selected_field_count=len(object_result.get("selected_fields") or []),
                    )
                else:
                    object_result = await determine_salesforce_object_and_fields(
                        prompt,
                        user_id,
                        conversation_history_local,
                        org_id=org_result.get("org_id"),
                        object_hint=source_entity_key,
                        related_entity_keys=related_entity_keys,
                        extract_field_scope=resolved_field_scope,
                    )
            if object_result.get("needs_clarification"):
                _sf_extract_abort_payload(
                    "object_fields_clarification",
                    job_id=job_id,
                    conversation_id=conversation_id,
                    message=object_result.get("clarification_message"),
                )
                clar_msg = object_result.get("clarification_message") or "Which Salesforce object should I use?"
                for chunk in _clarification_stream_chunks(clar_msg):
                    yield chunk
                return

            log_sf_extract(
                "object_fields_resolved",
                job_id=job_id,
                object_api_name=object_result.get("object_api_name"),
                selected_field_count=len(object_result.get("selected_fields") or []),
                field_metadata_count=len(object_result.get("field_metadata") or []),
                filters=object_result.get("filters"),
            )

            obj_label = object_result.get("object_label") or object_result.get("object_api_name") or "your object"
            await extraction_jobs.update(
                job_id,
                {"message": f"Building SOQL for {obj_label}…"},
            )
            yield extraction_setup_progress_sse_line(
                message=f"Building SOQL for {obj_label}…",
                phase="build_query",
                source_type="salesforce",
                job_id=job_id,
                progress=0,
            )

            # 3. Generate SOQL
            from services.architect_agent import get_user_extraction_limit
            extraction_limit = await get_user_extraction_limit(str(user_id))
            with SfExtractTimer("soql_generation", job_id=job_id, object=object_result.get("object_api_name")):
                soql_result = await generate_salesforce_soql_query(
                    prompt,
                    object_result,
                    conversation_history_local,
                    user_id=str(user_id),
                    extraction_limit=extraction_limit,
                    extract_field_scope=object_result.get("extract_field_scope") or extract_field_scope,
                    migration_intent=migration_intent,
                    classification=_sf_extract_classification_context(
                        intent_state=intent_state,
                        conversation_history=conversation_history_local,
                    ),
                )
            if soql_result.get("needs_clarification"):
                msg = soql_result.get("clarification_message") or "Could you clarify the data you want to retrieve?"
                _sf_extract_abort_payload(
                    "soql_clarification",
                    job_id=job_id,
                    conversation_id=conversation_id,
                    message=msg,
                    soql_debug=soql_result.get("_debug"),
                )
                if _is_field_related_clarification(msg):
                    invalid_fields = _extract_field_names_from_clarification(msg)
                    if not invalid_fields and soql_result.get("soql"):
                        m = re.search(r"\bwhere\s+(.+?)(?:\s+group\s+by|\s+order\s+by|\s+limit|$)", soql_result.get("soql", ""), re.IGNORECASE | re.DOTALL)
                        if m:
                            # Extract field from WHERE clause (e.g. "ex_Id__c = '122345'" -> ex_Id__c)
                            where_part = m.group(1)
                            field_match = re.search(r"([a-zA-Z_][a-zA-Z0-9_.]*(?:__c)?)\s*[=<>]", where_part)
                            if field_match:
                                invalid_fields = [field_match.group(1)]
                    if invalid_fields:
                        _, _, available_fields = _validate_soql_fields(object_result, invalid_fields)
                        clarification = await _build_invalid_field_clarification(
                            invalid_fields=invalid_fields,
                            available_fields=available_fields,
                            object_result=object_result,
                            org_result=org_result,
                            soql=soql_result.get("soql"),
                            prompt=prompt,
                        )
                        for chunk in _clarification_stream_chunks(clarification):
                            yield chunk
                    else:
                        for chunk in _clarification_stream_chunks(msg):
                            yield chunk
                    return
                for chunk in _clarification_stream_chunks(msg):
                    yield chunk
                return
            if not soql_result.get("soql"):
                _sf_extract_abort_payload(
                    "soql_empty",
                    job_id=job_id,
                    conversation_id=conversation_id,
                    soql_debug=soql_result.get("_debug"),
                )
                yield "I couldn't generate a SOQL query from the information provided. Please specify the Salesforce object and fields you need.\n"
                return

            log_sf_extract(
                "soql_ready",
                job_id=job_id,
                soql_preview=(soql_result.get("soql") or "")[:200],
                referenced_field_count=len(soql_result.get("referenced_fields") or []),
                limit=soql_result.get("limit"),
                soql_debug=soql_result.get("_debug"),
            )

            # Pre-flight validation: ensure all referenced fields exist in the org
            referenced_fields = soql_result.get("referenced_fields") or []
            if referenced_fields:
                valid, invalid_fields, available_fields = _validate_soql_fields(object_result, referenced_fields)
                if not valid:
                    _sf_extract_abort_payload(
                        "soql_field_validation_failed",
                        job_id=job_id,
                        conversation_id=conversation_id,
                        invalid_fields=invalid_fields,
                    )
                    clarification = await _build_invalid_field_clarification(
                        invalid_fields=invalid_fields,
                        available_fields=available_fields,
                        object_result=object_result,
                        org_result=org_result,
                        soql=soql_result.get("soql"),
                        prompt=prompt,
                    )
                    for chunk in _clarification_stream_chunks(clarification):
                        yield chunk
                    return

            await extraction_jobs.update(
                job_id,
                {"message": "Running query and fetching records…", "progress": 5},
            )
            yield extraction_setup_progress_sse_line(
                message="Running query and fetching records…",
                phase="execute",
                source_type="salesforce",
                job_id=job_id,
                progress=5,
            )

            await start_salesforce_extraction_job(
                user_id=str(user_id),
                org_info=org_result,
                object_result=object_result,
                soql_payload=soql_result,
                destination=destination,
                destination_name=destination_name,
                extraction_prompt=prompt,
                conversation_id=conversation_id,
                parent_job_id=parent_job_id,
                existing_job_id=job_id,
                migration_intent=migration_intent,
            )
            yield (
                "data: "
                + json.dumps({"status": "processing", "job_id": job_id, "source_type": "salesforce", "message": "Salesforce extraction started in background"})
                + "\n\n"
            )
            log_sf_extract(
                "extract_job_started",
                job_id=job_id,
                conversation_id=conversation_id,
            )

            # Hold stream open and mirror job progress + completion (avoids frontend /job-status polling).
            async for evt in stream_extraction_job_events_until_terminal(job_id, extraction_jobs):
                evt_type = evt.get("event_type") if isinstance(evt, dict) else None
                if evt_type == "extraction_job_finished":
                    job_status = evt.get("job_status") if isinstance(evt.get("job_status"), dict) else {}
                    log_sf_extract(
                        "extract_job_finished",
                        always=True,
                        job_id=job_id,
                        status=job_status.get("status"),
                        progress=job_status.get("progress"),
                        error=job_status.get("error"),
                    )
                yield f"data: {json.dumps(evt, default=str)}\n\n"
        except Exception as exc:
            import traceback

            log_sf_extract(
                "extract_exception",
                level="error",
                always=True,
                conversation_id=conversation_id,
                error_type=type(exc).__name__,
                error=str(exc)[:500],
                traceback=traceback.format_exc()[-800:],
            )
            print(f"❌ Error in Salesforce extraction handler: {exc}")
            yield f"**Error:** {str(exc)}\n"

    return generate()


async def handle_salesforce_metadata_deploy_stream(
    prompt: str,
    user_id: str,
    conversation_history: Optional[list] = None,
    source_name: Optional[str] = None,
    destination_name: Optional[str] = None,
    conversation_id: Optional[str] = None,
    deploy_contract: Optional[Dict[str, Any]] = None,
):
    """
    Handle Salesforce metadata/field deploy: extract field or object definitions from one org and deploy to another.
    Reuses classify_source_destination (source_name, destination_name), detect_salesforce_org_from_prompt for org resolution,
    and determine_salesforce_object_and_fields to parse which object(s) and fields to deploy (Phase 3),
    object-exists check (DB first, then org) per object (Phase 4),
    and Tooling API execution via background job (Phase 5). Custom object creation is handled by the background service.
    """
    try:
        conversation_history = conversation_history or []
        if deploy_contract:
            source_name = (source_name or deploy_contract.get("source_name") or "").strip() or None
            destination_name = (
                (destination_name or deploy_contract.get("destination_name") or "").strip() or None
            )
        else:
            source_name = (source_name or "").strip() or None
            destination_name = (destination_name or "").strip() or None

        if not source_name:
            return await _salesforce_clarification_response(
                "Which Salesforce org should I use as the source? Please specify the connection name (e.g. Production, Sandbox)."
            )
        if not destination_name:
            return await _salesforce_clarification_response(
                "Which Salesforce org should I deploy to? Please specify the connection name (e.g. Production, Sandbox)."
            )

        # Resolve source org (reuse existing)
        org_result_source = await detect_salesforce_org_from_prompt(
            prompt, user_id, conversation_history, source_name=source_name
        )
        if not org_result_source:
            return await _salesforce_clarification_response(
                "I could not detect any Salesforce connection for your account. Please connect your Salesforce org first."
            )
        if not org_result_source.get("found"):
            return await _salesforce_clarification_response(
                org_result_source.get("message") or f"Could not find source org '{source_name}'. Please check the connection name."
            )
        if not org_result_source.get("is_connected"):
            return await _salesforce_clarification_response(
                f"Source org '{source_name}' is not connected. Please reconnect it from the Connections page."
            )

        # Resolve destination org (reuse same function with destination_name as source_name)
        org_result_dest = await detect_salesforce_org_from_prompt(
            prompt, user_id, conversation_history, source_name=destination_name
        )
        if not org_result_dest:
            return await _salesforce_clarification_response(
                "I could not detect the destination Salesforce connection."
            )
        if not org_result_dest.get("found"):
            return await _salesforce_clarification_response(
                org_result_dest.get("message") or f"Could not find destination org '{destination_name}'. Please check the connection name."
            )
        if not org_result_dest.get("is_connected"):
            return await _salesforce_clarification_response(
                f"Destination org '{destination_name}' is not connected. Please reconnect it from the Connections page."
            )

        # Phase 3: Parse what to deploy — honor deploy_contract pins when binding validated fields
        source_org_id = org_result_source.get("org_id")
        deploy_object_hint = (deploy_contract or {}).get("source_entity_key") if deploy_contract else None
        deploy_field_hints = (deploy_contract or {}).get("field_hints") if deploy_contract else None
        if deploy_object_hint and deploy_field_hints:
            object_result = {
                "needs_clarification": False,
                "object_api_name": deploy_object_hint,
                "object_label": deploy_object_hint,
                "selected_fields": list(deploy_field_hints),
                "field_metadata": [
                    {"api_name": f, "label": f} for f in deploy_field_hints if str(f).strip()
                ],
                "extract_field_scope": {
                    "field_hints": list(deploy_field_hints),
                    "field_hints_strict": True,
                    "extract_field_scope": "explicit",
                },
                "org_id": source_org_id,
            }
        else:
            object_result = await determine_salesforce_object_and_fields(
                prompt,
                user_id,
                conversation_history,
                org_id=source_org_id,
                object_hint=deploy_object_hint,
                extract_field_scope=(
                    {
                        "field_hints": list(deploy_field_hints),
                        "field_hints_strict": bool(deploy_field_hints),
                        "extract_field_scope": "explicit" if deploy_field_hints else "unspecified",
                    }
                    if deploy_field_hints
                    else None
                ),
            )
        if object_result.get("needs_clarification"):
            return await _salesforce_clarification_response(
                object_result.get("clarification_message") or "Which object and fields should I deploy? Please specify (e.g. Account Name and Industry, or Project__c and its fields)."
            )
        object_api_name = object_result.get("object_api_name")
        selected_fields = object_result.get("selected_fields") or []
        if not object_api_name:
            return await _salesforce_clarification_response(
                "I couldn't determine which Salesforce object to deploy. Please specify the object and fields (e.g. Account Name and Industry)."
            )
        if not selected_fields:
            return await _salesforce_clarification_response(
                "I couldn't determine which fields to deploy. Please specify the object and fields (e.g. Account Name and Industry, or 'all fields' for that object)."
            )

        # Detect if user wants record types deployed alongside fields
        _prompt_lower = prompt.lower()
        include_record_types = any(
            kw in _prompt_lower
            for kw in ["record type", "recordtype", "record_type"]
        )

        # Build deploy targets list (one object for now; Phase 4+ may support multiple)
        field_api_names = list(selected_fields)

        # Include custom fields from source org's live describe so newly created fields (not yet in our DB) are deployed
        try:
            source_token_info = await get_valid_salesforce_token(
                user_id=str(user_id),
                org_id=source_org_id,
                connected_app_id=org_result_source.get("connected_app_id"),
            )
            instance_url = (source_token_info.get("instance_url") or "").rstrip("/")
            access_token = source_token_info.get("access_token")
            if instance_url and access_token:
                describe_url = f"{instance_url}/services/data/v59.0/sobjects/{object_api_name}/describe"
                async with httpx.AsyncClient(timeout=30) as client:
                    desc_resp = await client.get(
                        describe_url,
                        headers={"Authorization": f"Bearer {access_token}"},
                    )
                if desc_resp.status_code == 200:
                    desc_data = desc_resp.json()
                    for f in (desc_data.get("fields") or []):
                        name = f.get("name")
                        if name and f.get("custom") and name not in field_api_names:
                            field_api_names.append(name)
        except Exception as e:
            print(f"⚠️ Metadata deploy: live describe fallback failed (using DB list only): {e}")

        deploy_targets = [
            {
                "object_api_name": object_api_name,
                "object_label": object_result.get("object_label") or object_api_name,
                "field_api_names": field_api_names,
            }
        ]

        # Phase 4: Object-exists check (DB first, then org) — decide Tooling API vs Metadata API per object
        dest_org_id = org_result_dest.get("org_id")
        from db.connection import database

        # Metadata extraction is included; only deploy (5 base + 1 per field) is charged in metadata_deploy_background_service

        for target in deploy_targets:
            obj_name = target["object_api_name"]
            # Step A: Check DB for destination org
            row = await database.fetch_one(
                """
                SELECT 1 FROM salesforce_objects
                WHERE user_id = :user_id AND org_id = :org_id AND object_api_name = :object_api_name
                LIMIT 1
                """,
                values={
                    "user_id": str(user_id),
                    "org_id": dest_org_id,
                    "object_api_name": obj_name,
                },
            )
            if row:
                target["object_exists_in_destination"] = True
                target["use_tooling_api"] = True
                continue
            # Step B: Not in DB — check in destination org via describe
            try:
                dest_token_info = await get_valid_salesforce_token(
                    user_id=str(user_id),
                    org_id=dest_org_id,
                    connected_app_id=org_result_dest.get("connected_app_id"),
                )
                instance_url = (dest_token_info.get("instance_url") or "").rstrip("/")
                access_token = dest_token_info.get("access_token")
                if not instance_url or not access_token:
                    target["object_exists_in_destination"] = False
                    # Still use Tooling API — _deploy_custom_object will handle creation
                    target["use_tooling_api"] = True
                    continue
                describe_url = f"{instance_url}/services/data/v59.0/sobjects/{obj_name}/describe"
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(
                        describe_url,
                        headers={"Authorization": f"Bearer {access_token}"},
                    )
                target["object_exists_in_destination"] = resp.status_code == 200
                # Always use Tooling API; if object is missing we create it first
                target["use_tooling_api"] = True
            except Exception as e:
                print(f"⚠️ Phase 4: could not check object {obj_name} in destination org: {e}")
                target["object_exists_in_destination"] = False
                # Still route through Tooling API — _deploy_custom_object will handle creation
                target["use_tooling_api"] = True

        # Phase 5: For Tooling API targets, create migration batch and run metadata deploy in background (same flow as record migration)
        source_conn = org_result_source.get("connection_name") or source_name
        dest_conn = org_result_dest.get("connection_name") or destination_name
        tooling_targets = [t for t in deploy_targets if t.get("use_tooling_api")]
        tooling_summaries = []
        metadata_deploy_started_events: list = []  # job_id/batch_id per target for migration_started and polling

        if tooling_targets:
            from services.migration_records_service import MigrationRecordsService
            from services.metadata_deploy_background_service import MetadataDeployBackgroundService
            from datetime import datetime
            from services.chat_service import ChatService

            for target in tooling_targets:
                obj_name = target["object_api_name"]
                field_api_names = target.get("field_api_names", [])
                if not field_api_names:
                    continue
                batch_name = f"Metadata_Deploy_{obj_name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
                mapping_config = {
                    "type": "metadata_deploy",
                    "source_org_id": source_org_id,
                    "dest_org_id": dest_org_id,
                    "dest_connected_app_id": org_result_dest.get("connected_app_id"),  # same connection for rollback
                    "object_api_name": obj_name,
                    "source_conn": source_conn,
                    "dest_conn": dest_conn,
                }
                object_exists = target.get("object_exists_in_destination", True)
                # +1 for the custom object record itself when we create it
                total_records = len(field_api_names) + (0 if object_exists else 1)
                batch_id = await MigrationRecordsService.create_migration_batch(
                    user_id=str(user_id),
                    batch_name=batch_name,
                    document_type="Salesforce Metadata",
                    total_records=total_records,
                    mapping_config=mapping_config,
                    destination_type="salesforce",
                    batch_type="metadata",
                )
                meta_service = MetadataDeployBackgroundService()
                job_id = await meta_service.start_metadata_deploy_job(
                    batch_id=batch_id,
                    user_id=str(user_id),
                    object_api_name=obj_name,
                    field_api_names=field_api_names,
                    source_org_id=source_org_id,
                    dest_org_id=dest_org_id,
                    source_connected_app_id=org_result_source.get("connected_app_id"),
                    dest_connected_app_id=org_result_dest.get("connected_app_id"),
                    source_conn_name=source_conn,
                    dest_conn_name=dest_conn,
                    include_record_types=include_record_types,
                    object_exists_on_dest=object_exists,
                )
                metadata_deploy_started_events.append({
                    "job_id": job_id,
                    "batch_id": batch_id,
                    "destination": "salesforce",
                    "destination_name": dest_conn,
                    "record_count": total_records,
                    "total_records": total_records,
                })
                if conversation_id:
                    try:
                        await ChatService.log_data_event(
                            conversation_id=conversation_id,
                            user_id=str(user_id),
                            event_type="migration_started",
                            event_data={
                                "job_id": job_id,
                                "batch_id": batch_id,
                                "destination": "salesforce",
                                "destination_name": dest_conn,
                                "record_count": total_records,
                                "total_records": total_records,
                            },
                        )
                    except Exception as e:
                        print(f"⚠️ Failed to log migration_started for metadata deploy: {e}")
                rt_suffix = " + record types" if include_record_types else ""
                tooling_summaries.append(
                    f"**{obj_name}**: deploy started ({len(field_api_names)} fields{rt_suffix})."
                )

        # Build final message: Tooling API results (Phase 5)
        source_conn = org_result_source.get("connection_name") or source_name
        dest_conn = org_result_dest.get("connection_name") or destination_name
        fields_preview = ", ".join(selected_fields[:10])
        if len(selected_fields) > 10:
            fields_preview += f" (+{len(selected_fields) - 10} more)"
        msg_parts = [
            f"Metadata deploy: source **{source_conn}** → destination **{dest_conn}**. ",
            f"Object **{object_api_name}** with {len(selected_fields)} field(s): {fields_preview}. ",
        ]
        if tooling_summaries:
            msg_parts.append("; ".join(tooling_summaries) + ". ")
        final_message = "".join(msg_parts).strip()

        # Stream migration_started events first (so frontend can show progress and start polling), then the text
        if metadata_deploy_started_events:

            async def stream_with_migration_events():
                import json
                for ev in metadata_deploy_started_events:
                    payload = {"event_type": "migration_started", **ev}
                    yield f"data: {json.dumps(payload)}\n"
                yield f"{final_message}\n"

            return stream_with_migration_events()
        return await _salesforce_clarification_response(final_message)

    except Exception as e:
        print(f"❌ Error in Salesforce metadata deploy handler: {e}")
        return await _salesforce_clarification_response(
            f"Something went wrong while resolving your Salesforce orgs: {str(e)}"
        )


async def handle_salesforce_request_stream(prompt: str, conversation_history: list, user_id, source, source_name):
    """
    Handle Salesforce-related requests (existing logic)
    """
    try:
        # Architect chat is free; no credit deduction

        # Lazy imports to avoid circular dependency
        from services.architect_agent import (
            get_dynamic_document_type_categories,
            async_openai_call
        )
        
        
        # Get enhanced context with relevant Salesforce objects and fields
        enhanced_context = await build_salesforce_context_for_prompt(prompt, user_id, source, source_name)
        categories = await get_dynamic_document_type_categories(user_id, source, source_name)
        category_list = json.dumps(categories)
        
        # Build conversation context
        messages = [
            {
                "role": "system",
                "content": f"""Hey there! I'm Archian AI, your friendly Salesforce data pipeline expert! 😊

I'm here to help you make sense of your data and get it perfectly organized in Salesforce. Think of me as your smart friend who happens to know everything about Salesforce data structures and can help you migrate data from pretty much anywhere!
**CRITICAL INSTRUCTIONS FOR ENHANCED CONTEXT:**
{enhanced_context}

**MANDATORY REQUIREMENTS:**
1. **ALWAYS reference the specific Salesforce objects and fields** from the enhanced context above
2. **Use exact object names, field names, and data types** from the context  
3. **Provide detailed, technical explanations** like ChatGPT would
4. **Give specific examples** using the actual fields available
5. **Explain relationships** between objects when relevant
6. **Include best practices** for data structure and field usage
7. **Reference field requirements** (Required vs Optional) and custom fields

**CRITICAL: You MUST structure your response with bullet points and detailed field breakdowns like this:**

**For each relevant object, you MUST include:**
- **Object Name** and Category
- **Purpose and Description** 
- **Key Fields Breakdown** (with data types and requirements)
- **Custom Fields** (if any)
- **Best Practices** for using this object
- **Relationships** to other objects

**Your Response Style:**
- Be comprehensive and detailed like ChatGPT
- Use technical Salesforce terminology
- Provide step-by-step guidance when applicable
- Explain data types and their implications
- Give specific field examples from the context
- Include best practices for Salesforce data modeling

**IMPORTANT: Always focus on what Archian AI can do for you, not generic Salesforce advice. When you ask about building a data pipeline or migrating records, I'll explain that I can handle it automatically - I won't mention other tools like Data Import Wizard, Data Loader, MuleSoft, etc. I'm here to do the work for you!

If users ask about other tools, confidently explain what they do and then highlight how much easier, more efficient, and better Archian AI is. Tell them we've made data pipelines and data sync as simple as talking to a person - we're the best solution for making pipeline work feel natural and effortless!

Remind the user that: Archian AI can handle this automatically — just upload your file or connect to your database and we'll take care of the rest.

Your job is also to classify the user's intent:

- "analyze": if the user is asking where, how, or what object to use — but is not asking for an actual migration yet.
- "migrate": only if the user is clearly asking you to upload, migrate, or push data into Salesforce.

Use the following official Salesforce categories:

{category_list}

**IMPORTANT:** 
- Be conversational and reference previous messages when relevant
- Provide comprehensive, detailed responses
- Use technical Salesforce terminology
- Give specific examples and best practices
- Keep formatting clean and simple
- End with clear next steps for file upload"""
            }
        ]
        
        # Add complete conversation history for context
        if conversation_history:
            # Send complete conversation without truncation
            for msg in conversation_history:
                if msg.get('type') == 'user':
                    messages.append({"role": "user", "content": msg.get('text', '')})
                elif msg.get('type') == 'system':
                    messages.append({"role": "assistant", "content": msg.get('text', '')})
        
        # Add current prompt
        messages.append({"role": "user", "content": prompt})
        
        # Create streaming response
        response = await async_openai_call(
            None,
            task=llm_tasks.SALESFORCE_INFO,
            messages=messages,
            temperature=0.7,
            stream=True,
        )
        
        # Stream the response in real-time
        async def generate():
            try:
                for chunk in response:
                    if chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        yield content
            except Exception as e:
                yield f"\n\n❌ Error during streaming: {str(e)}"
        
        return generate()
        
    except Exception as e:
        return "❌ Archian AI was unable to generate a response. Please try again."


async def start_salesforce_extraction_job(
    user_id: str,
    org_info: Dict[str, Any],
    object_result: Dict[str, Any],
    soql_payload: Dict[str, Any],
    destination: str,
    destination_name: Optional[str],
    extraction_prompt: str,
    conversation_id: Optional[str] = None,
    parent_job_id: Optional[str] = None,
    extraction_rules: Optional[Dict[str, Any]] = None,
    extracted_data: Optional[List[Dict[str, Any]]] = None,
    existing_job_id: Optional[str] = None,
    migration_intent: Optional[Dict[str, Any]] = None,
):
    from utils.job_stores import extraction_jobs

    job_id = existing_job_id if existing_job_id else str(uuid4())
    job_data = {
        "status": "processing",
        "progress": 0,
        "result": None,
        "error": None,
        "created_at": datetime.utcnow().isoformat(),
        "extraction_goals": extraction_prompt,
        "source_type": "salesforce",
        "destination": destination,
        "destination_name": destination_name,
        "org_id": org_info.get("org_id"),
        "connection_name": org_info.get("connection_name"),
        "environment_type": org_info.get("environment_type"),
        "connected_app_id": org_info.get("connected_app_id"),
        "object_api_name": object_result.get("object_api_name"),
        "object_label": object_result.get("object_label"),
        "fields": soql_payload.get("used_fields"),
        "soql": soql_payload.get("soql"),
        "filter_summary": soql_payload.get("filter_summary"),
        "user_id": str(user_id),
        "job_type": "salesforce_extraction",
        "org_info": org_info,
        "salesforce_token_id": str(org_info.get("token_id")) if org_info.get("token_id") else None,
        "object_result": object_result,
        "soql_payload": soql_payload,
        "parent_job_id": parent_job_id,
        "conversation_id": conversation_id,
        "extraction_rules": extraction_rules,  # Store saved extraction rules
        "extracted_data": extracted_data,  # Store processed data from extraction_results table
        "migration_intent": migration_intent,
    }
    if existing_job_id:
        current = await extraction_jobs.get(job_id, {})
        if current.get("cancelled"):
            return job_id  # User cancelled during setup, do not spawn processor
        await extraction_jobs.update(job_id, job_data)
    else:
        await extraction_jobs.set(job_id, job_data)

    # Log extraction_started event with intent_state
    if conversation_id:
        try:
            from services.chat_service import ChatService
            from handlers.classification_handler import create_intent_state
            
            # Determine intent (extract or migrate based on destination)
            active_intent = "migrate" if destination and destination != "unknown" else "extract"
            source_name = org_info.get("connection_name")
            
            intent_state = create_intent_state(
                active_intent=active_intent,
                active_source="salesforce",
                active_destination=destination or "unknown",
                source_name=source_name,
                destination_name=destination_name,
                job_id=job_id,
                state="extracting"
            )
            
            # Add object and field information to intent_state for context
            if object_result:
                intent_state["object_api_name"] = object_result.get("object_api_name")
                intent_state["object_label"] = object_result.get("object_label")
                intent_state["selected_fields"] = object_result.get("selected_fields", [])
            
            await ChatService.log_data_event(
                conversation_id=conversation_id,
                user_id=str(user_id),
                event_type="extraction_started",
                event_data={
                    "job_id": job_id,
                    "source_type": "salesforce",
                    "source_name": source_name,
                    "destination": destination,
                    "destination_name": destination_name,
                    "intent_state": intent_state
                }
            )
        except Exception as e:
            print(f"⚠️ Failed to log extraction_started event: {e}")

    asyncio.create_task(
        _process_salesforce_extraction_job(
            job_id=job_id,
            user_id=str(user_id),
            org_info=org_info,
            object_result=object_result,
            soql_payload=soql_payload,
            destination=destination,
            destination_name=destination_name,
            extraction_prompt=extraction_prompt,
            conversation_id=conversation_id,
            parent_job_id=parent_job_id,
            extraction_rules=extraction_rules,  # Pass saved extraction rules
            extracted_data=extracted_data,  # Pass processed data from extraction_results table
            migration_intent=migration_intent,
        )
    )

    return job_id


async def _process_salesforce_extraction_job(
    job_id: str,
    user_id: str,
    org_info: Dict[str, Any],
    object_result: Dict[str, Any],
    soql_payload: Dict[str, Any],
    destination: str,
    destination_name: Optional[str],
    extraction_prompt: str,
    conversation_id: Optional[str] = None,
    parent_job_id: Optional[str] = None,
    extraction_rules: Optional[Dict[str, Any]] = None,
    extracted_data: Optional[List[Dict[str, Any]]] = None,
    migration_intent: Optional[Dict[str, Any]] = None,
):
    from utils.job_stores import extraction_jobs

    try:
        # Check if job exists (Redis-backed)
        if not await extraction_jobs.exists(job_id):
            return

        # Get job data to check for cleanup intent and cancellation
        job_data = await extraction_jobs.get(job_id, {})
        if migration_intent is None:
            raw_intent = job_data.get("migration_intent")
            if isinstance(raw_intent, dict):
                migration_intent = raw_intent
        if job_data.get("cancelled"):
            print(f"🛑 Salesforce extraction job {job_id} cancelled by user")
            await extraction_jobs.update(job_id, {"status": "cancelled"})
            return
        
        # Check if this is cleanup intent with existing extracted data (passed as parameter or in job metadata)
        # Priority: 1. Parameter, 2. Job metadata
        cleanup_extracted_data = extracted_data
        if not cleanup_extracted_data:
            cleanup_extracted_data = job_data.get("extracted_data")
        
        is_cleanup_flow = job_data.get("intent") == "cleanup" and cleanup_extracted_data
        
        if is_cleanup_flow:
            # Cleanup flow: Route through normal extraction pipeline with saved_extraction_rules
            await extraction_jobs.update(job_id, {
                "progress": 60,
                "message": "Processing cleanup data with AI..."
            })
            
            # Get cleanup rules from job metadata (from quality report)
            # These will be merged with prompt-generated rules after OpenAI call
            quality_report_rules = job_data.get("cleanup_extraction_rules", {})
            if not quality_report_rules:
                # Fallback to extraction_rules if cleanup_extraction_rules not set
                quality_report_rules = job_data.get("extraction_rules", {})
            
            job_data = await extraction_jobs.get(job_id, {})
            if job_data.get("cancelled"):
                print(f"🛑 Salesforce extraction job {job_id} cancelled by user")
                await extraction_jobs.update(job_id, {"status": "cancelled"})
                return

            # Route through extract_structured_data_from_salesforce WITHOUT saved_extraction_rules
            # Let extract_structured_data_from_salesforce generate rules from cleanup prompt
            # Rules will be merged and applied AFTER the OpenAI call (matching SQL/Google Sheets flow)
            try:
                structured_payload = await extract_structured_data_from_salesforce(
                    salesforce_data=cleanup_extracted_data,  # Pass original data (rules will be applied after OpenAI call)
                    user_id=user_id,
                    extraction_goals=extraction_prompt,
                    object_label=object_result.get("object_label") or object_result.get("object_api_name"),
                    destination=destination,
                    destination_name=destination_name,
                    saved_extraction_rules=None  # Let extract_structured_data_from_salesforce generate rules from cleanup prompt
                )
                # AI processing completed successfully
            except Exception as e:
                print(f"❌ Error in AI processing during cleanup: {e}")
                raise e
            
            # For cleanup flow: Merge quality report rules with prompt-generated rules
            # This matches SQL and Google Sheets flow - merge rules AFTER OpenAI call
            extraction_rules = None
            if structured_payload:
                # Initialize extraction_rules_result from prompt-generated rules
                extraction_rules_result = structured_payload.get("extraction_rules") or {}
                if not extraction_rules_result:
                    extraction_rules_result = {}
                
                # Merge quality report rules with prompt-generated rules (if quality_report_rules exists)
                if quality_report_rules:
                    # Merge quality report rules with prompt-generated rules
                    # Prompt rules take precedence for filter_rules, deduplication_rules, transformation_rules
                    # Quality report data_cleanup_rules should be merged (not overwritten)
                    if quality_report_rules.get("data_cleanup_rules"):
                        existing_data_cleanup = extraction_rules_result.get("data_cleanup_rules", {})
                        if isinstance(existing_data_cleanup, dict) and isinstance(quality_report_rules["data_cleanup_rules"], dict):
                            # Merge both dicts - prompt rules take precedence if there are conflicts
                            extraction_rules_result["data_cleanup_rules"] = {**quality_report_rules["data_cleanup_rules"], **existing_data_cleanup}
                        else:
                            # If not both dicts, use quality report rules as fallback
                            if not existing_data_cleanup:
                                extraction_rules_result["data_cleanup_rules"] = quality_report_rules["data_cleanup_rules"]
                    
                    # Use quality report rules for other rule types only if prompt didn't generate them
                    if quality_report_rules.get("filter_rules") and not extraction_rules_result.get("filter_rules"):
                        extraction_rules_result["filter_rules"] = quality_report_rules["filter_rules"]
                    if quality_report_rules.get("deduplication_rules") and not extraction_rules_result.get("deduplication_rules"):
                        extraction_rules_result["deduplication_rules"] = quality_report_rules["deduplication_rules"]
                    if quality_report_rules.get("transformation_rules") and not extraction_rules_result.get("transformation_rules"):
                        extraction_rules_result["transformation_rules"] = quality_report_rules["transformation_rules"]
                
                # Now accumulate with original_extraction_rules to preserve original transformation rules
                # Get existing extraction rules - prioritize original_extraction_rules from job metadata
                existing_extraction_rules = {}
                source_schema = None
                
                # First, try to get original_extraction_rules from job metadata (from initial extraction)
                original_extraction_rules_from_job = job_data.get("original_extraction_rules")
                
                # Defensive check: ensure original_extraction_rules_from_job is a dict (not a list, None, or other type)
                if original_extraction_rules_from_job is not None and isinstance(original_extraction_rules_from_job, dict) and len(original_extraction_rules_from_job) > 0:
                    existing_extraction_rules = original_extraction_rules_from_job
                    # Additional defensive check: ensure existing_extraction_rules is still a dict and not empty
                    if not isinstance(existing_extraction_rules, dict) or not existing_extraction_rules:
                        print(f"⚠️ [SALESFORCE CLEANUP FLOW] existing_extraction_rules is not a valid dict (type: {type(existing_extraction_rules)}, empty: {not existing_extraction_rules if isinstance(existing_extraction_rules, dict) else 'N/A'}), resetting to empty dict")
                        existing_extraction_rules = {}
                    else:
                        transformation_rules = existing_extraction_rules.get('transformation_rules')
                        if transformation_rules is not None and not isinstance(transformation_rules, dict):
                            print(f"⚠️ [SALESFORCE CLEANUP FLOW] transformation_rules exists but is not a dict (type: {type(transformation_rules)}), will not preserve transformation rules")
                elif original_extraction_rules_from_job is not None:
                    # original_extraction_rules exists but is not a dict (could be a list, empty dict, or other type)
                    print(f"⚠️ [SALESFORCE CLEANUP FLOW] original_extraction_rules found but is not a valid dict (type: {type(original_extraction_rules_from_job)}, value: {original_extraction_rules_from_job}), resetting to empty dict")
                    existing_extraction_rules = {}
                else:
                    # original_extraction_rules is None or not present
                    existing_extraction_rules = {}
                
                # Try to get source_schema from job metadata (prioritize original source_schema)
                # We need the ORIGINAL source schema (before transformations) for rule accumulation
                if not source_schema:
                    source_schema = job_data.get("source_schema")  # Original source schema from parent job
                    if not source_schema:
                        # Fallback: try to get from parent job directly
                        parent_job_id = job_data.get("parent_job_id")
                        if parent_job_id:
                            try:
                                parent_job = await extraction_jobs.get(parent_job_id, {})
                                if parent_job:
                                    source_schema = parent_job.get("source_schema")
                            except Exception as e:
                                print(f"⚠️ [SALESFORCE CLEANUP FLOW] Error getting source_schema from parent job: {e}")
                    
                    # If still no source_schema, extract from cleanup_extracted_data (original data before transformations)
                    if not source_schema and cleanup_extracted_data:
                        from utils.rule_accumulation import extract_source_schema_from_data
                        source_schema = extract_source_schema_from_data(cleanup_extracted_data)
                
                # Accumulate merged rules with original transformation rules
                if source_schema and existing_extraction_rules:
                    from utils.rule_accumulation import accumulate_cleanup_rules
                    try:
                        # Combine extraction_rules_result (prompt + quality report) with existing_extraction_rules (original transformation rules)
                        # Use extraction_rules_result as new_cleanup_rules (they already include quality report rules)
                        # Ensure existing_extraction_rules is a dict (will be normalized in accumulate_cleanup_rules)
                        if not isinstance(existing_extraction_rules, dict):
                            existing_extraction_rules = {}
                        extraction_rules = accumulate_cleanup_rules(
                            existing_rules=existing_extraction_rules,
                            new_cleanup_rules=extraction_rules_result,
                            source_schema=source_schema
                        )
                    except Exception as acc_error:
                        print(f"⚠️ [SALESFORCE CLEANUP FLOW] Error accumulating rules: {acc_error}")
                        import traceback
                        traceback.print_exc()
                        # Fallback: use extraction_rules_result (already merged)
                        extraction_rules = extraction_rules_result
                else:
                    # No original transformation rules - only apply data cleanup rules
                    # Filter out transformation rules from OpenAI response, keep only data_cleanup_rules
                    extraction_rules = {}
                    
                    # Only include data_cleanup_rules from quality report (not transformation rules from OpenAI)
                    if quality_report_rules and quality_report_rules.get("data_cleanup_rules"):
                        extraction_rules["data_cleanup_rules"] = quality_report_rules["data_cleanup_rules"]
                    
                    # Also include deduplication_rules if present in quality report
                    if quality_report_rules and quality_report_rules.get("deduplication_rules"):
                        extraction_rules["deduplication_rules"] = quality_report_rules["deduplication_rules"]
                    
                    # Do NOT include transformation_rules from OpenAI response when there are no original transformation rules
                    # The data structure is already correct, we only need to clean the values
                    if extraction_rules_result.get("transformation_rules"):
                        print(f"⚠️ [SALESFORCE CLEANUP FLOW] Skipping OpenAI-generated transformation_rules - no original transformation rules to preserve")
                    
                    # If extraction_rules is empty, set to None to avoid issues
                    if not extraction_rules:
                        extraction_rules = None
                
                # Update structured_payload with merged rules
                structured_payload["extraction_rules"] = extraction_rules
                
                # Apply merged rules to records from structured_payload (the data returned from OpenAI)
                # This matches SQL and Google Sheets flow - always apply if extraction_rules exists
                cleaned_records = structured_payload.get("records", [])
                if extraction_rules and cleaned_records:
                    from utils.extraction_utils import apply_all_extraction_rules
                    cleaned_records = apply_all_extraction_rules(cleaned_records, extraction_rules)
                    
                    # Update structured_payload with cleaned records
                    structured_payload["records"] = cleaned_records
            
            # Get preserved document_type/document_description from job_data if available (to maintain metadata)
            preserved_document_type = job_data.get("document_type")
            preserved_document_description = job_data.get("document_description")
            preserved_mapping_context = job_data.get("mapping_context_summary")
            
            # Use preserved metadata if available, otherwise use from structured_payload
            document_type = preserved_document_type or structured_payload.get("document_type")
            document_description = preserved_document_description or structured_payload.get("document_description")
            mapping_context_summary = preserved_mapping_context or structured_payload.get("mapping_context_summary")
            
            # If not in job_data, try to get from parent job
            if not document_type or not document_description:
                parent_job_id = job_data.get("parent_job_id")
                if parent_job_id:
                    parent_job = await extraction_jobs.get(parent_job_id, {})
                    if not document_type:
                        document_type = parent_job.get("result", {}).get("document_type")
                    if not document_description:
                        document_description = parent_job.get("result", {}).get("document_description")
                    if not mapping_context_summary:
                        mapping_context_summary = parent_job.get("result", {}).get("mapping_context_summary")
            
            # Final fallback: get from extraction_results table
            if not document_type or not document_description:
                from services.extraction_results_service import ExtractionResultsService
                parent_job_id = job_data.get("parent_job_id")
                if parent_job_id:
                    extraction_result_from_db = await ExtractionResultsService.get_extraction_result(
                        parent_job_id, user_id
                    )
                    if extraction_result_from_db:
                        if not document_type:
                            document_type = extraction_result_from_db.get("document_type")
                        if not document_description:
                            document_description = extraction_result_from_db.get("document_description")
            
            # Use preserved document_type and document_description, or fallback to defaults
            if not document_type:
                document_type = "Salesforce Data"
            if not document_description:
                document_description = f"Cleaned data from {object_result.get('object_label') or object_result.get('object_api_name')}"
            
            # Override structured_payload metadata with preserved values
            structured_payload["document_type"] = document_type
            structured_payload["document_description"] = document_description
            if mapping_context_summary:
                structured_payload["mapping_context_summary"] = mapping_context_summary
            
            # Set variables for final result (same as normal flow)
            actual_record_count = len(structured_payload.get("records", []))
            limitation_applied = structured_payload.get("limit_reached", False)
            plan_info_with_flag = structured_payload.get("plan_info")
            show_upgrade_option = structured_payload.get("show_upgrade_option", False)
            
            # Use extraction_rules from structured_payload (which includes merged and applied cleanup rules)
            extraction_rules = structured_payload.get("extraction_rules")
            
            # IMPORTANT: Keep _attachments in data for migration, but frontend will filter it in table display
            # We need _attachments metadata available when user clicks "Start Migration"
            # The frontend table component will filter out fields starting with '_' when rendering columns
            records_for_frontend = structured_payload.get("records", [])
            
            # Create final_result in same format as normal extraction (unified format)
            final_result = {
                "status": "success",
                "data": records_for_frontend,
                "total_records": len(structured_payload.get("records", [])),
                "object_api_name": object_result.get("object_api_name"),
                "object_label": object_result.get("object_label"),
                "connection_name": org_info.get("connection_name"),
                "table_name": object_result.get("object_api_name"),
                "soql": soql_payload.get("soql") if soql_payload else None,
                "user_prompt": extraction_prompt,
                "fields": soql_payload.get("used_fields") if soql_payload else None,
                "filter_summary": soql_payload.get("filter_summary") if soql_payload else None,
                "destination": destination,
                "destination_name": structured_payload.get("destination_name", destination_name),
                "extraction_rules": extraction_rules,
                "mapping_context_summary": mapping_context_summary,
                "show_upgrade_option": show_upgrade_option,
                "plan_info": plan_info_with_flag,
                "limitation_applied": limitation_applied,
                "extracted_schema_type": "salesforce",
                "org_id": org_info.get("org_id"),
                "salesforce_token_id": org_info.get("token_id"),
                "document_type": document_type,
                "document_description": document_description,
                "original_record_count": actual_record_count,
            }
            
            # Continue to unified post-processing (quality analysis and save logic - same as normal flow)
        else:
            # Normal extraction flow - extract from Salesforce
            # Update job progress (Redis-backed)
            await extraction_jobs.update(job_id, {
                "progress": 10,
                "message": "Preparing Salesforce extraction..."
            })

            # Get Salesforce token for the specified org
            from utils.salesforceapimigration import get_valid_salesforce_token

            token_info = await get_valid_salesforce_token(
                user_id=user_id,
                org_id=org_info.get("org_id"),
                connected_app_id=org_info.get("connected_app_id"),
            )
            access_token = token_info["access_token"]
            instance_url = token_info["instance_url"]
            token_id = token_info.get("id")
            if token_id:
                await extraction_jobs.update(job_id, {"salesforce_token_id": str(token_id)})

            await extraction_jobs.update(job_id, {
                "progress": 25,
                "message": "Running SOQL query..."
            })

            # Use Bulk API 2.0 for large extractions (threshold: 1500 records)
            soql_text = soql_payload.get("soql") or ""
            requested_limit: Optional[int] = None
            limit_match = re.search(r"\bLIMIT\s+(\d+)", soql_text, re.IGNORECASE)
            if limit_match:
                try:
                    requested_limit = int(limit_match.group(1))
                except ValueError:
                    requested_limit = None

            # Decide based on credit-based extraction allowance (>1500) and/or explicit LIMIT (>1500).
            # Use Bulk API when SOQL has no LIMIT (or small LIMIT) but the user's credits allow large pulls.
            try:
                from services.architect_agent import get_user_extraction_limit
                precheck_limit = await get_user_extraction_limit(user_id)
                can_extract_allowance = precheck_limit.get("can_extract", 0)
            except Exception:
                can_extract_allowance = 0
            allowance_over_threshold = isinstance(can_extract_allowance, int) and can_extract_allowance > 1500

            use_bulk_api = bool(allowance_over_threshold or (requested_limit and requested_limit > 1500))

            object_api_name = (
                object_result.get("object_api_name")
                or object_result.get("object_name")
                or ""
            )
            records: List[Dict[str, Any]] = []
            max_soql_retries = 5
            last_exc: Optional[Exception] = None
            token_refreshed = False  # Only force-refresh once per job

            for soql_attempt in range(max_soql_retries):
                try:
                    if use_bulk_api:
                        print("[SF EXTRACTION] Using Bulk API 2.0 for extraction")
                        await extraction_jobs.update(job_id, {
                            "progress": 25,
                            "message": "Running Bulk API 2.0 query (large dataset)..."
                        })
                        try:
                            records = await _fetch_salesforce_records_with_bulk_api(
                                access_token=access_token,
                                instance_url=instance_url,
                                soql=soql_text,
                                object_api_name=object_api_name,
                            )
                        except Exception as bulk_exc:
                            last_exc = bulk_exc
                            # Bulk API query has stricter SELECT support (e.g., compound fields like MailingAddress).
                            # If it fails, fall back to REST query so extraction still works.
                            print(f"⚠️ [BULK API 2.0] Bulk query failed, falling back to REST /query: {bulk_exc}")
                            await extraction_jobs.update(job_id, {
                                "progress": 25,
                                "message": "Bulk API query failed, retrying with REST API..."
                            })
                            records = await _fetch_salesforce_records(access_token, instance_url, soql_text)
                    else:
                        print("[SF EXTRACTION] Using REST API for extraction")
                        records = await _fetch_salesforce_records(access_token, instance_url, soql_text)
                    last_exc = None
                    break
                except Exception as e:
                    last_exc = e
                    err_text = str(e)

                    # On 401, force-refresh the token once and retry the query
                    is_401 = (
                        "401" in err_text
                        or "Unauthorized" in err_text
                        or "INVALID_SESSION_ID" in err_text
                        or "Session expired" in err_text
                    )
                    if is_401 and not token_refreshed:
                        print(f"⚠️ [SF EXTRACTION] 401 Unauthorized — refreshing token and retrying...")
                        try:
                            refreshed = await get_valid_salesforce_token(
                                user_id=user_id,
                                org_id=org_info.get("org_id"),
                                connected_app_id=org_info.get("connected_app_id"),
                                force_refresh=True,
                            )
                            access_token = refreshed["access_token"]
                            token_refreshed = True
                            continue
                        except Exception as refresh_err:
                            print(f"❌ [SF EXTRACTION] Token refresh failed: {refresh_err}")
                            raise

                    invalid_field = _parse_invalid_field_from_soql_error(err_text)
                    if invalid_field:
                        new_soql = _remove_field_from_soql(soql_text, invalid_field)
                        if new_soql and new_soql != soql_text:
                            print(f"⚠️ [SOQL] Removing invalid field '{invalid_field}' (not present on org), retrying...")
                            soql_text = new_soql
                            continue
                    raise

            if last_exc is not None:
                raise last_exc

            await extraction_jobs.update(job_id, {
                "progress": 50,
                "message": "Processing Salesforce data..."
            })

            cleaned_records = _clean_salesforce_records(records)
            actual_record_count = len(cleaned_records)

            # Extract attachment metadata if requested
            include_attachments = object_result.get("include_attachments", False)
            
            if include_attachments:
                print(f"📎 [ATTACHMENT EXTRACTION] Extracting attachments for {actual_record_count} records...")
                await extraction_jobs.update(job_id, {
                    "progress": 55,
                    "message": "Extracting file attachments..."
                })
                
                # Get record IDs - try both 'Id' and 'id' (case-insensitive)
                record_ids = []
                for record in cleaned_records:
                    record_id = record.get("Id") or record.get("id") or record.get("ID")
                    if record_id:
                        record_ids.append(record_id)
                
                if record_ids:
                    from utils.salesforce_file_attachment import fetch_attachments_for_records
                    
                    try:
                        attachment_metadata = await fetch_attachments_for_records(
                            record_ids=record_ids,
                            access_token=access_token,
                            instance_url=instance_url
                        )
                        
                        # Add attachment data to records
                        for record in cleaned_records:
                            # Try multiple case variations for Id field
                            record_id = record.get("Id") or record.get("id") or record.get("ID")
                            if record_id and record_id in attachment_metadata:
                                attachments = attachment_metadata[record_id]
                                # Add "Attachments" column with comma-separated file names
                                file_names = [att.get("title", "Untitled") for att in attachments]
                                record["Attachments"] = ", ".join(file_names) if file_names else ""
                                # Add full metadata (hidden, for migration)
                                record["_attachments"] = attachments
                            else:
                                record["Attachments"] = ""  # No attachments
                                record["_attachments"] = []
                        
                        attachment_count = sum(len(attachments) for attachments in attachment_metadata.values())
                        print(f"✅ [ATTACHMENT EXTRACTION] Found {attachment_count} attachments across {len(attachment_metadata)} records")
                    except Exception as e:
                        print(f"⚠️ [ATTACHMENT EXTRACTION] Error extracting attachments: {e}")
                        import traceback
                        traceback.print_exc()
                        # Continue without attachments - add empty columns
                        for record in cleaned_records:
                            record["Attachments"] = ""
                            record["_attachments"] = []
                else:
                    print(f"⚠️ [ATTACHMENT EXTRACTION] No record IDs found, skipping attachment extraction")
                    # Don't add empty columns if no record IDs - attachments weren't requested anyway

            # Credit-based extraction limit (how many records user can afford); import here to avoid circular import
            from services.architect_agent import get_user_extraction_limit
            extraction_limit = await get_user_extraction_limit(user_id)
            can_extract_count = extraction_limit.get("can_extract", 0) or 0
            message = "Insufficient credits for extraction. Add credits or upgrade your plan." if can_extract_count <= 0 else None

            # Only return empty data if truly 0 records can be extracted (credit-based)
            # If can_extract_count > 0, extract up to that many and show upgrade if capped
            if can_extract_count <= 0:
                plan_info_with_flag = extraction_limit.copy()
                plan_info_with_flag["is_plan_limit_reached"] = True

                # Update job status (Redis-backed)
                await extraction_jobs.update(job_id, {
                    "status": "completed",
                    "progress": 100,
                    "result": {
                        "status": "plan_limit_reached",
                        "message": message or "Insufficient credits for extraction",
                        "data": [],
                        "plan_info": plan_info_with_flag,
                        "show_upgrade_option": True,
                        "total_records": actual_record_count,
                        "object_api_name": object_result.get("object_api_name"),
                        "object_label": object_result.get("object_label"),
                        "soql": soql_payload.get("soql"),
                        "user_prompt": extraction_prompt,
                        "filter_summary": soql_payload.get("filter_summary"),
                        "extracted_schema_type": "salesforce",
                    },
                })
                
                # Save extraction result (even though no data was extracted due to credit limit)
                try:
                    from services.extraction_results_service import ExtractionResultsService
                    
                    # Use limited task to prevent overwhelming the system
                    from utils.task_limiter import create_limited_task
                    await create_limited_task(
                        ExtractionResultsService.save_extraction_result(
                            job_id=job_id,
                            user_id=user_id,
                            extracted_data=[],
                            source_type="salesforce",
                            extraction_goals=extraction_prompt,
                            document_type=None,
                            document_description=None,
                            source_name=org_info.get("connection_name"),
                            source_identifier=org_info.get("org_id"),
                            parent_job_id=parent_job_id,
                            conversation_id=conversation_id,
                            has_cleanup_rules=False,
                            has_transformation_rules=False,
                            status="plan_limit_reached"
                        )
                    )
                except Exception as save_error:
                    print(f"⚠️ Failed to save Salesforce extraction result (credit limit): {save_error}")
                
                return

            # Extract the allowed number of records (even if less than requested)
            allowed_records = can_extract_count
            limitation_will_be_applied = allowed_records < actual_record_count
            if limitation_will_be_applied:
                cleaned_records = cleaned_records[:allowed_records]

            await extraction_jobs.update(job_id, {
                "progress": 80,
                "message": "Normalizing Salesforce data..."
            })

            # For cleanup intent, get quality report rules from job metadata
            # They will be merged with prompt-generated rules after extraction
            job_data = await extraction_jobs.get(job_id, {})
            is_cleanup_flow = job_data.get("intent") == "cleanup"
            quality_report_rules = job_data.get("cleanup_extraction_rules") if is_cleanup_flow else None
            
            if job_data.get("cancelled"):
                print(f"🛑 Salesforce extraction job {job_id} cancelled by user")
                await extraction_jobs.update(job_id, {"status": "cancelled"})
                return

            # For cleanup flow, don't pass extraction_rules - let extract_structured_data_from_salesforce generate rules from prompt
            # For normal flow or flow rerun, pass extraction_rules if available
            saved_extraction_rules_for_extraction = None if is_cleanup_flow else extraction_rules

            structured_payload = await extract_structured_data_from_salesforce(
                salesforce_data=cleaned_records,
                user_id=user_id,
                extraction_goals=extraction_prompt,
                object_label=object_result.get("object_label") or object_result.get("object_api_name"),
                destination=destination,
                destination_name=destination_name,
                saved_extraction_rules=saved_extraction_rules_for_extraction,  # None for cleanup (generate from prompt), or passed rules for flow rerun
            )
            
            # Re-add Attachments column if it was removed during processing
            if include_attachments:
                records_after = structured_payload.get("records", [])
                if records_after and "Attachments" not in records_after[0]:
                    # Re-add Attachments column if it was removed
                    attachment_metadata_map = {}
                    for record in cleaned_records:
                        record_id = record.get("Id") or record.get("id") or record.get("ID")
                        if record_id and record.get("_attachments"):
                            attachment_metadata_map[record_id] = record.get("_attachments")
                    
                    # Re-add Attachments to records_after
                    for record in records_after:
                        record_id = record.get("Id") or record.get("id") or record.get("ID")
                        if record_id and record_id in attachment_metadata_map:
                            attachments = attachment_metadata_map[record_id]
                            file_names = [att.get("title", "Untitled") for att in attachments]
                            record["Attachments"] = ", ".join(file_names) if file_names else ""
                            record["_attachments"] = attachments
                        else:
                            record["Attachments"] = ""
                            record["_attachments"] = []
            
            # For cleanup flow: Merge quality report rules with prompt-generated rules
            if is_cleanup_flow and quality_report_rules:
                extraction_rules_result = structured_payload.get("extraction_rules") or {}
                
                # Initialize if None
                if not extraction_rules_result:
                    extraction_rules_result = {}
                
                # Merge quality report rules with prompt-generated rules
                # Prompt rules take precedence for filter_rules, deduplication_rules, transformation_rules
                # Quality report data_cleanup_rules should be merged (not overwritten)
                if quality_report_rules.get("data_cleanup_rules"):
                    existing_data_cleanup = extraction_rules_result.get("data_cleanup_rules", {})
                    if isinstance(existing_data_cleanup, dict) and isinstance(quality_report_rules["data_cleanup_rules"], dict):
                        # Merge both dicts - prompt rules take precedence if there are conflicts
                        extraction_rules_result["data_cleanup_rules"] = {**quality_report_rules["data_cleanup_rules"], **existing_data_cleanup}
                    else:
                        # If not both dicts, use quality report rules as fallback
                        if not existing_data_cleanup:
                            extraction_rules_result["data_cleanup_rules"] = quality_report_rules["data_cleanup_rules"]
                
                # Use quality report rules for other rule types only if prompt didn't generate them
                if quality_report_rules.get("filter_rules") and not extraction_rules_result.get("filter_rules"):
                    extraction_rules_result["filter_rules"] = quality_report_rules["filter_rules"]
                if quality_report_rules.get("deduplication_rules") and not extraction_rules_result.get("deduplication_rules"):
                    extraction_rules_result["deduplication_rules"] = quality_report_rules["deduplication_rules"]
                if quality_report_rules.get("transformation_rules") and not extraction_rules_result.get("transformation_rules"):
                    extraction_rules_result["transformation_rules"] = quality_report_rules["transformation_rules"]
                
                # Update structured_payload with merged rules
                structured_payload["extraction_rules"] = extraction_rules_result

            limitation_applied = structured_payload.get("limit_reached", False) or limitation_will_be_applied
            plan_info_with_flag = structured_payload.get("plan_info")
            
            # Ensure plan_info includes limitation information if records were limited (credit-based cap)
            if limitation_will_be_applied and extraction_limit:
                if not plan_info_with_flag:
                    plan_info_with_flag = extraction_limit.copy()
                plan_info_with_flag["is_plan_limit_reached"] = False  # Not fully reached, just limited
                plan_info_with_flag["limitation_applied"] = True

            # IMPORTANT: Keep _attachments in data for migration, but frontend will filter it in table display
            # We need _attachments metadata available when user clicks "Start Migration"
            # The frontend table component will filter out fields starting with '_' when rendering columns
            records_for_frontend = structured_payload.get("records", [])

            final_result = {
                "status": "success" if limitation_applied else "success",
                "data": records_for_frontend,
                "total_records": len(structured_payload.get("records", [])),
                "object_api_name": object_result.get("object_api_name"),
                "object_label": object_result.get("object_label"),
                "connection_name": org_info.get("connection_name"),
                "table_name": object_result.get("object_api_name"),
                "soql": soql_payload.get("soql"),
                "user_prompt": extraction_prompt,
                "fields": soql_payload.get("used_fields"),
                "filter_summary": soql_payload.get("filter_summary"),
                "destination": destination,
                "destination_name": structured_payload.get("destination_name", destination_name),
                "extraction_rules": structured_payload.get("extraction_rules"),
                "mapping_context_summary": structured_payload.get("mapping_context_summary"),
                "show_upgrade_option": structured_payload.get("show_upgrade_option", False) or limitation_will_be_applied,
                "plan_info": plan_info_with_flag or extraction_limit,
                "limitation_applied": limitation_applied,
                "extracted_schema_type": "salesforce",
                "org_id": org_info.get("org_id"),
                "salesforce_token_id": (await extraction_jobs.get(job_id, {})).get("salesforce_token_id"),
                "document_type": structured_payload.get("document_type"),
                "document_description": structured_payload.get("document_description"),
                "original_record_count": structured_payload.get("original_record_count") or actual_record_count,
            }

        # Check cancellation before data quality and save (user may have clicked Stop during AI processing)
        job_data = await extraction_jobs.get(job_id, {})
        if job_data.get("cancelled"):
            print(f"🛑 Salesforce extraction job {job_id} cancelled by user (before quality analysis/save)")
            await extraction_jobs.update(job_id, {"status": "cancelled"})
            return

        # Run data quality analysis on extracted data
        data_quality_report = None
        try:
            from utils.data_quality_analyzer import analyze_data_quality
            extracted_data = final_result.get("data", [])
            if extracted_data:
                data_quality_report = await asyncio.to_thread(analyze_data_quality, extracted_data)
                print(f"📊 Data quality analysis completed: Score {data_quality_report.get('summary', {}).get('data_quality_score', 0)}/100")
        except Exception as quality_error:
            print(f"⚠️ Error running data quality analysis: {quality_error}")
            # Continue without quality report if analysis fails

        # Check cancellation again before persisting (user may have clicked Stop during quality analysis)
        job_data = await extraction_jobs.get(job_id, {})
        if job_data.get("cancelled"):
            print(f"🛑 Salesforce extraction job {job_id} cancelled by user (before save)")
            await extraction_jobs.update(job_id, {"status": "cancelled"})
            return

        # Log extraction_completed event FIRST with intent_state
        # This must happen BEFORE updating job status so extraction_message_id is included in the result
        if conversation_id:
            try:
                from services.chat_service import ChatService
                from handlers.classification_handler import create_intent_state
                
                # Determine intent (extract or migrate based on destination)
                active_intent = "migrate" if destination and destination != "unknown" else "extract"
                source_name = org_info.get("connection_name")
                destination_name_final = structured_payload.get("destination_name", destination_name)
                
                # Update intent_state - mark as completed but still active (awaiting mapping/migration)
                intent_state = create_intent_state(
                    active_intent=active_intent,
                    active_source="salesforce",
                    active_destination=destination or "unknown",
                    source_name=source_name,
                    destination_name=destination_name_final,
                    job_id=job_id,
                    state="extraction_completed"
                )
                
                # Add object and field information to intent_state for context
                if object_result:
                    intent_state["object_api_name"] = object_result.get("object_api_name")
                    intent_state["object_label"] = object_result.get("object_label")
                    intent_state["selected_fields"] = object_result.get("selected_fields", [])

                from utils.document_category_match import enrich_extraction_category_fields

                sample_data = (
                    (final_result.get("data") or [None])[0]
                    if isinstance(final_result.get("data"), list)
                    else {}
                )
                category_meta = await enrich_extraction_category_fields(
                    user_id,
                    destination,
                    destination_name_final,
                    final_result.get("document_type"),
                    final_result.get("document_description"),
                    sample_data if isinstance(sample_data, dict) else {},
                )
                if category_meta.get("canonical_document_type"):
                    final_result["document_type"] = category_meta["canonical_document_type"]
                if category_meta.get("canonical_document_description"):
                    final_result["document_description"] = category_meta["canonical_document_description"]

                ext_meta = _sf_persisted_extraction_metadata(
                    migration_intent=migration_intent,
                    object_result=object_result,
                    destination=destination,
                    destination_name=destination_name_final,
                    org_info=org_info,
                    soql=soql_payload.get("soql"),
                    extraction_rules=final_result.get("extraction_rules"),
                )

                extraction_completed_message = await ChatService.log_data_event(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    event_type="extraction_completed",
                    event_data={
                        "job_id": job_id,
                        "source_type": "salesforce",
                        "source_name": source_name,
                        "record_count": final_result.get("total_records", 0),
                        "status": "success",
                        "destination": destination,
                        "destination_name": destination_name_final,
                        "document_type": final_result.get("document_type"),
                        "document_description": final_result.get("document_description"),
                        "extraction_rules": final_result.get("extraction_rules"),
                        "mapping_context_summary": final_result.get("mapping_context_summary"),
                        "intent_state": intent_state,
                        "data_quality_report": data_quality_report,
                        "soql": soql_payload.get("soql"),
                        **category_meta,
                        **ext_meta,
                    }
                )
                # Store message_id and intent_state in final_result BEFORE updating job status
                # This ensures extraction_message_id is included when job status is saved
                if extraction_completed_message and extraction_completed_message.get("id"):
                    final_result["extraction_message_id"] = extraction_completed_message["id"]
                    final_result["intent_state"] = intent_state
                    final_result["data_quality_report"] = data_quality_report
            except Exception as e:
                print(f"⚠️ Failed to log extraction_completed event: {e}")
        
        # Don't store full data in Redis when over threshold; frontend fetches from API
        from utils.redis_service import REDIS_SLIM_RESULT_THRESHOLD
        total_records_val = final_result.get("total_records", 0)
        if total_records_val > REDIS_SLIM_RESULT_THRESHOLD:
            final_result["data"] = None
            final_result["record_count"] = total_records_val

        # Save extraction to DB before marking job completed so downstream migration sees full rows
        try:
            from services.extraction_results_service import ExtractionResultsService

            # IMPORTANT: Save ORIGINAL data (with _attachments) to database for migration
            # The filtered data in final_result["data"] is only for frontend display
            # We need _attachments metadata in the database so migration can access it
            extracted_data = structured_payload.get("records", [])  # Original data with _attachments

            has_cleanup_rules = False
            has_transformation_rules = False
            if extracted_data and isinstance(extracted_data, list) and len(extracted_data) > 0:
                first_record = extracted_data[0]
                if isinstance(first_record, dict):
                    has_cleanup_rules = "data_cleanup_rules" in first_record
                    embedded_rules = extraction_rules_from_ai_record(first_record)
                    has_transformation_rules = extraction_rules_imply_non_identity_processing(
                        embedded_rules
                    )

            extraction_meta = _sf_persisted_extraction_metadata(
                migration_intent=migration_intent,
                object_result=object_result,
                destination=destination,
                destination_name=structured_payload.get("destination_name", destination_name),
                org_info=org_info,
                soql=soql_payload.get("soql"),
                extraction_rules=structured_payload.get("extraction_rules"),
            )

            await ExtractionResultsService.save_extraction_result(
                job_id=job_id,
                user_id=user_id,
                extracted_data=extracted_data,
                source_type="salesforce",
                extraction_goals=extraction_prompt,
                document_type=final_result.get("document_type"),
                document_description=final_result.get("document_description"),
                source_name=org_info.get("connection_name"),
                source_identifier=org_info.get("org_id"),
                parent_job_id=parent_job_id,
                conversation_id=conversation_id,
                has_cleanup_rules=has_cleanup_rules,
                has_transformation_rules=has_transformation_rules,
                status="success",
                extraction_metadata=extraction_meta,
            )
        except Exception as save_error:
            print(f"⚠️ Failed to save Salesforce extraction result: {save_error}")
            # Don't fail extraction if saving fails

        # Update job with final result (Redis-backed) - now includes extraction_message_id in result
        await extraction_jobs.update(job_id, {
            "status": "completed",
            "progress": 100,
            "result": final_result,
            "destination_name": structured_payload.get("destination_name", destination_name),
        })

        # Charge extraction fee (idempotent per job_id)
        # Extraction is free; no credit deduction

    except Exception as exc:
        print(f"❌ Salesforce extraction error for job {job_id}: {exc}")
        import traceback
        traceback.print_exc()
        # Get AI-generated explanation (non-blocking, with timeout)
        explanation_result = {"explanation": None, "solution": None}
        try:
            from services.error_explanation_service import explain_error
            object_name = object_result.get("object_api_name") if object_result else None
            context = {"object_name": object_name, "operation": "extraction", "user_query": extraction_prompt}
            # Include available fields for INVALID_FIELD / No such column errors
            exc_str = str(exc).lower()
            if "invalid" in exc_str or "no such column" in exc_str or "does not exist" in exc_str:
                field_meta = (object_result or {}).get("field_metadata") or []
                available = sorted({f.get("api_name") for f in field_meta if f.get("api_name")} | {"Id"})
                context["available_fields"] = available
            explanation_result = await explain_error(
                raw_error=str(exc),
                source="salesforce",
                context=context,
            )
        except Exception as explain_err:
            print(f"⚠️ Error explanation failed: {explain_err}")
        # Update job with error status (Redis-backed)
        await extraction_jobs.update(job_id, {
            "status": "failed",
            "progress": 100,
            "error": str(exc),
            "error_explanation": explanation_result.get("explanation"),
            "error_solution": explanation_result.get("solution"),
        })


async def determine_salesforce_object_and_fields(
    prompt: str,
    user_id: str,
    conversation_history: Optional[list] = None,
    org_id: Optional[str] = None,
    *,
    object_hint: Optional[str] = None,
    related_entity_keys: Optional[list] = None,
    extract_field_scope: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Two-stage approach: First select relevant object (lightweight), then determine fields for that object
    This scales to any number of objects (200-600+) while staying under token limits
    Similar to SQL handler's two-step approach
    
    Returns a dict with keys:
      - needs_clarification (bool)
      - clarification_message (optional str)
      - object_api_name
      - object_label
      - selected_fields (List[str])
      - filters (optional str)
      - field_metadata (List[Dict[str, Any]])
      - org_id (str | None)
    """
    try:
        # Validate prompt — plan contract / object hint can supply context without user text
        hint = (object_hint or "").strip()
        scope_fields = extract_field_scope or {}
        has_contract_fields = bool(
            scope_fields.get("fields")
            or scope_fields.get("columns")
            or scope_fields.get("field_hints")
        )
        if not prompt or not prompt.strip():
            if hint and hint.lower() not in ("unknown",):
                prompt = f"Extract {hint}"
            elif has_contract_fields:
                prompt = "Extract with confirmed plan fields"
            else:
                return {
                    "needs_clarification": True,
                    "clarification_message": "Please provide a request. What Salesforce data would you like to extract?",
                }
        
        print(f"🎯 Starting two-stage Salesforce extraction for prompt: '{prompt}'")
        
        from db.connection import database

        params: Dict[str, Any] = {"user_id": str(user_id)}
        org_filter = ""
        if org_id:
            params["org_id"] = org_id
            org_filter = "AND org_id = :org_id"

        # Get all objects (lightweight - no fields yet)
        objects = await database.fetch_all(
            f"""
            SELECT object_api_name, label, category, category_description, org_id
            FROM salesforce_objects
            WHERE user_id = :user_id
            {org_filter}
            ORDER BY label
            """,
            params,
        )

        if not objects:
            message = (
                "I could not find any Salesforce metadata for this org. Please reconnect Salesforce and sync metadata."
                if org_id
                else "I could not find any Salesforce metadata for your org. Please reconnect Salesforce and sync metadata."
            )
            return {
                "needs_clarification": True,
                "clarification_message": message,
            }

        selected_object = None
        hint = (object_hint or "").strip()
        if hint and hint.lower() not in ("unknown",):
            from utils.salesforce_mapping_target import lookup_salesforce_object_in_org

            meta = await lookup_salesforce_object_in_org(user_id, org_id, hint)
            if meta:
                selected_object = dict(meta)
                if org_id:
                    selected_object["org_id"] = org_id
                log_sf_extract(
                    "stage1_object_pinned",
                    object_api_name=selected_object.get("object_api_name"),
                    object_label=selected_object.get("label"),
                    org_id=org_id,
                    object_hint=hint,
                )

        # Stage 1: Select the most relevant object (lightweight - scales to any number of objects)
        if not selected_object:
            print(f"📋 Stage 1: Selecting relevant object from {len(objects)} available objects")
            selected_object = await select_relevant_salesforce_object(
                prompt, objects, conversation_history, org_id, selection_role="source"
            )
        
        if not selected_object:
            log_sf_extract(
                "stage1_no_object",
                level="warning",
                always=True,
                org_id=org_id,
                object_count=len(objects),
            )
            return {
                "needs_clarification": True,
                "clarification_message": "I couldn't determine which Salesforce object to use. Could you please specify the object name? (e.g., Account, Contact, Lead)"
            }
        
        object_name = selected_object.get('object_api_name', 'Unknown')
        print(f"✅ Stage 1 complete: Selected object {object_name}")
        log_sf_extract(
            "stage1_object_selected",
            object_api_name=object_name,
            object_label=selected_object.get("label"),
            org_id=org_id,
        )
        
        # Stage 2: Determine fields for the selected object
        print(f"🔧 Stage 2: Determining fields for {object_name}")
        result = await determine_fields_for_salesforce_object(
            prompt, 
            selected_object, 
            user_id, 
            conversation_history, 
            org_id,
            related_entity_keys=related_entity_keys,
            extract_field_scope=extract_field_scope,
        )
        
        print(f"✅ Stage 2 complete: Selected {len(result.get('selected_fields', []))} fields for {object_name}")
        log_sf_extract(
            "stage2_fields_selected",
            object_api_name=object_name,
            selected_field_count=len(result.get("selected_fields") or []),
            field_metadata_count=len(result.get("field_metadata") or []),
            needs_clarification=bool(result.get("needs_clarification")),
        )
        
        return result
        
    except Exception as exc:
        import traceback

        print(f"❌ Error determining Salesforce object/fields: {exc}")
        traceback.print_exc()
        log_sf_extract(
            "object_fields_exception",
            level="error",
            always=True,
            org_id=org_id,
            error_type=type(exc).__name__,
            error=str(exc)[:500],
        )
        return {
            "needs_clarification": True,
            "clarification_message": "I ran into an issue identifying the Salesforce object. Could you confirm which object and fields you'd like?",
            }


async def select_relevant_salesforce_object(
    prompt: str, 
    objects: list, 
    conversation_history: Optional[list] = None,
    org_id: Optional[str] = None,
    *,
    selection_role: str = "source",
) -> Optional[Dict[str, Any]]:
    """
    Stage 1: Select the most relevant Salesforce object based on user prompt.
    selection_role: "source" (extract FROM) or "destination" (map/migrate TO).
    Lightweight - only sends object names, labels, categories (no fields).
    Returns a dict with object_api_name, label, category, org_id
    """
    try:
        
        # Prepare lightweight object information (names + labels + categories only)
        object_summaries = []
        for obj in objects:
            # Safe dict conversion with type checking
            if isinstance(obj, dict):
                obj_dict = obj
            elif hasattr(obj, 'keys') and callable(getattr(obj, 'keys', None)):
                try:
                    obj_dict = dict(obj)
                except (TypeError, ValueError):
                    continue  # Skip invalid objects
            else:
                continue  # Skip non-dict objects
            
            # Use .get() for safe access
            object_api_name = obj_dict.get("object_api_name")
            if not object_api_name:  # Skip objects without required field
                continue
                
            object_summaries.append({
                "object_api_name": object_api_name,
                "label": obj_dict.get("label", object_api_name),
                "category": obj_dict.get("category", "Unknown"),
                "category_description": obj_dict.get("category_description", "No description")
            })
        
        # Build conversation context if available (last 4 messages, similar to SQL handler)
        conversation_context = ""
        if conversation_history:
            # Get last 4 messages (slicing works even if length is <= 4)
            recent_messages = conversation_history[-4:]
            # Format conversation history
            context_lines = []
            for msg in recent_messages:
                # Validate message structure
                if not msg or not isinstance(msg, dict):
                    continue
                    
                msg_type = msg.get('type', msg.get('role', 'user'))
                msg_text = msg.get('text', msg.get('content', ''))
                if msg_text:
                    role_label = "User" if msg_type in ['user', 'User'] else "Assistant"
                    # Truncate very long messages
                    if len(msg_text) > 500:
                        msg_text = msg_text[:500] + "..."
                    context_lines.append(f"{role_label}: {msg_text}")
            
            if context_lines:
                conversation_context = f"""
        
**Conversation Context (for reference):**
{chr(10).join(context_lines)}

⚠️ NOTE: The current user message may be a short clarification. Use the conversation context to understand the full user intent, especially what object/fields they want to extract.
"""

        from services.llm import tasks as llm_tasks

        is_destination = (selection_role or "source").strip().lower() == "destination"

        if is_destination:
            role_block = """
You are selecting the Salesforce **destination** object for field mapping and migration.

🚨 **CRITICAL: SOURCE vs DESTINATION OBJECT**
Choose the object the user wants data written **TO**, not where they extract FROM.

Examples:
- "extract from Service Appointments sheet and migrate to Service Appointment object in SF 1"
  → Select ServiceAppointment (destination), NOT the spreadsheet name.
- "deploy account records to contact object" → Select Contact (destination), NOT Account.
- "migrate leads to opportunity" → Select Opportunity (destination), NOT Lead.

Do NOT select Google Sheet / SQL table / source workbook names as Salesforce objects.
"""
            task_intro = (
                "You are Archian AI. Based on the user's request, select the Salesforce object "
                "they want to MAP or MIGRATE data **TO**."
            )
            llm_task = llm_tasks.SALESFORCE_MAPPING
        else:
            role_block = """
🚨 **CRITICAL: SOURCE vs DESTINATION OBJECT**
When the user mentions both a source object (where data comes FROM) and a destination object (where data goes TO), you must extract FROM the SOURCE object, NOT the destination object.

Examples:
- "deploy first 10 account records to contact object" → Extract FROM Account (source), migrate TO Contact (destination)
- "migrate account data to contact" → Extract FROM Account (source), migrate TO Contact (destination)
- "extract leads and push to opportunity" → Extract FROM Lead (source), migrate TO Opportunity (destination)

If the user says "account records" or "account data", that means Account is the SOURCE object to extract FROM.
If the user says "to contact object" or "to contact", that means Contact is the DESTINATION object (where data goes TO).

You are determining which object to EXTRACT FROM (the source), not the destination.

🚨 **CRITICAL: BASE OBJECT vs RELATED OBJECT FIELDS**
When the user mentions a base object AND related object fields in the SAME extraction request, the BASE OBJECT is what you extract FROM.

Pattern: "extract [BASE OBJECT] with [RELATED OBJECT] [field]"
- The object mentioned FIRST or as the PRIMARY object is the BASE OBJECT (extract FROM this)
- The object mentioned in the context of a FIELD (e.g., "account name", "account industry") is a RELATED OBJECT (not the base)

Examples:
- "extract contact records with account name" → Extract FROM Contact (base), Account.Name is a related field
- "get contacts with account industry" → Extract FROM Contact (base), Account.Industry is a related field
- "extract leads with owner name" → Extract FROM Lead (base), Owner.Name is a related field
- "contact object with account name" → Extract FROM Contact (base), Account.Name is a related field

**KEY RULE**: If the user says "[OBJECT] with [OTHER_OBJECT] [field]", the FIRST object mentioned is the BASE object to extract FROM.
The second object is a RELATED object whose fields can be accessed via relationship queries (e.g., Account.Name).

**DO NOT** select the related object as the base object just because it's mentioned.
"""
            task_intro = (
                "You are Archian AI, a Salesforce data extraction planner. Based on the user's "
                "current request, select the most relevant Salesforce object for data extraction."
            )
            llm_task = llm_tasks.SALESFORCE_EXTRACTION

        selection_prompt = f"""
{task_intro}

Available Objects: {[obj['object_api_name'] for obj in object_summaries]}
Object Details: {json.dumps(object_summaries, indent=2)}

**Current User Message:**
"{prompt}"
{conversation_context}

{role_block}

**IMPORTANT: Using Conversation History**
The conversation history above shows the full context of the user's interaction. Use it to understand intent when the current message is short.

Your task is to analyze the user's intent and select 1 object from the list above.

Return a JSON object with this structure:
{{
  "object_api_name": "Exact object_api_name from the list above",
  "needs_clarification": false,
  "clarification_message": ""
}}

Rules:
- object_api_name must be from the provided list.
- If the request is ambiguous or no object matches, set needs_clarification to true and provide a concise clarification message.
"""
        if not is_destination:
            selection_prompt += """
- **PRIORITY 1: BASE OBJECT DETECTION**: If user says "[OBJECT] with [OTHER_OBJECT] [field]", select the FIRST object as the base (e.g., "contact with account name" → Contact).
- **PRIORITY 2: The current user request is ALWAYS the highest priority. If it explicitly mentions an object name, use that object.**
- **PRIORITY 3: Use conversation history to understand context**: If the current request is a field modification (e.g., "ignore owner id"), use the object from the previous extraction in the conversation history.
- **IMPORTANT: Extract FROM the source/base object mentioned in the prompt, not the destination or related object.**
- **CRITICAL: When user mentions both objects, the PRIMARY/BASE object is the one to extract FROM, not the related object mentioned for fields.**
"""
        else:
            selection_prompt += """
- Prefer the object named as the migration/mapping **destination** (e.g. "migrate to Service Appointment object").
- Do NOT select source sheet/tab/table names as the Salesforce object.
"""
        from services.architect_agent import async_openai_call

        response = await async_openai_call(
            None,
            task=llm_task,
            messages=[{"role": "user", "content": selection_prompt}],
            max_tokens=500,
            temperature=0.2 if not is_destination else 0.1,
        )

        # Validate OpenAI response structure
        if not response.choices or not response.choices[0].message.content:
            print("⚠️ Empty or invalid response from OpenAI")
            return None
        
        ai_reply = response.choices[0].message.content.strip()

        # Parse JSON response - handle multiple formats
        if ai_reply.startswith('```json'):
            ai_reply = ai_reply[7:]
        if ai_reply.endswith('```'):
            ai_reply = ai_reply[:-3]
        
        # Extract JSON from response if it's wrapped in text
        if '{' in ai_reply and '}' in ai_reply:
            start = ai_reply.find('{')
            end = ai_reply.rfind('}') + 1
            ai_reply = ai_reply[start:end]
        else:
            # No JSON found in response
            print(f"⚠️ No JSON found in OpenAI response: {ai_reply[:100]}")
            return None
        
        # Use centralized JSON parsing function (handles markdown code blocks, JSON fixing, etc.)
        from utils.openai_helpers import parse_openai_json_response
        try:
            selection = parse_openai_json_response(ai_reply)
        except (ValueError, json.JSONDecodeError) as e:
            print(f"⚠️ Failed to parse JSON response: {e}")
            print(f"Response was: {ai_reply[:200]}")
            return None

        if selection.get("needs_clarification"):
            return None
        
        object_api_name = selection.get("object_api_name")
        if not object_api_name:
            return None

        if not is_destination:
            # Validation: Check if selected object makes sense given the prompt (source extraction only)
            prompt_lower = prompt.lower()
            prompt_words = prompt_lower.split()
            object_mentions = []
            for word in prompt_words:
                for obj in objects:
                    obj_dict = dict(obj) if hasattr(obj, "keys") else obj
                    obj_name = obj_dict.get("object_api_name", "").lower()
                    obj_label = obj_dict.get("label", "").lower()
                    if word == obj_name or word == obj_label or word in obj_name or word in obj_label:
                        if obj_name not in object_mentions:
                            object_mentions.append(obj_name)

            if len(object_mentions) > 1:
                first_mentioned = object_mentions[0]
                selected_lower = object_api_name.lower()
                if "with" in prompt_words or "and" in prompt_words:
                    with_index = -1
                    for i, word in enumerate(prompt_words):
                        if word in ["with", "and"]:
                            with_index = i
                            break
                    if with_index > 0:
                        before_with = " ".join(prompt_words[:with_index])
                        after_with = " ".join(prompt_words[with_index + 1 :])
                        if first_mentioned in before_with and selected_lower != first_mentioned:
                            print(
                                f"⚠️ [OBJECT SELECTION] Warning: User mentioned '{first_mentioned}' "
                                f"first but AI selected '{selected_lower}'"
                            )
                            print(f"   📝 Prompt pattern: '{before_with}' WITH '{after_with}'")

        # Find and return the full object info
        for obj in objects:
            # Safe dict conversion with type checking
            if isinstance(obj, dict):
                obj_dict = obj
            elif hasattr(obj, 'keys') and callable(getattr(obj, 'keys', None)):
                try:
                    obj_dict = dict(obj)
                except (TypeError, ValueError):
                    continue
            else:
                continue
            
            # Use .get() for safe access
            if obj_dict.get("object_api_name") == object_api_name:
                return obj_dict
        
        return None
        
    except Exception as e:
        print(f"Error selecting Salesforce object: {e}")
        import traceback
        traceback.print_exc()
        return None


def _field_selection_fallback_result(
    prompt: str,
    field_list: List[Dict[str, Any]],
    selected_object: Dict[str, Any],
    fallback_reason: str,
) -> Dict[str, Any]:
    """Return fallback field selection when AI response is empty or invalid."""
    prompt_lower = prompt.lower()
    want_all_fields = any(
        phrase in prompt_lower
        for phrase in ["all field", "all fields", "every field", "all available field", "extract all field", "migrate all field"]
    )
    if want_all_fields and field_list:
        fallback_fields = [f.get("api_name") for f in field_list if f.get("api_name")]
        print(f"⚠️ Fallback ({fallback_reason}): using all {len(fallback_fields)} fields from org (user asked for all fields)")
    else:
        required_fields = [field.get("api_name") for field in field_list if field.get("is_required")]
        fallback_fields = [f for f in required_fields if f] or [field.get("api_name") for field in field_list[:5] if field.get("api_name")]
        fallback_fields = fallback_fields[:15] if fallback_fields else []
    user_requested_id = any(keyword in prompt_lower for keyword in ["id", "record id", "salesforce id", "object id"])
    if "Id" in [f.get("api_name") for f in field_list if f.get("api_name")] and "Id" not in fallback_fields and user_requested_id:
        fallback_fields.insert(0, "Id")
    object_api_name = selected_object.get("object_api_name")
    return {
        "needs_clarification": False,
        "object_api_name": object_api_name,
        "object_label": selected_object.get("label") or object_api_name,
        "category": selected_object.get("category"),
        "selected_fields": fallback_fields,
        "filters": None,
        "field_metadata": field_list,
        "org_id": selected_object.get("org_id"),
        "include_attachments": False,
        "attachment_confidence": "low",
    }


async def determine_fields_for_salesforce_object(
    prompt: str,
    selected_object: Dict[str, Any],
    user_id: str,
    conversation_history: Optional[list] = None,
    org_id: Optional[str] = None,
    *,
    related_entity_keys: Optional[list] = None,
    extract_field_scope: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Stage 2: Determine fields for the selected Salesforce object
    Only fetches fields for the selected object (efficient)
    """
    try:
        from db.connection import database
        import re
        
        object_api_name = selected_object.get("object_api_name")
        if not object_api_name:
            return {
                "needs_clarification": True,
                "clarification_message": "Selected object is missing required information. Please try again.",
            }
        object_label = selected_object.get("label") or object_api_name

        from services.planning.extract_field_scope import (
            build_extract_field_scope,
            cross_system_field_prompt_block,
            filter_field_metadata_for_scope,
            apply_cross_system_field_guardrail,
        )

        field_scope = extract_field_scope if isinstance(extract_field_scope, dict) else {}
        if not field_scope.get("cross_system") and related_entity_keys:
            field_scope = build_extract_field_scope(
                field_scope,
                include_related_data=bool(related_entity_keys),
                parent_entity_keys=related_entity_keys,
            )
        
        # Fetch fields only for the selected object (including lookup metadata)
        # Fetch fields including is_defaulted_on_create
        # Increase limit to 200 to ensure user-requested fields are included
        fields = await database.fetch_all(
            f"""
            SELECT field_api_name, field_label, data_type, is_required, 
                   is_lookup, lookup_object_api_name, is_defaulted_on_create
            FROM salesforce_object_fields
            WHERE object_api_name = :object_api_name
              AND user_id = :user_id
              {"AND org_id = :org_id" if org_id else ""}
            ORDER BY is_required DESC, field_api_name
            LIMIT 200
            """,
            {
                "object_api_name": object_api_name,
                "user_id": str(user_id),
                **({"org_id": org_id} if org_id else {}),
            },
        )

        if not fields:
            return {
                "needs_clarification": True,
                "clarification_message": f"I could not find any fields for {object_api_name}. Please sync metadata for this object.",
            }

        # Build field list and identify lookup relationships
        field_list = []
        lookup_relationships = {}  # Map lookup_object -> relationship_name
        
        for field in fields:
            # Safe dict conversion with type checking
            if isinstance(field, dict):
                field_dict = field
            elif hasattr(field, 'keys') and callable(getattr(field, 'keys', None)):
                try:
                    field_dict = dict(field)
                except (TypeError, ValueError):
                    continue  # Skip invalid fields
            else:
                continue  # Skip non-dict fields
            
            # Use .get() for safe access with defaults
            field_api_name = field_dict.get("field_api_name")
            if not field_api_name:  # Skip fields without required name
                continue
                
            # Track lookup relationships
            if field_dict.get("is_lookup") and field_dict.get("lookup_object_api_name"):
                lookup_object = field_dict.get("lookup_object_api_name")
                # Build relationship name:
                # - Standard: AccountId -> Account
                # - Custom: Project__c -> Project__r
                if field_api_name.endswith("Id"):
                    relationship_name = field_api_name[:-2]  # Remove "Id"
                elif field_api_name.endswith("__c"):
                    relationship_name = field_api_name[:-3] + "__r"  # Replace __c with __r
                else:
                    relationship_name = field_api_name  # Fallback
                
                if lookup_object not in lookup_relationships:
                    lookup_relationships[lookup_object] = []
                lookup_relationships[lookup_object].append({
                    "field_api_name": field_api_name,
                    "relationship_name": relationship_name
                })
                
            # Skip fields that are defaulted on create unless user explicitly requests them
            is_defaulted = field_dict.get("is_defaulted_on_create", False)
            
            field_list.append(
                {
                    "api_name": field_api_name,
                    "label": field_dict.get("field_label", ""),
                    "data_type": field_dict.get("data_type", "String"),
                    "is_required": field_dict.get("is_required", False) and not is_defaulted,
                    "is_defaulted_on_create": is_defaulted,
                    "is_lookup": field_dict.get("is_lookup", False),
                    "lookup_object": field_dict.get("lookup_object_api_name"),
                }
            )
        
        # Related lookup fields: only when extract scope explicitly includes related data.
        prompt_lower = prompt.lower()
        mentioned_lookup_objects: List[str] = []
        include_related_extract = bool(field_scope.get("include_related_data"))
        dest_entity_key = (field_scope.get("destination_entity_key") or "").strip().lower()
        pinned_related = []
        if include_related_extract:
            pinned_related = [
                str(k).strip()
                for k in (related_entity_keys or [])
                if str(k).strip()
            ]
            for lookup_obj in lookup_relationships.keys():
                if lookup_obj in pinned_related:
                    mentioned_lookup_objects.append(lookup_obj)
                elif dest_entity_key and lookup_obj.lower() == dest_entity_key:
                    continue
                elif lookup_obj in pinned_related:
                    mentioned_lookup_objects.append(lookup_obj)
            for key in pinned_related:
                if key not in mentioned_lookup_objects and key in lookup_relationships:
                    mentioned_lookup_objects.append(key)

            related_field_hints = field_scope.get("related_field_hints") or []
            for lookup_obj in lookup_relationships.keys():
                if lookup_obj in mentioned_lookup_objects:
                    continue
                if dest_entity_key and lookup_obj.lower() == dest_entity_key:
                    continue
                lookup_obj_lower = lookup_obj.lower()
                lookup_obj_clean = lookup_obj_lower.replace("__c", "").replace("_", " ")
                if related_field_hints and (
                    lookup_obj_lower in prompt_lower or lookup_obj_clean in prompt_lower
                ):
                    mentioned_lookup_objects.append(lookup_obj)
                    continue
                relationship_names = [r["relationship_name"] for r in lookup_relationships[lookup_obj]]
                for rel_name in relationship_names:
                    rel_name_lower = rel_name.lower().replace("__r", "").replace("_", " ")
                    if not rel_name_lower:
                        continue
                    for hint in related_field_hints:
                        if rel_name_lower in prompt_lower and str(hint).lower() in prompt_lower:
                            mentioned_lookup_objects.append(lookup_obj)
                            break
                    if lookup_obj in mentioned_lookup_objects:
                        break
        
        # Fetch fields from mentioned lookup objects
        relationship_fields = {}  # Map relationship_name -> [fields from lookup object]
        if mentioned_lookup_objects:
            for lookup_obj in mentioned_lookup_objects:
                relationships = lookup_relationships.get(lookup_obj, [])

                # Determine which related fields the user actually asked for, per lookup object.
                # Example prompts:
                # - "contact with account name and account website" -> ["name", "website"]
                # - "contacts with owner name" -> ["name"]
                prompt_lower = prompt.lower()
                lookup_tokens: List[str] = []
                for hint in field_scope.get("related_field_hints") or []:
                    lookup_tokens.append(str(hint).lower().strip())

                lookup_obj_patterns = [
                    rf"\b{re.escape(lookup_obj.lower())}\b\s+([a-z0-9_]+)",
                    rf"\b{re.escape(lookup_obj.lower().replace('__c',''))}\b\s+([a-z0-9_]+)",
                ]
                for pat in lookup_obj_patterns:
                    for m in re.finditer(pat, prompt_lower):
                        tok = (m.group(1) or "").strip()
                        if tok and tok not in ["and", "with", "from", "to", "for", "of", "on", "in"]:
                            lookup_tokens.append(tok)

                # Additional heuristic for phrases like "account website" / "account name"
                # when the lookup object is mentioned indirectly via relationship name (e.g., Account).
                for rel in relationships:
                    rel_name = (rel.get("relationship_name") or "").lower()
                    rel_clean = rel_name.replace("__r", "").replace("_", " ").strip()
                    if not rel_clean:
                        continue
                    for m in re.finditer(rf"\b{re.escape(rel_clean)}\b\s+([a-z0-9_]+)", prompt_lower):
                        tok = (m.group(1) or "").strip()
                        if tok and tok not in ["and", "with", "from", "to", "for", "of", "on", "in"]:
                            lookup_tokens.append(tok)

                # Normalize + de-dup tokens
                lookup_tokens = [t for t in lookup_tokens if t]
                lookup_tokens = list(dict.fromkeys(lookup_tokens))  # preserve order

                # Fetch label for lookup object (for better UI labels like "Account Website")
                lookup_obj_label = lookup_obj
                try:
                    lookup_meta = await database.fetch_one(
                        """
                        SELECT label
                        FROM salesforce_objects
                        WHERE object_api_name = :obj
                          AND user_id = :user_id
                          {org_filter}
                        LIMIT 1
                        """.format(org_filter=("AND org_id = :org_id" if org_id else "")),
                        values={
                            "obj": lookup_obj,
                            "user_id": str(user_id),
                            **({"org_id": org_id} if org_id else {}),
                        },
                    )
                    if lookup_meta:
                        lookup_meta_dict = dict(lookup_meta) if hasattr(lookup_meta, "keys") else lookup_meta
                        lookup_obj_label = lookup_meta_dict.get("label") or lookup_obj_label
                except Exception:
                    # best-effort only; keep default label
                    pass

                # Fetch fields from lookup object
                # Fetch ALL lookup fields first (bounded by a high cap), then filter in Python
                # based on required + Name + user-requested tokens. This prevents missing fields
                # like Website due to DB-side LIMITs/ordering.
                org_filter_sql = " AND org_id = :org_id" if org_id else ""
                lookup_params = {
                    "lookup_object": lookup_obj,
                    "user_id": str(user_id),
                    **({"org_id": org_id} if org_id else {}),
                }

                lookup_fields = await database.fetch_all(
                    f"""
                    SELECT field_api_name, field_label, data_type, is_required
                    FROM salesforce_object_fields
                    WHERE object_api_name = :lookup_object
                      AND user_id = :user_id
                      {org_filter_sql}
                    ORDER BY is_required DESC, field_api_name
                    LIMIT 250
                    """,
                    lookup_params,
                )
                # Build relationship field paths
                for relationship_info in relationships:
                    relationship_name = relationship_info["relationship_name"]
                    if relationship_name not in relationship_fields:
                        relationship_fields[relationship_name] = []
                    
                    for lookup_field in lookup_fields:
                        if isinstance(lookup_field, dict):
                            lookup_field_dict = lookup_field
                        elif hasattr(lookup_field, 'keys'):
                            lookup_field_dict = dict(lookup_field)
                        else:
                            continue
                        
                        lookup_field_name = lookup_field_dict.get("field_api_name")
                        if lookup_field_name:
                            # Filter relationship fields to what user asked for.
                            # If user explicitly requested tokens (e.g., website/name), only allow those matches.
                            # Always allow required fields for the lookup object (keeps schema integrity without blowing up tokens).
                            # If no tokens found, allow only "Name" + required fields by default to keep output small/safe.
                            lf_label = (lookup_field_dict.get("field_label") or "").lower()
                            lf_api = (lookup_field_name or "").lower()
                            is_required_lookup_field = bool(lookup_field_dict.get("is_required"))
                            if lookup_tokens:
                                token_match = any(t in lf_label or t in lf_api for t in lookup_tokens)
                                if not token_match and not is_required_lookup_field:
                                    continue
                            else:
                                if lookup_field_name != "Name" and not is_required_lookup_field:
                                    continue

                            # Build relationship field path: RelationshipName.FieldName
                            relationship_field_path = f"{relationship_name}.{lookup_field_name}"
                            relationship_fields[relationship_name].append({
                                "api_name": relationship_field_path,
                                # UI-friendly label: "<Object Label> <Field Label>" (e.g., "Account Website")
                                "label": (
                                    lookup_field_dict.get("field_label", lookup_field_name)
                                    if str(lookup_field_dict.get("field_label", "")).strip().lower().startswith(str(lookup_obj_label).strip().lower())
                                    else f"{lookup_obj_label} {lookup_field_dict.get('field_label', lookup_field_name)}"
                                ),
                                "data_type": lookup_field_dict.get("data_type", "String"),
                                "is_required": lookup_field_dict.get("is_required", False),
                                "is_relationship_field": True,
                                "relationship_name": relationship_name,
                                "lookup_object": lookup_obj,
                            })
            
            # Add relationship fields to field_list
            for relationship_name, rel_fields in relationship_fields.items():
                field_list.extend(rel_fields)

        field_list = filter_field_metadata_for_scope(field_list, field_scope, prompt=prompt)

        # If user explicitly asks for record type, add RecordType.Name as a virtual field
        # Only added on demand (not by default) -- same pattern as Account.Name for lookups
        record_type_keywords = ["record type", "recordtype", "record_type"]
        prompt_lower_rt = prompt.lower()
        user_wants_record_type = any(kw in prompt_lower_rt for kw in record_type_keywords)
        if user_wants_record_type:
            # Check if the object actually has record types before adding the field (EC-3)
            rt_check = await database.fetch_one(
                f"""
                SELECT has_record_types FROM salesforce_objects
                WHERE object_api_name = :object_api_name
                  AND user_id = :user_id
                  {"AND org_id = :org_id" if org_id else ""}
                LIMIT 1
                """,
                {
                    "object_api_name": object_api_name,
                    "user_id": str(user_id),
                    **({"org_id": org_id} if org_id else {}),
                },
            )
            has_record_types = rt_check and dict(rt_check).get("has_record_types", False)
            if has_record_types:
                field_list.append({
                    "api_name": "RecordType.Name",
                    "label": "Record Type Name",
                    "data_type": "string",
                    "is_required": False,
                    "is_defaulted_on_create": False,
                    "is_lookup": False,
                    "lookup_object": None,
                })

        # Build conversation context if available (last 4 messages, similar to SQL handler)
        conversation_context = ""
        if conversation_history:
            # Get last 4 messages (slicing works even if length is <= 4)
            recent_messages = conversation_history[-4:]
            # Format conversation history
            context_lines = []
            for msg in recent_messages:
                msg_type = msg.get('type', msg.get('role', 'user'))
                msg_text = msg.get('text', msg.get('content', ''))
                if msg_text:
                    role_label = "User" if msg_type in ['user', 'User'] else "Assistant"
                    # Truncate very long messages
                    if len(msg_text) > 500:
                        msg_text = msg_text[:500] + "..."
                    context_lines.append(f"{role_label}: {msg_text}")
            
            if context_lines:
                conversation_context = f"""
        
**Conversation Context (for reference):**
{chr(10).join(context_lines)}

⚠️ NOTE: The current user message may be a short clarification. Use the conversation context to understand the full user intent, especially what fields they want to extract.
"""
        
        field_metadata_json = json.dumps(field_list, indent=2)
        cross_system_block = cross_system_field_prompt_block(field_scope)
        strict_block = ""
        if field_scope.get("field_hints_strict") and field_scope.get("field_hints"):
            strict_block = f"""
**STRICT FIELD MODE (CRITICAL):**
The user requested ONLY these extract fields: {json.dumps(field_scope.get("field_hints"))}
Select ONLY base-object fields that match this list. Do NOT add Email, Phone, Assistant fields,
or relationship fields (e.g. Account.Name) unless they appear in that list.
"""

        field_selection_prompt = f"""
You are Archian AI, a Salesforce data extraction planner. Based on the user's request, select the most relevant fields from the chosen Salesforce object.

**Selected Object:**
- Object API Name: {object_api_name}
- Object Label: {object_label}

**Available Fields:**
{field_metadata_json}

**Current User Message:**
"{prompt}"
{conversation_context}
{cross_system_block}
{strict_block}

**IMPORTANT: Using Conversation History**
The conversation history above shows the full context of the user's interaction. Use it to understand:
- **Field Modification Requests**: If the conversation shows a previous extraction and the current request is modifying fields (e.g., "ignore owner id", "remove field X", "keep only first name and email"), respect those modifications.
- **Field Requests**: If the user explicitly mentions specific fields, include those fields.
- **Context from Previous Extractions**: If the conversation shows a previous extraction with specific fields, and the current request is a modification, use the previous fields as a baseline.
- **Exclusion Requests**: If the user says "ignore", "exclude", "remove", or "don't include" specific fields, DO NOT include those fields.

**RELATIONSHIP QUERIES (NEW):**
- The available fields list may include relationship fields in the format: `RelationshipName.FieldName` (e.g., `Account.Name`, `Project__r.Status__c`)
- These allow you to extract fields from related (lookup) objects in a single query
- **When to use**: If the user requests fields from a related object (e.g., "Account Name" when extracting Contacts), use the relationship field path (e.g., `Account.Name`)
- **Relationship field format**: `RelationshipName.FieldName`
  - Standard relationships: `AccountId` field → Use `Account.Name`, `Account.Industry`, etc.
  - Custom relationships: `Project__c` field → Use `Project__r.Name`, `Project__r.Status__c`, etc.
- **Examples**:
  - User: "Extract contacts with account name" → Include `["FirstName", "LastName", "Account.Name"]`
  - User: "Get contacts and their account industry" → Include `["Id", "Name", "Account.Industry"]`
- **Benefits**: Single query gets both base object and related object data - no separate queries needed
- If a relationship field is in the available fields list, you can use it directly in selected_fields

**RECORD TYPE (IMPORTANT):**
- If `RecordType.Name` appears in the available fields list, it means the object has record types and the user asked for them
- **When to include**: Only include `RecordType.Name` if the user explicitly asks for record type (e.g., "with record type", "include record type", "extract contacts with record type")
- **How to use**: Add `RecordType.Name` to selected_fields exactly as shown -- it is a valid SOQL relationship field
- **Example**: User: "Extract contacts with record type" → Include `["FirstName", "LastName", "Email", "RecordType.Name"]`
- Do NOT include `RecordTypeId` -- use `RecordType.Name` for human-readable values

**DEFAULTED FIELDS (CRITICAL):**
- Some fields have `"is_defaulted_on_create": true` - these are automatically populated by Salesforce when records are created
- **DO NOT include defaulted fields** unless the user explicitly requests them by name
- Examples of defaulted fields: `CreatedById`, `CreatedDate`, `LastModifiedById`, `LastModifiedDate`, `SystemModstamp`, `OwnerId` (sometimes)
- **Rule**: Only include defaulted fields if the user specifically mentions them (e.g., "include created date", "add owner id")
- **Field matching**: Match user requests flexibly:
  - "knowledge base" → matches fields with "knowledge" in api_name or label (e.g., `knowledge_Base__c`, `KnowledgeArticleId`)
  - "first name" → matches `FirstName`, `first_name`, `First_Name`
  - Use case-insensitive matching and handle variations (spaces, underscores, capitalization)

**FILE ATTACHMENTS DETECTION:**
Determine if the user wants to extract file attachments along with the records.

Look for keywords/phrases like:
- "with attachments"
- "with files"
- "include attachments"
- "include files"
- "migrate attachments"
- "copy files"
- "with documents"
- "with notes" (Notes are ContentNote objects, but also check for file attachments)

Exclude if user says:
- "without attachments"
- "exclude files"
- "data only"
- "fields only"

If unclear, default to false (no attachments).

Return a JSON object with this structure:
{{
  "selected_fields": ["FieldApiName1", "FieldApiName2", "RelationshipName.FieldName"],
  "filters": "Optional short description of filters to apply (or empty string)",
  "include_attachments": true/false,
  "attachment_confidence": "high" | "medium" | "low",
  "needs_clarification": false,
  "clarification_message": ""
}}

Rules:
- selected_fields must only include api_names from the provided list (can include relationship fields like `Account.Name`).
- Include between 3 and 60 fields (more if user explicitly requests many fields but maximum limit is 100).
- **CRITICAL - Id Field**: Do NOT include "Id" field unless the user explicitly requests it (e.g., "include id", "with record id", "add salesforce id"). The Id field is a system-generated identifier and is not needed for most data extraction scenarios.
- **If user requests fields from a related object, use relationship field paths (e.g., `Account.Name`) if available in the fields list.**
- **CRITICAL - Defaulted Fields**: Fields with `"is_defaulted_on_create": true` should NOT be included unless the user explicitly requests them by name. Examples: CreatedById, CreatedDate, LastModifiedById, LastModifiedDate, SystemModstamp, OwnerId.
- **Field Name Matching**: Match user requests flexibly using case-insensitive matching:
  - "knowledge base" → matches `knowledge_Base__c`, `KnowledgeBase`, `KnowledgeArticleId`, or any field with "knowledge" in api_name or label
  - "first name" → matches `FirstName`, `first_name`, `First_Name`
  - Handle variations: spaces, underscores, capitalization differences
- Respect user-specified fields. If a requested field is unavailable, set needs_clarification to true and explain.
- If STRICT FIELD MODE is active, select only matching fields — do not add extras.
- Prefer important fields such as Name, Email, Phone, and required fields when user intent is unclear AND strict mode is off.
- If the user says "all fields" or "all data", include all available fields (up to 200), but still exclude defaulted fields and Id unless explicitly requested.
- **IMPORTANT**: If the user asks to "ignore", "exclude", "remove", or "not include" specific fields, DO NOT include those fields in selected_fields.
- **CRITICAL**: If conversation context shows the user already specified what fields they want (e.g., "first name, last name, email, knowledge base"), use that information even if the current message is short. Do NOT ask for clarification if the information is already in the conversation context.
- If the request is ambiguous about fields, set needs_clarification to true and provide a concise clarification message.
"""
        from services.architect_agent import async_openai_call
        response = await async_openai_call(
            None,
            task=llm_tasks.SALESFORCE_EXTRACTION,
            messages=[{"role": "user", "content": field_selection_prompt}],
            max_tokens=500,
            temperature=0.2,
        )
        
        # Validate OpenAI response structure
        if not response.choices or not response.choices[0].message.content:
            print("⚠️ Empty or invalid response from OpenAI")
            return _field_selection_fallback_result(prompt, field_list, selected_object, "empty response")
        
        ai_reply = response.choices[0].message.content.strip()
        
        # Parse JSON response - handle multiple formats
        if ai_reply.startswith('```json'):
            ai_reply = ai_reply[7:]
        if ai_reply.endswith('```'):
            ai_reply = ai_reply[:-3]
        
        # Extract JSON from response if it's wrapped in text
        if '{' in ai_reply and '}' in ai_reply:
            start = ai_reply.find('{')
            end = ai_reply.rfind('}') + 1
            ai_reply = ai_reply[start:end]
        else:
            print(f"⚠️ No JSON found in OpenAI response: {ai_reply[:100]}")
            return _field_selection_fallback_result(prompt, field_list, selected_object, "no JSON")
        
        # Use centralized JSON parsing function (handles markdown code blocks, JSON fixing, etc.)
        from utils.openai_helpers import parse_openai_json_response
        try:
            field_selection = parse_openai_json_response(ai_reply)
        except (ValueError, json.JSONDecodeError) as e:
            print(f"⚠️ Failed to parse JSON response: {e}")
            print(f"Response was: {ai_reply[:200]}")
            return _field_selection_fallback_result(prompt, field_list, selected_object, "parse error")

        if field_selection.get("needs_clarification"):
            # Try parsing raw SOQL from prompt (e.g. "run this as extraction query: select X, Y from Z")
            parsed = _parse_soql_from_prompt(prompt)
            if parsed:
                parsed_fields, parsed_object = parsed
                if parsed_object.lower() == object_api_name.lower():
                    available_set = {f.get("api_name") for f in field_list if f.get("api_name")}
                    available_set.add("Id")
                    validated_parsed = [f for f in parsed_fields if f in available_set]
                    if validated_parsed:
                        field_selection = {
                            "needs_clarification": False,
                            "selected_fields": _dedupe_preserve_order(validated_parsed),
                            "filters": field_selection.get("filters", ""),
                            "include_attachments": field_selection.get("include_attachments", False),
                            "attachment_confidence": field_selection.get("attachment_confidence", "low"),
                        }
                        print(f"📋 [FIELD SELECTION] Parsed {len(validated_parsed)} fields from raw SOQL in prompt")
            if field_selection.get("needs_clarification"):
                return {
                    "needs_clarification": True,
                    "clarification_message": field_selection.get("clarification_message")
                    or f"Which fields should I extract from {object_api_name}?",
                }

        selected_fields = field_selection.get("selected_fields") or []

        # If AI returned no fields, try parsing raw SOQL from prompt
        if not selected_fields:
            parsed = _parse_soql_from_prompt(prompt)
            if parsed:
                parsed_fields, parsed_object = parsed
                if parsed_object.lower() == object_api_name.lower():
                    available_set = {f.get("api_name") for f in field_list if f.get("api_name")}
                    available_set.add("Id")
                    validated_parsed = [f for f in parsed_fields if f in available_set]
                    if validated_parsed:
                        selected_fields = _dedupe_preserve_order(validated_parsed)
                        print(f"📋 [FIELD SELECTION] Parsed {len(selected_fields)} fields from raw SOQL (AI returned empty)")

        # Use .get() for safe access in comprehensions
        available_fields = {field.get("api_name") for field in field_list if field.get("api_name")}
        # Ensure "Id" is always in available_fields (standard field on all Salesforce objects)
        # This is critical for attachment extraction which requires record IDs
        if "Id" not in available_fields:
            available_fields.add("Id")
            print(f"📎 [FIELD SELECTION] Added Id to available_fields (standard field)")
        validated_fields = [field for field in selected_fields if field in available_fields]
        
        # Only add Id if user explicitly requested it or if it's already in selected_fields
        # Do NOT automatically add Id unless user specifically asks for it
        prompt_lower = prompt.lower()
        user_requested_id = any(keyword in prompt_lower for keyword in ["id", "record id", "salesforce id", "object id"])
        if "Id" in available_fields and "Id" not in validated_fields and user_requested_id:
            validated_fields.insert(0, "Id")

        # Enforce: always include required fields from the BASE object, and keep at least 6 base-object fields
        # (relationship fields are useful, but base fields are needed for display + downstream logic).
        base_field_meta = [
            f for f in field_list
            if f.get("api_name") and not f.get("is_relationship_field")
        ]
        base_required_fields = [
            f.get("api_name") for f in base_field_meta
            if f.get("is_required") and f.get("api_name")
        ]
        # Add required base fields
        for req in base_required_fields:
            if req in available_fields and req not in validated_fields:
                validated_fields.append(req)

        # Ensure minimum base fields count (prefer common useful fields)
        MIN_BASE_FIELDS = 6
        current_base_fields = [f for f in validated_fields if "." not in f]
        if len(current_base_fields) < MIN_BASE_FIELDS:
            preferred_base_order = (
                ["Name", "FirstName", "LastName", "Email", "Phone"]
                if field_scope.get("cross_system")
                else ["Id", "Name", "FirstName", "LastName", "Email", "Phone"]
            )
            # Add preferred fields first
            for pref in preferred_base_order:
                if len([f for f in validated_fields if "." not in f]) >= MIN_BASE_FIELDS:
                    break
                if pref in available_fields and pref not in validated_fields:
                    validated_fields.append(pref)
            # If still short, fill with any other base fields from metadata
            if len([f for f in validated_fields if "." not in f]) < MIN_BASE_FIELDS:
                for meta in base_field_meta:
                    api = meta.get("api_name")
                    if not api or api in validated_fields:
                        continue
                    validated_fields.append(api)
                    if len([f for f in validated_fields if "." not in f]) >= MIN_BASE_FIELDS:
                        break

        if not validated_fields:
            # fallback to first few fields (prefer required)
            required_fields = [field.get("api_name") for field in field_list if field.get("is_required") and field.get("api_name")]
            fallback_fields = [f for f in required_fields if f] or [field.get("api_name") for field in field_list[:5] if field.get("api_name")]
            validated_fields = fallback_fields[:]

        # Remove duplicates while preserving order
        validated_fields = _dedupe_preserve_order(validated_fields)
        
        # Don't hardcode limit - let OpenAI decide based on user intent
        # If user says "all fields", OpenAI will return all available fields (up to 50 from DB query)
        # If user doesn't specify, OpenAI will return reasonable number (3-15 as per prompt)

        if not validated_fields:
            return {
                "needs_clarification": True,
                "clarification_message": f"I couldn't determine which fields to use for {object_api_name}. Please list the fields you need.",
            }

        filters_text = field_selection.get("filters")
        filters_text = filters_text.strip() if isinstance(filters_text, str) else None

        # Parse attachment detection fields
        include_attachments = field_selection.get("include_attachments", False)
        attachment_confidence = field_selection.get("attachment_confidence", "low")

        # If attachments are requested, ensure Id field is included (needed for attachment extraction)
        if include_attachments and "Id" in available_fields and "Id" not in validated_fields:
            validated_fields.insert(0, "Id")
            print(f"📎 [FIELD SELECTION] Added Id field for attachment extraction")

        validated_fields = apply_cross_system_field_guardrail(
            validated_fields,
            field_scope,
            field_metadata=field_list,
            prompt=prompt,
            for_attachments=include_attachments,
        )

        if not validated_fields:
            return {
                "needs_clarification": True,
                "clarification_message": f"I couldn't determine which descriptive fields to use for {object_api_name} in a cross-system migration. Please list the business fields you need.",
            }

        return {
            "needs_clarification": False,
            "object_api_name": object_api_name,
            "object_label": object_label,
            "category": selected_object.get("category"),
            "selected_fields": validated_fields,
            "filters": filters_text,
            "field_metadata": field_list,
            "org_id": selected_object.get("org_id"),
            "include_attachments": include_attachments,
            "attachment_confidence": attachment_confidence,
            "extract_field_scope": field_scope,
            "lookup_relationships": lookup_relationships,
        }

    except Exception as exc:
        print(f"❌ Error in field selection: {exc}")
        import traceback
        traceback.print_exc()
        # Fallback: return with basic fields
        # Initialize variables in case exception happened before they were set
        field_list = field_list if 'field_list' in locals() else []
        object_api_name = object_api_name if 'object_api_name' in locals() else selected_object.get("object_api_name", "Unknown")
        
        if field_list:
            required_fields = [field.get("api_name") for field in field_list if field.get("is_required") and field.get("api_name")]
            fallback_fields = [f for f in required_fields if f] or [field.get("api_name") for field in field_list[:5] if field.get("api_name")]
            
            # Only add Id if user explicitly requested it
            prompt_lower = prompt.lower() if 'prompt' in locals() else ""
            user_requested_id = any(keyword in prompt_lower for keyword in ["id", "record id", "salesforce id", "object id"])
            if "Id" in [f.get("api_name") for f in field_list if f.get("api_name")] and "Id" not in fallback_fields and user_requested_id:
                fallback_fields.insert(0, "Id")
        else:
            # If field_list is empty/undefined, return clarification
            return {
                "needs_clarification": True,
                    "clarification_message": f"I encountered an error processing fields for {object_api_name}. Please try again or specify the fields you need.",
                }
        
        return {
            "needs_clarification": False,
            "object_api_name": object_api_name,
            "object_label": selected_object.get("label") or object_api_name,
            "category": selected_object.get("category"),
            "selected_fields": fallback_fields,  # No hardcoded limit - use what we have
            "filters": None,
            "field_metadata": field_list,
            "org_id": selected_object.get("org_id"),
            "include_attachments": False,  # Default to False on exception fallback
            "attachment_confidence": "low",
        }


def _sf_extract_classification_context(
    *,
    intent_state: Optional[Dict[str, Any]] = None,
    conversation_history: Optional[list] = None,
) -> Dict[str, Any]:
    """PLAN_SCOPE / pipeline tokens for SOQL sanitize (not prompt keywords)."""
    cls: Dict[str, Any] = {}
    if isinstance(intent_state, dict):
        for key in (
            "_plan_scope",
            "data_operations",
            "operations",
            "_pipeline_tokens",
            "_continuation_steps",
            "plan_route",
            "active_job_id",
        ):
            val = intent_state.get(key)
            if val is not None:
                cls[key] = val
    for msg in reversed(conversation_history or []):
        if not isinstance(msg, dict):
            continue
        meta = msg.get("metadata") or {}
        pending = meta.get("planner_pending") or {}
        snap = pending.get("classification") if isinstance(pending, dict) else None
        if not isinstance(snap, dict):
            continue
        scope = snap.get("_plan_scope")
        if isinstance(scope, dict) and scope:
            cls.setdefault("_plan_scope", scope)
        for key in ("data_operations", "plan_route", "active_job_id", "_pipeline_tokens"):
            if snap.get(key) is not None:
                cls.setdefault(key, snap[key])
        if cls:
            break
    return cls


async def generate_salesforce_soql_query(
    prompt: str,
    object_result: Dict[str, Any],
    conversation_history: Optional[list] = None,
    user_id: Optional[str] = None,
    extraction_limit: Optional[Dict[str, Any]] = None,
    *,
    extract_field_scope: Optional[Dict[str, Any]] = None,
    migration_intent: Optional[Dict[str, Any]] = None,
    classification: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Generate a SOQL query based on object/field selections.
    Returns dict with:
      - soql
      - used_fields
      - limit
      - filter_summary
      - needs_clarification / clarification_message
    """
    try:
        from services.architect_agent import get_full_conversation_context

        object_api_name = object_result.get("object_api_name")
        selected_fields = object_result.get("selected_fields") or []
        field_metadata = object_result.get("field_metadata") or []
        filters_text = object_result.get("filters")
        include_attachments = object_result.get("include_attachments", False)
        field_scope = (
            extract_field_scope
            if isinstance(extract_field_scope, dict)
            else object_result.get("extract_field_scope")
        ) or {}

        from services.planning.extract_field_scope import (
            apply_cross_system_field_guardrail,
            cross_system_field_prompt_block,
            guardrail_soql_for_cross_system,
        )
        from services.planning.soql_extract_contract import (
            build_soql_extract_contract,
            enforce_soql_select_contract,
            soql_authority_prompt_block,
            user_context_prompt_block,
        )

        if field_scope.get("cross_system"):
            selected_fields = apply_cross_system_field_guardrail(
                selected_fields,
                field_scope,
                field_metadata=field_metadata,
                prompt=prompt,
                for_attachments=include_attachments,
            )

        # If attachments are requested, ensure Id is in selected_fields (needed for attachment extraction)
        if include_attachments and "Id" not in selected_fields:
            selected_fields.insert(0, "Id")
            print(f"📎 [SOQL GENERATION] Added Id field to selected_fields for attachment extraction")

        if not object_api_name or not selected_fields:
            return {
                "needs_clarification": True,
                "clarification_message": "I need the Salesforce object and field names before I can build a SOQL query.",
                "_debug": {"reason": "missing_object_or_fields"},
            }

        extract_contract = build_soql_extract_contract(
            object_result,
            extract_field_scope=field_scope,
            migration_intent=migration_intent,
        )
        extract_contract["selected_fields"] = selected_fields
        extract_contract["allowed_select_fields"] = list(selected_fields)

        field_metadata_json = json.dumps(field_metadata, indent=2)
        selected_fields_json = json.dumps(selected_fields)
        debug_base = {
            "object_api_name": object_api_name,
            "selected_field_count": len(selected_fields),
            "field_metadata_count": len(field_metadata),
            "filters": filters_text,
            "approx_metadata_tokens": approx_token_count(field_metadata_json),
            "approx_selected_tokens": approx_token_count(selected_fields_json),
        }
        log_sf_extract("soql_build_start", **debug_base)

        # First, check if current prompt explicitly mentions a limit
        current_limit = None
        prompt_lower = prompt.lower()
        limit_patterns = [
            r'\b(?:only|extract|get|fetch)\s+(\d+)\s+records?\b',
            r'\b(\d+)\s+records?\s+(?:only|from|of)',
            r'\blimit\s+(?:to|of)?\s*(\d+)',
        ]
        for pattern in limit_patterns:
            match = re.search(pattern, prompt_lower)
            if match:
                try:
                    current_limit = int(match.group(1))
                    print(f"📊 Found limit in current prompt: {current_limit}")
                    break
                except ValueError:
                    continue
        
        # Extract limit from conversation history (for field modification requests)
        # Only if current prompt doesn't mention a limit
        previous_limit = None
        if conversation_history and not current_limit:
            # Search conversation history for previous limits
            # Look for: "extract only X records", "LIMIT X", "extracted X records"
            for msg in reversed(conversation_history[-20:]):  # Last 20 messages
                msg_text = msg.get('text', '') or msg.get('content', '')
                if not msg_text:
                    continue
                
                # Check user messages for limit mentions
                if msg.get('type') == 'user':
                    # Look for patterns like "only 10 records", "extract 10", "10 records"
                    for pattern in limit_patterns:
                        match = re.search(pattern, msg_text.lower())
                        if match:
                            try:
                                previous_limit = int(match.group(1))
                                print(f"📊 Found previous limit from user message: {previous_limit}")
                                break
                            except ValueError:
                                continue
                    if previous_limit:
                        break
                
                # Check assistant messages for SOQL queries or extraction results
                elif msg.get('type') in ['assistant', 'system']:
                    # Look for SOQL queries with LIMIT
                    soql_limit_match = re.search(r'\bLIMIT\s+(\d+)', msg_text, re.IGNORECASE)
                    if soql_limit_match:
                        try:
                            previous_limit = int(soql_limit_match.group(1))
                            print(f"📊 Found previous limit from SOQL query: {previous_limit}")
                            break
                        except ValueError:
                            continue
                    
                    # Look for extraction results showing record count
                    extracted_match = re.search(r'\bextracted\s+(\d+)\s+records?\b', msg_text.lower())
                    if extracted_match:
                        try:
                            previous_limit = int(extracted_match.group(1))
                            print(f"📊 Found previous limit from extraction result: {previous_limit}")
                            break
                        except ValueError:
                            continue
        
        # Build conversation context for AI (always build if history exists)
        conversation_context = ""
        if conversation_history:
            recent_messages = conversation_history[-10:] if len(conversation_history) > 10 else conversation_history
            conversation_context = "\n\n**Conversation History:**\n"
            for msg in recent_messages:
                msg_type = msg.get('type', msg.get('role', 'user'))
                msg_text = msg.get('text', '') or msg.get('content', '')
                if msg_text:
                    role_label = "User" if msg_type in ['user', 'User'] else "Assistant"
                    conversation_context += f"{role_label}: {msg_text}\n"


        # Build limit instruction based on current or previous limit
        limit_instruction = ""
        if current_limit:
            # Current prompt explicitly mentions a limit - use it
            limit_instruction = f"""
**Extraction limit:** The current request explicitly mentions {current_limit} records. Use LIMIT {current_limit} in the SOQL query.
"""
        elif previous_limit:
            # Check if current prompt is a field modification (short, contains field modification keywords)
            prompt_words = prompt.strip().split()
            is_short = len(prompt_words) <= 10
            is_field_modification = any(keyword in prompt_lower for keyword in [
                'ignore', 'remove', 'exclude', "don't need", "dont need", 'skip', 'keep', 'only', 'just', 'except'
            ])
            
            if is_short and is_field_modification:
                limit_instruction = f"""
**CRITICAL: Extraction limit preservation**
The conversation history shows a previous extraction with LIMIT {previous_limit} records. Since this is a field modification request (modifying fields from a previous extraction), you MUST preserve the same limit: LIMIT {previous_limit}. Do NOT use the default limit of 200.
"""
            else:
                # Previous limit exists but not a clear field modification - suggest it
                limit_instruction = f"""
**Note:** The conversation history shows a previous extraction with LIMIT {previous_limit} records. If the current request is related to the previous extraction, consider using the same limit. If the user explicitly requests a different number, use that instead.
"""

        # Build plan limit instruction (if extraction_limit provided)
        plan_limit_instruction = ""
        can_extract = extraction_limit.get("can_extract", 0) if extraction_limit else None
        if extraction_limit:
            limit_reached = extraction_limit.get("limit_reached", False)
            extraction_cap = extraction_limit.get("extraction_cap", None)
            
            if limit_reached or can_extract == 0:
                plan_limit_instruction = """
**CREDIT LIMIT REACHED:**
- User has insufficient credits for extraction
- They can extract 0 more records until they add credits or upgrade
- Return needs_clarification: true with a message about credit limit
"""
            elif can_extract > 0:
                # Only add plan limit instruction if it's more restrictive than current/previous limit
                effective_limit = min(can_extract, current_limit or previous_limit or 200)
                if effective_limit == can_extract and (not extraction_cap or can_extract < extraction_cap):
                    plan_limit_instruction = f"""
**CREDIT LIMIT APPLIED:**
- User can extract maximum {can_extract} records (based on current credit balance)
- Use LIMIT {can_extract} in the SOQL query (unless user explicitly requests fewer)
"""

        soql_prompt = f"""
You are Archian AI, a Salesforce SOQL expert. Build a valid SOQL query from the EXTRACT CONTRACT below.

{soql_authority_prompt_block(extract_contract)}

Field metadata (reference for WHERE clause field types and labels):
{field_metadata_json}

{user_context_prompt_block(prompt, conversation_context)}

{limit_instruction}

{plan_limit_instruction}

{cross_system_field_prompt_block(field_scope)}

Return JSON with the following shape:
{{
  "soql": "SELECT ... FROM {object_api_name} ...",
  "used_fields": ["FieldApiName1", "FieldApiName2"],
  "referenced_fields": ["FieldApiName1", "FieldApiName2", "FieldInWhereClause"],
  "limit": 200,
  "filter_summary": "Brief explanation of filters used (or empty string)",
  "needs_clarification": false,
  "clarification_message": ""
}}

**referenced_fields (REQUIRED):** List EVERY field API name used anywhere in the SOQL: SELECT clause, WHERE clause, ORDER BY, and subqueries. Include relationship paths (e.g. Account.Name, Project__r.Status__c). This list will be validated against the org metadata before execution.

Rules:
- **SELECT clause:** Use ONLY fields from the EXTRACT CONTRACT `SELECT fields` list (exact paths, exact case). Never add fields because the user mentioned them.
- If filters_hint or user context provides business filter guidance, include an appropriate WHERE clause; otherwise omit WHERE.
- **CRITICAL - Deduplication, validation, and cleanup (do NOT encode in SOQL):**
  - If the user asks to remove duplicates, dedupe, validate emails, filter invalid values, reject empty last names, or similar **data quality** work, generate a plain SELECT of the requested fields only.
  - Do **NOT** use GROUP BY, HAVING, or aggregate queries solely to deduplicate or validate — a later **run_data_ops_plan** pipeline step handles that.
  - Only use GROUP BY when the user explicitly asks for **aggregated/summary** data (counts, sums, averages by group).
  - Do **NOT** add WHERE clauses whose only purpose is email format checks, non-null last name, or duplicate removal unless the user explicitly asks for a **business** row filter in SOQL (e.g. "only contacts in California").
- **CRITICAL - Duplicate SELECT aliases (Bulk API):**
  - Do **NOT** select both `Phone` and `Account.Phone` on Contact — Bulk API fails with "duplicate alias: Phone".
  - When both contact phone and account phone are needed, include only `Account.Phone` for account phone; omit bare `Phone` unless the user explicitly asked for **contact** phone only (then use `Phone` only, not `Account.Phone`).
- **CRITICAL SOQL Syntax Rules (DO NOT USE SQL SYNTAX):**
  - **String Concatenation**: SOQL does NOT support `+` operator for concatenation. Use `CONCAT(Field1, ' ', Field2)` instead.
    - ❌ WRONG: `SELECT FirstName + ' ' + LastName AS Name FROM Contact`
    - ✅ CORRECT: `SELECT FirstName, LastName FROM Contact` (return separate fields, concatenate in application code)
    - ✅ CORRECT (if needed): `SELECT CONCAT(FirstName, ' ', LastName) FROM Contact` (but prefer separate fields)
  - **Field Aliases**: SOQL does NOT support `AS` aliases in SELECT clause. Use exact field API names only.
    - ❌ WRONG: `SELECT FirstName AS First, LastName AS Last FROM Contact`
    - ✅ CORRECT: `SELECT FirstName, LastName FROM Contact`
  - **Relationship Queries**: SOQL supports relationship queries using dot notation to access related object fields.
    - ✅ CORRECT: `SELECT FirstName, LastName, Account.Name, Account.Industry FROM Contact`
    - ✅ CORRECT (Custom): `SELECT Name, Project__r.Status__c FROM Custom_Object__c`
    - **Relationship fields** (fields with dots like `Account.Name`) can be included directly in the SELECT clause only if listed in the EXTRACT CONTRACT SELECT fields
    - Relationship queries allow traversing up to 5 levels deep (e.g., `Contact.Account.Owner.FirstName`)
  - **Aggregate Functions**: Use standard SOQL aggregate functions (COUNT, SUM, AVG, MAX, MIN) with GROUP BY if needed.
  - **Date/Time Functions**: Use SOQL date functions (YEAR, MONTH, DAY, etc.) not SQL date functions.
- **LIMIT Handling (CRITICAL):**
  - **PRIORITY 1**: If the current request explicitly mentions a limit (e.g., "only 10 records", "extract 50"), use that limit (capped by credit-based extraction limit if provided).
  - **PRIORITY 2**: If a credit-based extraction limit is provided in "CREDIT LIMIT APPLIED" section above, use that limit (unless user explicitly requests fewer).
  - **PRIORITY 3**: If this is a field modification request and a previous limit is mentioned above in "CRITICAL: Extraction limit preservation", preserve that limit (capped by credit-based limit if provided).
  - **PRIORITY 4**: If no limit is specified and no plan limit exists, omit LIMIT (return all matching records).
  - **IMPORTANT**: When preserving a previous limit for field modifications, use the exact same number - do not change it (but ensure it doesn't exceed the credit-based extraction limit).
- Do not add SELECT fields beyond the EXTRACT CONTRACT list.
- If you cannot confidently build the query, set needs_clarification to true with a concise clarification_message.
- The SOQL must be valid syntax and should not end with a semicolon.
- **IMPORTANT**: If the user wants combined fields (e.g., "Full Name" from FirstName + LastName), return the fields separately in SOQL. The application will handle combining them.
"""
        approx_prompt_tokens = approx_token_count(soql_prompt)
        log_sf_extract(
            "soql_llm_request",
            task=llm_tasks.SQL_QUERY_GENERATION,
            approx_prompt_tokens=approx_prompt_tokens,
            approx_prompt_chars=len(soql_prompt),
            **debug_base,
        )
        from services.architect_agent import async_openai_call
        try:
            response = await async_openai_call(
                None,
                task=llm_tasks.SQL_QUERY_GENERATION,
                messages=[{"role": "user", "content": soql_prompt}],
                temperature=0.1,
            )
        except Exception as llm_exc:
            log_sf_extract(
                "soql_llm_failed",
                level="warning",
                always=True,
                error_type=type(llm_exc).__name__,
                error=str(llm_exc)[:800],
                **debug_base,
            )
            return {
                "needs_clarification": True,
                "clarification_message": "I ran into an issue creating the SOQL query. Could you confirm the filters or limit you expect?",
                "_debug": {**debug_base, "llm_error": str(llm_exc)[:500]},
            }

        ai_reply = response.choices[0].message.content.strip()
        log_sf_extract(
            "soql_llm_response",
            response_chars=len(ai_reply),
            response_preview=ai_reply[:200],
            **debug_base,
        )

        # Use centralized JSON parsing function (handles markdown code blocks, JSON fixing, etc.)
        from utils.openai_helpers import parse_openai_json_response
        try:
            soql_payload = parse_openai_json_response(ai_reply)
        except (ValueError, json.JSONDecodeError) as e:
            print(f"❌ Error parsing SOQL query JSON response: {e}")
            print(f"⚠️ AI response (first 500 chars): {ai_reply[:500]}")
            log_sf_extract(
                "soql_json_parse_failed",
                level="warning",
                always=True,
                error=str(e)[:300],
                **debug_base,
            )
            return {
                "needs_clarification": True,
                "clarification_message": "I ran into an issue creating the SOQL query. Could you confirm the fields or limit you expect?",
                "_debug": {**debug_base, "parse_error": str(e)},
            }

        if soql_payload.get("needs_clarification"):
            log_sf_extract(
                "soql_llm_needs_clarification",
                level="warning",
                always=True,
                message=(soql_payload.get("clarification_message") or "")[:300],
                **debug_base,
            )
            soql_payload["_debug"] = debug_base
            return soql_payload

        soql_query = soql_payload.get("soql")
        if not soql_query or object_api_name not in soql_query:
            log_sf_extract(
                "soql_llm_invalid_query",
                level="warning",
                always=True,
                soql_preview=(soql_query or "")[:200],
                **debug_base,
            )
            return {
                "needs_clarification": True,
                "clarification_message": "I couldn't generate a valid SOQL query. Could you confirm the fields or filters you need?",
                "_debug": debug_base,
            }

        # Post-process SOQL to fix common syntax issues
        soql_query = _sanitize_soql_query(soql_query)

        allowed_select = extract_contract.get("allowed_select_fields") or selected_fields
        soql_query, enforced_select, removed_select = enforce_soql_select_contract(
            soql_query, allowed_select
        )
        used_fields = _dedupe_preserve_order(soql_payload.get("used_fields") or selected_fields)
        if removed_select:
            log_sf_extract(
                "soql_select_contract_enforced",
                level="warning",
                always=True,
                removed_fields=removed_select[:20],
                **debug_base,
            )
            print(
                f"🔧 [SOQL GENERATION] Removed unauthorized SELECT fields: {removed_select}"
            )
        if enforced_select:
            used_fields = _dedupe_preserve_order(enforced_select)

        from utils.soql_extraction_sanitize import apply_soql_extraction_post_process

        soql_before_ops_strip = soql_query
        soql_query = apply_soql_extraction_post_process(
            soql_query,
            prompt,
            classification=classification or {},
        )
        if soql_query != soql_before_ops_strip:
            print(
                "🔧 [SOQL GENERATION] Stripped data-ops filters/GROUP BY from extract SOQL "
                "(handled by run_data_ops_plan)"
            )

        soql_query, guarded_select = guardrail_soql_for_cross_system(
            soql_query,
            field_scope,
            field_metadata=field_metadata,
            prompt=prompt,
            for_attachments=include_attachments,
        )
        if guarded_select:
            used_fields = _dedupe_preserve_order(guarded_select)

        # Safety check: If attachments are requested, ensure Id is in used_fields
        # This ensures Id is in the SOQL query even if AI didn't include it
        if include_attachments and "Id" not in used_fields:
            used_fields.insert(0, "Id")
            print(f"📎 [SOQL GENERATION] Added Id to used_fields for attachment extraction (safety check)")
            # Also update SOQL query to include Id if it's missing
            soql_upper = soql_query.upper()
            if "SELECT" in soql_upper and "ID" not in soql_upper.split("FROM")[0]:
                # Insert Id at the beginning of SELECT clause
                select_match = re.search(r"SELECT\s+(.+?)\s+FROM", soql_query, re.IGNORECASE)
                if select_match:
                    select_clause = select_match.group(1)
                    soql_query = soql_query.replace(f"SELECT {select_clause}", f"SELECT Id, {select_clause}", 1)
                    print(f"📎 [SOQL GENERATION] Added Id to SOQL query: {soql_query[:100]}...")

        # Ensure LIMIT exists only when needed (do NOT force default LIMIT 200)
        limit_value = soql_payload.get("limit")
        limit_match = re.search(r"\bLIMIT\s+(\d+)", soql_query, re.IGNORECASE)
        if limit_match:
            try:
                limit_value = int(limit_match.group(1))
            except ValueError:
                limit_value = None
        else:
            # If plan limit is small enough to be safely expressed as SOQL LIMIT, apply it.
            # Otherwise (e.g., Pro plan 150K / unlimited), omit LIMIT and let the fetch layer + plan enforcement handle it.
            if extraction_limit:
                can_extract = extraction_limit.get("can_extract", 0)
                # If can_extract is a positive int and within SOQL LIMIT range, cap the query.
                if isinstance(can_extract, int) and 0 < can_extract <= 2000:
                    limit_value = can_extract
                    soql_query = f"{soql_query.rstrip()} LIMIT {limit_value}"
                else:
                    limit_value = None
            else:
                limit_value = None

        filter_summary = soql_payload.get("filter_summary")
        if isinstance(filter_summary, str):
            filter_summary = filter_summary.strip()
        else:
            filter_summary = None

        # Extract referenced_fields for pre-execution validation (all fields in SELECT, WHERE, ORDER BY)
        referenced_fields = soql_payload.get("referenced_fields") or used_fields
        referenced_fields = _dedupe_preserve_order([
            f for f in referenced_fields if f and isinstance(f, str)
        ])

        # Log the generated SOQL query
        print(f"📋 Generated SOQL Query: {soql_query}")

        return {
            "needs_clarification": False,
            "soql": soql_query,
            "used_fields": used_fields,
            "referenced_fields": referenced_fields,
            "limit": limit_value,
            "filter_summary": filter_summary,
            "_debug": {**debug_base, "path": "llm"},
        }

    except Exception as exc:
        print(f"❌ Error generating SOQL query: {exc}")
        log_sf_extract(
            "soql_build_exception",
            level="error",
            always=True,
            error_type=type(exc).__name__,
            error=str(exc)[:800],
        )
        return {
            "needs_clarification": True,
            "clarification_message": "I ran into an issue creating the SOQL query. Could you confirm the filters or limit you expect?",
            "_debug": {"exception": str(exc)[:500]},
        }


def _coerce_parsed_json_to_record_list(parsed: Any) -> List[Dict[str, Any]]:
    """Normalize OpenAI JSON payload to a list of record dicts."""
    if isinstance(parsed, list):
        return [x for x in parsed if isinstance(x, dict)]
    if isinstance(parsed, dict):
        return [parsed]
    return []


def _parse_salesforce_ai_extraction_json(response_text: str) -> List[Dict[str, Any]]:
    """
    Parse the first JSON array or object from the model reply. Handles markdown fences,
    trailing prose after valid JSON (Extra data), and mildly malformed JSON — aligned with
    file extraction robustness (see utils.openai_helpers.fix_malformed_json).
    """
    from utils.openai_helpers import fix_malformed_json

    text = (response_text or "").strip()
    if not text:
        return []

    # 1) Fenced ```json ... ``` block (preferred)
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fence:
        inner = fence.group(1).strip()
        try:
            return _coerce_parsed_json_to_record_list(json.loads(inner))
        except json.JSONDecodeError:
            try:
                return _coerce_parsed_json_to_record_list(json.loads(fix_malformed_json(inner)))
            except Exception:
                pass

    # 2) First complete JSON value only (avoids json.loads "Extra data" when model adds text after JSON)
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch not in "[{":
            continue
        try:
            parsed, _end = decoder.raw_decode(text, i)
            return _coerce_parsed_json_to_record_list(parsed)
        except json.JSONDecodeError:
            continue

    # 3) Legacy greedy fallbacks + fix_malformed_json
    json_match = re.search(r"\[.*\]", text, re.DOTALL)
    if json_match:
        blob = json_match.group(0)
    else:
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        blob = f"[{json_match.group(0)}]" if json_match else "[]"
    try:
        return _coerce_parsed_json_to_record_list(json.loads(blob))
    except json.JSONDecodeError:
        return _coerce_parsed_json_to_record_list(json.loads(fix_malformed_json(blob)))


async def extract_structured_data_from_salesforce(
    salesforce_data: List[Dict[str, Any]],
    user_id: str,
    extraction_goals: str,
    object_label: str,
    destination: str = "salesforce",
    destination_name: Optional[str] = None,
    saved_extraction_rules: Optional[Dict[str, Any]] = None,
    extraction_profile: Optional[str] = None,
    intent: Optional[str] = None,
    template: Optional[str] = None,
) -> Dict[str, Any]:
    """Normalize Salesforce records using the same prompt flow as SQL/file extraction."""
    from services.architect_agent import (
        get_user_extraction_limit,
        create_sample_records_from_schema,
        safe_json_serialize,
        async_openai_call,
    )
    from handlers.prompt_handler import PromptBuilder
    import pandas as pd
    from handlers.file_handler import apply_cleanup_and_validation_rules
    from utils.extraction_profile import (
        profile_allows_pre_openai_rule_apply,
        resolve_extraction_profile,
        sanitize_extraction_rules_for_profile,
    )

    profile = resolve_extraction_profile(
        saved_extraction_rules=saved_extraction_rules,
        extraction_profile=extraction_profile,
        prompt=extraction_goals,
        template=template,
        intent=intent,
        source="salesforce",
    )

    extraction_limit = await get_user_extraction_limit(user_id)

    df = pd.DataFrame(salesforce_data)
    original_record_count = len(df)

    # Convert to records for filtering
    normalized_records = df.fillna("").to_dict(orient="records")
    
    # ⭐ FILTERING LOGIC - Apply user-specified filter criteria BEFORE plan limits
    # Use saved extraction rules if provided, otherwise extract from OpenAI response
    filter_rules = None
    deduplication_rules = None
    transformation_rules = None
    
    if saved_extraction_rules and isinstance(saved_extraction_rules, dict):
        # Use saved extraction rules (from flow rerun)
        filter_rules = saved_extraction_rules.get("filter_rules")
        deduplication_rules = saved_extraction_rules.get("deduplication_rules")
        transformation_rules = saved_extraction_rules.get("transformation_rules")
    elif saved_extraction_rules is not None:
        # saved_extraction_rules exists but is not a dict (could be a list or other type)
        print(f"⚠️ [SALESFORCE HANDLER] saved_extraction_rules found but is not a dict (type: {type(saved_extraction_rules)}), ignoring")
    
    if profile_allows_pre_openai_rule_apply(profile):
        if saved_extraction_rules and transformation_rules:
            from handlers.file_handler import apply_transformations
            normalized_records = apply_transformations(normalized_records, transformation_rules)
        if filter_rules and filter_rules.get("conditions"):
            from handlers.file_handler import apply_filter_rules
            normalized_records = apply_filter_rules(normalized_records, filter_rules)
        if deduplication_rules and deduplication_rules.get("enabled"):
            from handlers.file_handler import apply_deduplication_rules
            normalized_records = apply_deduplication_rules(normalized_records, deduplication_rules)
    
    # ⭐ NOW apply plan limits to the filtered and deduplicated data
    if extraction_limit["can_extract"] > 0:
            normalized_records = normalized_records[:extraction_limit["can_extract"]]
    else:
        plan_info_with_flag = extraction_limit.copy()
        plan_info_with_flag["is_plan_limit_reached"] = True
        return {
            "records": [],
            "document_type": "Salesforce Data",
            "document_description": f"Records from {object_label}",
            "plan_info": plan_info_with_flag,
            "original_record_count": original_record_count,
            "show_upgrade_option": True,
            "limit_reached": True,
            "extraction_rules": saved_extraction_rules or {},
            "mapping_context_summary": None,
        }

    current_record_count = len(normalized_records)
    limit_applied = current_record_count < original_record_count
    # Use real data for OpenAI extraction analysis (limit to 5 records for token management)
    # Data is only sent for analysis, not stored, and data sharing with OpenAI is disabled
    sample_data = normalized_records[:5] if len(normalized_records) > 5 else normalized_records

    plan_limit_instruction = PromptBuilder.build_plan_limit_instruction(extraction_limit)

    instruction = await PromptBuilder.build_extraction_prompt(
        destination=destination,
        destination_name=destination_name,
        extraction_goals=f"Extract from Salesforce {object_label}: {extraction_goals}",
        sample_data=safe_json_serialize(sample_data),
        plan_limit_instruction=plan_limit_instruction,
        user_id=str(user_id),
        source="salesforce",
        source_name=object_label,
        extraction_profile=profile.value,
    )

    from services.llm import tasks as llm_tasks

    response = await async_openai_call(
        None,
        task=llm_tasks.SALESFORCE_EXTRACTION,
        messages=[{"role": "user", "content": instruction}],
        max_tokens=4000,
        temperature=0.1,
    )

    response_text = response.choices[0].message.content.strip()

    try:
        extracted_data = _parse_salesforce_ai_extraction_json(response_text)
    except Exception as e:
        print(f"❌ [SALESFORCE HANDLER] JSON decode error while parsing extraction output: {e}")
        raise

    document_type = f"{object_label} Data"
    document_description = f"Records from {object_label}"
    
    # Extract extraction_rules and mapping_context_summary before they're removed
    extraction_rules = {}
    mapping_context_summary = None
    if extracted_data and len(extracted_data) > 0:
        first_record_meta = extracted_data[0]
        
        # Defensive check: ensure first_record_meta is a dict
        if isinstance(first_record_meta, dict):
            mapping_context_summary = first_record_meta.get("mapping_context_summary")
            
            # Use saved extraction rules if provided, otherwise extract from OpenAI response
            if saved_extraction_rules:
                # Use saved extraction rules (from flow rerun)
                extraction_rules = saved_extraction_rules.copy() if isinstance(saved_extraction_rules, dict) else {}
            else:
                # Extract from OpenAI response
                filter_rules = first_record_meta.get("filter_rules")
                deduplication_rules = first_record_meta.get("deduplication_rules")
                transformation_rules = first_record_meta.get("transformation_rules")
                
                if filter_rules:
                    extraction_rules["filter_rules"] = filter_rules
                if deduplication_rules:
                    extraction_rules["deduplication_rules"] = deduplication_rules
                if transformation_rules:
                    extraction_rules["transformation_rules"] = transformation_rules
        else:
            print(f"⚠️ [SALESFORCE HANDLER] first_record_meta is not a dict (type: {type(first_record_meta)}), cannot extract extraction_rules")
        
        document_type = first_record_meta.pop("document_type", document_type)
        document_description = first_record_meta.pop(
            "document_description", document_description
        )
        # Extract destination_name from classification if it was None or "unknown"
        # Only extract from object_name when destination is google_sheets
        if (destination_name == None or destination_name == "unknown") and destination == "google_sheets":
            connector_name = first_record_meta.pop("connector_name", None)
            destination_name = connector_name or first_record_meta.pop("object_name", None)
            print(f"Destination name: {destination_name}")
        source_schema = sorted(
            {k for r in normalized_records[:50] if isinstance(r, dict) for k in r.keys()}
        )
        extraction_rules = sanitize_extraction_rules_for_profile(
            extraction_rules, profile, source_schema=source_schema
        )

        for record in extracted_data:
            record.pop('object_name', None)
            record.pop("document_type", None)
            record.pop("document_description", None)

    # Preserve attachment columns (Attachments and _attachments) before processing
    # These columns are added by attachment extraction and should not be filtered out
    # Only preserve if attachments were actually requested (columns exist in input)
    attachment_columns_preserved = {}
    if normalized_records and ("Attachments" in normalized_records[0] or "_attachments" in normalized_records[0]):
        # Store attachment data by record ID for re-attachment after processing
        for idx, record in enumerate(normalized_records):
            record_id = record.get("Id") or record.get("id") or record.get("ID")
            if record_id and ("Attachments" in record or "_attachments" in record):
                attachment_columns_preserved[record_id] = {
                    "Attachments": record.get("Attachments", ""),
                    "_attachments": record.get("_attachments", [])
                }
    
    # Use shared utility function for structured data processing
    from utils.extraction_utils import apply_extraction_rules_to_structured_data
    
    processed_records = normalized_records
    if extracted_data:
        try:
            # If saved_extraction_rules provided, create a mock extracted_data structure with saved rules
            if saved_extraction_rules:
                # Create mock_extracted_data with saved rules for the utility function
                mock_extracted_data = [saved_extraction_rules.copy()]
                # Remove transformation_rules if they were already applied early (to prevent double transformation)
                if transformation_rules and "transformation_rules" in mock_extracted_data[0]:
                    del mock_extracted_data[0]["transformation_rules"]
                # Also include mapping_context_summary if available
                if mapping_context_summary:
                    mock_extracted_data[0]["mapping_context_summary"] = mapping_context_summary
                processed_records = apply_extraction_rules_to_structured_data(
                    normalized_records,
                    mock_extracted_data,
                    extraction_profile=profile,
                    saved_extraction_rules=saved_extraction_rules,
                    prompt=extraction_goals,
                    template=template,
                    intent=intent,
                    source="salesforce",
                )
            else:
                processed_records = apply_extraction_rules_to_structured_data(
                    normalized_records,
                    extracted_data,
                    extraction_profile=profile,
                    prompt=extraction_goals,
                    template=template,
                    intent=intent,
                    source="salesforce",
                )
        except Exception as processing_error:
            print(f"⚠️ Error applying extraction rules to Salesforce data: {processing_error}")
            processed_records = normalized_records
    
    # Re-attach attachment columns if they were removed during processing
    # Only re-add if attachments were actually requested (preserved map exists)
    if attachment_columns_preserved:
        for record in processed_records:
            record_id = record.get("Id") or record.get("id") or record.get("ID")
            if record_id and record_id in attachment_columns_preserved:
                # Only re-add if missing (don't overwrite if already present)
                if "Attachments" not in record:
                    record["Attachments"] = attachment_columns_preserved[record_id]["Attachments"]
                if "_attachments" not in record:
                    record["_attachments"] = attachment_columns_preserved[record_id]["_attachments"]
            elif record_id:
                # Record with ID but not in preserved map - add empty attachments only if attachments were requested
                # (indicated by presence of attachment columns in preserved map)
                if "Attachments" not in record:
                    record["Attachments"] = ""
                if "_attachments" not in record:
                    record["_attachments"] = []

    _cap = extraction_limit.get("extraction_cap")
    show_upgrade_option = (
        (_cap is not None and extraction_limit.get("can_extract", 0) < _cap)
        or extraction_limit.get("limit_reached", False)
    )
    plan_info_with_flag = extraction_limit.copy()
    plan_info_with_flag["is_plan_limit_reached"] = limit_applied

    return {
        "records": processed_records,
        "document_type": document_type,
        "document_description": document_description,
        "extraction_rules": extraction_rules if extraction_rules else None,
        "mapping_context_summary": mapping_context_summary,
        "plan_info": plan_info_with_flag,
        "original_record_count": original_record_count,
        "show_upgrade_option": show_upgrade_option,
        "limit_reached": limit_applied,
        "destination_name": destination_name
    }
