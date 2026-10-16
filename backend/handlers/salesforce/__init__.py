"""Salesforce handler package — public API matches former salesforce_handler module."""

from handlers.salesforce.common import (
    _clarification_stream_chunks,
    _sanitize_soql_query,
    _dedupe_preserve_order,
    _validate_soql_fields,
    _build_invalid_field_clarification,
    _salesforce_clarification_response,
    _serialize_salesforce_value,
    _clean_salesforce_records,
    _execute_salesforce_query,
    _fetch_salesforce_records,
    _sanitize_soql_for_bulk_api,
    _parse_soql_from_prompt,
    _is_field_related_clarification,
    _extract_field_names_from_clarification,
    _parse_invalid_field_from_soql_error,
    _remove_field_from_soql,
    _normalize_bulk_api_query_records,
    _fetch_salesforce_records_with_bulk_api,
)

from handlers.salesforce.map import (
    build_salesforce_context_for_prompt,
    analyze_mapping_transformations,
    call_salesforce_architect_agent,
    categorize_salesforce_objects_in_batches,
    detect_salesforce_org_from_prompt,
    _object_api_names_from_salesforce_mapping,
    map_to_salesforce,
)

from handlers.salesforce.extract import (
    _sf_extract_abort_payload,
    _sf_persisted_extraction_metadata,
    handle_salesforce_data_extraction_stream,
    handle_salesforce_metadata_deploy_stream,
    handle_salesforce_request_stream,
    start_salesforce_extraction_job,
    _process_salesforce_extraction_job,
    determine_salesforce_object_and_fields,
    select_relevant_salesforce_object,
    _field_selection_fallback_result,
    determine_fields_for_salesforce_object,
    _sf_extract_classification_context,
    generate_salesforce_soql_query,
    _coerce_parsed_json_to_record_list,
    _parse_salesforce_ai_extraction_json,
    extract_structured_data_from_salesforce,
)

__all__ = ['_build_invalid_field_clarification', '_clarification_stream_chunks', '_clean_salesforce_records', '_coerce_parsed_json_to_record_list', '_dedupe_preserve_order', '_execute_salesforce_query', '_extract_field_names_from_clarification', '_fetch_salesforce_records', '_fetch_salesforce_records_with_bulk_api', '_field_selection_fallback_result', '_is_field_related_clarification', '_normalize_bulk_api_query_records', '_object_api_names_from_salesforce_mapping', '_parse_invalid_field_from_soql_error', '_parse_salesforce_ai_extraction_json', '_parse_soql_from_prompt', '_process_salesforce_extraction_job', '_remove_field_from_soql', '_salesforce_clarification_response', '_sanitize_soql_for_bulk_api', '_sanitize_soql_query', '_serialize_salesforce_value', '_sf_extract_abort_payload', '_sf_extract_classification_context', '_sf_persisted_extraction_metadata', '_validate_soql_fields', 'analyze_mapping_transformations', 'build_salesforce_context_for_prompt', 'call_salesforce_architect_agent', 'categorize_salesforce_objects_in_batches', 'detect_salesforce_org_from_prompt', 'determine_fields_for_salesforce_object', 'determine_salesforce_object_and_fields', 'extract_structured_data_from_salesforce', 'generate_salesforce_soql_query', 'handle_salesforce_data_extraction_stream', 'handle_salesforce_metadata_deploy_stream', 'handle_salesforce_request_stream', 'map_to_salesforce', 'select_relevant_salesforce_object', 'start_salesforce_extraction_job']
