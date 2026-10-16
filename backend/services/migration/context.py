"""Migration request context shared by HTTP endpoints and agent tools."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MigrationStartResult:
    job_id: str
    batch_id: str
    total_records: int
    destination: str
    destination_name: Optional[str] = None
    source_job_id: Optional[str] = None
    instance_url: Optional[str] = None
    org_id: Optional[str] = None
    connected_app_id: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MigrationContext:
    user_id: str
    extraction_job_id: str
    mapping: Dict[str, Any]
    document_type: str
    destination_object_api_name: Optional[str] = None
    destination: str = "salesforce"
    destination_name: Optional[str] = None
    document_description: Optional[str] = None
    conversation_id: Optional[str] = None
    mapping_id: Optional[str] = None
    mapping_version: int = 1
    is_transformed_data: bool = False
    transformation_rules: Optional[Dict[str, Any]] = None
    migration_rules: Optional[Dict[str, Any]] = None
    connector_id: Optional[str] = None
    source_connector_id: Optional[str] = None
    sql_query: Optional[str] = None
    table_name: Optional[str] = None
    spreadsheet_id: Optional[str] = None
    spreadsheet_name: Optional[str] = None
    sheet_name: Optional[str] = None
    operation: Optional[str] = None
    source_type: Optional[str] = None
    extracted_schema_type: Optional[str] = None
    record_ordinals: Optional[List[int]] = None
    parent_batch_id: Optional[str] = None
    mapping_config_transform_applied: bool = False

    @property
    def source_job_id(self) -> str:
        return self.extraction_job_id

    @classmethod
    def from_http_body(cls, body: Dict[str, Any], user_id: str) -> "MigrationContext":
        source_job_id = body.get("source_job_id") or body.get("job_id") or ""
        dest = (body.get("destination") or "salesforce").lower()
        if dest == "gsheets":
            dest = "google_sheets"
        return cls(
            user_id=str(user_id),
            extraction_job_id=str(source_job_id),
            mapping=dict(body.get("mapping") or {}),
            document_type=str(body.get("document_type") or ""),
            destination=dest,
            destination_name=body.get("destination_name"),
            document_description=body.get("document_description"),
            conversation_id=body.get("conversation_id"),
            mapping_id=body.get("mapping_id"),
            is_transformed_data=bool(body.get("is_transformed_data", False)),
            transformation_rules=body.get("transformation_rules"),
            migration_rules=body.get("migration_rules") or {},
            connector_id=body.get("connector_id") or body.get("destination_connector_id"),
            source_connector_id=body.get("source_connector_id"),
            sql_query=body.get("sql_query"),
            table_name=body.get("table_name"),
            spreadsheet_id=body.get("spreadsheet_id"),
            spreadsheet_name=body.get("spreadsheet_name"),
            sheet_name=body.get("sheet_name"),
            operation=body.get("operation"),
            source_type=body.get("source_type"),
            extracted_schema_type=body.get("extracted_schema_type"),
            record_ordinals=body.get("record_ordinals"),
            parent_batch_id=body.get("parent_batch_id"),
            mapping_config_transform_applied=bool(
                body.get("mapping_config_transform_applied", False)
            ),
        )

    def to_storage_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "extraction_job_id": self.extraction_job_id,
            "mapping": self.mapping,
            "document_type": self.document_type,
            "destination_object_api_name": self.destination_object_api_name,
            "destination": self.destination,
            "destination_name": self.destination_name,
            "document_description": self.document_description,
            "conversation_id": self.conversation_id,
            "mapping_id": self.mapping_id,
            "mapping_version": self.mapping_version,
            "is_transformed_data": self.is_transformed_data,
            "transformation_rules": self.transformation_rules,
            "migration_rules": self.migration_rules,
            "connector_id": self.connector_id,
            "source_connector_id": self.source_connector_id,
            "sql_query": self.sql_query,
            "table_name": self.table_name,
            "spreadsheet_id": self.spreadsheet_id,
            "spreadsheet_name": self.spreadsheet_name,
            "sheet_name": self.sheet_name,
            "operation": self.operation,
            "source_type": self.source_type,
            "extracted_schema_type": self.extracted_schema_type,
            "record_ordinals": self.record_ordinals,
            "parent_batch_id": self.parent_batch_id,
            "mapping_config_transform_applied": self.mapping_config_transform_applied,
        }

    @classmethod
    def from_storage_dict(cls, data: Dict[str, Any]) -> "MigrationContext":
        return cls(
            user_id=str(data.get("user_id") or ""),
            extraction_job_id=str(data.get("extraction_job_id") or ""),
            mapping=dict(data.get("mapping") or {}),
            document_type=str(data.get("document_type") or ""),
            destination_object_api_name=(
                str(data["destination_object_api_name"]).strip()
                if data.get("destination_object_api_name")
                else None
            ),
            destination=(data.get("destination") or "salesforce").lower(),
            destination_name=data.get("destination_name"),
            document_description=data.get("document_description"),
            conversation_id=data.get("conversation_id"),
            mapping_id=data.get("mapping_id"),
            mapping_version=int(data.get("mapping_version") or 1),
            is_transformed_data=bool(data.get("is_transformed_data")),
            transformation_rules=data.get("transformation_rules"),
            migration_rules=data.get("migration_rules") or {},
            connector_id=data.get("connector_id"),
            source_connector_id=data.get("source_connector_id"),
            sql_query=data.get("sql_query"),
            table_name=data.get("table_name"),
            spreadsheet_id=data.get("spreadsheet_id"),
            spreadsheet_name=data.get("spreadsheet_name"),
            sheet_name=data.get("sheet_name"),
            operation=data.get("operation"),
            source_type=data.get("source_type"),
            extracted_schema_type=data.get("extracted_schema_type"),
            record_ordinals=data.get("record_ordinals"),
            parent_batch_id=data.get("parent_batch_id"),
            mapping_config_transform_applied=bool(
                data.get("mapping_config_transform_applied")
            ),
        )
