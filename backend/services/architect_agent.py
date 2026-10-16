"""Archian AI architect agent (formerly salesforce_architect_agent)."""

import json
import re
import os
import asyncio
from datetime import datetime
from dotenv import load_dotenv
import pandas as pd
from typing import Dict, Any, Union, Optional, List
from utils.openai_helpers import parse_openai_json_response, fix_malformed_json
from utils.ocr_utils import extract_text_from_image
from db.connection import database
from utils.document_utils import get_dynamic_document_type_categories
from uuid import UUID


## Always load .env so local development values override any inherited placeholder
load_dotenv(override=True)

# Import handlers
from handlers.google_sheets_handler import (
    handle_google_sheets_extraction_stream,
    handle_google_sheets_info_stream,
    handle_google_sheets_create_from_prompt_stream,
    detect_google_sheet_from_prompt,
    process_google_sheets_extraction_background,
    store_google_spreadsheet_metadata
)
from handlers.sql_handler import (
    handle_sql_data_extraction_stream,
    detect_sql_connection_from_prompt,
    get_sql_connector_with_tables,
    determine_sql_table_and_query,
    execute_sql_query_and_stream,
    extract_structured_data_from_sql,
    ask_for_sql_connection_clarification,
    select_relevant_tables,
    generate_sql_query_for_tables
)
from handlers.file_handler import (
    apply_filter_rules,
    apply_deduplication_rules,
    apply_cleanup_and_validation_rules,
    apply_transformations,
    extract_tables_from_pdf_with_textract,
    extract_text_from_pdf_with_textract
)
from handlers.prompt_handler import PromptBuilder
from handlers.salesforce_handler import (
    handle_salesforce_request_stream,
    handle_salesforce_data_extraction_stream,
    handle_salesforce_metadata_deploy_stream,
    build_salesforce_context_for_prompt,
    call_salesforce_architect_agent,
    categorize_salesforce_objects_in_batches
)
from handlers.general_ai_handler import (
    handle_general_ai_response_stream
)
from handlers.classification_handler import (
    classify_source_destination
)
from handlers.sql_handler import (
    handle_sql_table_info_stream,
)


def get_full_conversation_context(conversation_history: list, max_tokens: int = 80000) -> str:
    """
    Get full conversation history with smart truncation
    Use this when we want maximum context for better AI decisions
    """
    if not conversation_history:
        return ""
    
    # Use complete conversation without truncation
    conversation_context = "\n\nFull Conversation History:\n"
    for i, msg in enumerate(conversation_history):
        msg_type = msg.get('type', 'unknown')
        msg_text = msg.get('text', '')
        
        # Include metadata if available (helps AI understand source context)
        metadata = msg.get('metadata')
        metadata_str = ""
        if metadata and isinstance(metadata, dict):
            metadata_parts = []
            if metadata.get('source_type'):
                metadata_parts.append(f"source_type: {metadata.get('source_type')}")
            if metadata.get('source_name'):
                metadata_parts.append(f"source_name: {metadata.get('source_name')}")
            if metadata.get('destination'):
                metadata_parts.append(f"destination: {metadata.get('destination')}")
            if metadata.get('destination_name'):
                metadata_parts.append(f"destination_name: {metadata.get('destination_name')}")
            if metadata_parts:
                metadata_str = f" [Metadata: {', '.join(metadata_parts)}]"
        
        conversation_context += f"{i+1}. {msg_type}: {msg_text}{metadata_str}\n"
    
    return conversation_context


def create_optimized_schema(schema_data: dict) -> dict:
    """
    Create optimized schema by removing ID columns and unnecessary metadata
    """
    if not schema_data.get('columns'):
        return {}
    
    optimized_columns = {}
    for col in schema_data['columns']:
        column_name = col.get('column_name', '')
        data_type = col.get('data_type', '')
        
        # Only include column name and data type (KEEP IDs to enable JOINs)
        if column_name and data_type:
            optimized_columns[column_name] = data_type
    
    return optimized_columns




def convert_datetime_for_json(obj):
    """
    Convert datetime objects to ISO format strings for JSON serialization
    Handles pandas Timestamp, datetime, and other datetime-like objects
    """
    if pd.isna(obj):
        return ""
    elif hasattr(obj, 'isoformat'):  # datetime, pandas.Timestamp
        return obj.isoformat()
    elif hasattr(obj, 'strftime'):  # other datetime-like objects
        return obj.strftime('%Y-%m-%d %H:%M:%S')
    else:
        return str(obj)


