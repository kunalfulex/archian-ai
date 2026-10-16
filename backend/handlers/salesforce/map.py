"""Salesforce mapping and org-context helpers."""

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

async def build_salesforce_context_for_prompt(prompt: str, user_id, source, source_name) -> str:
    """
    Build enhanced Salesforce context for user prompts by dynamically matching against database categories and descriptions.
    
    This function now dynamically extracts sales keywords and categories from the user's actual Salesforce objects,
    making the context generation more intelligent and personalized to each organization's setup.
    
    Key Features:
    - Dynamic keyword extraction from object labels
    - Dynamic category extraction from object categories
    - Fallback to essential defaults if dynamic data is insufficient
    - Intelligent scoring based on actual available objects
    """
    try:
        from db.connection import database

        print(f"👤 User ID: {user_id}")

        # Resolve org context based on source_name (fallback to default org)
        org_filter = ""
        org_values = {"user_id": str(user_id)}
        resolved_org = None
        normalized_source_name = (source_name or "").strip()

        if normalized_source_name:
            print(f"🔍 Resolving Salesforce org for source_name: {normalized_source_name}")
            resolved_org = await database.fetch_one(
                """
                SELECT connected_app_id, org_display_name AS connection_name, environment_type, is_default, org_id
                FROM salesforce_tokens
                WHERE user_id = :user_id
                  AND LOWER(TRIM(org_display_name)) = LOWER(TRIM(:source_name))
                ORDER BY is_default DESC NULLS LAST, issued_at DESC
                LIMIT 1
                """,
                {"user_id": str(user_id), "source_name": normalized_source_name},
            )

        if not resolved_org:
            print("🔁 Falling back to default Salesforce org (is_default = true)")
            resolved_org = await database.fetch_one(
                """
                SELECT connected_app_id, org_display_name AS connection_name, environment_type, is_default, org_id
                FROM salesforce_tokens
                WHERE user_id = :user_id AND is_default = true
                ORDER BY issued_at DESC
                LIMIT 1
                """,
                {"user_id": str(user_id)},
            )
        if not resolved_org:
            print("🔁 Falling back to most recent Salesforce org")
            resolved_org = await database.fetch_one(
                """
                SELECT connected_app_id, org_display_name AS connection_name, environment_type, is_default, org_id
                FROM salesforce_tokens
                WHERE user_id = :user_id
                ORDER BY issued_at DESC
                LIMIT 1
                """,
                {"user_id": str(user_id)},
            )

        if not resolved_org:
            print("⚠️ No Salesforce org could be resolved; context will be empty.")
            return """
📊 **No Salesforce Objects Found**

It looks like you haven't connected your Salesforce org yet, or your metadata hasn't been imported.

🔗 **To get accurate, org-specific guidance:**

1. **Go to Connections tab** → Connect your Salesforce org
2. **Import metadata** → Let Archian AI analyze your data structure
3. **Get personalized advice** → Based on your actual Salesforce setup

💡 **Why connect your org?**
- Understand your specific object structure
- Get field mapping recommendations
- Receive org-specific migration guidance
- Avoid generic advice that might not fit your setup

🚀 **Ready to connect?** Navigate to the Connections tab to get started!

**Note:** If you haven't connected your Salesforce org yet, I recommend doing so by navigating to the Connections tab. This will allow Archian AI to provide more personalized guidance based on your specific Salesforce setup.
"""

        org_id = resolved_org["org_id"]
        connection_name = resolved_org["connection_name"]

        if org_id:
            org_filter = "AND org_id = :org_id"
            org_values["org_id"] = org_id

        print(f"🏢 Using Salesforce org: {connection_name} (org_id={org_id})")

        from services.planning.connector_info_context import (
            build_connector_info_enrichment,
            resolve_salesforce_connector,
        )

        connector = await resolve_salesforce_connector(str(user_id), normalized_source_name or connection_name)
        enrichment_block = ""
        info_meta: dict = {}
        if connector:
            enrichment_block, info_meta = await build_connector_info_enrichment(
                str(user_id),
                connector,
                prompt,
            )

        # Step 1: Get all Salesforce objects for this org with their categories and descriptions
        objects_query = f"""
        SELECT object_api_name, label, category, category_description
        FROM salesforce_objects 
        WHERE user_id = :user_id
        {org_filter}
        AND category IS NOT NULL
        ORDER BY label
        """

        all_objects = await database.fetch_all(objects_query, values=org_values)
        if not all_objects:
            return f"""
📊 **No Salesforce Objects Found**

It looks like you haven't connected your Salesforce org yet, or your metadata hasn't been imported.

🔗 **To get accurate, org-specific guidance:**

1. **Go to Connections tab** → Connect your Salesforce org
2. **Import metadata** → Let Archian AI analyze your data structure
3. **Get personalized advice** → Based on your actual Salesforce setup

💡 **Why connect your org?**
- Understand your specific object structure
- Get field mapping recommendations
- Receive org-specific migration guidance
- Avoid generic advice that might not fit your setup

🚀 **Ready to connect?** Navigate to the Connections tab to get started!

**Note:** If you haven't connected your Salesforce org yet, I recommend doing so by navigating to the Connections tab. This will allow Archian AI to provide more personalized guidance based on your specific Salesforce setup.

_(Connection attempted: {connection_name or 'Unknown'})_
"""
        
        # Step 2: Let OpenAI identify relevant objects
        print(f"🤖 Letting OpenAI identify relevant objects for: '{prompt}'")
        
        # Create detailed object information for OpenAI
        available_objects = []
        for obj in all_objects:
            obj_dict = dict(obj)
            available_objects.append({
                'api_name': obj_dict['object_api_name'],
                'label': obj_dict['label'],
                'category': obj_dict['category'],
                'category_description': obj_dict.get('category_description', '')
            })
        
        # Ask OpenAI to identify relevant objects (skip when label/graph already matched)

        focus_keys = info_meta.get("focus_entity_keys") or []
        selected_api_names = [k for k in focus_keys if k][:3]
        prompt_lower = (prompt or "").lower()
        inventory_query = not selected_api_names and any(
            term in prompt_lower
            for term in (
                "available",
                "what objects",
                "which objects",
                "list objects",
                "show objects",
                "what are the objects",
            )
        )

        if inventory_query:
            inventory_lines = ["\n📋 **Synced Salesforce objects**\n"]
            for obj in all_objects[:80]:
                od = dict(obj)
                inventory_lines.append(
                    f"- **{od.get('label')}** (`{od.get('object_api_name')}`)"
                    f" — {od.get('category') or 'Uncategorized'}\n"
                )
            if len(all_objects) > 80:
                inventory_lines.append(f"_…and {len(all_objects) - 80} more objects_\n")
            header = enrichment_block or f"\n📌 **Salesforce org:** {connection_name}\n"
            return header + "".join(inventory_lines)

        if not selected_api_names:
            object_selection_prompt = f"""
        User asked: "{prompt}"
        
        Available Salesforce objects in their org with full details:
        {[f"• {obj['label']} ({obj['api_name']}) - Category: {obj['category']} - Description: {obj['category_description']}" for obj in available_objects[:50]]}
        
        Based on the user's question, identify the most relevant Salesforce objects (1-3 objects max).
        Consider the object labels, categories, and descriptions to understand what each object is used for.
        Return ONLY the object API names, one per line.
        """
        
            try:
                from services.architect_agent import async_openai_call
                from services.llm import tasks as llm_tasks

                response = await async_openai_call(
                    None,
                    task=llm_tasks.SALESFORCE_INFO,
                    messages=[{"role": "user", "content": object_selection_prompt}],
                    max_tokens=500,
                    temperature=0.7,
                )
                
                selected_api_names = response.choices[0].message.content.strip().split('\n')
                selected_api_names = [name.strip() for name in selected_api_names if name.strip()]
            except Exception as e:
                print(f"❌ Error with OpenAI object selection: {e}")
                selected_api_names = []
        
        try:
            print(f"🎯 Selected objects: {selected_api_names}")
            
            # Find the selected objects in our database
            matched_objects = []
            for obj in all_objects:
                obj_dict = dict(obj)
                if obj_dict['object_api_name'] in selected_api_names:
                    matched_objects.append(obj)
            
            # If selection didn't find any or found too few, add some fallbacks
            if len(matched_objects) < 1:
                print("⚠️ No matched objects, using fallbacks")
                fallback_objects = ['Lead', 'Account', 'Contact', 'Opportunity']
                for obj in all_objects:
                    obj_dict = dict(obj)
                    if obj_dict['object_api_name'] in fallback_objects:
                        matched_objects.append(obj)
                        if len(matched_objects) >= 3:
                            break
                            
        except Exception as e:
            print(f"❌ Error selecting Salesforce objects: {e}")
            matched_objects = all_objects[:3]
        
        # Step 3: Build rich context with object details and fields
        context_parts = []
        
        for obj in matched_objects:
            # Convert Record object to dictionary for easier access
            obj_dict = dict(obj)
            
            print(f"🔍 Processing object: {obj_dict['label']} ({obj_dict['object_api_name']})")
            
            # Get fields for this object
            fields_query = f"""
            SELECT field_api_name, field_label, data_type, is_required, is_custom, field_description
            FROM salesforce_object_fields 
            WHERE object_api_name = :object_api_name 
            AND user_id = :user_id
            {"AND org_id = :org_id" if org_filter else ""}
            ORDER BY is_required DESC, is_custom ASC
            LIMIT 100
            """
            
            field_query_values = {
                "object_api_name": obj_dict["object_api_name"],
                "user_id": user_id,
            }
            if org_filter:
                field_query_values["org_id"] = org_values.get("org_id")
            
            print(f"   📋 Fetching fields for {obj_dict['object_api_name']}...")
            fields = await database.fetch_all(fields_query, values=field_query_values)
            
            # Found fields for object
            if fields and len(fields) > 0:
                print(f"   📝 Sample fields: {[f['field_label'] for f in fields[:3]]}")
            else:
                print(f"   ⚠️ No fields found in database for {obj_dict['object_api_name']}")
            

            
            # Build object context
            object_context = f"\n🔹 **{obj_dict['label']}** ({obj_dict['object_api_name']})\n"
            object_context += f"📂 Category: {obj_dict['category']}\n"
            
            if obj_dict["category_description"]:
                object_context += f"📝 Description: {obj_dict['category_description']}\n"
            
            if fields:
                object_context += f"📋 Available Fields:\n"
                for field in fields:
                    field_type = "🔴 Required" if field["is_required"] else "🟢 Optional"
                    custom_indicator = " (Custom)" if field["is_custom"] else ""
                    field_desc = f" - {field['field_description']}" if field["field_description"] else ""
                    object_context += f"   • {field['field_label']} ({field['data_type']}) {field_type}{custom_indicator}{field_desc}\n"
            else:
                object_context += f"⚠️ No fields found for this object\n"
            
            context_parts.append(object_context)
        
        if context_parts:
            header = enrichment_block or ""
            if not header:
                header = f"\n📌 **Salesforce org:** {connection_name}\n"
            final_context = header + "\n".join(context_parts)
            
            # Summary of what we're building
            print(f"🎯 CONTEXT BUILT SUCCESSFULLY:")
            print(f"   • Selected {len(matched_objects)} Salesforce objects for context")
            print(f"   • Total context length: {len(final_context)} characters")
            
            return final_context
        else:
            return "\n📊 Available Salesforce Objects:\nUse the categories above to understand where your data fits in Salesforce."
            
    except Exception as e:
        print(f"❌ Error building Salesforce context: {e}")
        import traceback
        traceback.print_exc()
        return "\n📊 Available Salesforce Objects:\nUse the categories above to understand where your data fits in Salesforce."


