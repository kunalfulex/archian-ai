"""Data models for mapping plan pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class ParsedMappingInstruction:
    raw: str = ""
    target_object_api_name: Optional[str] = None
    target_table: Optional[str] = None
    target_sheet: Optional[str] = None
    parse_tier: str = "none"


@dataclass
class MappingPlanRequest:
    user_id: Any
    destination: str
    destination_name: Optional[str]
    flat_data: Dict[str, Any]
    source_document_type: str = ""
    source_document_description: str = ""
    source_type: Optional[str] = None
    source_name: Optional[str] = None
    source_object_api_name: Optional[str] = None
    mapping_instruction: Optional[str] = None
    destination_connector_id: Optional[str] = None
    destination_spreadsheet_id: Optional[str] = None
    google_sheets_connection_id: Optional[str] = None
    target_object_api_name: Optional[str] = None
    target_table: Optional[str] = None
    target_sheet: Optional[str] = None
    target_resolution_source: Optional[str] = None
    is_sql_data: bool = False


@dataclass
class MappingPlanResult:
    mapping: Dict[str, Any]
    transformation_rules: Dict[str, Any]
    destination_document_type: str
    destination_document_description: str
    destination_object_api_name: Optional[str] = None
    destination_table: Optional[str] = None
    destination_sheet: Optional[str] = None
    source_document_type: str = ""
    source_document_description: str = ""
    mapping_plan: Optional[Dict[str, Any]] = None
    available_objects: Optional[Dict[str, Any]] = None
    available_tables: Optional[Dict[str, Any]] = None
    available_sheets: Optional[Dict[str, Any]] = None
    connector_id: Optional[str] = None
    token_id: Optional[str] = None
    spreadsheet_id: Optional[str] = None
    primary_table: Optional[str] = None
    destination: Optional[str] = None
    destination_name: Optional[str] = None
    org_rules_display_by_object: Optional[Dict[str, Any]] = None
    target_resolution_source: Optional[str] = None
    mapping_audit: Optional[Dict[str, Any]] = None