def safe_json_serialize(data):
    """
    Safely serialize data to JSON by converting any datetime objects
    """
    def convert_datetime(obj):
        if hasattr(obj, 'isoformat'):
            return obj.isoformat()
        elif hasattr(obj, 'strftime'):
            return obj.strftime('%Y-%m-%d %H:%M:%S')
        else:
            return obj
    
    # Convert the data structure recursively
    if isinstance(data, dict):
        return {k: convert_datetime(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [convert_datetime(item) for item in data]
    elif hasattr(data, 'isoformat') or hasattr(data, 'strftime'):
        return convert_datetime(data)
    else:
        return data


async def get_user_extraction_limit(user_id: str) -> Dict[str, Any]:
    """Get user's extraction limit based on credit balance (all plans).
    can_extract = records affordable with current balance (extraction fee + 1 credit per record).
    show_upgrade_option = True when balance is low or can_extract is 0."""
    try:
        from services.entitlements import UNLIMITED_EXTRACTION_RECORDS, get_entitlements
        from utils.credit_config import (
            CREDIT_PER_EXTRACTION,
            affordable_extraction_record_count,
            LOW_CREDIT_UPGRADE_THRESHOLD,
        )

        entitlements = get_entitlements()
        if not entitlements.billing_enabled:
            return {
                "can_extract": UNLIMITED_EXTRACTION_RECORDS,
                "plan_type": entitlements.edition,
                "extraction_cap": None,
                "already_migrated": 0,
                "remaining_records": UNLIMITED_EXTRACTION_RECORDS,
                "limit_reached": False,
                "credit_balance": 0,
                "remaining_credits": 0,
                "show_upgrade_option": False,
            }

        user_plan = await entitlements.get_user_plan(user_id)
        if not user_plan:
            return {
                "can_extract": 0,
                "reason": "No active plan found",
                "limit_reached": True,
                "credit_balance": 0,
                "remaining_credits": 0,
                "show_upgrade_option": True,
            }
        
        balance = await entitlements.get_balance(user_id)
        current_usage = await entitlements.get_user_usage(user_id)
        already_migrated = (current_usage or {}).get("records_migrated") or 0
        
        # Credit-based only: how many records can user afford? (extraction fee + 1 credit per record)
        # No hardcoded cap — user can extract as many records as their credit balance allows
        EXTRACTION_CAP = balance if balance and balance > 0 else 0
        can_extract = affordable_extraction_record_count(balance, cap=EXTRACTION_CAP)
        limit_reached = can_extract <= 0
        show_upgrade = limit_reached or (balance is not None and balance < LOW_CREDIT_UPGRADE_THRESHOLD)
        
        return {
            "can_extract": can_extract,
            "plan_type": user_plan.get("plan_type", ""),
            "extraction_cap": EXTRACTION_CAP,
            "already_migrated": already_migrated,
            "remaining_records": can_extract,
            "limit_reached": limit_reached,
            "credit_balance": balance,
            "remaining_credits": balance,
            "show_upgrade_option": show_upgrade,
        }
        
    except Exception as e:
        print(f"❌ Error getting user extraction limit: {e}")
        return {
            "can_extract": 0,
            "reason": f"Error: {str(e)}",
            "limit_reached": True,
            "credit_balance": 0,
            "remaining_credits": 0,
            "show_upgrade_option": True,
        }


# Helper for LLM calls — resolves provider, model and API key from the DB task config.
# ``client`` is accepted for backwards compatibility and ignored.
async def async_openai_call(client=None, *, task=None, **kwargs):
    if not task:
        raise ValueError("async_openai_call requires a task key")

    from services.llm.service import llm_complete_openai_compat

    messages = kwargs.pop("messages", None) or []
    return await llm_complete_openai_compat(
        task,
        messages=messages,
        temperature=kwargs.pop("temperature", None),
        max_tokens=kwargs.pop("max_tokens", None),
        user_id=kwargs.pop("user_id", None),
        job_id=kwargs.pop("job_id", None),
        conversation_id=kwargs.pop("conversation_id", None),
        **kwargs,
    )


async def generate_extraction_rules_from_prompt(
    extraction_goals: str,
    sample_data: List[Dict[str, Any]],
    user_id: str,
    destination: str = "salesforce",
    destination_name: Optional[str] = None,
    source: str = "file",
    progress_callback=None
) -> Dict[str, Any]:
    """
    Generate extraction rules (filter_rules, deduplication_rules, transformation_rules, 
    data_cleanup_rules) from user prompt using OpenAI.
    
    This function extracts the rule generation logic from extract_structured_data_from_file
    so it can be reused in cleanup flows.
    
    Args:
        extraction_goals: User's extraction/cleanup goals/requirements
        sample_data: Sample data records (first 5 records recommended)
        user_id: User ID
        destination: Target destination ("salesforce", "google_sheets", etc.)
        destination_name: Specific destination name
        source: Data source type ("file", "google_sheets", "sql", "salesforce")
        progress_callback: Optional progress callback function
        
    Returns:
        Dictionary with extraction_rules structure:
        {
            "filter_rules": {...},
            "deduplication_rules": {...},
            "transformation_rules": {...},
            "data_cleanup_rules": {...},
            "mapping_context_summary": "..."
        }
    """
    try:
        from handlers.prompt_handler import PromptBuilder
        
        # Get user's extraction limit (needed for plan_limit_instruction)
        # Note: get_user_extraction_limit is defined in this same file
        extraction_limit = await get_user_extraction_limit(user_id)
        
        # Build plan limit instruction
        plan_limit_instruction = PromptBuilder.build_plan_limit_instruction(extraction_limit)
        
        # Build extraction prompt using PromptBuilder
        instruction = await PromptBuilder.build_extraction_prompt(
            destination=destination,
            destination_name=destination_name,
            extraction_goals=extraction_goals,
            sample_data=safe_json_serialize(sample_data),
            plan_limit_instruction=plan_limit_instruction,
            user_id=str(user_id),
            source=source
        )
        
        if progress_callback:
            progress_callback(50, "Generating cleanup rules with AI...")
        
        # Call OpenAI to generate rules
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.EXTRACTION_RULES,
            messages=[{"role": "user", "content": instruction}],
            temperature=0,
        )
        
        reply = response.choices[0].message.content
        
        if progress_callback:
            progress_callback(80, "Processing AI response...")
        
        # Parse OpenAI response
        flat_data = parse_openai_json_response(reply)
        
        if isinstance(flat_data, dict):
            flat_data_list = [flat_data]
        elif isinstance(flat_data, list):
            flat_data_list = [item for item in flat_data if isinstance(item, dict)]
        else:
            raise ValueError("AI service returned an unexpected format — not a dict or list.")
        
        # Extract rules and metadata from OpenAI response
        extraction_rules = {}
        mapping_context_summary = None
        document_type = None
        document_description = None
        object_name = None
        
        if flat_data_list and len(flat_data_list) > 0:
            first_record = flat_data_list[0]
            
            # Extract document_type, document_description, and object_name (similar to extract_structured_data_from_file)
            # These are destination-specific and should match the new destination if destination was overridden
            for record in flat_data_list:
                if "document_type" in record:
                    document_type = record.pop("document_type", None)
                    document_description = record.pop("document_description", None)
                    object_name = record.pop("object_name", None)
                    break
            
            # Extract filter_rules
            filter_rules = first_record.get("filter_rules")
            if filter_rules:
                extraction_rules["filter_rules"] = filter_rules
            
            # Extract deduplication_rules
            deduplication_rules = first_record.get("deduplication_rules")
            if deduplication_rules:
                extraction_rules["deduplication_rules"] = deduplication_rules
            
            # Extract transformation_rules
            transformation_rules = first_record.get("transformation_rules")
            if transformation_rules:
                extraction_rules["transformation_rules"] = transformation_rules
            
            # Extract data_cleanup_rules
            data_cleanup_rules = first_record.get("data_cleanup_rules")
            if data_cleanup_rules:
                extraction_rules["data_cleanup_rules"] = data_cleanup_rules
            
            # Extract mapping_context_summary
            mapping_context_summary = first_record.get("mapping_context_summary")
            if mapping_context_summary:
                extraction_rules["mapping_context_summary"] = mapping_context_summary
            
            # Include document_type, document_description, and object_name in return value
            if document_type:
                extraction_rules["document_type"] = document_type
            if document_description:
                extraction_rules["document_description"] = document_description
            if object_name:
                extraction_rules["object_name"] = object_name
            if not document_type and not document_description:
                print(f"⚠️ [RULE GENERATION] No document_type or document_description found in OpenAI response")
        
        return extraction_rules
        
    except Exception as e:
        print(f"⚠️ Error generating extraction rules from prompt: {e}")
        import traceback
        traceback.print_exc()
        # Return empty rules on error (fallback to existing rules)
        return {}

async def extract_structured_data_from_file(
    local_file_path,
    user_id,
    extraction_goals="",
    progress_callback=None,
    destination="salesforce",
    destination_name=None,
    source="file",
    saved_extraction_rules=None,
    source_name=None,
    extraction_profile=None,
    intent=None,
    template=None,
):
    import asyncio

    try:
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
            source=source,
        )

        ext = os.path.splitext(local_file_path)[1].lower()

        # ✅ Handle spreadsheet files
        if ext in [".csv", ".xlsx", ".txt", ".jpg", ".jpeg", ".png", ".pdf"]:
            # Get user's extraction limit
            extraction_limit = await get_user_extraction_limit(user_id)

            
            # Track original file record count for plan limit detection
            original_file_records = 0
            
            if ext == ".csv":
                df = await asyncio.to_thread(pd.read_csv, local_file_path)
                # 🧹 Filter out empty rows before sending to OpenAI
                df = df.dropna(how='all')
                original_file_records = len(df)
                # ⭐ DON'T apply plan limits yet - we need to filter first
                # Convert ALL data to records (filtering will happen after OpenAI analyzes it)
                real_data = await asyncio.to_thread(
                    lambda d=df: d.fillna("").map(convert_datetime_for_json).to_dict(orient="records")
                )
                # Use real data for OpenAI extraction analysis (limit to 5 records for token management)
                # Data is only sent for analysis, not stored, and data sharing with OpenAI is disabled
                sample_data = real_data[:5] if len(real_data) > 5 else real_data

            elif ext == ".xlsx":
                df = await asyncio.to_thread(pd.read_excel, local_file_path)
                # 🧹 Filter out empty rows before sending to OpenAI
                df = df.dropna(how='all')
                original_file_records = len(df)
                # ⭐ DON'T apply plan limits yet - we need to filter first
                # Convert ALL data to records (filtering will happen after OpenAI analyzes it)
                real_data = await asyncio.to_thread(
                    lambda d=df: d.fillna("").map(convert_datetime_for_json).to_dict(orient="records")
                )
                # Use real data for OpenAI extraction analysis (limit to 5 records for token management)
                # Data is only sent for analysis, not stored, and data sharing with OpenAI is disabled
                sample_data = real_data[:5] if len(real_data) > 5 else real_data

            elif ext == ".txt":
                def _read_txt():
                    with open(local_file_path, "r", encoding="utf-8") as f:
                        return f.read()
                txt_content = await asyncio.to_thread(_read_txt)
                real_data = [{"raw_text": txt_content}]
                # Use real data for OpenAI extraction analysis
                # Data is only sent for analysis, not stored, and data sharing with OpenAI is disabled
                sample_data = real_data
                original_file_records = 1  # Text files are treated as single record

            elif ext in [".jpg", ".jpeg", ".png"]:
                # Try to extract tables first using AWS Textract (same as PDF) - run in thread to avoid blocking event loop
                table_data = await asyncio.to_thread(extract_tables_from_pdf_with_textract, local_file_path)
                if table_data and len(table_data) > 0:
                    # Image contains structured table data - treat like CSV/Excel
                    original_file_records = len(table_data)
                    
                    # ⭐ DON'T apply plan limits yet - we need to filter first
                    # Use ALL table data (filtering will happen after OpenAI analyzes it)
                    real_data = table_data
                    # Use real data for OpenAI extraction analysis (limit to 5 records for token management)
                    # Data is only sent for analysis, not stored, and data sharing with OpenAI is disabled
                    sample_data = real_data[:5] if len(real_data) > 5 else real_data
                else:
                    # No tables found, fall back to text extraction - run in thread to avoid blocking
                    from utils.ocr_utils import extract_text_from_image
                    text_content = await asyncio.to_thread(extract_text_from_image, local_file_path)
                    real_data = [{"raw_text": text_content}]
                    # Use real data for OpenAI extraction analysis
                    # Data is only sent for analysis, not stored, and data sharing with OpenAI is disabled
                    sample_data = real_data
                    original_file_records = 1  # Image files are treated as single record
                
            elif ext == ".pdf":
                # Try to extract tables first using AWS Textract - run in thread to avoid blocking event loop
                table_data = await asyncio.to_thread(extract_tables_from_pdf_with_textract, local_file_path)
                if table_data and len(table_data) > 0:
                    # PDF contains structured table data - treat like CSV/Excel
                    original_file_records = len(table_data)
                    
                    # ⭐ DON'T apply plan limits yet - we need to filter first
                    # Use ALL table data (filtering will happen after OpenAI analyzes it)
                    real_data = table_data
                    # Use real data for OpenAI extraction analysis (limit to 5 records for token management)
                    # Data is only sent for analysis, not stored, and data sharing with OpenAI is disabled
                    sample_data = real_data[:5] if len(real_data) > 5 else real_data
                else:
                    # No tables found, fall back to text extraction - run in thread to avoid blocking
                    real_text = await asyncio.to_thread(extract_text_from_pdf_with_textract, local_file_path)
                    real_data = [{"raw_text": real_text}]
                    # Use real data for OpenAI extraction analysis
                    # Data is only sent for analysis, not stored, and data sharing with OpenAI is disabled
                    sample_data = real_data
                    original_file_records = 1
            
            # 🔄 Check if chunking is needed for large datasets
            CHUNK_THRESHOLD = 200  # Start chunking for files with more than 200 records

            if len(sample_data) > CHUNK_THRESHOLD and ext in [".csv", ".xlsx"]:

                return await process_large_file_in_chunks(
                    sample_data, user_id, extraction_goals, 
                    extraction_limit, original_file_records, progress_callback
                )
            else:
                # Process as single file (not chunked)
                # Update progress for single file processing
                if progress_callback:
                    progress_callback(25, "Preparing file for processing...")
                    await asyncio.sleep(0.3)
        
            # Build plan limit instruction
            plan_limit_instruction = PromptBuilder.build_plan_limit_instruction(extraction_limit)
            
            # Build extraction prompt using PromptBuilder
            instruction = await PromptBuilder.build_extraction_prompt(
                destination=destination,
                destination_name=destination_name,
                extraction_goals=extraction_goals,
                sample_data=safe_json_serialize(sample_data),
                plan_limit_instruction=plan_limit_instruction,
                user_id=str(user_id),
                source=source,
                source_name=source_name,
                extraction_profile=profile.value,
            )

            # Update progress for single file processing
            if progress_callback:
                progress_callback(50, "Processing with OpenAI...")
                await asyncio.sleep(0.5)  # Small delay for frontend to catch progress
            
            from services.llm import tasks as llm_tasks

            response = await async_openai_call(
                None,
                task=llm_tasks.FILE_EXTRACTION,
                messages=[{"role": "user", "content": instruction}],
                temperature=0,
            )

            reply = response.choices[0].message.content
            
            # Update progress after OpenAI call
            if progress_callback:
                progress_callback(80, "Processing OpenAI response...")
                await asyncio.sleep(0.5)  # Small delay for frontend to catch progress
            
            flat_data = parse_openai_json_response(reply)

            if isinstance(flat_data, dict):
                flat_data_list = [flat_data]
            elif isinstance(flat_data, list):
                flat_data_list = [item for item in flat_data if isinstance(item, dict)]
            else:
                raise ValueError("AI service returned an unexpected format — not a dict or list.")

            document_type = None
            document_description = None
            object_name = None
            for record in flat_data_list:
                if "document_type" in record:
                    document_type = record.pop("document_type")
                    document_description = record.pop("document_description")
                    object_name = record.pop("object_name")
                    break
            
            # Extract destination_name from classification if it was None or "unknown"
            # Only extract from object_name when destination is google_sheets
            if (destination_name == None or destination_name == "unknown") and destination == "google_sheets":
                # Use object_name if available
                if object_name:
                    destination_name = object_name
            
            # Clean up metadata from ALL records - metadata is only for internal processing
            for record in flat_data_list:
                record.pop("object_name", None)
                record.pop("document_type", None)
                record.pop("document_description", None)

            if not document_type:
                document_type = "Spreadsheet"

            # Use credit-based upgrade flag when present, else derive from limit
            show_upgrade_option = extraction_limit.get(
                "show_upgrade_option",
                extraction_limit.get("limit_reached", False),
            )
            
            # Final progress update for single file processing
            if progress_callback:
                progress_callback(95, "Processing complete")
                await asyncio.sleep(0.3)
            
            # Check if we have structured data (dictionaries with real field names, not just raw_text)
            is_structured_data = False
            if real_data and len(real_data) > 0:
                first_record_keys = set(real_data[0].keys())
                # Structured if it has keys other than 'raw_text'
                is_structured_data = len(first_record_keys - {'raw_text'}) > 0
            
            # ⭐ FILTERING LOGIC - Apply user-specified filter criteria BEFORE plan limits
            # This ensures we get the most relevant records within plan limits
            if is_structured_data and real_data:
                # Use saved extraction rules if provided, otherwise extract from OpenAI's response
                filter_rules = None
                deduplication_rules = None
                transformation_rules = None
                
                if saved_extraction_rules:
                    # Use saved extraction rules (from flow rerun)
                    filter_rules = saved_extraction_rules.get("filter_rules")
                    deduplication_rules = saved_extraction_rules.get("deduplication_rules")
                    transformation_rules = saved_extraction_rules.get("transformation_rules")
                else:
                    # Extract filter rules from OpenAI's response
                    if flat_data_list and len(flat_data_list) > 0:
                        filter_rules = flat_data_list[0].get("filter_rules")
                        deduplication_rules = flat_data_list[0].get("deduplication_rules")
                
                if profile_allows_pre_openai_rule_apply(profile):
                    if saved_extraction_rules and transformation_rules:
                        real_data = apply_transformations(real_data, transformation_rules)
                    if filter_rules and filter_rules.get("conditions"):
                        real_data = apply_filter_rules(real_data, filter_rules)
                    if deduplication_rules and deduplication_rules.get("enabled"):
                        real_data = apply_deduplication_rules(real_data, deduplication_rules)
                
                # ⭐ NOW apply plan limits to the filtered and deduplicated data
                if extraction_limit["can_extract"] > 0:
                        real_data = real_data[:extraction_limit["can_extract"]]
                else:
                    return [], "Credit Limit Reached", None, "Insufficient credits for extraction. Add credits or upgrade your plan.", extraction_limit, 0, extraction_limit.get("show_upgrade_option", True)

            # Apply cleanup and validation rules to real customer data
            # For structured data (CSV/Excel/PDF tables), apply rules to real data
            # For unstructured data (PDF text/TXT/Images), return AI's structured output directly
            # Initialize all extraction rules outside conditional to ensure they're always defined
            transformation_rules = None
            filter_rules = None
            deduplication_rules = None
            
            # Use saved extraction rules if provided, otherwise extract from OpenAI's response
            if saved_extraction_rules:
                # Use saved extraction rules (from flow rerun)
                transformation_rules = saved_extraction_rules.get("transformation_rules")
                filter_rules = saved_extraction_rules.get("filter_rules")
                deduplication_rules = saved_extraction_rules.get("deduplication_rules")
            else:
                # Extract extraction rules from OpenAI's response
                if flat_data_list and len(flat_data_list) > 0:
                    transformation_rules = flat_data_list[0].get("transformation_rules")
                    filter_rules = flat_data_list[0].get("filter_rules")
                    deduplication_rules = flat_data_list[0].get("deduplication_rules")
            
            # Extract extraction_rules before they're removed by apply_extraction_rules_to_structured_data
            extraction_rules = {}
            # Extract mapping context summary from first record
            mapping_context_summary = None
            if flat_data_list and len(flat_data_list) > 0:
                first_record = flat_data_list[0]
                mapping_context_summary = first_record.get("mapping_context_summary")
            
            # Build extraction_rules dict - use saved rules if available, otherwise use OpenAI rules
            if saved_extraction_rules:
                # Use saved extraction rules (from flow rerun)
                extraction_rules = saved_extraction_rules.copy()
            else:
                # Extract from OpenAI response
                if filter_rules:
                    extraction_rules["filter_rules"] = filter_rules
                if deduplication_rules:
                    extraction_rules["deduplication_rules"] = deduplication_rules
                if transformation_rules:
                    extraction_rules["transformation_rules"] = transformation_rules

            if is_structured_data:
                source_schema = sorted(
                    {k for r in real_data[:50] if isinstance(r, dict) for k in r.keys()}
                )
                extraction_rules = sanitize_extraction_rules_for_profile(
                    extraction_rules, profile, source_schema=source_schema
                )

                from utils.extraction_utils import apply_extraction_rules_to_structured_data
                if saved_extraction_rules:
                    # Create mock extracted_data with saved rules for the utility function
                    mock_extracted_data = [saved_extraction_rules.copy()]
                    # Remove transformation_rules if they were already applied early (to prevent double transformation)
                    if transformation_rules and "transformation_rules" in mock_extracted_data[0]:
                        del mock_extracted_data[0]["transformation_rules"]
                    # Also include mapping_context_summary if available
                    if mapping_context_summary:
                        mock_extracted_data[0]["mapping_context_summary"] = mapping_context_summary
                    processed_real_data = apply_extraction_rules_to_structured_data(
                        real_data,
                        mock_extracted_data,
                        extraction_profile=profile,
                        saved_extraction_rules=saved_extraction_rules,
                        prompt=extraction_goals,
                        template=template,
                        intent=intent,
                        source=source,
                    )
                else:
                    processed_real_data = apply_extraction_rules_to_structured_data(
                        real_data,
                        flat_data_list,
                        extraction_profile=profile,
                        prompt=extraction_goals,
                        template=template,
                        intent=intent,
                        source=source,
                    )
            else:
                # For PDF/TXT/Images, apply transformation rules to filter/transform data
                if real_data and len(real_data) > 0:
                    real_text = real_data[0].get("raw_text", "")
                    
                    if transformation_rules:
                        # Apply transformation rules to filter/transform data
                        processed_real_data = apply_transformations([{"raw_text": real_text}], transformation_rules)
                        
                        # Filter to only include fields from OpenAI's sample schema
                        if flat_data_list and len(flat_data_list) > 0:
                            openai_schema_fields = [key for key in flat_data_list[0].keys() 
                                                   if key not in ['object_name', 'document_type', 'document_description', 
                                                                     'data_cleanup_rules', 'transformation_rules', 'filter_rules', 'deduplication_rules']]
                            
                            filtered_data = []
                            for record in processed_real_data:
                                filtered_record = {field: record.get(field, "") 
                                                 for field in openai_schema_fields 
                                                 if field in record}
                                filtered_data.append(filtered_record)
                            
                            processed_real_data = filtered_data
                    else:
                        # Fallback to old method if no transformation rules
                        processed_real_data = apply_ai_structure_to_real_data(real_text, flat_data_list)
                else:
                    processed_real_data = flat_data_list
                
                # Remove metadata fields from first record for unstructured data path
                if processed_real_data and isinstance(processed_real_data, list) and len(processed_real_data) > 0:
                    if "data_cleanup_rules" in processed_real_data[0]:
                        del processed_real_data[0]["data_cleanup_rules"]
                    if "transformation_rules" in processed_real_data[0]:
                        del processed_real_data[0]["transformation_rules"]
                    if "filter_rules" in processed_real_data[0]:
                        del processed_real_data[0]["filter_rules"]
                    if "deduplication_rules" in processed_real_data[0]:
                        del processed_real_data[0]["deduplication_rules"]
                    if "mapping_context_summary" in processed_real_data[0]:
                        del processed_real_data[0]["mapping_context_summary"]


            return processed_real_data, document_type, None, document_description, extraction_limit, original_file_records, show_upgrade_option, object_name, destination_name, extraction_rules, mapping_context_summary

    except Exception as e:
        print("File extraction error:", e)
        
        # Provide user-friendly error messages for common AI service issues
        error_message = str(e)
        if "503" in error_message or "Service Temporarily Unavailable" in error_message:
            raise Exception("AI service is temporarily unavailable. Please try again in a few minutes.")
        elif "502" in error_message or "Bad gateway" in error_message:
            raise Exception("AI processing service is experiencing issues. Please try again later.")
        elif "429" in error_message or "rate limit" in error_message.lower():
            raise Exception("Rate limit exceeded. Please wait a moment before trying again.")
        elif "401" in error_message or "unauthorized" in error_message.lower():
            raise Exception("Authentication failed. Please refresh the page and try again.")
        elif "timeout" in error_message.lower():
            raise Exception("Request timed out. Please try again with a smaller file.")
        elif "connection" in error_message.lower():
            raise Exception("Network connection error. Please check your internet connection and try again.")
        else:
            raise e


