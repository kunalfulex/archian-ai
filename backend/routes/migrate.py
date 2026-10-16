"""Map-fields and migrate-to-* endpoints (Salesforce, Google Sheets, database) plus background migration start."""

from typing import Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from routes.auth import get_current_user
from services.background_migration_service import background_migration_service
from services.entitlements import get_entitlements
from utils.batch_config import compute_migration_batch_size, MIGRATION_BATCH_SIZE_MAX_SALESFORCE
from utils.migration_sse_stream import migration_sse_streaming_response
from utils.salesforceapimigration import get_valid_salesforce_token

router = APIRouter()


@router.post("/map-fields")
async def map_fields(
    request: Request,
    user_id: UUID = Depends(get_current_user)
):
    """
    Map extracted data fields to destination objects/tables/spreadsheets.
    Supports multiple destinations: Salesforce, Google Sheets, SQL databases.
    """
    try:
        body = await request.json()
        flat_data = body.get("extracted_schema", {})
        document_type = body.get("document_type")
        document_description = body.get("document_description")
        destination = body.get("destination", "salesforce")  # Default to Salesforce for backward compatibility
        destination_name = body.get("destination_name")
        mapping_context_summary = body.get("mapping_context_summary")  # User's extraction prompt summary
        
        # Check if this is SQL data based on presence of SQL-specific fields
        is_sql_data = body.get("extracted_schema_type") == "sql" or body.get("connector_id") is not None

        filtered_flat = {
            k: v
            for k, v in (flat_data or {}).items()
            if k not in ("Attachments", "_attachments", "attachments")
        }

        from services.mapping.service import execute_mapping

        classification_snapshot = dict(body.get("classification_snapshot") or {})
        for snap_key in (
            "destination_object_api_name",
            "destination_table_name",
            "destination_sheet_name",
        ):
            if body.get(snap_key):
                classification_snapshot[snap_key] = body[snap_key]

        target_resolution = None
        if mapping_context_summary or classification_snapshot:
            try:
                from services.mapping_plan.target_resolution import resolve_mapping_target_async

                target_resolution = await resolve_mapping_target_async(
                    destination=destination,
                    mapping_instruction=mapping_context_summary,
                    classification_snapshot=classification_snapshot,
                    user_id=user_id,
                    destination_name=destination_name,
                )
            except Exception:
                pass

        extraction_row = {
            "document_type": document_type,
            "document_description": document_description,
            "source_document_type": body.get("source_document_type") or document_type,
            "source_document_description": body.get("source_document_description")
            or document_description,
            "source_type": body.get("extracted_schema_type"),
            "source_name": body.get("source_name"),
            "extraction_metadata": body.get("extraction_metadata") or {},
        }
        result = await execute_mapping(
            user_id=user_id,
            destination=destination,
            destination_name=destination_name,
            flat_data=filtered_flat,
            extraction_row=extraction_row,
            mapping_instruction=mapping_context_summary,
            target_resolution=target_resolution,
            destination_connector_id=body.get("destination_connector_id"),
            is_sql_data=is_sql_data,
        )
        try:
            from services.pipeline_learning.service import PipelineLearningService

            if PipelineLearningService.enabled():
                mapping_cfg = result.get("mapping") or {}
                PipelineLearningService.schedule_capture(
                    lambda: PipelineLearningService.capture_mapping_ai(
                        user_id=str(user_id),
                        conversation_id=body.get("conversation_id"),
                        job_id=body.get("job_id") or "",
                        mapping_config=mapping_cfg,
                        mapping_plan=result.get("mapping_plan"),
                        transformation_rules=result.get("transformation_rules"),
                        destination_object=result.get("destination_object_api_name"),
                        destination_type=destination,
                        source_type=body.get("extracted_schema_type"),
                    )
                )
        except Exception:
            pass
        return JSONResponse(content=result)

    except HTTPException:
        raise
    except Exception as e:
        print("Map fields error:", e)
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/migrate-to-salesforce")
async def migrate_to_salesforce_endpoint(
    request: Request,
    user_id: UUID = Depends(get_current_user)
):
    from services.migration import MigrationContext, MigrationService
    from services.migration.errors import MigrationServiceError

    try:
        body = await request.json()
        ctx = MigrationContext.from_http_body(body, str(user_id))
        result = await MigrationService.start(ctx)

        response_data = {
            "status": "success",
            "processing_type": "background",
            "background_job": {
                "job_id": result.job_id,
                "status": "running",
                "total_records": result.total_records,
                "processed_records": 0,
                "estimated_completion": None,
            },
            "job_id": result.job_id,
            "instance_url": result.instance_url,
            "total_records": result.total_records,
            "batch_id": result.batch_id,
            "org_id": result.org_id,
            "connected_app_id": result.connected_app_id,
            "destination_name": result.destination_name,
        }

        stream_resp = migration_sse_streaming_response(
            ctx.conversation_id,
            job_id=result.job_id,
            batch_id=result.batch_id,
            destination="salesforce",
            destination_name=result.destination_name,
            total_records=result.total_records,
            source_job_id=result.source_job_id,
            user_id=str(user_id),
        )
        if stream_resp:
            return stream_resp

        return JSONResponse(content=response_data)

    except MigrationServiceError as e:
        if e.payload:
            return JSONResponse(content=e.payload, status_code=e.status_code)
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except Exception as e:
        print("❌ Migration to Salesforce failed:", str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/start-background-migration")
async def start_background_migration(
    request: Request,
    user_id: UUID = Depends(get_current_user)
):
    """Start a background migration job for large datasets"""
    try:
        body = await request.json()
        data = body.get("data")
        mapping = body.get("mapping")
        document_type = body.get("document_type")
        if not all([data, mapping, document_type]):
            raise HTTPException(status_code=400, detail="Missing data, mapping, or document type.")

        total_records = len(data)
        batch_size = body.get("batch_size")
        if batch_size is None:
            batch_size = compute_migration_batch_size(total_records, "salesforce")
        max_concurrent = body.get("max_concurrent", 5)
        destination_name = body.get("destination_name")
        org_id = body.get("org_id") or body.get("salesforce_org_id")
        connected_app_id = body.get("connected_app_id") or body.get("destination_id")

        # Validate batch size and concurrency limits
        if batch_size < 1 or batch_size > MIGRATION_BATCH_SIZE_MAX_SALESFORCE:
            raise HTTPException(
                status_code=400,
                detail=f"Batch size must be between 1 and {MIGRATION_BATCH_SIZE_MAX_SALESFORCE}",
            )
        
        if max_concurrent < 1 or max_concurrent > 20:
            raise HTTPException(status_code=400, detail="Max concurrent must be between 1 and 20")

        # Get valid Salesforce token scoped to the correct org/app (defaulting when omitted)
        try:
            token_info = await get_valid_salesforce_token(
                user_id=str(user_id),
                org_id=org_id,
                connected_app_id=connected_app_id,
            )
        except Exception as token_error:
            # Provide user-friendly error when token is missing or invalid
            message = str(token_error)
            if "not found" in message.lower() or "no salesforce token" in message.lower():
                raise HTTPException(
                    status_code=400,
                    detail="Salesforce org is not connected. Please connect the selected Salesforce org and try again.",
                )
            raise

        access_token = token_info["access_token"]
        instance_url = token_info["instance_url"]
        org_id = org_id or token_info.get("org_id")
        connected_app_id = connected_app_id or token_info.get("connected_app_id")
        destination_name = destination_name or body.get("connection_name") or token_info.get("connection_name")

        # Transform mapping to match transformed data keys (flatten split field arrays)
        from utils.mapping_utils import transform_mapping_for_migration
        transformed_mapping = transform_mapping_for_migration(mapping)
        print(f"🔄 Mapping transformed: {len(mapping)} keys -> {len(transformed_mapping)} keys")

        # Credit check before migration (skip if unlimited plan)
        entitlements = get_entitlements()
        user_plan = await entitlements.get_user_plan(str(user_id))
        credits_per_month = user_plan.get("credits_per_month") if user_plan else None
        if credits_per_month != -1:
            from utils.credit_config import compute_migration_credits
            estimated_credits = compute_migration_credits(len(data))
            if estimated_credits > 0 and not await entitlements.has_sufficient_balance(str(user_id), estimated_credits):
                raise HTTPException(
                    status_code=402,
                    detail="Insufficient credits to run this migration. Buy more credits or upgrade your plan.",
                )

        # Start background migration
        job_id = await background_migration_service.start_background_migration(
            user_id=str(user_id),
            data=data,
            mapping=transformed_mapping,
            document_type=document_type,
            access_token=access_token,
            instance_url=instance_url,
            batch_size=batch_size,
            max_concurrent=max_concurrent,
            org_id=org_id,
            connected_app_id=connected_app_id,
            destination_name=destination_name,
        )

        return JSONResponse(content={
            "status": "success",
            "message": "Background migration started",
            "job_id": job_id,
            "total_records": len(data),
            "batch_size": batch_size,
            "max_concurrent": max_concurrent,
            "org_id": org_id,
            "connected_app_id": connected_app_id,
            "destination_name": destination_name,
        })

    except Exception as e:
        print("❌ Failed to start background migration:", str(e))
        raise HTTPException(status_code=500, detail=str(e))


# Request model for Google Sheets migration (backend-pull: source_job_id required, no data)
class MigrateToGoogleSheetsRequest(BaseModel):
    mapping: Dict
    spreadsheet_id: str
    sheet_name: str
    operation: str = "append"  # append, overwrite, upsert
    document_type: Optional[str] = None
    spreadsheet_name: Optional[str] = None
    conversation_id: Optional[str] = None
    source_job_id: Optional[str] = None  # Required: extraction job_id - backend loads from DB
    job_id: Optional[str] = None  # Alias for source_job_id
    is_transformed_data: Optional[bool] = False
    transformation_rules: Optional[Dict] = None
    migration_rules: Optional[Dict] = None  # e.g. missing_field_preference


class MigrateToDatabaseRequest(BaseModel):
    mapping: Dict
    connector_id: str
    table_name: str
    operation: str = "append"
    document_type: Optional[str] = None
    document_description: Optional[str] = None
    destination: Optional[str] = "sql"
    destination_name: Optional[str] = None
    conversation_id: Optional[str] = None
    source_job_id: Optional[str] = None  # Required: extraction job_id - backend loads from DB
    job_id: Optional[str] = None  # Alias for source_job_id
    is_transformed_data: Optional[bool] = False
    transformation_rules: Optional[Dict] = None
    migration_rules: Optional[Dict] = None  # e.g. missing_field_preference
    # Source lineage for chat events / save-flow (optional; frontend should send)
    source_type: Optional[str] = None
    extracted_schema_type: Optional[str] = None
    sql_query: Optional[str] = None
    source_connector_id: Optional[str] = None


@router.post("/migrate-to-google-sheets")
async def migrate_to_google_sheets_endpoint(
    request: MigrateToGoogleSheetsRequest,
    user_id: UUID = Depends(get_current_user),
):
    """Migrate data to Google Sheets. Backend loads records from DB by source_job_id."""
    from services.migration import MigrationContext, MigrationService
    from services.migration.errors import MigrationServiceError

    try:
        body = request.model_dump()
        body["destination"] = "google_sheets"
        ctx = MigrationContext.from_http_body(body, str(user_id))
        result = await MigrationService.start(ctx)

        extra = result.extra or {}
        response_payload = {
            "status": "success",
            "processing_type": "background",
            "background_job": {
                "job_id": result.job_id,
                "status": "running",
                "total_records": result.total_records,
                "processed_records": 0,
                "estimated_completion": None,
            },
            "job_id": result.job_id,
            "spreadsheet_url": extra.get("spreadsheet_url"),
            "spreadsheet_id": extra.get("spreadsheet_id"),
            "sheet_name": extra.get("sheet_name"),
            "operation": extra.get("operation"),
            "batch_id": result.batch_id,
        }

        stream_resp = migration_sse_streaming_response(
            ctx.conversation_id,
            job_id=result.job_id,
            batch_id=result.batch_id,
            destination="google_sheets",
            destination_name=result.destination_name,
            total_records=result.total_records,
            source_job_id=result.source_job_id,
            user_id=str(user_id),
        )
        if stream_resp:
            return stream_resp

        return JSONResponse(content=response_payload)

    except MigrationServiceError as e:
        if e.payload:
            return JSONResponse(content=e.payload, status_code=e.status_code)
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error in Google Sheets migration: {e}")
        import traceback

        print(f"   Traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=500, detail=f"Migration to Google Sheets failed: {str(e)}"
        )


@router.post("/migrate-to-database")
async def migrate_to_database_endpoint(
    request: MigrateToDatabaseRequest,
    user_id: UUID = Depends(get_current_user),
):
    """Migrate data to a SQL database table. Backend loads records from DB by source_job_id."""
    from services.migration import MigrationContext, MigrationService
    from services.migration.errors import MigrationServiceError

    try:
        body = request.model_dump()
        body["destination"] = "sql"
        ctx = MigrationContext.from_http_body(body, str(user_id))
        result = await MigrationService.start(ctx)

        extra = result.extra or {}
        response_payload = {
            "status": "success",
            "processing_type": "background",
            "background_job": {
                "job_id": result.job_id,
                "status": "running",
                "total_records": result.total_records,
                "processed_records": 0,
                "job_type": "sql",
            },
            "job_id": result.job_id,
            "batch_id": result.batch_id,
            "operation": extra.get("operation"),
            "connector_id": extra.get("connector_id"),
            "table_name": extra.get("table_name"),
            "destination_name": result.destination_name,
        }

        sse_extra = {
            k: extra[k]
            for k in (
                "source_type",
                "extracted_schema_type",
                "sql_query",
                "source_connector_id",
                "table_name",
            )
            if extra.get(k) is not None
        }
        stream_resp = migration_sse_streaming_response(
            ctx.conversation_id,
            job_id=result.job_id,
            batch_id=result.batch_id,
            destination="sql",
            destination_name=result.destination_name,
            total_records=result.total_records,
            source_job_id=result.source_job_id,
            sse_extra=sse_extra,
            user_id=str(user_id),
        )
        if stream_resp:
            return stream_resp

        return JSONResponse(content=response_payload)

    except MigrationServiceError as e:
        if e.payload:
            return JSONResponse(content=e.payload, status_code=e.status_code)
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error in Database migration: {e}")
        import traceback

        print(f"   Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Migration to Database failed: {str(e)}")
