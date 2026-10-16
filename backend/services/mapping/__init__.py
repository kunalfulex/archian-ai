"""Mapping executor — field wiring from approved plan pins (no endpoint re-planning)."""

from services.mapping.context import (
    MappingExecutionContext,
    build_mapping_execution_context,
    pinned_destination_entity_key,
)
from services.mapping.field_map_gs import execute_gsheets_field_mapping
from services.mapping.field_map_sf import execute_salesforce_field_mapping
from services.mapping.field_map_sql import execute_sql_field_mapping
from services.mapping.persist import persist_mapping_execution_result
from services.mapping.schema_loader import (
    SalesforceMappingSchema,
    load_salesforce_mapping_schema,
)
from services.mapping.service import (
    build_mapping_plan_request,
    execute_mapping,
    mapping_result_to_legacy_dict,
)
from services.mapping.validate import mapping_config_is_empty

__all__ = [
    "MappingExecutionContext",
    "build_mapping_execution_context",
    "pinned_destination_entity_key",
    "mapping_config_is_empty",
    "execute_salesforce_field_mapping",
    "execute_sql_field_mapping",
    "execute_gsheets_field_mapping",
    "execute_mapping",
    "build_mapping_plan_request",
    "mapping_result_to_legacy_dict",
    "persist_mapping_execution_result",
    "SalesforceMappingSchema",
    "load_salesforce_mapping_schema",
]