async def handle_text_prompt_for_architect_agent(prompt: str, user_id) -> Union[dict, str]:
    """
    Archian AI architect agent that replies to natural prompts about object/field suggestions, architecture, or best practices.
    """
    try:
        # Get enhanced context with relevant Salesforce objects and fields
        enhanced_context = await build_salesforce_context_for_prompt(prompt, user_id)
        
        categories = await get_dynamic_document_type_categories(user_id, destination="salesforce")
        category_list = json.dumps(categories)
        system_prompt = f"""
Hey there! I'm Archian AI, your friendly Salesforce data pipeline expert! 😊

I'm here to help you make sense of your data and get it perfectly organized in Salesforce. Think of me as your smart friend who happens to know everything about Salesforce data structures and can help you migrate data from pretty much anywhere!

Here's what I can do for you:
• Help you understand where your data belongs in Salesforce (I love explaining this stuff!)
• Work with files like spreadsheets, PDFs, forms, and images
• Connect directly to your SQL databases (MySQL, PostgreSQL, Microsoft SQL Server, Oracle, BigQuery)
• Automatically extract, map, and migrate your data to the right Salesforce objects

I was created by Kunal Fule and his team with one simple mission: making data pipelines as easy as possible for everyone. No more headaches trying to figure out where your customer data should go!

When you're not sure about something, just ask! I'll ask a quick clarifying question and then guide you to either upload a file or connect to your database. From there, I'll handle all the technical stuff so you don't have to worry about it.

I always use official Salesforce best practices and prefer standard objects when possible (they're usually the best fit). I'll explain things clearly, often using simple analogies like "Accounts are like households, Contacts are the people living in them" to make everything crystal clear.

IMPORTANT: Always focus on what I can do for you, not generic Salesforce advice. When you ask about building a data pipeline or migrating records, I'll explain that I can handle it automatically - I won't mention other tools like Data Import Wizard, Data Loader, MuleSoft, etc. I'm here to do the work for you!

If users ask about other tools, confidently explain what they do and then highlight how much easier, more efficient, and better Archian AI is. Tell them we've made data pipelines and data sync as simple as talking to a person - we're the best solution for making pipeline work feel natural and effortless!

Ready to get started? Just tell me what you're working with! 🚀
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

**Example Structure:**
🔹 **Lead Object (Sales)**
📋 **Purpose**: Primary object for storing sales prospect data
📊 **Key Fields**:
   • Company (Required String) - Store the prospect's company name
   • Last Name (Required String) - Contact's last name
   • Email (Optional Email) - Contact's email address
   • Industry (Optional Picklist) - Business industry classification
   • Status (Required Picklist) - Current lead status
   • Is Appointiv Lead (Required Boolean) - Custom field for your business

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

Respond ONLY with valid JSON. Add your explanation, commentary, sample example and ask user if they "Would you like me to explain how this data is structured in Salesforce, including which fields belong to which object and how they're connected? Just say 'Yes' and I'll show you the full breakdown." inside the "text_response" key.

The "text_response" should sound like a helpful Salesforce consultant, using analogies if needed, and encourage the user to upload a file for Archian AI to help further.

**IMPORTANT:** Your response must be as detailed and helpful as ChatGPT, using the exact Salesforce metadata I've provided to give precise, technical guidance. Don't give generic advice - use the specific objects and fields from the context.

**FINAL INSTRUCTION:** Provide comprehensive, detailed responses with all the information upfront. Structure your response logically with clear sections for each Salesforce object, but keep formatting simple - the frontend will handle the styling. 

**CRITICAL:**
- Do NOT ask follow-up questions - provide ALL information upfront since chat history is not maintained
- Do NOT include phrases like "Would you like me to explain..." or "Just say 'Yes'..."
- Give complete, comprehensive responses that cover all aspects of the user's question
- End with clear next steps for file upload, not with questions
- Keep formatting clean and simple - no special characters or complex formatting

**Example Response Style:**
**Lead Object (Sales)**
**Purpose**: Primary object for storing sales prospect data and nurturing potential customers
**Key Fields Breakdown:**

• **Company** (Required String) - Store the prospect's company name for business identification
• **Last Name** (Required String) - Contact person's last name for individual identification  
• **Email** (Optional Email) - Contact's email address for communication
• **Industry** (Optional Picklist) - Business industry classification for segmentation
• **Status** (Required Picklist) - Current lead status (New, Contacted, Qualified, etc.)
• **Is Appointiv Lead** (Required Boolean) - Custom field indicating if lead came from Appointiv
• **Owner ID** (Required Reference) - Salesforce user responsible for the lead
• **Converted** (Required Boolean) - Tracks if lead has been converted to opportunity

**Opportunity Object (Sales)**  
**Purpose**: Track potential revenue-generating deals and sales opportunities
**Key Fields Breakdown:**

• **Name** (Required String) - Opportunity name/description
• **Stage** (Required Picklist) - Sales stage (Prospecting, Qualification, Proposal, etc.)
• **Close Date** (Required Date) - Expected close date for the deal
• **Amount** (Optional Currency) - Potential deal value
• **Probability** (Optional Percent) - Likelihood of closing the deal

**Account Object (Account Management)**
**Purpose**: Store company/organization information
**Key Fields Breakdown:**

• **Account Name** (Required String) - Company name
• **Type** (Optional Picklist) - Account type (Customer, Prospect, Partner, etc.)
• **Industry** (Optional Picklist) - Business industry
• **Billing Address** (Optional Text) - Company billing location

**Best Practices:**
1. **Lead Management**: Always capture Company and Last Name as minimum required data
2. **Data Quality**: Use Industry picklist for consistent business classification
3. **Conversion Process**: Convert qualified leads to opportunities when they show buying intent
4. **Custom Fields**: Leverage "Is Appointiv Lead" to track lead source effectiveness

**Object Relationships:**
- **Lead** → **Opportunity** (when converted)
- **Opportunity** → **Account** (company information)
- **Opportunity** → **Contact** (individual contact details)

**Next Steps:**
Archian AI can handle this automatically — just upload your file or connect to your database and we'll take care of the rest. We'll extract, map, and migrate your data to the appropriate Salesforce objects with proper field mappings.

Just return the JSON block below.

Do NOT include markdown formatting like ```json — only plain JSON.

Here is the required format:
{{
  "intent": "analyze" or "migrate",
  "category": "most relevant Salesforce category from the context",
  "category_description": "description from the context", 
  "reasoning": "detailed technical explanation using specific objects/fields from context",
  "text_response": "comprehensive, ChatGPT-style response with specific Salesforce details, field examples, and actionable guidance. Provide ALL the detailed information upfront - do NOT ask follow-up questions since chat history is not maintained. Give complete explanations with field breakdowns, best practices, and relationships in one comprehensive response."
}}

If the prompt is vague (e.g., just says "hi"), still return valid JSON and ask a clarifying question in "text_response".
"""

        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.SALESFORCE_INFO,
            messages=[
                {
                    "role": "system",
                    "content": (system_prompt)
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.5
        )
        reply = response.choices[0].message.content.strip()
        # print('reply 1', reply)  # Commented out debug print
        parsed_reply = parse_openai_json_response(reply)
        # print('parsed_reply 1', parsed_reply)  # Commented out debug print
        return parsed_reply

    except Exception as e:
        print("Archian AI Architect Agent Error:", e)
        return "❌ Archian AI was unable to generate a response. Please try again."







async def evaluate_and_refine_mapping(mapping: dict, flat_data: dict, base_objects: list[str], lookup_objects: list[str], user_id: UUID, org_id: Optional[str] = None) -> dict:

    # Set to track used lookup objects
    used_lookup_objects = set()
    
    # Track which base objects have actual source field mappings (not just required fields)
    base_object_source_field_counts = {obj: 0 for obj in base_objects}
    
    # Get source field names from flat_data
    source_field_names = set(flat_data.keys()) if isinstance(flat_data, dict) else set()

    # Step 1: Process mapping entries (handle both single objects and arrays for split fields)
    refined_mapping = {}
    for key, field_value in mapping.items():
        # Check if this is an array (split fields) or single object
        if isinstance(field_value, list):
            # Array of mapping objects (split field case)
            # For split fields, keep ALL items in the array (don't filter based on value/required)
            # because they're part of a transformation and all need to be present
            filtered_array = []
            for field_obj in field_value:
                if isinstance(field_obj, dict):
                    # Check if this is a split field - if so, always include it
                    is_split_field = field_obj.get("is_split_field", False)
                    if is_split_field:
                        # Always keep split field items, regardless of value/required status
                        filtered_array.append(field_obj)
                    else:
                        # For non-split items in array, keep all items (no filtering based on empty values)
                        # COMMENTED OUT: Previously filtered out empty values unless required
                        # value = field_obj.get("value")
                        # is_required = field_obj.get("is_required", False)
                        # if value or is_required:
                        #     filtered_array.append(field_obj)
                        filtered_array.append(field_obj)
                    # Track lookup objects from array items
                    obj_name = field_obj.get("object")
                    if obj_name in lookup_objects:
                        used_lookup_objects.add(obj_name)
                    # Track base object mappings that have actual source fields
                    elif obj_name in base_objects and key in source_field_names:
                        base_object_source_field_counts[obj_name] += 1
            # Only add array if it has at least one item
            if filtered_array:
                refined_mapping[key] = filtered_array
        elif isinstance(field_value, dict):
            # Single mapping object (regular case)
            value = field_value.get("value")
            is_required = field_value.get("is_required", False)
            obj_name = field_value.get("object")
            
            # Track base object mappings that have actual source fields
            if obj_name in base_objects and key in source_field_names:
                base_object_source_field_counts[obj_name] += 1
            
            # COMMENTED OUT: Previously filtered out empty values unless required
            # Keep all mappings regardless of value emptiness
            # if value or is_required:
            #     refined_mapping[key] = field_value
            #     if obj_name in lookup_objects:
            #         used_lookup_objects.add(obj_name)
            refined_mapping[key] = field_value
            if obj_name in lookup_objects:
                used_lookup_objects.add(obj_name)
        else:
            # Unknown format, skip
            continue
    
    # Step 1.5: Determine primary base object (the one with most actual source field mappings)
    primary_base_object = None
    if base_objects:
        # Find base object with most source field mappings
        max_count = max(base_object_source_field_counts.values()) if base_object_source_field_counts else 0
        if max_count > 0:
            # Get the base object with the most mappings
            for obj, count in base_object_source_field_counts.items():
                if count == max_count:
                    primary_base_object = obj
                    break
        
        # Fallback: if no source field mappings found, use first base object
        if not primary_base_object:
            primary_base_object = base_objects[0]
        
        # Removed verbose debug log: primary base object summary
        
        # Step 1.6: Remove mappings to other base objects that don't have corresponding source fields
        # This removes orphaned mappings (e.g., Email__c, Name mapped to Customer_Contact_Details__c when source only has FirstName, LastName, Email)
        filtered_refined_mapping = {}
        removed_count = 0
        for key, field_value in refined_mapping.items():
            if isinstance(field_value, list):
                # For arrays, check if any item maps to a non-primary base object without source field
                filtered_array = []
                for field_obj in field_value:
                    obj_name = field_obj.get("object")
                    # Keep if: lookup object, primary base object, or has source field
                    if (obj_name in lookup_objects or 
                        obj_name == primary_base_object or 
                        key in source_field_names):
                        filtered_array.append(field_obj)
                    else:
                        removed_count += 1
                        # Removed verbose debug log: orphaned mapping removal (array item)
                if filtered_array:
                    filtered_refined_mapping[key] = filtered_array
            elif isinstance(field_value, dict):
                obj_name = field_value.get("object")
                # Keep if: lookup object, primary base object, or has source field
                if (obj_name in lookup_objects or 
                    obj_name == primary_base_object or 
                    key in source_field_names):
                    filtered_refined_mapping[key] = field_value
                else:
                    removed_count += 1
                    # Removed verbose debug log: orphaned mapping removal (single item)
        
        if removed_count > 0:
            pass
        
        refined_mapping = filtered_refined_mapping

    # Step 2: For each used lookup object, ensure its required fields are present
    for lookup_object in used_lookup_objects:
        required_fields_query = """
            SELECT field_api_name
            FROM salesforce_object_fields
            WHERE object_api_name = :obj
              AND is_required = TRUE
              AND is_defaulted_on_create = FALSE
              AND is_creatable = TRUE
              AND user_id = :user_id
        """
        required_fields_values = {"obj": lookup_object, "user_id": str(user_id)}
        if org_id:
            required_fields_query += " AND org_id = :org_id"
            required_fields_values["org_id"] = org_id
        
        required_fields = await database.fetch_all(
            required_fields_query,
            values=required_fields_values
        )

        # Add missing required fields if not already in mapping
        for field_row in required_fields:
            field_name = field_row["field_api_name"]
            # Check if already mapped (handle both single objects and arrays)
            already_mapped = False
            for mapping_value in refined_mapping.values():
                if isinstance(mapping_value, list):
                    # Array case - check if any item matches
                    already_mapped = any(
                        isinstance(item, dict) and item.get("object") == lookup_object and item.get("field") == field_name
                        for item in mapping_value
                    )
                elif isinstance(mapping_value, dict):
                    # Single object case
                    if mapping_value.get("object") == lookup_object and mapping_value.get("field") == field_name:
                        already_mapped = True
                if already_mapped:
                    break
            
            if not already_mapped:
                field_mapping = {
                    "object": lookup_object,
                    "field": field_name,
                    "value": "Unknown",
                    "missing": True,
                    "is_required": True,
                    "is_custom": False,
                }
                # Only add lookup_of if lookup_object is different from primary base object
                # If lookup_object is the same as primary_base_object, it's not a lookup relationship
                if primary_base_object and lookup_object != primary_base_object:
                    field_mapping["lookup_of"] = f"{primary_base_object}.{lookup_object}Id"  # Example: Contact.IndividualId
                
                refined_mapping[f"{field_name}"] = field_mapping

    return refined_mapping


def _drain_deferred_planning_clarification_terminal(classification: dict) -> list:
    if not classification.get("_planning_terminal_deferred"):
        return []
    classification.pop("_planning_terminal_deferred", None)
    from services.planning.planning_activity import terminal_planning_clarification_sse_lines

    return terminal_planning_clarification_sse_lines()


def _drain_deferred_planning_success_terminal(classification: dict, plan) -> list:
    if not classification.get("_planning_terminal_deferred"):
        return []
    classification.pop("_planning_terminal_deferred", None)
    from services.planning.planning_activity import terminal_planning_success_sse_lines

    return terminal_planning_success_sse_lines(plan)


async def _emit_plan_or_route(
    classification: dict,
    prompt: str,
    conversation_history: list,
    user_id,
    conversation_id: Optional[str],
    pending_file_upload: Optional[dict] = None,
):
    """Emit plan preview (and stop) if approval required; otherwise execute."""
    from agent.debug import log_agent_runtime
    from agent.events import planner_clarification_required, planner_plan_ready
    from services.planning.observability import log_planner_debug
    from services.planning.schemas import ExecutionPlan

    log_agent_runtime(
        "emit_plan_or_route.enter",
        {
            "prompt_excerpt": (prompt or "")[:120],
            "plan_status": classification.get("plan_status"),
            "intent": classification.get("intent"),
            "mode": classification.get("mode"),
            "plan_steps": classification.get("plan_steps_summary"),
            "active_job_id": classification.get("active_job_id"),
        },
    )
    plan_raw = classification.get("execution_plan") or {}
    try:
        ep = ExecutionPlan.model_validate(plan_raw) if plan_raw else None
    except Exception:
        ep = None
    log_planner_debug(
        "emit_plan_or_route_enter",
        user_id=str(user_id) if user_id else None,
        conversation_id=conversation_id,
        classification=classification,
        plan=ep,
        extra={
            "needs_clarification": classification.get("needs_clarification"),
            "plan_steps_summary_len": len(classification.get("plan_steps_summary") or []),
        },
    )

    from services.planning.intent_routing import should_route_to_general_response
    from services.planning.plan_extract_gates import strip_conversation_plan_from_classification

    if should_route_to_general_response(classification, prompt):
        strip_conversation_plan_from_classification(classification)
        route_prompt = _effective_architect_prompt(prompt, classification)
        async for chunk in _route_architect_after_classification(
            route_prompt,
            conversation_history,
            user_id,
            conversation_id,
            classification,
            pending_file_upload=pending_file_upload,
        ):
            yield chunk
        return

    if classification.get("needs_clarification"):
        from services.planning.clarification_apply import build_planner_pending_payload

        log_planner_debug(
            "emit_plan_or_route_branch",
            user_id=str(user_id) if user_id else None,
            conversation_id=conversation_id,
            classification=classification,
            plan=ep,
            gate="needs_clarification",
        )
        pending = (
            build_planner_pending_payload(
                classification,
                prompt=prompt,
                conversation_id=conversation_id,
                conversation_history=conversation_history,
            )
            if conversation_id
            else None
        )
        yield planner_clarification_required(classification, planner_pending=pending)
        return

    # Only normalize draft plans for the approval gate — approved/running must execute.
    if (
        not classification.get("needs_clarification")
        and ep
        and ep.steps
        and (classification.get("plan_status") or "draft") == "draft"
    ):
        from services.planning.schemas import merge_plan_into_classification

        classification = merge_plan_into_classification(classification, ep)
        classification["plan_status"] = "awaiting_approval"

    steps = classification.get("plan_steps_summary") or []
    ep_steps = list(ep.steps or []) if ep else []
    plan_status = (classification.get("plan_status") or "draft").lower()
    if (
        not classification.get("needs_clarification")
        and not steps
        and not ep_steps
        and plan_status in ("awaiting_approval", "awaiting_clarification", "draft")
    ):
        from services.planning.clarification_apply import build_planner_pending_payload

        classification = dict(classification)
        open_clars = list(classification.get("clarifications") or [])
        if not open_clars and ep:
            open_clars = [
                c.model_dump(mode="json")
                for c in ep.blocking_clarifications()
            ]
        if open_clars:
            classification["clarifications"] = open_clars
            classification["needs_clarification"] = True
            classification["plan_status"] = "awaiting_clarification"
            pending = build_planner_pending_payload(
                classification,
                prompt=prompt,
                conversation_id=conversation_id,
                conversation_history=conversation_history,
            ) if conversation_id else None
            log_planner_debug(
                "emit_plan_or_route_branch",
                user_id=str(user_id) if user_id else None,
                conversation_id=conversation_id,
                classification=classification,
                plan=ep,
                gate="empty_plan_needs_clarification",
            )
            for line in _drain_deferred_planning_clarification_terminal(classification):
                yield line
            yield planner_clarification_required(
                classification,
                planner_pending=pending,
            )
            return

        from services.planning.plan_emit_recovery import try_recover_empty_plan_at_emit

        classification, ep, recovered = await try_recover_empty_plan_at_emit(
            classification,
            ep,
            prompt=prompt,
            user_id=str(user_id) if user_id else None,
        )
        if recovered:
            steps = classification.get("plan_steps_summary") or []
            ep_steps = list(ep.steps or []) if ep else []
            if classification.get("needs_clarification"):
                pending = build_planner_pending_payload(
                    classification,
                    prompt=prompt,
                    conversation_id=conversation_id,
                    conversation_history=conversation_history,
                ) if conversation_id else None
                log_planner_debug(
                    "emit_plan_or_route_branch",
                    user_id=str(user_id) if user_id else None,
                    conversation_id=conversation_id,
                    classification=classification,
                    plan=ep,
                    gate="empty_plan_recovered_clarification",
                )
                for line in _drain_deferred_planning_clarification_terminal(classification):
                    yield line
                yield planner_clarification_required(
                    classification,
                    planner_pending=pending,
                )
                return
            if steps or ep_steps:
                log_planner_debug(
                    "emit_plan_or_route_branch",
                    user_id=str(user_id) if user_id else None,
                    conversation_id=conversation_id,
                    classification=classification,
                    plan=ep,
                    gate="empty_plan_recovered_steps",
                )
                # Fall through to awaiting_approval / plan_ready handling below.
            else:
                recovered = False

        if not recovered:
            from agent.events import assistant_message

            log_planner_debug(
                "emit_plan_or_route_branch",
                user_id=str(user_id) if user_id else None,
                conversation_id=conversation_id,
                classification=classification,
                plan=ep,
                gate="empty_plan_no_steps",
            )
            from services.planning.planning_activity import terminal_planning_failure_sse_lines

            classification.pop("_planning_terminal_deferred", None)
            for line in terminal_planning_failure_sse_lines():
                yield line
            yield assistant_message(
                "Planning could not build an execution plan yet. "
                "Please send your request again or complete any remaining setup steps."
            )
            return

    if (
        classification.get("plan_status") in ("awaiting_approval", "awaiting_clarification")
        and not classification.get("needs_clarification")
    ):
        from services.planning.clarification_apply import build_planner_pending_payload

        pending = build_planner_pending_payload(
            classification,
            prompt=prompt,
            conversation_id=conversation_id,
            conversation_history=conversation_history,
        ) if conversation_id else None
        log_planner_debug(
            "emit_plan_or_route_branch",
            user_id=str(user_id) if user_id else None,
            conversation_id=conversation_id,
            classification=classification,
            plan=ep,
            gate="awaiting_approval",
        )
        from services.planning.map_flow_debug import classification_map_pins, log_map_flow

        log_map_flow(
            "emit_plan_or_route_awaiting_approval_stop",
            classification_pins=classification_map_pins(classification),
            has_execution_plan=bool(classification.get("execution_plan")),
            prompt_excerpt=(prompt or "")[:200],
        )
        for line in _drain_deferred_planning_success_terminal(classification, ep):
            yield line
        persisted_message_id = classification.get("_persisted_message_id")
        if conversation_id:
            try:
                from services.chat_service import ChatService

                steps = classification.get("plan_steps_summary") or []
                lines = ["**Execution plan** (review and approve to continue)", ""]
                from services.planning.chat_metadata import (
                    build_planner_chat_metadata,
                    format_plan_destination_line,
                    format_plan_mapping_line,
                    format_plan_source_line,
                )

                src_line = format_plan_source_line(classification)
                if src_line:
                    lines.append(src_line)
                dest_line = format_plan_destination_line(classification)
                if dest_line:
                    lines.append(dest_line)
                map_line = format_plan_mapping_line(classification)
                if map_line:
                    lines.append(map_line)
                if src_line or dest_line or map_line:
                    lines.append("")
                goal_summary = (classification.get("goal_summary") or "").strip()
                if goal_summary:
                    lines.append(goal_summary)
                    lines.append("")
                for i, s in enumerate(steps, 1):
                    lines.append(f"{i}. {s.get('label') or s.get('step_type')}")
                lines.append("")
                lines.append("_Click **Run plan** below to start._")

                message, _conversation = await ChatService.add_message(
                    conversation_id,
                    str(user_id),
                    "assistant",
                    "\n".join(lines),
                    build_planner_chat_metadata(
                        classification,
                        event_type="planner_plan_ready",
                        planner_pending=pending,
                        extra={"plan_steps_summary": steps},
                    ),
                )
                if message and message.get("id"):
                    persisted_message_id = message["id"]
                    try:
                        from services.conversation.session_sync import sync_session_after_planner

                        await sync_session_after_planner(
                            str(user_id),
                            conversation_id,
                            message_id=str(message["id"]),
                            intent_state=classification.get("intent_state"),
                        )
                    except Exception as sync_exc:
                        print(f"⚠️ Failed to sync planner session: {sync_exc}")
                    classification["_persisted_message_id"] = message["id"]
            except Exception as persist_err:
                print(f"⚠️ Failed to persist planner plan message: {persist_err}")
        if classification.get("execution_plan"):
            yield planner_plan_ready(
                classification,
                planner_pending=pending,
                message_id=persisted_message_id,
            )
        return

    if classification.get("execution_plan") and not should_route_to_general_response(
        classification, prompt
    ):
        log_planner_debug(
            "emit_plan_or_route_branch",
            user_id=str(user_id) if user_id else None,
            conversation_id=conversation_id,
            classification=classification,
            plan=ep,
            gate="execution_plan_fallback",
        )
        yield planner_plan_ready(classification)

    if ep and ep.blocking_clarifications() and not classification.get("needs_clarification"):
        from services.planning.clarification_apply import build_planner_pending_payload

        log_planner_debug(
            "emit_plan_or_route_branch",
            user_id=str(user_id) if user_id else None,
            conversation_id=conversation_id,
            classification=classification,
            plan=ep,
            gate="blocking_clarifications_mismatch",
        )
        classification = dict(classification)
        classification["needs_clarification"] = True
        pending = build_planner_pending_payload(
            classification,
            prompt=prompt,
            conversation_id=conversation_id,
            conversation_history=conversation_history,
        ) if conversation_id else None
        yield planner_clarification_required(classification, planner_pending=pending)
        return

    log_planner_debug(
        "emit_plan_or_route_no_ui_event",
        user_id=str(user_id) if user_id else None,
        conversation_id=conversation_id,
        classification=classification,
        plan=ep,
        extra={
            "plan_status": classification.get("plan_status"),
            "has_execution_plan": bool(classification.get("execution_plan")),
            "step_count": len(ep.steps or []) if ep else 0,
        },
    )

    from services.planning.intent_routing import classification_ready_for_agent_execution

    if not classification_ready_for_agent_execution(classification):
        from services.planning.clarification_apply import build_planner_pending_payload
        from services.planning.map_flow_debug import classification_map_pins, log_map_flow
        from services.planning.planner_emit import outcome_from_classification
        from services.planning.planner_session_loop import PlannerTurnKind
        from agent.events import planner_error

        log_map_flow(
            "emit_plan_or_route_skip_execute",
            reason="classification_not_ready_for_agent_execution",
            classification_pins=classification_map_pins(classification),
            prompt_excerpt=(prompt or "")[:200],
        )
        log_agent_runtime(
            "emit_plan_or_route.skip_execute",
            {
                "plan_status": classification.get("plan_status"),
                "needs_clarification": classification.get("needs_clarification"),
                "step_count": len(
                    (classification.get("execution_plan") or {}).get("steps") or []
                ),
            },
        )
        outcome = outcome_from_classification(
            classification,
            prompt=prompt,
            conversation_id=conversation_id,
            conversation_history=conversation_history,
        )
        if outcome.kind == PlannerTurnKind.CLARIFICATION:
            yield planner_clarification_required(
                outcome.classification,
                planner_pending=outcome.planner_pending,
            )
        else:
            yield planner_error(
                outcome.message or "",
                classification=outcome.classification,
                planner_pending=outcome.planner_pending,
            )
        return

    log_agent_runtime("emit_plan_or_route.execute", {"plan_status": classification.get("plan_status")})
    from services.planning.map_flow_debug import classification_map_pins, log_map_flow

    log_map_flow(
        "emit_plan_or_route_execute",
        classification_pins=classification_map_pins(classification),
        prompt_excerpt=(prompt or "")[:200],
    )
    route_prompt = _effective_architect_prompt(prompt, classification)
    async for chunk in _route_architect_after_classification(
        route_prompt,
        conversation_history,
        user_id,
        conversation_id,
        classification,
        pending_file_upload=pending_file_upload,
    ):
        yield chunk


def _effective_architect_prompt(prompt: str, classification: dict) -> str:
    """Merge original user goal with planner-resolved destination (e.g. new sheet title)."""
    base = (prompt or "").strip()
    if not base:
        artifact = classification.get("planning_artifact")
        if isinstance(artifact, dict):
            intent = artifact.get("intent") or {}
            if isinstance(intent, dict):
                base = (intent.get("original_prompt") or "").strip()
        if not base:
            base = (classification.get("original_prompt") or "").strip()
        if not base:
            plan_meta = (classification.get("execution_plan") or {}).get("metadata") or {}
            if isinstance(plan_meta, dict):
                base = (plan_meta.get("original_prompt") or "").strip()
        if not base and (classification.get("plan_route") or "") == "continuation_path":
            base = (classification.get("reasoning") or "Continue pipeline step").strip()
    from services.planning.create_metadata import should_materialize_create_step
    from services.planning.plan_scope import read_plan_scope

    goal = read_plan_scope(classification) or {}
    title = (classification.get("planner_new_spreadsheet_name") or "").strip()
    if should_materialize_create_step(classification, goal) and title:
        extra = f"Create a new Google spreadsheet named {title}"
        return f"{base}\n{extra}".strip() if base else extra
    return base


async def _route_architect_after_classification(
    prompt: str,
    conversation_history: list,
    user_id,
    conversation_id: Optional[str],
    classification: dict,
    pending_file_upload: Optional[dict] = None,
):
    """Yield SSE chunks after classification is complete."""
    from services.planning.intent_routing import should_route_to_general_response

    if should_route_to_general_response(classification, prompt):
        from agent.runtime import run_agent_architect_stream

        general_cls = dict(classification)
        general_cls["intent"] = general_cls.get("intent") or "general"
        general_cls["mode"] = "general"
        general_cls.pop("operations", None)
        async for chunk in run_agent_architect_stream(
            prompt,
            str(user_id),
            conversation_history,
            conversation_id,
            general_cls,
        ):
            yield chunk
        return

    from agent.runtime import run_agent_architect_stream

    async for chunk in run_agent_architect_stream(
        prompt,
        str(user_id),
        conversation_history,
        conversation_id,
        classification,
        pending_file_upload=pending_file_upload,
    ):
        yield chunk


async def _yield_planning_activity_while_task(task: "asyncio.Task") -> Any:
    """Drain planning_activity SSE while ``task`` is in flight."""
    from services.planning.planning_activity import drain_planning_activity_sse_lines

    while not task.done():
        for sse_line in drain_planning_activity_sse_lines():
            yield sse_line
        await asyncio.sleep(0.025)
    for sse_line in drain_planning_activity_sse_lines():
        yield sse_line


async def handle_text_prompt_for_architect_agent_stream_real(
    prompt: str,
    conversation_history: list,
    user_id,
    conversation_id: Optional[str] = None,
    planner_resume: Optional[dict] = None,
    plan_auto_run: bool = False,
    pending_file_upload: Optional[dict] = None,
):
    """
    Real streaming version with intelligent prompt classification.
    Routes requests to appropriate handlers based on user intent.

    Async generator — consume with ``async for``, do not ``await`` this function.
    """
    from agent.events import (
        classification_completed,
        classification_started,
        operation_phase,
        planner_clarification_required,
    )
    from services.planning.clarification_apply import build_planner_pending_payload
    from services.planning.clarification_turn import (
        classify_architect_turn,
        is_explicit_planner_ui_resume,
    )
    from services.planning.orchestrator import resume_classification_after_clarifications
    from utils.operation_phase_map import format_identify_route_label

    try:
        if planner_resume and not is_explicit_planner_ui_resume(planner_resume):
            legacy_reply = (planner_resume.get("reply_text") or prompt or "").strip()
            planner_resume = None
            if legacy_reply:
                prompt = legacy_reply

        if planner_resume:
            pending = planner_resume.get("planner_pending") or planner_resume
            selections = planner_resume.get("selections") or []
            reply_text = (planner_resume.get("reply_text") or prompt or "").strip()
            original_prompt = (pending.get("original_prompt") or "").strip()
            approve_plan = bool(planner_resume.get("approve_plan"))
            approve_schema_deploy = bool(planner_resume.get("approve_schema_deploy"))
            skip_schema_deploy = bool(planner_resume.get("skip_schema_deploy"))
            approve_recovery = bool(planner_resume.get("approve_recovery"))
            skip_recovery = bool(planner_resume.get("skip_recovery"))
            resume_plan_execution = bool(
                planner_resume.get("resume_plan_execution")
                or pending.get("extract_step_clarification")
            )
            route_prompt = original_prompt or prompt

            yield classification_started()

            classification: Optional[Dict[str, Any]] = None

            if approve_recovery or skip_recovery:
                from services.planning.plan_execution import apply_recovery_approval

                classification = apply_recovery_approval(
                    planner_resume.get("planner_pending") or pending,
                    skip_recovery=skip_recovery,
                )
                from services.pipeline_learning.service import PipelineLearningService

                proposal = pending.get("recovery_proposal") or {}
                if PipelineLearningService.enabled():
                    outcome = "approved" if approve_recovery else "dismissed"
                    PipelineLearningService.schedule_capture(
                        lambda outcome=outcome: PipelineLearningService.capture_recovery_attempt(
                            user_id=str(user_id),
                            conversation_id=str(conversation_id) if conversation_id else None,
                            batch_id=str(pending.get("recovery_batch_id") or ""),
                            proposal=proposal,
                            outcome=outcome,
                        )
                    )
                if approve_recovery and conversation_id:
                    try:
                        from services.chat_service import ChatService

                        await ChatService.log_data_event(
                            str(conversation_id),
                            str(user_id),
                            "recovery_approved",
                            {
                                "recovery_batch_id": pending.get("recovery_batch_id"),
                                "recovery_job_id": pending.get("recovery_job_id"),
                            },
                        )
                    except Exception:
                        pass
                if skip_recovery:
                    from agent.events import assistant_message

                    yield operation_phase(
                        "understand_request",
                        "done",
                        label="Recovery dismissed",
                    )
                    yield assistant_message(
                        "Recovery dismissed. Review failed records in the migration panel if needed."
                    )
                    if conversation_id:
                        try:
                            from services.chat_service import ChatService

                            await ChatService.log_data_event(
                                str(conversation_id),
                                str(user_id),
                                "recovery_dismissed",
                                {
                                    "recovery_batch_id": pending.get("recovery_batch_id"),
                                    "recovery_job_id": pending.get("recovery_job_id"),
                                },
                            )
                        except Exception:
                            pass
                    return
            else:
                from services.planning.planning_activity import (
                    drain_planning_activity_sse_lines,
                    emit_planning_activity,
                    install_planning_activity_bus,
                    reset_planning_activity_bus,
                )

                resume_activity_label = (
                    "Starting approved plan"
                    if approve_plan
                    else "Continuing plan"
                    if resume_plan_execution or approve_schema_deploy or skip_schema_deploy
                    else "Applying your selection"
                )

                bus_token = install_planning_activity_bus()
                emit_planning_activity(
                    "apply_selection",
                    resume_activity_label,
                    "running",
                    segment="scope",
                )

                route_prompt_holder = [route_prompt]

                async def _planner_resume_work() -> Dict[str, Any]:
                    if approve_schema_deploy or skip_schema_deploy:
                        from services.planning.plan_execution import apply_schema_deploy_approval

                        return apply_schema_deploy_approval(
                            planner_resume.get("planner_pending") or pending,
                            skip_deploy=skip_schema_deploy,
                        )
                    if approve_plan:
                        from services.planning.plan_approve import approve_execution_plan
                        from services.planning.map_flow_debug import (
                            classification_map_pins,
                            log_map_flow,
                        )

                        log_map_flow(
                            "planner_resume_approve_plan_start",
                            conversation_id=conversation_id,
                            pending_plan_id=(
                                pending.get("plan_id")
                                or (pending.get("execution_plan") or {}).get("plan_id")
                            ),
                            pending_pins=classification_map_pins(
                                pending.get("classification") or {}
                            ),
                            original_prompt_excerpt=original_prompt[:200],
                        )
                        result = approve_execution_plan(pending)
                        from services.pipeline_learning.service import PipelineLearningService

                        if PipelineLearningService.enabled():
                            PipelineLearningService.schedule_capture(
                                lambda: PipelineLearningService.capture_plan_approved(
                                    user_id=str(user_id),
                                    conversation_id=str(conversation_id) if conversation_id else None,
                                    prompt=original_prompt or route_prompt,
                                    classification=dict(result),
                                    pending=pending,
                                )
                            )
                        from services.labeling.capture_hooks import schedule_plan_label_evidence

                        schedule_plan_label_evidence(
                            user_id=str(user_id),
                            conversation_id=str(conversation_id) if conversation_id else None,
                            prompt=original_prompt or route_prompt,
                            classification=dict(result),
                            event_type="plan_approved",
                        )
                        return result
                    if resume_plan_execution:
                        from services.planning.plan_execution import apply_extract_step_resume

                        result = apply_extract_step_resume(pending, reply_text or prompt)
                        route_prompt_holder[0] = result.pop(
                            "_merged_execution_prompt", original_prompt or prompt
                        )
                        return result
                    return await resume_classification_after_clarifications(
                        pending,
                        selections,
                        user_id=str(user_id),
                        prompt=original_prompt,
                        reply_text=reply_text or None,
                        plan_auto_run=plan_auto_run,
                        conversation_history=conversation_history,
                    )

                try:
                    resume_task = asyncio.create_task(_planner_resume_work())
                    async for sse_line in _yield_planning_activity_while_task(resume_task):
                        yield sse_line
                    classification = await resume_task
                finally:
                    emit_planning_activity(
                        "apply_selection",
                        resume_activity_label,
                        "done",
                        segment="scope",
                    )
                    route_label = format_identify_route_label(classification or {})
                    if route_label:
                        emit_planning_activity(
                            "identify_route",
                            "Source and destination",
                            "done",
                            segment="scope",
                            detail=route_label,
                        )
                    for sse_line in drain_planning_activity_sse_lines():
                        yield sse_line
                    reset_planning_activity_bus(bus_token)

                route_prompt = route_prompt_holder[0]

            from services.file_extraction.pending_upload import resolve_pending_file_upload

            pending_file_upload = await resolve_pending_file_upload(
                pending_file_upload=pending_file_upload,
                classification=classification,
            )
            yield classification_completed(classification)

            if classification.get("needs_clarification"):
                pending = build_planner_pending_payload(
                    classification,
                    prompt=route_prompt,
                    conversation_id=conversation_id,
                    conversation_history=conversation_history,
                )
                yield planner_clarification_required(
                    classification,
                    planner_pending=pending,
                )
                return

            async for chunk in _emit_plan_or_route(
                classification,
                route_prompt,
                conversation_history,
                user_id,
                conversation_id,
                pending_file_upload=pending_file_upload,
            ):
                yield chunk
            return

        yield classification_started()
        classify_kwargs: Dict[str, Any] = {
            "conversation_id": conversation_id,
            "plan_auto_run": plan_auto_run,
        }
        if pending_file_upload:
            classify_kwargs["explicit_source"] = "file"
            classify_kwargs["explicit_source_name"] = pending_file_upload.get("filename")

        from services.planning.planning_activity import (
            drain_planning_activity_sse_lines,
            emit_planning_activity,
            install_planning_activity_bus,
            reset_planning_activity_bus,
        )

        bus_token = install_planning_activity_bus()
        emit_planning_activity(
            "parse_prompt",
            "Understanding your request",
            "running",
            segment="scope",
        )
        try:
            classify_task = asyncio.create_task(
                classify_architect_turn(
                    prompt,
                    user_id,
                    conversation_history,
                    **classify_kwargs,
                )
            )
            async for sse_line in _yield_planning_activity_while_task(classify_task):
                yield sse_line
            classification = await classify_task
            for sse_line in drain_planning_activity_sse_lines():
                yield sse_line
        finally:
            reset_planning_activity_bus(bus_token)
        if pending_file_upload:
            from services.file_extraction.pending_upload import (
                pin_file_upload_on_classification,
                resolve_pending_file_upload,
                stage_pending_file_upload,
            )

            classification = pin_file_upload_on_classification(
                classification,
                filename=pending_file_upload.get("filename"),
            )
            if pending_file_upload.get("content"):
                classification["_pending_file_ref"] = await stage_pending_file_upload(
                    user_id=str(user_id),
                    conversation_id=conversation_id,
                    filename=pending_file_upload.get("filename") or "upload",
                    file_content=pending_file_upload["content"],
                )
            pending_file_upload = await resolve_pending_file_upload(
                pending_file_upload=pending_file_upload,
                classification=classification,
            )
        yield classification_completed(classification)

        if classification.get("needs_clarification"):
            pending = build_planner_pending_payload(
                classification,
                prompt=prompt,
                conversation_id=conversation_id,
                conversation_history=conversation_history,
            )
            yield planner_clarification_required(
                classification,
                planner_pending=pending,
            )
            if conversation_id:
                from services.chat_service import ChatService
                from services.planning.chat_metadata import build_planner_chat_metadata

                from services.planning.clarification_compose import (
                    resolve_clarification_display_message,
                )

                clarifications = classification.get("clarifications") or []
                assistant_text = resolve_clarification_display_message(
                    clarifications,
                    classification=classification,
                    user_prompt=prompt or "",
                )

                async def _persist_clarification() -> None:
                    await ChatService.add_message(
                        conversation_id,
                        str(user_id),
                        "assistant",
                        assistant_text,
                        build_planner_chat_metadata(
                            classification,
                            event_type="planner_clarification_required",
                            planner_pending=pending,
                        ),
                    )

                ChatService.schedule_message_persist(_persist_clarification())
            return

        async for chunk in _emit_plan_or_route(
            classification,
            prompt,
            conversation_history,
            user_id,
            conversation_id,
            pending_file_upload=pending_file_upload,
        ):
            yield chunk
    except Exception as e:
        print(f"Error in main handler: {e}")
        import traceback

        traceback.print_exc()
        try:
            classification = await classify_source_destination(
                prompt, user_id, conversation_history, conversation_id=conversation_id
            )
            fallback = await handle_general_ai_response_stream(
                prompt,
                conversation_history,
                source=classification.get("source"),
                source_name=classification.get("source_name"),
                destination=classification.get("destination"),
                destination_name=classification.get("destination_name"),
                user_id=str(user_id),
            )
            async for chunk in fallback:
                yield chunk
        except Exception:
            fallback = await handle_general_ai_response_stream(
                prompt, conversation_history, user_id=str(user_id)
            )
            async for chunk in fallback:
                yield chunk




async def process_large_file_in_chunks(sample_data, user_id, extraction_goals, extraction_limit, original_file_records, progress_callback=None):
    """
    Process large files in chunks to avoid OpenAI rate limits
    """
    import asyncio

    
    # Calculate optimal chunk size based on estimated tokens
    CHUNK_SIZE = 50  # 50 records per chunk for faster, non-blocking processing
    total_records = len(sample_data)
    total_chunks = (total_records + CHUNK_SIZE - 1) // CHUNK_SIZE
    
    print(f"🔄 Processing {total_records} records in {total_chunks} chunks of {CHUNK_SIZE} records each")
    
    all_chunk_results = []
    document_type = None
    document_description = None
    object_name = None
    
    # Process each chunk
    for i in range(0, total_records, CHUNK_SIZE):
        chunk_number = (i // CHUNK_SIZE) + 1
        chunk_data = sample_data[i:i + CHUNK_SIZE]
        
        print(f"🔄 [CHUNK {chunk_number}/{total_chunks}] Processing {len(chunk_data)} records...")
        
        # Update progress: 25% + (chunk progress * 65%)
        # 25% for file download, 65% for chunking, 10% for final processing
        # Progress before processing this chunk
        chunk_progress_before = 25 + int(((chunk_number - 1) / total_chunks) * 65)
        # Progress after processing this chunk
        chunk_progress_after = 25 + int((chunk_number / total_chunks) * 65)
        
        if progress_callback:
            progress_callback(chunk_progress_before, f"Processing chunk {chunk_number} of {total_chunks}")
            await asyncio.sleep(0.3)  # Small delay for frontend to catch progress
        
        try:
            # Process chunk with OpenAI
            chunk_results = None
            
            try:
                chunk_results = await process_chunk_with_openai(
                    chunk_data, user_id, extraction_goals, 
                    extraction_limit, chunk_number, total_chunks
                )
                
                if chunk_results and len(chunk_results) < len(chunk_data) * 0.8:  # Less than 80% of expected records
                    print(f"⚠️ Chunk {chunk_number} returned only {len(chunk_results)} records (expected {len(chunk_data)})")
                    # Don't retry - OpenAI is consistently returning fewer records for this data
                    
            except Exception as e:
                print(f"❌ Chunk {chunk_number} failed with error: {e}")
                chunk_results = None
            
            if chunk_results:
                all_chunk_results.append(chunk_results)
                
                # Extract document metadata from first chunk
                if chunk_number == 1:
                    for record in chunk_results:
                        if "document_type" in record:
                            document_type = record.get("document_type")
                            document_description = record.get("document_description")
                            object_name = record.get("object_name")
                            break
            else:
                print(f"⚠️ Chunk {chunk_number} returned no results")
            
            # Update progress after chunk completion
            chunk_progress_after = 25 + int((chunk_number / total_chunks) * 65)
            if progress_callback:
                progress_callback(chunk_progress_after, f"Completed chunk {chunk_number} of {total_chunks}")
                await asyncio.sleep(0.3)  # Small delay for frontend to catch progress
            
            # Rate limit delay between chunks (except for the last chunk)
            if chunk_number < total_chunks:
                await asyncio.sleep(2)
                
        except Exception as e:
            print(f"❌ Error processing chunk {chunk_number}: {str(e)}")
            # Continue with other chunks even if one fails
            continue
    
    # Clean up metadata from ALL chunks - metadata is only for internal processing
    all_results = []
    total_records_before_cleanup = 0
    
    for i, chunk_results in enumerate(all_chunk_results):
        total_records_before_cleanup += len(chunk_results)
        for record in chunk_results:
            # Create a copy to avoid modifying the original
            clean_record = record.copy()
            clean_record.pop("object_name", None)
            clean_record.pop("document_type", None)
            clean_record.pop("document_description", None)
            all_results.append(clean_record)
    
    # All chunks processed
    
    if not document_type:
        document_type = "Spreadsheet"
    
    return all_results, document_type, None, document_description, extraction_limit, original_file_records


async def process_chunk_with_openai(chunk_data, user_id, extraction_goals, extraction_limit, chunk_number, total_chunks):
    """
    Process a single chunk with OpenAI
    """
    import json
    import asyncio
    
    
    # Fetch Salesforce categories for this user
    categories = await get_dynamic_document_type_categories(user_id, destination="salesforce")
    category_list = json.dumps(categories)
    
    # Add plan limit information to OpenAI instruction for non-PDF files
    plan_limit_instruction = ""
    if extraction_limit["limit_reached"]:
        plan_limit_instruction = f"""

 **CREDIT LIMIT REACHED - IMPORTANT INSTRUCTION:**
- User has insufficient credits for extraction
- They can extract 0 more records until they add credits or upgrade
- **DO NOT extract any records** - return empty data
- Only classify the document type
"""
    elif extraction_limit.get("extraction_cap") is not None and extraction_limit.get("can_extract", 0) > 0:
        plan_limit_instruction = f"""

📊 **CREDIT LIMIT APPLIED - IMPORTANT INSTRUCTION:**
- User can afford to extract maximum {extraction_limit["can_extract"]} records (credit-based)
- **Limit your extraction to {extraction_limit["can_extract"]} records maximum**
- If the document has more records, extract only the first {extraction_limit["can_extract"]}
- This is a {extraction_limit["plan_type"]} plan limitation
"""

    instruction = f"""
You are Archian AI, an intelligent data extraction agent.

📄 Document Context:
- The user wants to extract: "{extraction_goals or 'all relevant structured data'}"
- This is chunk {chunk_number} of {total_chunks} from a large file

{plan_limit_instruction}

🧠 Your Task:
The following is structured tabular data extracted from a spreadsheet. Your job is to extract and return a structured JSON representation of this data.

🔍 Instructions (Follow Precisely):
🚨 **IMPORTANT: If the user mentioned a specific Salesforce object (like "contact object", "account object", "lead object"), you MUST use that object type, not infer from content!**
1. 🏷️ **Document Classification**
   - Infer the `document_type` from the Sample Data only if user has not mentioned a specific Salesforce object and document_type can only be selected from the category list below.
   - Choose **only one** pair from the category list below.
   - Format your output with:
     ```json
     "object_name": "object_name",
     "document_type": "category",
     "document_description": "category_description"
     ```
   - ⚠️ **Do NOT** modify, reword, truncate, or add punctuation (like periods) to the `category_description`.
   - ⚠️ **Do NOT** modify, reword, truncate, or add punctuation (like periods) to the `object_name`.
   - ⚠️ **Do NOT** modify, reword, truncate, or add punctuation (like periods) to the `category` and **DO NOT** use object_name in document_type, document_type will only have value from "category" key.
   - Use the category list below **exactly as-is**:

{category_list}

2. 📤 **Output Format**
   - Output must be **flat JSON** (no nested objects or arrays inside each record).
   - If the data is multi-record (i.e., tabular), return a **list of objects**.
   - Include `"object_name"`, `"document_type"` and `"document_description"` in the **first data record** (not as a separate metadata record).
   - **Do NOT create a separate record just for metadata** - include metadata fields in the first actual data record.

3. 🧹 **Header Normalization**
   - Convert short or informal headers (e.g., "FName", "DOB") into standardized formats:
     - `"FName"` → `"FirstName"`
     - `"DOB"` → `"DateOfBirth"`

4. 👤 **Full Name Splitting**
   - **Only** when a single compound name column exists (e.g. `Name`, `Full Name`) and the user asked to split it.
   - If the source already has separate name columns (`Name` alone is NOT split columns), **keep `Name` as-is**.
   - Do not split just to match a Salesforce or Google Sheets destination schema.

5. 🏠 **Location or Address Handling**
   - If a field like "Location", "Address", or "Current Location" is present and contains data like:
     - `"123 Montgomery st., Atlanta, Georgia, United States, 94103"`
   - Split it into the following fields:
     - `MailingStreet`: e.g., "123 Montgomery st."
     - `MailingCity`: e.g., "Atlanta"
     - `MailingState`: e.g., "Georgia"
     - `MailingCountry`: e.g., "United States" 
     - `MailingPostalCode`: e.g., "94103"
   - Do NOT include the original "Location" field if splitting is successful.
   - If only partial information is present, include only what can be confidently extracted.

6. 📋 **Boolean Fields**
   - For checkboxes or radio buttons, include values **only if checked** (`true`).
   - Do not include fields with `false` values.

7. 🚫 **Empty Fields**
   - Exclude any field with an empty value or null.

8. 🧪 **Ordering**
   - If the output contains multiple records, arrange them in this order:
     1. Records with **invalid or malformed values**
     2. All other records

📦 Sample Data:
{json.dumps(safe_json_serialize(chunk_data), indent=2)}
"""

    try:
        # Make OpenAI call truly async to prevent blocking the event loop
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.FILE_EXTRACTION,
            messages=[{"role": "user", "content": instruction}],
            temperature=0,
        )

        reply = response.choices[0].message.content
        flat_data = parse_openai_json_response(reply)

        if isinstance(flat_data, dict):
            flat_data_list = [flat_data]
        elif isinstance(flat_data, list):
            flat_data_list = [item for item in flat_data if isinstance(item, dict)]
        else:
            raise ValueError("OpenAI returned an unexpected format — not a dict or list.")

        return flat_data_list
        
    except Exception as e:
        print(f"❌ Error processing chunk {chunk_number} with AI service: {str(e)}")
        return []







def apply_ai_structure_to_real_data(real_text: str, ai_structured_data: list) -> list:
    """
    Apply AI-generated field structure to real text data
    This extracts real data using the structure learned from sample data
    """
    if not real_text or not ai_structured_data:
        return []
    
    # Get the field structure from AI output (excluding metadata)
    field_structure = {}
    for record in ai_structured_data:
        for key, value in record.items():
            if key not in ['object_name', 'document_type', 'document_description', 'data_cleanup_rules']:
                field_structure[key] = value
        break  # Use first record to determine structure
    
    # Extract real data using regex patterns based on field structure
    real_records = []
    
    # Split text into lines for processing
    lines = [line.strip() for line in real_text.split('\n') if line.strip()]
    
    # For each field in the structure, extract real values
    current_record = {}
    
    # Pattern matching for common field types
    import re
    
    # Extract names (FirstName, LastName)
    if 'FirstName' in field_structure or 'LastName' in field_structure:
        name_pattern = r'\b([A-Z][a-z]+)\s+([A-Z][a-z]+)\b'
        names = re.findall(name_pattern, real_text)
        for i, (first, last) in enumerate(names):
            if i < len(real_records):
                real_records[i]['FirstName'] = first
                real_records[i]['LastName'] = last
            else:
                real_records.append({'FirstName': first, 'LastName': last})
    
    # Extract emails
    if 'Email' in field_structure:
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        emails = re.findall(email_pattern, real_text)
        for i, email in enumerate(emails):
            if i < len(real_records):
                real_records[i]['Email'] = email
            else:
                real_records.append({'Email': email})
    
    # Extract phone numbers
    if 'Phone' in field_structure:
        phone_pattern = r'(\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}|[0-9]{10,11})'
        phones = re.findall(phone_pattern, real_text)
        for i, phone in enumerate(phones):
            if i < len(real_records):
                real_records[i]['Phone'] = phone
            else:
                real_records.append({'Phone': phone})
    
    # If we couldn't extract structured data, return the original real data
    if not real_records:
        return [{"raw_text": real_text}]
    
    return real_records



def create_sample_records_from_schema(sql_data: list, count: int = 5) -> list:
    """
    Create fake sample records based on schema of extracted data
    This ensures no real customer data is sent to OpenAI while maintaining schema structure
    """
    if not sql_data or len(sql_data) == 0:
        return []
    
    # Get schema from first record
    sample_records = []
    for i in range(count):
        sample_record = {}
        for field, value in sql_data[0].items():
            if isinstance(value, str):
                sample_record[field] = f"Sample {field.replace('_', ' ').title()} {i+1}"
            elif isinstance(value, int):
                sample_record[field] = 1000 + i
            elif isinstance(value, float):
                sample_record[field] = round(1000.5 + i, 2)
            elif isinstance(value, bool):
                sample_record[field] = i % 2 == 0
            elif value is None:
                sample_record[field] = f"Sample {field.replace('_', ' ').title()} {i+1}"
            else:
                sample_record[field] = f"Sample {field.replace('_', ' ').title()} {i+1}"
        sample_records.append(sample_record)
    
    return sample_records






