"""REST API for canonical migration/mapping context (agent + UI)."""
from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from routes.auth import get_current_user
from services.migration.context import MigrationContext
from services.migration.context_repository import (
    get_context,
    patch_context,
    upsert_context,
)
from services.migration.errors import MigrationServiceError

router = APIRouter(prefix="/conversations", tags=["migration-context"])


class MigrationContextUpsertBody(BaseModel):
    extraction_job_id: str
    mapping: Dict[str, Any]
    document_type: str
    destination: str = "salesforce"
    destination_name: Optional[str] = None
    document_description: Optional[str] = None
    mapping_id: Optional[str] = None
    is_transformed_data: bool = False
    transformation_rules: Optional[Dict[str, Any]] = None
    migration_rules: Optional[Dict[str, Any]] = None
    connector_id: Optional[str] = None
    source_connector_id: Optional[str] = None
    table_name: Optional[str] = None
    spreadsheet_id: Optional[str] = None
    spreadsheet_name: Optional[str] = None
    sheet_name: Optional[str] = None
    operation: Optional[str] = None
    source_type: Optional[str] = None
    extracted_schema_type: Optional[str] = None
    sql_query: Optional[str] = None


class MigrationContextPatchBody(BaseModel):
    mapping: Dict[str, Any]
    transformation_rules: Optional[Dict[str, Any]] = None
    table_name: Optional[str] = None
    migration_rules: Optional[Dict[str, Any]] = None
    expected_version: Optional[int] = Field(None, alias="mapping_version")


@router.put("/{conversation_id}/migrations/context")
async def put_migration_context(
    conversation_id: UUID,
    body: MigrationContextUpsertBody,
    user_id: UUID = Depends(get_current_user),
):
    ctx = MigrationContext(
        user_id=str(user_id),
        extraction_job_id=body.extraction_job_id,
        mapping=body.mapping,
        document_type=body.document_type,
        destination=body.destination,
        destination_name=body.destination_name,
        document_description=body.document_description,
        conversation_id=str(conversation_id),
        mapping_id=body.mapping_id,
        is_transformed_data=body.is_transformed_data,
        transformation_rules=body.transformation_rules,
        migration_rules=body.migration_rules,
        connector_id=body.connector_id,
        source_connector_id=body.source_connector_id,
        table_name=body.table_name,
        spreadsheet_id=body.spreadsheet_id,
        spreadsheet_name=body.spreadsheet_name,
        sheet_name=body.sheet_name,
        operation=body.operation,
        source_type=body.source_type,
        extracted_schema_type=body.extracted_schema_type,
        sql_query=body.sql_query,
    )
    saved = await upsert_context(ctx)
    return {
        "status": "ok",
        "mapping_id": saved.mapping_id,
        "mapping_version": saved.mapping_version,
        "context": saved.to_storage_dict(),
    }


@router.patch("/{conversation_id}/migrations/context")
async def patch_migration_context(
    conversation_id: UUID,
    body: MigrationContextPatchBody,
    extraction_job_id: str = Query(..., alias="job_id"),
    user_id: UUID = Depends(get_current_user),
):
    updated = await patch_context(
        user_id=str(user_id),
        conversation_id=str(conversation_id),
        extraction_job_id=extraction_job_id,
        mapping=body.mapping,
        transformation_rules=body.transformation_rules,
        table_name=body.table_name,
        migration_rules=body.migration_rules,
        expected_version=body.expected_version,
    )
    if not updated:
        raise HTTPException(
            status_code=404,
            detail="Migration context not found or version conflict.",
        )
    return {
        "status": "ok",
        "mapping_id": updated.mapping_id,
        "mapping_version": updated.mapping_version,
    }


@router.get("/{conversation_id}/migrations/context")
async def read_migration_context(
    conversation_id: UUID,
    extraction_job_id: str = Query(..., alias="job_id"),
    user_id: UUID = Depends(get_current_user),
):
    ctx = await get_context(
        user_id=str(user_id),
        conversation_id=str(conversation_id),
        extraction_job_id=extraction_job_id,
    )
    if not ctx:
        raise HTTPException(status_code=404, detail="Migration context not found.")
    return {"status": "ok", "context": ctx.to_storage_dict()}