async def analyze_mapping_transformations(
    mapping: dict,
    flat_data: dict,
    object_description_block: str,
    base_objects: list,
    lookup_objects: list,
    sample_data: list,
    mapping_context_summary: Optional[str] = None,
) -> dict:
    """
    Analyze the mapping to determine if field transformations are needed (e.g., name splitting).
    Returns transformation_rules and enhanced_mapping with new split field entries.
    """
    mapping_audit_dict: Optional[Dict[str, Any]] = None
    try:
        from services.architect_agent import async_openai_call
        from utils.openai_helpers import parse_openai_json_response
        import json

        from utils.mapping_audit import (
            audit_mapping_structure,
            audit_result_to_dict,
            resolve_post_map_transforms,
            sanitize_enhanced_mapping,
        )
        from utils.mapping_transform_context import classify_salesforce_destination_name_shape
        from utils.mapping_utils import align_mapping_keys_to_extracted_data

        mapping = align_mapping_keys_to_extracted_data(mapping or {}, flat_data or {})

        try:
            objects_structure = json.loads(object_description_block)
        except Exception:
            objects_structure = {}
        dest_name_shape = classify_salesforce_destination_name_shape(
            objects_structure if isinstance(objects_structure, dict) else {}
        )
        base_obj = (base_objects or [None])[0] if base_objects else None

        mapping_audit_dict = audit_result_to_dict(
            audit_mapping_structure(
                mapping=mapping or {},
                flat_data=flat_data or {},
                destination_type="salesforce",
                base_container=str(base_obj) if base_obj else None,
                base_objects=base_objects,
                destination_name_shape=dest_name_shape,
            )
        )
        resolved = resolve_post_map_transforms(
            flat_data=flat_data or {},
            mapping=mapping or {},
            destination_name_shape=dest_name_shape,
            destination_type="salesforce",
            base_container=str(base_obj) if base_obj else None,
            base_objects=base_objects,
            sample_data=sample_data,
        )
        if resolved.get("skip_transform_llm") and resolved.get("enhanced_mapping"):
            fields = (resolved.get("transformation_rules") or {}).get("fields") or []
            action = (fields[0].get("action") if fields else "") or ""
            if str(action).startswith("split"):
                print("🔧 [MAP_SF_TRANSFORM] Using programmatic compound name → FirstName + LastName split (no transform LLM)")
            else:
                print("🔧 [MAP_SF_TRANSFORM] Using programmatic first+last → Name combine (no transform LLM)")
            return {
                "transformation_rules": resolved.get("transformation_rules") or {"fields": []},
                "enhanced_mapping": resolved.get("enhanced_mapping") or {},
                "mapping_audit": resolved.get("mapping_audit") or mapping_audit_dict,
            }
        if resolved.get("skip_transform_llm"):
            print("🔧 [MAP_SF_TRANSFORM] Skipping transform LLM — destination fields already aligned")
            return {
                "transformation_rules": {"fields": []},
                "enhanced_mapping": {},
                "mapping_audit": resolved.get("mapping_audit") or mapping_audit_dict,
            }
        
        # Build prompt for transformation analysis
        mapping_json = json.dumps(mapping, indent=2)
        sample_data_json = json.dumps(sample_data, indent=2)
        


        instruction_section = ""
        if mapping_context_summary and str(mapping_context_summary).strip():
            instruction_section = f"""
USER MAPPING INSTRUCTION (highest priority — do not contradict):
{mapping_context_summary.strip()}

If the user asked to COMBINE fields (e.g. first name + last name → Name), use action "combine", not split.
If the user asked to map to a specific object, only transform fields for that object.

"""

        from utils.mapping_transform_context import build_transform_llm_context_section

        transform_context_section = build_transform_llm_context_section(
            flat_data=flat_data or {},
            mapping=mapping or {},
            destination_name_shape=dest_name_shape,
            destination_type="salesforce",
        )

        prompt = f"""You are Archian AI analyzing field mappings for transformations.
{instruction_section}
{transform_context_section}

INPUTS:
- Current Mapping: {mapping_json}
- Sample Data: {sample_data_json}
- Destination Schema: {object_description_block}

DECISION TREE (follow in order):

1. CHECK IF ALREADY SPLIT (Skip **splitting** only — still evaluate **combining**):
   - Name fields: source has separate first/last keys → data is pre-split; do NOT split again
   - Address fields: source has "street"/"city"/"state"/"postal_code" → do NOT split again
   - If pre-split AND destination has matching separate component fields with 1:1 mapping → no transformation
   - If pre-split BUT destination only has generic Name/FullName (see context) → go to step 3 COMBINE

2. IDENTIFY SPLITTING NEEDS:
   Required conditions (ALL must be true):
   ✓ Source field name suggests compound person or address data. Include at least:
     - "Full Name", "Contact Name", "Customer Name", "Employee Name", "Display Name", "Legal Name"
     - Plain **"Name"** or **"name"** when the value looks like a person full name (not username, not company name)
     - "Address", "Mailing Address", "Full Address", etc.
   ✓ Sample value contains multiple parts ("John Doe", "Mary Ann Lee", "123 Main St, City, ST")
   ✓ Destination schema lists the component fields (e.g. **FirstName** AND **LastName** on Contact)
   ✓ Current mapping sends the compound value to **only one** of those components (e.g. only **LastName** or only **FirstName**) or to a generic **Name** field — then you MUST split into FirstName + LastName (preserve object and lookup_of on each array entry)
   
   For lookup objects: Copy exact `lookup_of` value to it's split field entries

3. IDENTIFY COMBINING NEEDS:
   Required conditions (ALL must be true):
   ✓ Multiple source fields ("first_name", "last_name") map to SAME destination field on the SAME object
   ✓ OR source has separate first/last fields (mapped OR listed as unmapped in context) and destination only has a generic Name/FullName field (no separate FirstName+LastName)
   ✓ When combining, add ALL participating source keys to enhanced_mapping (even if omitted from Current Mapping)

🚨 **CRITICAL RULE FOR COMBINE (MUST FOLLOW):**
- NEVER combine fields across DIFFERENT Salesforce objects.
- Combine is ONLY allowed when the fields map to the SAME destination field on the SAME object.
- ✅ Allowed: Contact.FirstName + Contact.LastName → Contact.Name
- ❌ Not allowed: Contact.Phone + Account.Phone → anything

OUTPUT FORMAT:
{{
  "transformation_rules": {{
    "fields": [
      {{
        "source_field": "Contact Name",
        "action": "split_2",  // Options: split_2, split_3, split_address, combine
        "split_char": " ",
        "output_fields": ["FirstName", "LastName"]
      }}
    ]
  }},
  "enhanced_mapping": {{
    "Contact Name": [  // Use EXACT original source key, value is ARRAY for splits
      {{
        "object": "Contact",
        "field": "FirstName",
        "lookup_of": "Leave__c.ContactName__c",  // Copy from original
        "is_split_field": true
      }},
      {{
        "object": "Contact",
        "field": "LastName",
        "lookup_of": "Leave__c.ContactName__c",
        "is_split_field": true
      }}
    ],
    "first_name": {{  // For combines: same destination, add combine metadata
      "object": "Contact",
      "field": "FullName",
      "is_combined_field": true,
      "combine_group": "name",
      "combine_order": 0
    }}
  }}
}}

RULES:
- Only include fields needing transformation in enhanced_mapping
- If no transformations needed: {{"transformation_rules": {{"fields": []}}, "enhanced_mapping": {{}}}}
- Return valid JSON only, no markdown

Analyze now.
"""
        
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.FIELD_MAPPING,
            messages=[
                {
                    "role": "system",
                    "content": "You are Archian AI, specializing in data transformation analysis for Salesforce field mappings."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.0
        )
        
        reply = response.choices[0].message.content
        result = parse_openai_json_response(reply)
        
        # Ensure we have the expected structure
        transformation_rules = result.get("transformation_rules", {"fields": []})
        enhanced_mapping = sanitize_enhanced_mapping(
            mapping or {},
            result.get("enhanced_mapping", {}) or {},
            flat_data or {},
        )

        from utils.mapping_transform_context import apply_post_llm_transform_reconciliation

        reconciled = apply_post_llm_transform_reconciliation(
            mapping=mapping or {},
            flat_data=flat_data or {},
            destination_name_shape=dest_name_shape,
            destination_type="salesforce",
            transformation_rules=transformation_rules,
            enhanced_mapping=enhanced_mapping,
            sample_data=sample_data,
            base_container=str(base_obj) if base_obj else None,
            base_objects=base_objects,
        )
        transformation_rules = reconciled["transformation_rules"]
        enhanced_mapping = sanitize_enhanced_mapping(
            mapping or {},
            reconciled.get("enhanced_mapping") or {},
            flat_data or {},
        )

        print(f"🔧 Transformation Analysis Results:")
        print(f"   • Transformation rules count: {len(transformation_rules.get('fields', []))}")
        print(f"   • Enhanced mapping entries: {len(enhanced_mapping)}")
        
        return {
            "transformation_rules": transformation_rules,
            "enhanced_mapping": enhanced_mapping,
            "mapping_audit": mapping_audit_dict,
        }
        
    except Exception as e:
        print(f"⚠️ Error analyzing transformations: {e}")
        import traceback
        traceback.print_exc()
        # Return empty on error - mapping will work without transformations
        return {
            "transformation_rules": {"fields": []},
            "enhanced_mapping": {},
            "mapping_audit": mapping_audit_dict,
        }


async def call_salesforce_architect_agent(
    flat_data,
    document_type,
    object_description_block,
    relevant_objects,
    user_id,
    is_sql_data=False,
    org_id: Optional[str] = None,
    mapping_context_summary: Optional[str] = None,
    *,
    skip_generic_transform_analysis: bool = False,
    pinned_base_object: Optional[str] = None,
):
    try:
        # Lazy imports to avoid circular dependency
        from services.architect_agent import (
            async_openai_call,
            safe_json_serialize,
            evaluate_and_refine_mapping
        )
        from utils.openai_helpers import parse_openai_json_response
        from uuid import uuid4

        # Mapping build is free; no credit deduction

        # Split relevant_objects into base + lookups for prompt guidance
        base_objects = relevant_objects.get("base", [])
        lookup_objects = relevant_objects.get("lookup", [])
        print("base_objects:", base_objects)
        print("lookup_objects:", lookup_objects)
        # Use real data directly for transformation analysis (no masking)
        # Data is only sent for analysis, not stored, and data sharing with OpenAI is disabled
        if isinstance(flat_data, dict):
            sample_data = [flat_data]
        elif isinstance(flat_data, list) and len(flat_data) > 0:
            # If it's a list, use first record (limit to 1 for token management)
            sample_data = [flat_data[0]]
        else:
            # Fallback: create empty sample if data structure is unexpected
            sample_data = []
        print("✅ Using real data for OpenAI transformation analysis (data sharing disabled)")

        from utils.mapping_transform_context import source_fields_from_flat_data
        from utils.mapping_utils import flat_data_uses_salesforce_extract_keys

        _sample_row = (
            sample_data[0]
            if sample_data
            else (flat_data if isinstance(flat_data, dict) else {})
        )
        use_sf_extract_keys = not is_sql_data and flat_data_uses_salesforce_extract_keys(
            _sample_row
        )
        _source_columns = source_fields_from_flat_data(_sample_row)
        _source_columns_json = json.dumps(_source_columns)

        if is_sql_data:
            field_name_requirements = """
🚨 **CRITICAL - FIELD NAME REQUIREMENTS (SQL)**:
- Use the EXACT SQL column names from the sample data as JSON keys (see CRITICAL FOR SQL DATA above).
- Do not rename columns to Salesforce API names or file-style names (company, first_name).
"""
        elif use_sf_extract_keys:
            field_name_requirements = f"""
🚨 **CRITICAL - USE EXACT EXTRACTED COLUMN NAMES (Salesforce API)**:
- Each key in your JSON must be **exactly** one of the SOURCE COLUMNS listed below (case-sensitive).
- Do **NOT** invent aliases like "company", "first_name", or "email" — they are **not** in the extract.
- Relationship fields from SOQL are flattened: `Account.Name` → **AccountName**, `Account.Phone` → **AccountPhone**.
- **AccountName** → `Account.Name` with `"lookup_of": "{base_objects[0] if base_objects else 'Contact'}.AccountId"` (or the correct lookup field from the reference block).
- **AccountPhone** → `Account.Phone` with the **same** `lookup_of` as AccountName.
- **Phone** (when present) is the **Contact** phone → map to `Contact.Phone` only — never to `Account.Phone`.
- Map **every** SOURCE COLUMN below; do not drop AccountName or AccountPhone.

📋 **SOURCE COLUMNS (map all):** {_source_columns_json}
"""
        else:
            field_name_requirements = """
🚨 **CRITICAL - FIELD NAME REQUIREMENTS**:
- Each key in the final JSON must be the EXACT FIELD NAME from the sample data below
- Use the EXACT field names like "email", "first_name", "last_name", "phone", "company" (as shown in sample data)
- Do **NOT** use Salesforce field names like "Email", "FirstName", "LastName" as keys
- Do **NOT** change the field names - use them exactly as they appear in the sample data
- The value for each key must be an object describing the Salesforce mapping.
"""

        # Create dynamic example mapping based on destination object fields (not source field names)
        base_object = base_objects[0] if base_objects else "Contact"
        
        # Parse object_description_block to get actual field information
        objects_structure = {}
        actual_lookup_field = None
        first_lookup = None  # Will be determined from base object fields
        base_object_fields = {}
        lookup_object_fields = {}
        
        try:
            objects_structure = json.loads(object_description_block)
            
            # Get base object fields
            if base_object in objects_structure and "fields" in objects_structure[base_object]:
                base_object_fields = objects_structure[base_object]["fields"]
                
                # Find lookup fields in base object and extract their lookup_object values
                lookup_fields_found = []
                for field_name, field_info in base_object_fields.items():
                    if field_info.get("is_lookup") and field_info.get("lookup_object"):
                        lookup_obj = field_info.get("lookup_object")
                        lookup_fields_found.append({
                            "field": field_name,
                            "lookup_object": lookup_obj
                        })
                
                # Determine first_lookup from actual lookup fields found in base object
                if lookup_fields_found:
                    # Use the first lookup field found, or prioritize based on lookup_objects array if provided
                    if lookup_objects:
                        # Try to find a lookup that matches one in the lookup_objects array (priority order)
                        for lookup_obj_name in lookup_objects:
                            for lf in lookup_fields_found:
                                if lf["lookup_object"] == lookup_obj_name:
                                    first_lookup = lookup_obj_name
                                    actual_lookup_field = lf["field"]
                                    break
                            if first_lookup:
                                break
                    
                    # If no match found in lookup_objects array, use the first lookup field found
                    if not first_lookup:
                        first_lookup = lookup_fields_found[0]["lookup_object"]
                        actual_lookup_field = lookup_fields_found[0]["field"]
                else:
                    # Fallback: use first from lookup_objects array if available
                    if lookup_objects:
                        first_lookup = lookup_objects[0]
                    else:
                        first_lookup = "Account"  # Final fallback
            else:
                # Fallback: use first from lookup_objects array if available
                if lookup_objects:
                    first_lookup = lookup_objects[0]
                else:
                    first_lookup = "Account"  # Final fallback
            
            # Get lookup object fields (if we have a lookup object)
            if first_lookup and first_lookup in objects_structure and "fields" in objects_structure[first_lookup]:
                lookup_object_fields = objects_structure[first_lookup]["fields"]
            
            if actual_lookup_field:
                print(f"✅ Found actual lookup field: {base_object}.{actual_lookup_field} -> {first_lookup}")
            else:
                print(f"⚠️ Could not find lookup field from {base_object} to {first_lookup}, using fallback")
                
        except Exception as e:
            print(f"⚠️ Error parsing object_description_block: {e}, using fallback fields")
            import traceback
            traceback.print_exc()
            # Fallback: use first from lookup_objects array if available
            if lookup_objects:
                first_lookup = lookup_objects[0]
            else:
                first_lookup = "Account"  # Final fallback
        
        # Use actual lookup field name, or fallback to standard pattern
        lookup_field_name = actual_lookup_field or f"{first_lookup}Id" if first_lookup else "AccountId"
        
        # Find common standard fields in base object for example mapping
        # Priority: FirstName, LastName, Email (common standard fields)
        example_base_fields = []
        preferred_base_fields = ["FirstName", "LastName", "Email", "Phone"]
        
        if base_object_fields:
            for preferred_field in preferred_base_fields:
                if preferred_field in base_object_fields:
                    field_info = base_object_fields[preferred_field]
                    example_base_fields.append({
                        "field_api_name": preferred_field,
                        "is_required": field_info.get("is_required", False),
                        "is_custom": False  # Standard fields are never custom
                    })
                    if len(example_base_fields) >= 2:  # Get at least 2 base object fields
                        break
            
            # If we didn't find preferred fields, use any available fields (limit to 2)
            if len(example_base_fields) < 2:
                for field_name, field_info in list(base_object_fields.items())[:5]:
                    if field_name not in [f["field_api_name"] for f in example_base_fields]:
                        # Skip system fields and lookups for base object examples
                        if not field_info.get("is_lookup") and field_name not in ["Id", "CreatedDate", "LastModifiedDate"]:
                            example_base_fields.append({
                                "field_api_name": field_name,
                                "is_required": field_info.get("is_required", False),
                                "is_custom": field_name.endswith("__c")  # Custom fields end with __c
                            })
                            if len(example_base_fields) >= 2:
                                break
        
        # Fallback: if no fields found, use default examples
        if not example_base_fields:
            example_base_fields = [
                {"field_api_name": "FirstName", "is_required": False, "is_custom": False},
                {"field_api_name": "LastName", "is_required": True, "is_custom": False}
            ]
        
        # Find Name field in lookup object (most common)
        lookup_name_field = None
        if "Name" in lookup_object_fields:
            lookup_name_field = {
                "field_api_name": "Name",
                "is_required": lookup_object_fields["Name"].get("is_required", False),
                "is_custom": False
            }
        else:
            # If Name doesn't exist, find any required field in lookup object
            for field_name, field_info in lookup_object_fields.items():
                if field_info.get("is_required") and not field_info.get("is_lookup"):
                    lookup_name_field = {
                        "field_api_name": field_name,
                        "is_required": True,
                        "is_custom": field_name.endswith("__c")
                    }
                    break
        
        # Build example mapping using generic source field names but actual destination fields
        example_mapping_dict = {}
        
        # Add base object field examples
        for idx, base_field in enumerate(example_base_fields[:2], 1):
            source_field_placeholder = f"source_field_{idx}"  # Generic placeholder
            example_mapping_dict[source_field_placeholder] = {
                "object": base_object,
                "field": base_field["field_api_name"],
                "is_required": base_field["is_required"],
                "value": "[SAMPLE_VALUE]",
                "is_custom": base_field["is_custom"],
                "is_duplicate_check": base_field["field_api_name"] in ["Email", "LastName", "Phone"]  # Common duplicate check fields
            }
            
            # Add missing flag if required field
            if base_field["is_required"]:
                example_mapping_dict[source_field_placeholder]["missing"] = True
        
        # Add multiple lookup object field examples (if available) to show that ALL lookup fields need lookup_of
        if lookup_name_field and first_lookup:
            # First lookup field (e.g., Account Name)
            example_mapping_dict["source_field_lookup"] = {
                "object": first_lookup, # lookup object
                "field": lookup_name_field["field_api_name"], # lookup field
                "is_required": lookup_name_field["is_required"],
                "lookup_of": f"{base_object}.{lookup_field_name}", # lookup field reference - REQUIRED for ALL lookup fields
                "value": "[SAMPLE_VALUE]",
                "missing": lookup_name_field["is_required"],
                "is_custom": lookup_name_field["is_custom"],
                "is_duplicate_check": lookup_name_field["field_api_name"] == "Name"  # Name is commonly used for duplicates
            }
            
            # Second lookup field example (e.g., Account Website) - shows ALL lookup fields need lookup_of
            # Try to find a common non-Name field in lookup object
            lookup_second_field = None
            preferred_second_fields = ["Website", "Phone", "Industry", "BillingCity"]
            for pref_field in preferred_second_fields:
                if pref_field in lookup_object_fields:
                    field_info = lookup_object_fields[pref_field]
                    if not field_info.get("is_lookup"):  # Don't use another lookup field
                        lookup_second_field = {
                            "field_api_name": pref_field,
                            "is_required": field_info.get("is_required", False),
                            "is_custom": pref_field.endswith("__c")
                        }
                        break
            
            # If no preferred field found, use any non-Name, non-lookup field
            if not lookup_second_field:
                for field_name, field_info in lookup_object_fields.items():
                    if (field_name != "Name" and 
                        not field_info.get("is_lookup") and 
                        field_name not in ["Id", "CreatedDate", "LastModifiedDate"]):
                        lookup_second_field = {
                            "field_api_name": field_name,
                            "is_required": field_info.get("is_required", False),
                            "is_custom": field_name.endswith("__c")
                        }
                        break
            
            # Add second lookup field example if found
            if lookup_second_field:
                example_mapping_dict["source_field_lookup_2"] = {
                    "object": first_lookup, # Same lookup object
                    "field": lookup_second_field["field_api_name"], # Different field (e.g., Website)
                    "is_required": lookup_second_field["is_required"],
                    "lookup_of": f"{base_object}.{lookup_field_name}", # SAME lookup_of as first field - REQUIRED
                    "value": "[SAMPLE_VALUE]",
                    "is_custom": lookup_second_field["is_custom"],
                    "is_duplicate_check": False  # Website/Phone typically not used for duplicates
                }
        
        example_mapping = json.dumps(example_mapping_dict, indent=2)
        # Escape curly braces for f-string safety
        print("example_mapping:", example_mapping)
        example_mapping_safe = example_mapping.replace("{", "{{").replace("}", "}}")

        # Create different prompts for SQL vs file data
        if is_sql_data:
            # For SQL data, preserve original field names and disable normalization
            mapping_context_section = ""
            if mapping_context_summary:
                mapping_context_section = f"""
📝 **User's Extraction Request Context:**
{mapping_context_summary}

Use this context to:
- Prioritize object mentioned by the user for mapping
"""
            prompt = f"""
You are Archian AI, a Salesforce Cloud data architect. Your job is to map each field from the SQL query results to the most appropriate Salesforce object and field.

✅ **DATA USAGE**: The data below contains real SQL column names and values for mapping analysis.
Use the EXACT SQL column names to create mapping rules that can be applied to real customer data.

🚨 **CRITICAL FOR SQL DATA**: 
- **PRESERVE ORIGINAL FIELD NAMES**: Use the EXACT field names from the SQL data as keys in your mapping
- **DO NOT NORMALIZE OR STANDARDIZE** field names (e.g., keep "acc_name" not "AccountName")
- **DO NOT RENAME** fields (e.g., keep "firstname" not "FirstName")
- The field names must match exactly what the SQL query returns

📄 Document Type: {document_type}
{mapping_context_section}

🚨 **CRITICAL OWNERID RULE - READ FIRST**:
- **If OwnerId field value is missing or empty, DO NOT include OwnerId in the mapping at all**
- **Salesforce will automatically assign the connected user as OwnerId when creating records**
- **This rule takes precedence over all other mapping rules**

🎯 Your priorities are as follows:
1. **CRITICAL - SELECT ONLY ONE BASE OBJECT**: You must select ONLY ONE base object from this list: {', '.join(base_objects)}.
   - **Use mapping_context_summary to guide selection**: If the user mentioned a specific object (e.g., "Contact object", "Lead object"), prioritize that object
   - **If no specific object mentioned**: Choose the base object that best matches the extracted schema fields (field names and data types)
   - **Map ALL source fields to this SINGLE selected base object** (and its lookup objects if needed)
   - **DO NOT map fields to multiple different base objects** - choose ONE and stick with it

🚨 **CRITICAL - FIELD EXISTENCE VERIFICATION (MUST READ)**:
- **ONLY map to fields that ACTUALLY EXIST in the object_description_block below**
- **BEFORE mapping any field, you MUST verify it exists in the object's field list in the "📚 Reference" section**
- **Custom objects may NOT have standard fields** like `FirstName`, `LastName`, `Email`, `Phone`, etc.
- **DO NOT assume standard fields exist** - always check the object_description_block first
- **If a field doesn't exist in the object_description_block, DO NOT map to it** - find an alternative field that does exist
- **Example**: If you want to map to `FirstName` but the object_description_block shows the object only has `Name` and `Email__c`, then map to `Name` instead
- **Verification Process**:
  1. Select your base object (e.g., `Customer_Contact_Details__c`)
  2. Find that object in the `object_description_block` below
  3. Check its `fields` section to see what fields actually exist
  4. ONLY map to fields that are listed in that `fields` section
  5. If the field you want doesn't exist, find the closest matching field that does exist

2. When multiple objects contain semantically similar fields (e.g., "Title", "Occupation", "Name", "Email"), always prioritize mapping to the equivalent field on the selected base object first.
3. Only map to the **lookup objects**: {', '.join(lookup_objects)} if:
   - The field clearly refers to an external or referenced entity (e.g., a separate company, advisor, or household).
   - The selected base object does not have a suitable matching field.
4. Use **custom field suggestions** *only if* no match exists in either the selected base object or lookup objects.

📌 Required Field Handling (Very Important):
- **🚨 CRITICAL - DO NOT MAP LOOKUP/REFERENCE FIELDS DIRECTLY**:
  - **DO NOT include lookup/reference fields** (like `AccountId`, `ContactId`, `OpportunityId`, etc.) as direct field mappings in your output
  - **These fields are NOT in the source data** and should NOT be mapped
  - **These fields are automatically populated** when you add `lookup_of` to fields mapping to the lookup object
  - **Example**: If "AccountName" from source data maps to `Account.Name`, add `"lookup_of": "Contact.AccountId"` to the AccountName mapping. DO NOT add a separate "AccountId" field mapping - AccountId is not in the source data!
- **ONLY for the selected base object**:
  - **Include all its required fields in the mapping, even if the value is missing in the extracted data.**
  - **EXCEPTION**: Do NOT include lookup/reference fields (like `AccountId`, `ContactId`) even if they are required - handle these via `lookup_of` on lookup object fields
  - **DO NOT include required fields from other base objects** that you did not select
- For any lookup object that appears in the mapping:
  - Only include required fields **if at least one field from the extracted data maps to that lookup object**.
  - Do **not** include required fields for lookup objects if no extracted field is mapped to them.

⚠️ Missing Data Rules:
- For any required field not found in the extracted data:
  - Use `"value": "Unknown"` as a placeholder.
  - Add `"missing": true` to the mapping entry.
- **DO NOT create mappings for lookup/reference fields** (like `AccountId`, `ContactId`) - these are handled via `lookup_of` on lookup object fields, not as direct mappings

🚨 **CRITICAL OWNERID RULE - MUST FOLLOW**:
- **If OwnerId field value is missing or empty, DO NOT include OwnerId in the mapping at all**
- **Salesforce will automatically assign the connected user as OwnerId when creating records**
- **This rule takes precedence over all other required field rules**
- **DO NOT add OwnerId to mapping if the value is missing, empty, or "Required field"**


🚨 **CRITICAL RULE - READ CAREFULLY**:
- **NEVER map source fields directly to lookup FIELDS in the base object** (e.g., DO NOT map "Contact Name" to `Leave__c.ContactName__c`)
- **NEVER add lookup/reference fields** (like `AccountId`, `ContactId`) as direct mappings in your output - these are NOT source fields from the extracted data
- **ALWAYS map source fields to FIELDS IN THE LOOKUP OBJECT** (e.g., map "Contact Name" to `Contact.LastName` or `Contact.FirstName`)
- **THEN** add the `lookup_of` key to specify which lookup field in the base object creates the relationship
- **The lookup field ID is automatically resolved** from the lookup object fields - you don't need to map it directly

**CORRECT Approach:**
1. Identify that a source field (e.g., "Contact Name") refers to a related entity (Contact)
2. Map it to a field IN the lookup object (e.g., `Contact.LastName`)
3. Add `lookup_of` to specify the lookup field in base object (e.g., `"lookup_of": "Leave__c.ContactName__c"`)

**INCORRECT Approach (DO NOT DO THIS):**
- Mapping "Contact Name" to `Leave__c.ContactName__c` directly ❌
- This would just put the text value into the lookup field ID, which won't work!

**HOW TO FIND THE CORRECT LOOKUP FIELD NAME FOR `lookup_of`:**
- Look at the `object_description_block` below (in the "📚 Reference" section)
- For the base object (e.g., "{base_objects[0] if base_objects else 'Contact'}"), examine its fields
- Find the field where:
  * `"data_type": "reference"` (this indicates a lookup/reference field)
  * `"is_lookup": true` is present
  * `"lookup_object"` matches the lookup object you're mapping to (e.g., "{first_lookup if lookup_objects else 'Account'}")
- Use the `field_api_name` of that field as the lookup field name for `lookup_of`
- **Pattern**: `"lookup_of": "{base_objects[0] if base_objects else 'Contact'}.{{actual_lookup_field_name_from_object_description_block}}"`
- **Example**: If source field is "Company Name" and you map it to `Account.Name`, and {base_objects[0] if base_objects else 'Contact'} has a field `AccountId` with `"data_type": "reference"`, `"is_lookup": true`, and `"lookup_object": "Account"`, then use:
  ```json
  "Company Name": {{
    "object": "Account",
    "field": "Name",
    "lookup_of": "{base_objects[0] if base_objects else 'Contact'}.AccountId"
  }}
  ```
- **DO NOT assume the field name ends with "Id"** - always check the `object_description_block` for the actual field name
- Include mapped **lookup object** required fields as well, even if the data is missing.
- **WITHOUT `lookup_of`, the relationship will NOT be created in Salesforce!**

🔍 Duplicate Check Tagging (Important):
For every field in the final mapping:
1. Determine if the field should be used to check for existing records before inserting:
   - If yes → add `"is_duplicate_check": true`
   - If no → add `"is_duplicate_check": false`

2. Apply this rule to **both**:
   - Fields belonging to base objects (e.g. Contact.Email, Lead.Phone)
   - Fields belonging to any **lookup object** that appears in the mapping (e.g. Account.Name, Individual.LastName)

3. Use Salesforce best practices:
   - Prefer fields like `Email`, `Phone`, `Name`, `DUNS Number`, `Student ID`
   - Avoid soft-identifiers like `Title`, `Notes`, `Description`

4. Your output JSON **must include `is_duplicate_check` for every field**, even if false.

🧪 **Ordering**
   - Arrange Output in this order:
     1. Records with **missing required fields**
     2. Records with **invalid or malformed values**
     3. All other records

✅ Example Mapping and Output Format:
📢 Return only a single flat JSON object containing all mapped fields.
Do **not** include markdown, code blocks, explanations, or any other text.

{field_name_requirements}

💡 General Rule:
If a field maps to any lookup object, always include the `lookup_of` key with the corresponding reference field from the base object.
This rule applies to **all** lookup object mappings — not just the examples below.

⚠️ **CRITICAL REMINDER BEFORE OUTPUT**:
- **EVERY field that maps to a lookup object MUST have `lookup_of`**
- **ALL fields mapping to the same lookup object should have the SAME `lookup_of` value**
- **Example**: If "AccountName", "AccountWebsite", "AccountPhone" all map to Account object, 
  they ALL must have `"lookup_of": "Contact.AccountId"` (or the correct lookup field name)
- **Missing `lookup_of` on ANY lookup field will cause the migration to FAIL**

🧪 Example Output:
{example_mapping_safe}

📚 Reference: Use the following structured Salesforce objects and fields for context:
{object_description_block}

🎯 Mapping Rules Based on Structure:
- **CRITICAL**: Use the `object_description_block` above to verify field existence BEFORE mapping
- **ONLY map to fields that are listed in the object's `fields` section** - do not assume fields exist
- Use the `is_required: true` fields to identify required fields
- Map extracted fields to the most appropriate Salesforce field that ACTUALLY EXISTS in the object
- For lookup objects, ensure you include the `lookup_of` reference
- Required fields should be included even if missing from extracted data (but only if they exist in the object)

✅ **FINAL VALIDATION CHECKLIST (MUST VERIFY BEFORE RETURNING)**:
1. **For EVERY field that maps to a lookup object** (Account, Contact, etc.):
   - ✅ Does it have a `lookup_of` key?
   - ✅ Is the `lookup_of` value in the format `"BaseObject.LookupFieldName"` (e.g., `"Contact.AccountId"`)?
   - ✅ Does the `lookup_of` reference the correct lookup field from the base object?
   
2. **For ALL fields mapping to the SAME lookup object**:
   - ✅ Do they ALL have the SAME `lookup_of` value?
   - ✅ Example: If AccountName and AccountWebsite both map to Account, 
     both must have `"lookup_of": "Contact.AccountId"` (or correct field name)

3. **If you mapped any fields to lookup objects but forgot `lookup_of`**:
   - ❌ GO BACK and add `lookup_of` to ALL of them
   - ❌ DO NOT return the mapping without `lookup_of` on lookup object fields

📦 Sample Data for Mapping:
{json.dumps(safe_json_serialize(sample_data), indent=2)}
"""
        elif use_sf_extract_keys:
            mapping_context_section = ""
            if mapping_context_summary:
                mapping_context_section = f"""
📝 **User's Extraction Request Context:**
{mapping_context_summary}

Use this context to:
- Prioritize objects/fields mentioned by the user
- Understand the user's intent and requirements
- Make more accurate mapping decisions
"""
            prompt = f"""
You are Archian AI, a Salesforce Cloud data architect. Your job is to map each field from the sample data to the most appropriate Salesforce object and field.

✅ **DATA USAGE**: The data below contains real field names and values for mapping analysis. 
Use the field names and values to create accurate mapping rules that can be applied to real customer data.

📄 Document Type: {document_type}
{mapping_context_section}

🚨 **CRITICAL OWNERID RULE - READ FIRST**:
- **If OwnerId field value is missing or empty, DO NOT include OwnerId in the mapping at all**
- **Salesforce will automatically assign the connected user as OwnerId when creating records**
- **This rule takes precedence over all other mapping rules**

🎯 Your priorities are as follows:
1. **CRITICAL - SELECT ONLY ONE BASE OBJECT**: You must select ONLY ONE base object from this list: {', '.join(base_objects)}.
   - **Use mapping_context_summary to guide selection**: If the user mentioned a specific object (e.g., "Contact object", "Lead object"), prioritize that object
   - **If no specific object mentioned**: Choose the base object that best matches the extracted schema fields (field names and data types)
   - **Map ALL source fields to this SINGLE selected base object** (and its lookup objects if needed)
   - **DO NOT map fields to multiple different base objects** - choose ONE and stick with it

🚨 **CRITICAL - FIELD EXISTENCE VERIFICATION (MUST READ)**:
- **ONLY map to fields that ACTUALLY EXIST in the object_description_block below**
- **BEFORE mapping any field, you MUST verify it exists in the object's field list in the "📚 Reference" section**
- **Custom objects may NOT have standard fields** like `FirstName`, `LastName`, `Email`, `Phone`, etc.
- **DO NOT assume standard fields exist** - always check the object_description_block first
- **If a field doesn't exist in the object_description_block, DO NOT map to it** - find an alternative field that does exist

2. When multiple objects contain semantically similar fields (e.g., "Title", "Occupation", "Name", "Email"), always prioritize mapping to the equivalent field on the selected base object first.
3. Only map to the **lookup objects**: {', '.join(lookup_objects)} if:
   - The field clearly refers to an external or referenced entity (e.g., a separate company, advisor, or household).
   - The selected base object does not have a suitable matching field.
4. Use **custom field suggestions** *only if* no match exists in either the selected base object or lookup objects.

📌 Required Field Handling (Very Important):
- **🚨 CRITICAL - DO NOT MAP LOOKUP/REFERENCE FIELDS DIRECTLY**:
  - **DO NOT include lookup/reference fields** (like `AccountId`, `ContactId`, `OpportunityId`, etc.) as direct field mappings in your output
  - **These fields are NOT in the source data** and should NOT be mapped
  - **These fields are automatically populated** when you add `lookup_of` to fields mapping to the lookup object
  - **Example**: If "AccountName" from source data maps to `Account.Name`, add `"lookup_of": "Contact.AccountId"` to the AccountName mapping. DO NOT add a separate "AccountId" field mapping - AccountId is not in the source data!
- **ONLY for the selected base object**:
  - **Include all its required fields in the mapping, even if the value is missing in the extracted data.**
  - **EXCEPTION**: Do NOT include lookup/reference fields (like `AccountId`, `ContactId`) even if they are required - handle these via `lookup_of` on lookup object fields
  - **DO NOT include required fields from other base objects** that you did not select
- For any lookup object that appears in the mapping:
  - Only include required fields **if at least one field from the extracted data maps to that lookup object**.
  - Do **not** include required fields for lookup objects if no extracted field is mapped to them.

⚠️ Missing Data Rules:
- For any required field not found in the extracted data:
  - Use `"value": "Unknown"` as a placeholder.
  - Add `"missing": true` to the mapping entry.
- **DO NOT create mappings for lookup/reference fields** (like `AccountId`, `ContactId`) - these are handled via `lookup_of` on lookup object fields, not as direct mappings

🚨 **CRITICAL OWNERID RULE - MUST FOLLOW**:
- **If OwnerId field value is missing or empty, DO NOT include OwnerId in the mapping at all**
- **Salesforce will automatically assign the connected user as OwnerId when creating records**
- **This rule takes precedence over all other required field rules**
- **DO NOT add OwnerId to mapping if the value is missing, empty, or "Required field"**

🚨 **CRITICAL RULE - READ CAREFULLY**:
- **NEVER map source fields directly to lookup FIELDS in the base object** (e.g., DO NOT map "Contact Name" to `Leave__c.ContactName__c`)
- **NEVER add lookup/reference fields** (like `AccountId`, `ContactId`) as direct mappings in your output - these are NOT source fields from the extracted data
- **ALWAYS map source fields to FIELDS IN THE LOOKUP OBJECT** (e.g., map "Contact Name" to `Contact.LastName` or `Contact.FirstName`)
- **THEN** add the `lookup_of` key to specify which lookup field in the base object creates the relationship
- **The lookup field ID is automatically resolved** from the lookup object fields - you don't need to map it directly

**HOW TO FIND THE CORRECT LOOKUP FIELD NAME FOR `lookup_of`:**
- Look at the `object_description_block` below (in the "📚 Reference" section)
- For the base object (e.g., "{base_objects[0] if base_objects else 'Contact'}"), examine its fields
- Find the field where:
  * `"data_type": "reference"` (this indicates a lookup/reference field)
  * `"is_lookup": true` is present
  * `"lookup_object"` matches the lookup object you're mapping to (e.g., "{first_lookup if lookup_objects else 'Account'}")
- Use the `field_api_name` of that field as the lookup field name for `lookup_of`
- **Pattern**: `"lookup_of": "{base_objects[0] if base_objects else 'Contact'}.{{actual_lookup_field_name_from_object_description_block}}"`
- **Example**: If source field is **AccountName** and you map it to `Account.Name`, use:
  ```json
  "AccountName": {{
    "object": "Account",
    "field": "Name",
    "lookup_of": "{base_objects[0] if base_objects else 'Contact'}.AccountId"
  }}
  ```
- **DO NOT assume the field name ends with "Id"** - always check the `object_description_block` for the actual field name
- Include mapped **lookup object** required fields as well, even if the data is missing.
- **WITHOUT `lookup_of`, the relationship will NOT be created in Salesforce!**

🔍 Duplicate Check Tagging (Important):
For every field in the final mapping:
1. Determine if the field should be used to check for existing records before inserting:
   - If yes → add `"is_duplicate_check": true`
   - If no → add `"is_duplicate_check": false`

2. Apply this rule to **both**:
   - Fields belonging to base objects (e.g. Contact.Email, Lead.Phone)
   - Fields belonging to any **lookup object** that appears in the mapping (e.g. Account.Name, Individual.LastName)

3. Use Salesforce best practices:
   - Prefer fields like `Email`, `Phone`, `Name`, `DUNS Number`, `Student ID`
   - Avoid soft-identifiers like `Title`, `Notes`, `Description`

4. Your output JSON **must include `is_duplicate_check` for every field**, even if false.

✅ Example Mapping and Output Format:
📢 Return only a single flat JSON object containing all mapped fields.
Do **not** include markdown, code blocks, explanations, or any other text.

{field_name_requirements}

💡 General Rule:
If a field maps to any lookup object, always include the `lookup_of` key with the corresponding reference field from the base object.
This rule applies to **all** lookup object mappings — not just the examples below.

⚠️ **CRITICAL REMINDER BEFORE OUTPUT**:
- **EVERY field that maps to a lookup object MUST have `lookup_of`**
- **ALL fields mapping to the same lookup object should have the SAME `lookup_of` value**
- **Example**: If "AccountName", "AccountWebsite", "AccountPhone" all map to Account object, 
  they ALL must have `"lookup_of": "Contact.AccountId"` (or the correct lookup field name)
- **Missing `lookup_of` on ANY lookup field will cause the migration to FAIL**

🧪 Example Output:
{example_mapping_safe}

📚 Reference: Use the following structured Salesforce objects and fields for context:
{object_description_block}

✅ **FINAL VALIDATION CHECKLIST (MUST VERIFY BEFORE RETURNING)**:
1. Every SOURCE COLUMN appears as a key in your JSON (exact spelling).
2. No invented keys (company, first_name, email).
3. AccountName and AccountPhone use lookup_of on Account fields; Contact Phone stays on Contact.

📦 Sample Data for Mapping:
{json.dumps(safe_json_serialize(sample_data), indent=2)}
"""
        else:
            # For file data, use the original prompt with normalization
            mapping_context_section = ""
            if mapping_context_summary:
                mapping_context_section = f"""
📝 **User's Extraction Request Context:**
{mapping_context_summary}

Use this context to:
- Prioritize objects/fields mentioned by the user
- Understand the user's intent and requirements
- Make more accurate mapping decisions
"""
            prompt = f"""
You are Archian AI, a Salesforce Cloud data architect. Your job is to map each field from the sample data to the most appropriate Salesforce object and field.

✅ **DATA USAGE**: The data below contains real field names and values for mapping analysis. 
Use the field names and values to create accurate mapping rules that can be applied to real customer data.

📄 Document Type: {document_type}
{mapping_context_section}

🚨 **CRITICAL OWNERID RULE - READ FIRST**:
- **If OwnerId field value is missing or empty, DO NOT include OwnerId in the mapping at all**
- **Salesforce will automatically assign the connected user as OwnerId when creating records**
- **This rule takes precedence over all other mapping rules**

🎯 Your priorities are as follows:
1. **CRITICAL - SELECT ONLY ONE BASE OBJECT**: You must select ONLY ONE base object from this list: {', '.join(base_objects)}.
   - **Use mapping_context_summary to guide selection**: If the user mentioned a specific object (e.g., "Contact object", "Lead object"), prioritize that object
   - **If no specific object mentioned**: Choose the base object that best matches the extracted schema fields (field names and data types)
   - **Map ALL source fields to this SINGLE selected base object** (and its lookup objects if needed)
   - **DO NOT map fields to multiple different base objects** - choose ONE and stick with it

🚨 **CRITICAL - FIELD EXISTENCE VERIFICATION (MUST READ)**:
- **ONLY map to fields that ACTUALLY EXIST in the object_description_block below**
- **BEFORE mapping any field, you MUST verify it exists in the object's field list in the "📚 Reference" section**
- **Custom objects may NOT have standard fields** like `FirstName`, `LastName`, `Email`, `Phone`, etc.
- **DO NOT assume standard fields exist** - always check the object_description_block first
- **If a field doesn't exist in the object_description_block, DO NOT map to it** - find an alternative field that does exist
- **Example**: If you want to map to `FirstName` but the object_description_block shows the object only has `Name` and `Email__c`, then map to `Name` instead
- **Verification Process**:
  1. Select your base object (e.g., `Customer_Contact_Details__c`)
  2. Find that object in the `object_description_block` below
  3. Check its `fields` section to see what fields actually exist
  4. ONLY map to fields that are listed in that `fields` section
  5. If the field you want doesn't exist, find the closest matching field that does exist

2. When multiple objects contain semantically similar fields (e.g., "Title", "Occupation", "Name", "Email"), always prioritize mapping to the equivalent field on the selected base object first.
3. Only map to the **lookup objects**: {', '.join(lookup_objects)} if:
   - The field clearly refers to an external or referenced entity (e.g., a separate company, advisor, or household).
   - The selected base object does not have a suitable matching field.
4. Use **custom field suggestions** *only if* no match exists in either the selected base object or lookup objects.

📌 Required Field Handling (Very Important):
- **🚨 CRITICAL - DO NOT MAP LOOKUP/REFERENCE FIELDS DIRECTLY**:
  - **DO NOT include lookup/reference fields** (like `AccountId`, `ContactId`, `OpportunityId`, etc.) as direct field mappings in your output
  - **These fields are NOT in the source data** and should NOT be mapped
  - **These fields are automatically populated** when you add `lookup_of` to fields mapping to the lookup object
  - **Example**: If "AccountName" from source data maps to `Account.Name`, add `"lookup_of": "Contact.AccountId"` to the AccountName mapping. DO NOT add a separate "AccountId" field mapping - AccountId is not in the source data!
- **ONLY for the selected base object**:
  - **Include all its required fields in the mapping, even if the value is missing in the extracted data.**
  - **EXCEPTION**: Do NOT include lookup/reference fields (like `AccountId`, `ContactId`) even if they are required - handle these via `lookup_of` on lookup object fields
  - **DO NOT include required fields from other base objects** that you did not select
- For any lookup object that appears in the mapping:
  - Only include required fields **if at least one field from the extracted data maps to that lookup object**.
  - Do **not** include required fields for lookup objects if no extracted field is mapped to them.

⚠️ Missing Data Rules:
- For any required field not found in the extracted data:
  - Use `"value": "Unknown"` as a placeholder.
  - Add `"missing": true` to the mapping entry.
- **DO NOT create mappings for lookup/reference fields** (like `AccountId`, `ContactId`) - these are handled via `lookup_of` on lookup object fields, not as direct mappings

🚨 **CRITICAL OWNERID RULE - MUST FOLLOW**:
- **If OwnerId field value is missing or empty, DO NOT include OwnerId in the mapping at all**
- **Salesforce will automatically assign the connected user as OwnerId when creating records**
- **This rule takes precedence over all other required field rules**
- **DO NOT add OwnerId to mapping if the value is missing, empty, or "Required field"**

🏷️ **RECORD TYPE RULE**:
- If the source data contains a field named `RecordTypeName` (or similar like `Record Type Name`), and the destination object has a `RecordTypeId` field in the object_description_block:
  - **Map `RecordTypeName` → `{{DestinationObject}}.RecordTypeId`** as a direct field mapping (NOT via lookup_of)
  - Example: `"RecordTypeName": {{"object": "Contact", "field": "RecordTypeId"}}`
  - The migration engine will automatically resolve the name string to the correct Salesforce RecordTypeId -- you just need to map it
  - The `description` of `RecordTypeId` in the object_description_block lists valid record type names for reference
- **DO NOT** use `lookup_of` for RecordTypeId -- it is resolved internally, not via a lookup query


🚨 **CRITICAL RULE - READ CAREFULLY**:
- **NEVER map source fields directly to lookup FIELDS in the base object** (e.g., DO NOT map "Contact Name" to `Leave__c.ContactName__c`)
- **NEVER add lookup/reference fields** (like `AccountId`, `ContactId`) as direct mappings in your output - these are NOT source fields from the extracted data
- **ALWAYS map source fields to FIELDS IN THE LOOKUP OBJECT** (e.g., map "Contact Name" to `Contact.LastName` or `Contact.FirstName`)
- **THEN** add the `lookup_of` key to specify which lookup field in the base object creates the relationship
- **The lookup field ID is automatically resolved** from the lookup object fields - you don't need to map it directly

**CORRECT Approach:**
1. Identify that a source field (e.g., "Contact Name") refers to a related entity (Contact)
2. Map it to a field IN the lookup object (e.g., `Contact.LastName`)
3. Add `lookup_of` to specify the lookup field in base object (e.g., `"lookup_of": "Leave__c.ContactName__c"`)

**INCORRECT Approach (DO NOT DO THIS):**
- Mapping "Contact Name" to `Leave__c.ContactName__c` directly ❌
- This would just put the text value into the lookup field ID, which won't work!

**HOW TO FIND THE CORRECT LOOKUP FIELD NAME FOR `lookup_of`:**
- Look at the `object_description_block` below (in the "📚 Reference" section)
- For the base object (e.g., "{base_objects[0] if base_objects else 'Contact'}"), examine its fields
- Find the field where:
  * `"data_type": "reference"` (this indicates a lookup/reference field)
  * `"is_lookup": true` is present
  * `"lookup_object"` matches the lookup object you're mapping to (e.g., "{first_lookup if lookup_objects else 'Account'}")
- Use the `field_api_name` of that field as the lookup field name for `lookup_of`
- **Pattern**: `"lookup_of": "{base_objects[0] if base_objects else 'Contact'}.{{actual_lookup_field_name_from_object_description_block}}"`
- **Example**: If source field is "Company Name" and you map it to `Account.Name`, and {base_objects[0] if base_objects else 'Contact'} has a field `AccountId` with `"data_type": "reference"`, `"is_lookup": true`, and `"lookup_object": "Account"`, then use:
  ```json
  "Company Name": {{
    "object": "Account",
    "field": "Name",
    "lookup_of": "{base_objects[0] if base_objects else 'Contact'}.AccountId"
  }}
  ```
- **DO NOT assume the field name ends with "Id"** - always check the `object_description_block` for the actual field name
- Include mapped **lookup object** required fields as well, even if the data is missing.
- **WITHOUT `lookup_of`, the relationship will NOT be created in Salesforce!**

🔍 Duplicate Check Tagging (Important):
For every field in the final mapping:
1. Determine if the field should be used to check for existing records before inserting:
   - If yes → add `"is_duplicate_check": true`
   - If no → add `"is_duplicate_check": false`

2. Apply this rule to **both**:
   - Fields belonging to base objects (e.g. Contact.Email, Lead.Phone)
   - Fields belonging to any **lookup object** that appears in the mapping (e.g. Account.Name, Individual.LastName)

3. Use Salesforce best practices:
   - Prefer fields like `Email`, `Phone`, `Name`, `DUNS Number`, `Student ID`
   - Avoid soft-identifiers like `Title`, `Notes`, `Description`

4. Your output JSON **must include `is_duplicate_check` for every field**, even if false.

🧪 **Ordering**
   - Arrange Output in this order:
     1. Records with **missing required fields**
     2. Records with **invalid or malformed values**
     3. All other records

✅ Example Mapping and Output Format:
📢 Return only a single flat JSON object containing all mapped fields.
Do **not** include markdown, code blocks, explanations, or any other text.

🚨 **CRITICAL - FIELD NAME REQUIREMENTS**:
- Each key in the final JSON must be the EXACT FIELD NAME from the sample data below
- Use the EXACT field names like "email", "first_name", "last_name", "phone", "company" (as shown in sample data)
- Do **NOT** use Salesforce field names like "Email", "FirstName", "LastName" as keys
- Do **NOT** change the field names - use them exactly as they appear in the sample data
- The value for each key must be an object describing the Salesforce mapping.

💡 General Rule:
If a field maps to any lookup object, always include the `lookup_of` key with the corresponding reference field from the base object.
This rule applies to **all** lookup object mappings — not just the examples below.

⚠️ **CRITICAL REMINDER BEFORE OUTPUT**:
- **EVERY field that maps to a lookup object MUST have `lookup_of`**
- **ALL fields mapping to the same lookup object should have the SAME `lookup_of` value**
- **Example**: If "AccountName", "AccountWebsite", "AccountPhone" all map to Account object, 
  they ALL must have `"lookup_of": "Contact.AccountId"` (or the correct lookup field name)
- **Missing `lookup_of` on ANY lookup field will cause the migration to FAIL**

🧪 Example Output:
{example_mapping_safe}

📚 Reference: Use the following structured Salesforce objects and fields for context:
{object_description_block}

🎯 Mapping Rules Based on Structure:
- **CRITICAL**: Use the `object_description_block` above to verify field existence BEFORE mapping
- **ONLY map to fields that are listed in the object's `fields` section** - do not assume fields exist
- Use the `is_required: true` fields to identify required fields
- Map extracted fields to the most appropriate Salesforce field that ACTUALLY EXISTS in the object
- For lookup objects, ensure you include the `lookup_of` reference
- Required fields should be included even if missing from extracted data (but only if they exist in the object)

✅ **FINAL VALIDATION CHECKLIST (MUST VERIFY BEFORE RETURNING)**:
1. **For EVERY field that maps to a lookup object** (Account, Contact, etc.):
   - ✅ Does it have a `lookup_of` key?
   - ✅ Is the `lookup_of` value in the format `"BaseObject.LookupFieldName"` (e.g., `"Contact.AccountId"`)?
   - ✅ Does the `lookup_of` reference the correct lookup field from the base object?
   
2. **For ALL fields mapping to the SAME lookup object**:
   - ✅ Do they ALL have the SAME `lookup_of` value?
   - ✅ Example: If AccountName and AccountWebsite both map to Account, 
     both must have `"lookup_of": "Contact.AccountId"` (or correct field name)

3. **If you mapped any fields to lookup objects but forgot `lookup_of`**:
   - ❌ GO BACK and add `lookup_of` to ALL of them
   - ❌ DO NOT return the mapping without `lookup_of` on lookup object fields

📦 Sample Data for Mapping:
{json.dumps(safe_json_serialize(sample_data), indent=2)}
"""


        # print("prompt ", prompt)  # Commented out debug print


        response = await async_openai_call(
            None,
            task=llm_tasks.SALESFORCE_MAPPING,
            messages=[
                {
                    "role": "system",
                    "content": "You are Archian AI, a Salesforce Cloud architect specializing in object and field mappings."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.0
        )

        reply = response.choices[0].message.content
        
        mapping = parse_openai_json_response(reply)

        from utils.mapping_utils import (
            align_mapping_keys_to_extracted_data,
            flat_data_uses_salesforce_extract_keys,
            repair_salesforce_mapping_to_extract_keys,
        )

        if not is_sql_data and flat_data_uses_salesforce_extract_keys(flat_data):
            mapping = repair_salesforce_mapping_to_extract_keys(mapping, flat_data)
            print("🔧 [MAP_SF] Repaired mapping keys to Salesforce extract columns")

        mapping = align_mapping_keys_to_extracted_data(mapping, flat_data)

        from utils.sf_mapping_lookup_sanitize import sanitize_salesforce_mapping_lookup_of

        mapping, _ = sanitize_salesforce_mapping_lookup_of(
            mapping,
            objects_structure if isinstance(objects_structure, dict) else {},
            base_objects,
        )

        # Print mapping in readable JSON format
        print("🔍 [SALESFORCE MAPPING] Mapping result:")
        print(json.dumps(mapping, indent=2, default=str))
        # Step 2: Analyze if transformations are needed (e.g., name splitting)
        mapping_audit_payload: Optional[Dict[str, Any]] = None
        if skip_generic_transform_analysis:
            transformation_rules = {"fields": []}
            enhanced_mapping_entries = {}
        else:
            transformation_analysis = await analyze_mapping_transformations(
                mapping=mapping,
                flat_data=flat_data,
                object_description_block=object_description_block,
                base_objects=base_objects,
                lookup_objects=lookup_objects,
                sample_data=sample_data,
                mapping_context_summary=mapping_context_summary,
            )
            transformation_rules = transformation_analysis.get("transformation_rules", {"fields": []})
            enhanced_mapping_entries = transformation_analysis.get("enhanced_mapping", {})
            mapping_audit_payload = transformation_analysis.get("mapping_audit")
        
        from utils.mapping_utils import resolve_extracted_field_key

        skipped_fields: set = set()
        if enhanced_mapping_entries:
            from utils.mapping_audit import apply_enhanced_mapping_safely

            print(
                f"🔧 Applying {len(enhanced_mapping_entries)} enhanced mapping entries (sanitized)"
            )
            mapping, skipped_fields = apply_enhanced_mapping_safely(
                mapping, enhanced_mapping_entries, flat_data
            )
            for key in skipped_fields:
                print(f"   ⏭️ Skipped invalid transform for '{key}'")
        
        if skipped_fields and transformation_rules.get("fields"):
            data_keys = list((flat_data or {}).keys())
            original_count = len(transformation_rules["fields"])
            transformation_rules["fields"] = [
                rule for rule in transformation_rules["fields"]
                if rule.get("source_field") not in skipped_fields
                and resolve_extracted_field_key(
                    str(rule.get("source_field") or ""), data_keys
                )
                not in skipped_fields
                and not (
                    rule.get("action", "").startswith("split_")
                    and len(rule.get("output_fields", [])) == 1
                )
            ]
            filtered_count = len(transformation_rules["fields"])
            if original_count != filtered_count:
                print(
                    f"   🔧 Filtered transformation_rules: {original_count} → {filtered_count}"
                )

        mapping = align_mapping_keys_to_extracted_data(mapping, flat_data)

        # Step 4: Refine mapping (required fields / primary object filter)
        primary = (pinned_base_object or "").strip()
        if not primary and len(base_objects) == 1:
            primary = str(base_objects[0] or "").strip()

        if primary:
            from services.mapping.required_fields import inject_required_salesforce_fields

            refined_mapping = await inject_required_salesforce_fields(
                mapping,
                flat_data,
                base_objects=base_objects,
                lookup_objects=lookup_objects,
                user_id=user_id,
                org_id=org_id,
                primary_base_object=primary,
            )
        else:
            # Legacy path only — direct /map-fields without pinned target or single-object schema
            refined_mapping = await evaluate_and_refine_mapping(
                mapping, flat_data, base_objects, lookup_objects, user_id, org_id=org_id
            )
        refined_mapping = align_mapping_keys_to_extracted_data(refined_mapping, flat_data)
        refined_mapping, _ = sanitize_salesforce_mapping_lookup_of(
            refined_mapping,
            objects_structure if isinstance(objects_structure, dict) else {},
            base_objects,
        )

        from utils.mapping_audit import audit_mapping_structure, audit_result_to_dict

        mapping_audit_payload = audit_result_to_dict(
            audit_mapping_structure(
                refined_mapping,
                flat_data or {},
                destination_type="salesforce",
                base_objects=base_objects,
                document_type=document_type,
            )
        )
        # Step 5: Store transformation_rules separately (will be returned in response)
        # Note: transformation_rules will be attached to the response, not the mapping dict
        # to avoid breaking code that iterates over mapping.items()
        
        return {
            "mapping": refined_mapping,
            "transformation_rules": transformation_rules,
            "mapping_audit": mapping_audit_payload,
        }

    except Exception as e:
        print("Architect agent error:", e)
        import traceback
        traceback.print_exc()
        # Return empty mapping with no transformation rules on error
        return {
            "mapping": {},
            "transformation_rules": {"fields": []},
            "mapping_audit": None,
        }


async def categorize_salesforce_objects_in_batches(user_id: UUID, org_id: str, batch_size=10):
    """
    Deprecated: delegates to unified EntityLabelingService (background queue).
    Returns the number of entities queued in this batch (0 when none remain).
    """
    import logging

    from db.connection import database
    from services.connectors.salesforce_ref import resolve_salesforce_token
    from services.labeling.schedule import schedule_entity_labeling

    logger = logging.getLogger(__name__)
    logger.info(
        "categorize_salesforce_objects_in_batches is deprecated; using EntityLabelingService"
    )

    token = await resolve_salesforce_token(str(user_id), org_id)
    if not token:
        return 0

    connector_ref = {
        "connector_id": token["connector_id"],
        "org_id": org_id,
    }

    rows = await database.fetch_all(
        """
        SELECT object_api_name FROM salesforce_objects
        WHERE user_id = :user_id AND org_id = :org_id
          AND COALESCE(is_deleted, FALSE) = FALSE
          AND category IS NULL
        ORDER BY object_api_name
        LIMIT :limit
        """,
        {"user_id": str(user_id), "org_id": org_id, "limit": batch_size},
    )
    keys = [dict(r).get("object_api_name") for r in rows or [] if dict(r).get("object_api_name")]
    if not keys:
        return 0

    await schedule_entity_labeling(
        user_id=str(user_id),
        connector_type="salesforce",
        connector_ref=connector_ref,
        entity_keys=keys,
    )
    return len(keys)


async def detect_salesforce_org_from_prompt(
    prompt: str, 
    user_id: str, 
    conversation_history: list = None,
    intent_state: Optional[Dict] = None,
    source_name: Optional[str] = None
) -> dict:
    """
    Detect Salesforce Org details from user prompt and conversation.
    Searches both current prompt AND conversation history for org names.
    If intent_state is provided, uses org from intent_state.
    
    Returns dict with connection details, or None if not detected.
    """
    try:
        import re
        from db.connection import database
        from typing import Dict, Optional
        
        # Fetch user's Salesforce orgs from salesforce_tokens (org_display_name is the display name)
        available_orgs_raw = await database.fetch_all(
            """
            SELECT 
                connected_app_id,
                org_display_name AS connection_name,
                environment_type,
                instance_url,
                org_id,
                id AS token_id,
                true AS is_connected,
                is_default
            FROM salesforce_tokens
            WHERE user_id = :user_id
            ORDER BY is_default DESC NULLS LAST, issued_at DESC
            """,
            {"user_id": user_id}
        )

        available_orgs = [dict(org) for org in available_orgs_raw] if available_orgs_raw else []
        
        # Format orgs for the AI prompt
        import json
        org_list = []
        if available_orgs:
            for org in available_orgs:
                org_list.append({
                    "connected_app_id": org.get("connected_app_id"),
                    "connection_name": org.get("connection_name") or "Salesforce",
                    "environment_type": org.get("environment_type"),
                    "is_connected": org.get("is_connected"),
                    "instance_url": org.get("instance_url"),
                    "org_id": org.get("org_id"),
                    "token_id": str(org.get("token_id")) if org.get("token_id") else None,
                    "is_default": bool(org.get("is_default")),
                })
        
        # If no orgs available, return not found
        if not org_list:
            return {
                "found": False,
                "message": "No Salesforce orgs configured. Please connect a Salesforce org first."
            }
        
        # PRIORITY 1: Check if source_name matches any org's connection_name
        # This is the highest priority since it comes from classification (which analyzed full context)
        if source_name:
            source_name_normalized = source_name.strip()
            for org in org_list:
                org_name = org.get("connection_name")
                if org_name and org_name.strip().lower() == source_name_normalized.lower():
                    print(f"✅ Found org '{org_name}' from source_name parameter")
                    return {
                        "found": True,
                        "connection_name": org.get("connection_name"),
                        "org_id": org.get("org_id"),
                        "connected_app_id": org.get("connected_app_id"),
                        "token_id": org.get("token_id"),
                        "instance_url": org.get("instance_url"),
                        "is_connected": org.get("is_connected"),
                        "environment_type": org.get("environment_type"),
                        "source": "source_name"
                    }
        
        # PRIORITY 2: Check if user's prompt explicitly mentions an org name
        # Only use intent_state if prompt doesn't mention a specific org (clarification scenario)
        prompt_lower = prompt.lower()
        mentioned_org = None
        for org in org_list:
            org_name = org.get("connection_name")
            if org_name and org_name.lower() in prompt_lower:
                mentioned_org = org
                print(f"✅ Found org '{org_name}' explicitly mentioned in prompt")
                break
        
        # If user explicitly mentioned an org, use it (don't use intent_state)
        if mentioned_org:
            print(f"✅ Using org explicitly mentioned in prompt: {mentioned_org.get('connection_name')}")
            return {
                "found": True,
                "connection_name": mentioned_org.get("connection_name"),
                "org_id": mentioned_org.get("org_id"),
                "connected_app_id": mentioned_org.get("connected_app_id"),
                "token_id": mentioned_org.get("token_id"),
                "instance_url": mentioned_org.get("instance_url"),
                "is_connected": mentioned_org.get("is_connected"),
                "environment_type": mentioned_org.get("environment_type"),
            }
        
        # PRIORITY 3: If no explicit mention, check intent_state (for clarification flows)
        # Only use intent_state if prompt is short (likely a clarification)
        prompt_words = prompt.strip().split()
        is_short_clarification = len(prompt_words) <= 5  # Very short prompts like "yes", "that one", "first name, email"
        
        if intent_state and intent_state.get("active_source") == "salesforce" and is_short_clarification:
            source_name = intent_state.get("source_name")
            if source_name:
                # Find matching org from available_orgs
                for org in org_list:
                    if org.get("connection_name") == source_name:
                        print(f"✅ Using org from active intent_state (clarification): {source_name}")
                        return {
                            "found": True,
                            "connection_name": org.get("connection_name"),
                            "org_id": org.get("org_id"),
                            "connected_app_id": org.get("connected_app_id"),
                            "token_id": org.get("token_id"),
                            "instance_url": org.get("instance_url"),
                            "is_connected": org.get("is_connected"),
                            "environment_type": org.get("environment_type"),
                        }
        
        # PRIORITY 4: Search conversation_history for org names (most recent first)
        if conversation_history:
            print(f"🔍 Searching conversation history for Salesforce org names...")
            org_names = [org.get("connection_name") for org in org_list]
            for msg in reversed(conversation_history):
                msg_text = msg.get('text', '') or msg.get('content', '')
                if not msg_text:
                    continue
                
                # Check if any org name appears in this message
                for org_name in org_names:
                    if org_name.lower() in msg_text.lower():
                        # Find matching org
                        for org in org_list:
                            if org.get("connection_name") == org_name:
                                print(f"✅ Found org '{org_name}' in conversation history: '{msg_text[:100]}...'")
                                return {
                                    "found": True,
                                    "connection_name": org.get("connection_name"),
                                    "org_id": org.get("org_id"),
                                    "connected_app_id": org.get("connected_app_id"),
                                    "token_id": org.get("token_id"),
                                    "instance_url": org.get("instance_url"),
                                    "is_connected": org.get("is_connected"),
                                    "environment_type": org.get("environment_type"),
                                }
        
        # Build conversation context for AI detection
        conversation_context = ""
        if conversation_history:
            # Include recent messages (last 5) to help AI find org name
            recent_messages = conversation_history[-5:] if len(conversation_history) > 5 else conversation_history
            conversation_context = "\n\n**Recent Conversation History:**\n"
            for msg in recent_messages:
                msg_type = msg.get('type', msg.get('role', 'user'))
                msg_text = msg.get('text', '') or msg.get('content', '')
                if msg_text:
                    role_label = "User" if msg_type in ['user', 'User'] else "Assistant"
                    conversation_context += f"{role_label}: {msg_text}\n"
        
        # PRIORITY 5: Use AI to detect Salesforce org from conversation (final fallback)
        detection_prompt = f"""
        Analyze the user's conversation and identify which Salesforce org they're referring to.
        
        **AVAILABLE SALESFORCE ORGS FOR THIS USER:**
        {json.dumps(org_list, indent=2)}
        
        **Current User Message:**
        "{prompt}"
        {conversation_context}
        
        **INSTRUCTIONS:**
        1. First, check if the user mentions any connection name or environment type that matches the AVAILABLE ORGS above
        2. Consider context clues:
           - "production", "prod", "live" → production environment
           - "sandbox", "test", "dev", "development" → sandbox environment
           - Specific connection names mentioned by the user
        3. If user says "my Salesforce" or "Salesforce" and only ONE org exists, use that one
        4. If user says "my Salesforce" or "Salesforce" and only ONE org is CONNECTED, use that one (prefer connected over not connected)
        
        **MATCHING RULES:**
        - Prefer CONNECTED orgs over NOT CONNECTED orgs
        - If user mentions environment type (production/sandbox), match that
        - If user mentions a specific connection name, match exactly
        - If ambiguous and multiple orgs exist, set "found": false and "needs_clarification": true
        
        Return your response in this EXACT JSON format:
        {{
            "connection_name": "Exact name from available list",
            "environment_type": "production or sandbox",
            "is_connected": true or false,
            "found": true or false,
            "needs_clarification": false,
            "confidence": 0.85
        }}
        
        **Examples:**
        
        Available: [{{"connection_name": "Production Org", "environment_type": "production", "is_connected": true}}]
        User: "Migrate contacts to Salesforce"
        Response: {{"connection_name": "Production Org", "environment_type": "production", "is_connected": true, "found": true, "needs_clarification": false, "confidence": 0.9}}
        
        Available: [{{"connection_name": "Dev Sandbox", "environment_type": "sandbox", "is_connected": true}}, {{"connection_name": "Prod Org", "environment_type": "production", "is_connected": true}}]
        User: "Push data to my production Salesforce"
        Response: {{"connection_name": "Prod Org", "environment_type": "production", "is_connected": true, "found": true, "needs_clarification": false, "confidence": 0.95}}
        
        Available: [{{"connection_name": "Sandbox 1", "environment_type": "sandbox", "is_connected": true}}, {{"connection_name": "Sandbox 2", "environment_type": "sandbox", "is_connected": false}}]
        User: "Migrate to my sandbox"
        Response: {{"connection_name": "Sandbox 1", "environment_type": "sandbox", "is_connected": true, "found": true, "needs_clarification": false, "confidence": 0.8}}
        
        Available: [{{"connection_name": "Org A", "environment_type": "production", "is_connected": true}}, {{"connection_name": "Org B", "environment_type": "production", "is_connected": true}}]
        User: "Migrate to Salesforce"
        Response: {{"connection_name": null, "environment_type": null, "is_connected": null, "found": false, "needs_clarification": true, "confidence": 0.0}}
        
        Available: [{{"connection_name": "My Salesforce", "environment_type": "production", "is_connected": false}}]
        User: "Migrate to Salesforce"
        Response: {{"connection_name": "My Salesforce", "environment_type": "production", "is_connected": false, "found": true, "needs_clarification": false, "confidence": 0.7}}
        
        Return ONLY the JSON, no other text.
        """
        
        from services.architect_agent import async_openai_call
        
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.SALESFORCE_INFO,
            messages=[{"role": "user", "content": detection_prompt}],
            max_tokens=500,
            temperature=0.3,
        )
        
        ai_response = response.choices[0].message.content.strip()
        
        # Parse AI response - handle markdown code blocks
        # Use centralized JSON parsing function (handles markdown code blocks, JSON fixing, etc.)
        from utils.openai_helpers import parse_openai_json_response
        try:
            detection_result = parse_openai_json_response(ai_response)
        except (ValueError, json.JSONDecodeError) as e:
            print(f"❌ Error parsing detection JSON response: {e}")
            raise ValueError("No valid JSON found in AI response")
        
        # Check if org was found
        if detection_result.get("found", False):
            # Look up org details from already-fetched data (no second query needed)
            connection_name = detection_result.get("connection_name")
            environment_type = detection_result.get("environment_type")
            
            if connection_name:
                # Find matching org from available_orgs
                org_result = next(
                    (
                        org for org in available_orgs
                        if org.get("connection_name") == connection_name
                        and org.get("environment_type") == environment_type
                    ),
                    None,
                )
                
                if org_result:
                    result = {
                        "connected_app_id": org_result.get("connected_app_id"),
                        "connection_name": org_result.get("connection_name"),
                        "environment_type": org_result.get("environment_type"),
                        "is_connected": org_result.get("is_connected"),
                        "instance_url": org_result.get("instance_url") if org_result.get("is_connected") else None,
                        "org_id": org_result.get("org_id"),
                        "token_id": str(org_result.get("token_id")) if org_result.get("token_id") else None,
                        "source": "ai_detection",
                        "found": True,
                        "needs_clarification": False,
                        "confidence": detection_result.get("confidence", 0.5)
                    }
                    return result
        
        # Check if clarification is needed
        if detection_result.get("needs_clarification", False):
            org_names = [
                f"{org['connection_name']} ({org['environment_type']})"
                + (" - Default" if org.get("is_default") else "")
                for org in org_list
            ]
            return {
                "found": False,
                "needs_clarification": True,
                "message": f"You have multiple Salesforce orgs. Please specify which one: {', '.join(org_names)}",
                "available_orgs": org_list
            }
        
        # Not found
        org_names = [
            f"{org['connection_name']} ({org['environment_type']})"
            + (" - Default" if org.get("is_default") else "")
            for org in org_list
        ]
        return {
            "found": False,
            "message": f"Could not determine which Salesforce org to use. Available: {', '.join(org_names)}",
            "available_orgs": org_list
        }
            
    except Exception as e:
        print(f"❌ Error detecting Salesforce org from prompt: {e}")
        return None


def _object_api_names_from_salesforce_mapping(mapping: Any) -> List[str]:
    """Distinct Salesforce object API names referenced by the architect mapping (object field on each entry)."""
    if not isinstance(mapping, dict):
        return []
    seen: set = set()
    for _key, val in mapping.items():
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    o = (item.get("object") or "").strip()
                    if o:
                        seen.add(o)
        elif isinstance(val, dict):
            o = (val.get("object") or "").strip()
            if o:
                seen.add(o)
    return sorted(seen)


async def map_to_salesforce(
    flat_data: dict,
    document_type: str,
    document_description: str,
    user_id,
    is_sql_data: bool = False,
    org_id: Optional[str] = None,
    token_id: Optional[str] = None,
    destination_name: Optional[str] = None,
    mapping_context_summary: Optional[str] = None,
    explicit_target_object_api_name: Optional[str] = None,
    *,
    skip_generic_transform_analysis: bool = False,
    plan_pinned_entity: bool = False,
):
    """
    Map extracted data fields to Salesforce objects and fields.
    
    Args:
        flat_data: First record from extracted data (dict with field names and values)
        document_type: Category/document type
        document_description: Description of the document
        user_id: User ID (UUID)
        is_sql_data: Whether the data is from SQL extraction
        
    Returns:
        dict: Mapping result with structure:
            {
                "document_type": str,
                "document_description": str,
                "mapping": dict,  # Field mappings
                "available_objects": dict  # Available Salesforce objects and fields
            }
    """
    try:
        from db.connection import database
        from utils.document_category_match import reconcile_document_category, apply_reconcile_to_tuple
        from services.mapping.schema_loader import load_salesforce_mapping_schema
        from services.mapping.required_fields import annotate_mapping_field_metadata

        pinned_target = (explicit_target_object_api_name or "").strip()
        if not pinned_target and not plan_pinned_entity:
            reconcile = await reconcile_document_category(
                user_id,
                "salesforce",
                destination_name,
                document_type,
                document_description,
                flat_data,
            )
            document_type, document_description = apply_reconcile_to_tuple(
                document_type, document_description, reconcile
            )

        if not pinned_target and not plan_pinned_entity and org_id and mapping_context_summary:
            from services.mapping_plan.catalog_target_resolver import (
                resolve_salesforce_destination_from_catalog,
            )

            catalog_hint = (
                await resolve_salesforce_destination_from_catalog(
                    user_id=user_id,
                    org_id=org_id,
                    prompt=mapping_context_summary,
                )
                or ""
            ).strip()
            if catalog_hint:
                pinned_target = catalog_hint

        schema = await load_salesforce_mapping_schema(
            user_id=user_id,
            org_id=org_id,
            flat_data=flat_data if isinstance(flat_data, dict) else {},
            document_type=document_type,
            document_description=document_description,
            explicit_target_object_api_name=pinned_target or explicit_target_object_api_name,
            destination_name=destination_name,
        )

        if not pinned_target and not plan_pinned_entity and not (explicit_target_object_api_name or "").strip():
            from utils.salesforce_mapping_target import apply_mapping_target_object_override

            override_bases, document_type, document_description, override_api = (
                await apply_mapping_target_object_override(
                    user_id=user_id,
                    org_id=org_id,
                    mapping_context_summary=mapping_context_summary,
                    base_object_names=schema.base_object_names,
                    document_type=schema.document_type,
                    document_description=schema.document_description,
                )
            )
            if override_api and str(override_api).strip() not in schema.base_object_names:
                schema = await load_salesforce_mapping_schema(
                    user_id=user_id,
                    org_id=org_id,
                    flat_data=flat_data if isinstance(flat_data, dict) else {},
                    document_type=document_type,
                    document_description=document_description,
                    explicit_target_object_api_name=str(override_api).strip(),
                    destination_name=destination_name,
                )
            elif override_bases and override_bases != schema.base_object_names:
                schema = await load_salesforce_mapping_schema(
                    user_id=user_id,
                    org_id=org_id,
                    flat_data=flat_data if isinstance(flat_data, dict) else {},
                    document_type=document_type,
                    document_description=document_description,
                    explicit_target_object_api_name=str(override_bases[0]).strip(),
                    destination_name=destination_name,
                )
        document_type = schema.document_type
        document_description = schema.document_description
        base_object_names = schema.base_object_names
        lookup_object_names = schema.lookup_object_names
        objects_structure = schema.objects_structure
        object_description_block = schema.object_description_block
        available_objects = schema.available_objects

        pinned_base = pinned_target or schema.target_object_api_name
        mapping_result = await call_salesforce_architect_agent(
            flat_data=flat_data,
            document_type=document_type,
            object_description_block=object_description_block,
            relevant_objects={
                "base": base_object_names,
                "lookup": lookup_object_names,
            },
            user_id=user_id,
            is_sql_data=is_sql_data,
            org_id=org_id,
            mapping_context_summary=mapping_context_summary,
            skip_generic_transform_analysis=skip_generic_transform_analysis,
            pinned_base_object=pinned_base if (plan_pinned_entity or pinned_target) else None,
        )

        mapping_audit_payload = None
        if isinstance(mapping_result, dict) and "mapping" in mapping_result:
            mapping = mapping_result["mapping"]
            transformation_rules = mapping_result.get("transformation_rules", {"fields": []})
            mapping_audit_payload = mapping_result.get("mapping_audit")
        else:
            mapping = mapping_result if isinstance(mapping_result, dict) else {}
            transformation_rules = {"fields": []}

        from utils.mapping_destination_validate import (
            filter_salesforce_mapping_to_destination_schema,
        )
        from utils.sf_mapping_lookup_sanitize import sanitize_salesforce_mapping_lookup_of

        mapping = filter_salesforce_mapping_to_destination_schema(
            mapping, objects_structure
        )
        mapping, _ = sanitize_salesforce_mapping_lookup_of(
            mapping,
            objects_structure,
            base_object_names,
        )

        mapping = await annotate_mapping_field_metadata(
            mapping,
            user_id=user_id,
            org_id=org_id,
        )

        # Display-only org rules for objects actually used in the returned mapping (not all lookup candidates)
        objs_for_rules = _object_api_names_from_salesforce_mapping(mapping)
        if not objs_for_rules and base_object_names:
            objs_for_rules = [base_object_names[0]]

        org_rules_display_by_object: Dict[str, Any] = {}
        if org_id and objs_for_rules:
            try:
                from services.salesforce_org_rules_service import build_org_rules_display_for_object

                rules_rows = await database.fetch_all(
                    """
                    SELECT object_api_name, validation_rules_json, duplicate_rules_json
                    FROM salesforce_objects
                    WHERE user_id = :user_id AND org_id = :org_id
                      AND object_api_name = ANY(:objs)
                    """,
                    values={
                        "user_id": str(user_id),
                        "org_id": org_id,
                        "objs": list(objs_for_rules),
                    },
                )
                for rr in rules_rows or []:
                    oan = rr["object_api_name"]
                    org_rules_display_by_object[oan] = build_org_rules_display_for_object(
                        oan,
                        rr["validation_rules_json"],
                        rr["duplicate_rules_json"],
                    )
            except Exception as exc:
                print(f"⚠️ [map_to_salesforce] Could not load org_rules_display: {exc}")

        from utils.migration_rules_utils import dual_publish_mapping_transform_api

        from utils.mapping_transform_context import source_fields_from_flat_data

        sf_map_result = {
            "destination": "salesforce",
            "document_type": document_type,
            "document_description": document_description,
            "mapping": mapping,
            "available_objects": available_objects,
            "source_fields": source_fields_from_flat_data(flat_data if isinstance(flat_data, dict) else {}),
            "org_id": org_id,
            "token_id": token_id,
            "connector_id": token_id,
            "destination_name": destination_name,
            "org_rules_display_by_object": org_rules_display_by_object,
            "mapping_audit": mapping_audit_payload,
        }
        dual_publish_mapping_transform_api(sf_map_result, transformation_rules)
        return sf_map_result

    except Exception as e:
        print(f"❌ Error in map_to_salesforce: {e}")
        import traceback
        traceback.print_exc()
        raise
