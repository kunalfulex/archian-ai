"""
Prompt Handler - Centralized AI Prompt Management
Manages all AI prompts for data extraction with support for multi-destination migration
"""

import json
from typing import Dict, Any, List, Optional


class PromptBuilder:
    """
    Builds AI prompts dynamically based on destination and extraction context.
    Currently supports: Salesforce (default), with architecture ready for Google Sheets, SQL, etc.
    """
    
    @staticmethod
    def get_base_instructions(extraction_goals: str, plan_limit_instruction: str, source: str = "file") -> str:
        """
        Base instructions that apply to all destinations
        
        Args:
            extraction_goals: User's extraction goals/requirements
            plan_limit_instruction: Plan limit instructions
            source: Data source type ("file", "salesforce", "google_sheets", "sql")
        """
        # Determine data context and task description based on source
        if source == "file":
            data_context = "structured tabular data extracted from a spreadsheet, image, or raw text file"
            task_description = "extract and return a structured JSON representation of this data"
        elif source == "salesforce":
            data_context = "structured data records extracted from Salesforce"
            task_description = "classify, transform, and prepare this data for migration according to the user's requirements"
        elif source == "google_sheets":
            data_context = "structured data records extracted from Google Sheets"
            task_description = "classify, transform, and prepare this data for migration according to the user's requirements"
        elif source == "sql":
            data_context = "structured data records extracted from SQL database"
            task_description = "classify, transform, and prepare this data for migration according to the user's requirements"
        else:
            data_context = "structured data records"
            task_description = "classify, transform, and prepare this data for migration according to the user's requirements"
        
        return f"""You are Archian AI, an intelligent data processing agent.
📄 User Request:
- The user wants to: "{extraction_goals or 'process and migrate the data'}"
- Data Source: {source}
- Data: {data_context}


{plan_limit_instruction}

🧠 Your Task:
The following is {data_context}. Your job is to {task_description}."""

    
    @staticmethod
    def get_extract_only_semantic_profile_section() -> str:
        """When no destination is chosen, infer semantic profile instead of catalog labels."""
        return """1. **Semantic data profile (no destination yet)**

   - Do **not** pick `document_type` / `document_description` from a destination catalog.
   - Include on the **first record** a `semantic_data_profile` object:
     ```json
     "semantic_data_profile": {
       "entity_guess": "Contact | Account | Order | …",
       "key_columns": ["email", "first_name", …],
       "notes": "brief rationale"
     }
     ```
   - Still set `document_type` and `document_description` as your best generic labels for the **source** entity being extracted (free text when no catalog applies).
   - These labels describe what you extracted, **not** the future migration destination.
   - Do not assume Salesforce, SQL, or Google Sheets as the migration target."""

    @staticmethod
    def get_salesforce_classification_section(category_list: str) -> str:
        """
        Salesforce-specific document classification section
        """
        return f"""1. 🏷️ **Document Classification (Salesforce)**

🚨 **CRITICAL: User-Specified Destination Takes Priority**
   - **If the user mentions a specific Salesforce object** (e.g., "contact object", "account object", "lead object"), you MUST use that exact object from the category list below
   - **Match the user's mentioned object name** to the exact `object_name` in the category list
   - **Only infer from Sample Data** if the user did NOT specify a destination object
   - **Examples:**
     - User says "deploy to contact object" → Use `"object_name": "Contact"` from category list
     - User says "migrate to account" → Use `"object_name": "Account"` from category list
     - User says "extract leads" → Use `"object_name": "Lead"` from category list
     - User says "process this data" (no object mentioned) → Infer from Sample Data & choose appropriate set from the category list based on the data keys available in the sample data. If no data keys are available, choose the most appropriate set from the category list.

   - Choose **only one** pair from the category list below.
   - Format your output with:
     ```json
     "object_name": "object_name_from_list",
     "document_type": "category_value_from_list",
     "document_description": "description_value_from_list"
     ```
     **CRITICAL**: 
     - `object_name` = the value from the `object_name` field of the selected category object
     - `document_type` = the value from the `category` field of the selected category object (e.g., if category is "Contact", use "Contact"; if category is "Account", use "Account")
     - `document_description` = the value from the `description` field of the selected category object
     - **DO NOT** use the literal strings "category", "object_name", or "description" - use the actual VALUES from those fields
   - ⚠️ **Do NOT** modify, reword, truncate, or add punctuation (like periods) to the `description`.
   - ⚠️ **Do NOT** modify, reword, truncate, or add punctuation (like periods) to the `object_name`.
   - ⚠️ **Do NOT** modify, reword, truncate, or add punctuation (like periods) to the `category` and **DO NOT** use object_name in document_type. **CRITICAL**: `document_type` must be set to the actual VALUE of the `category` field from the selected category object in the list below (e.g., "Contact", "Account", "Lead"), NOT the literal string "category".
   - Use the category list below **exactly as-is**:

{category_list}"""
    
    @staticmethod
    def get_google_sheet_classification_section(schema_list: str) -> str:
        """
        Google Sheets-specific document classification section
        """
        return f"""1. 🏷️ **Document Classification (Google Sheets)**

🚨 **Classify the SOURCE workbook being extracted (not the migration destination)**
   - Each entry uses `spreadsheet_name` = Google **workbook** title; `sheet_name` (if present) is the tab (often "Sheet1").
   - Pick the schema entry that matches the **source** data in Sample Data (column headers + content), not a future destination workbook.
   - **Only infer from Sample Data** if no matching workbook appears in the schema list.

   - Choose **only one** entry from the schema list below.
   - Format your output with:
     ```json
     "object_name": "spreadsheet_name_from_list",
     "document_type": "category_value_from_list",
     "document_description": "description_value_from_list"
     ```
     **CRITICAL**: 
     - `object_name` = the value from the `spreadsheet_name` field of the selected schema object
     - `document_type` = the value from the `category` field of the selected schema object
     - `document_description` = the value from the `description` field of the selected schema object
     - **DO NOT** use the literal strings "category", "spreadsheet_name", or "description" - use the actual VALUES from those fields
   - ⚠️ **Do NOT** modify, reword, truncate, or add punctuation to `description`, `spreadsheet_name`, or `category`.
   - Use the schema list below **exactly as-is**:
   
{schema_list}"""

    @staticmethod
    def get_preserve_source_columns_section(source: str) -> str:
        """When extracting from a connected source, keep original column headers."""
        if source not in ("google_sheets", "sql", "salesforce"):
            return ""
        return """3. 📌 **Preserve source column names (REQUIRED for connector extraction)**

   - Sample Data already has the **authoritative headers** from the source system.
   - **Keep output column names identical to the source** (e.g. a source column `Name` must remain `Name`, not `First Name`, `Last Name`, `FirstName`, or `LastName`).
   - **Do NOT** split, merge, or rename columns during extraction unless the user's request **explicitly** asks (e.g. "split Name into first and last name").
   - **Do NOT** reshape columns to match a migration destination schema — field mapping happens later.
   - For `transformation_rules`: omit the field entirely when no transforms are needed, or use only `keep` with `output_field` equal to the original column name.
   - **Never** add `split_2` / `split_3` on `Name` (or similar) just because the destination has separate first/last columns."""
    
    @staticmethod
    def get_sql_classification_section(schema_list: str) -> str:
        """
        SQL-specific document classification section
        """
        return f"""1. 🏷️ **Document Classification (SQL Database)**

🚨 **CRITICAL RULE: ALL VALUES MUST COME FROM THE SCHEMA LIST BELOW**
   - **You MUST select ONE complete entry from the schema list below**
   - **You MUST use the exact values from that selected entry for ALL fields: `table_name`, `category`, and `description`**
   - **DO NOT generate, create, or infer new values for `document_type` or `document_description`**
   - **DO NOT modify, reword, or change any values from the schema list**

📋 **Selection Rules:**

   **Scenario 1: User mentions a specific table name**
   - Match the user's mentioned table name to the exact `table_name` in the schema list
   - Select that entry and use its `table_name`, `category`, and `description` values exactly as shown
   - **Examples:**
     - User says "extract from users table" → Find entry with `table_name: "users"` → Use that entry's `category` and `description`
     - User says "migrate to customers" → Find entry with `table_name: "customers"` → Use that entry's `category` and `description`

   **Scenario 2: User does NOT mention a table name**
   - Analyze the sample data to understand what type of data it contains
   - Find the most appropriate entry in the schema list that matches the sample data structure/fields
   - Select that entry and use its `table_name`, `category`, and `description` values exactly as shown
   - **Example:**
     - Sample data has fields like "email", "name", "created_at" → Find entry with matching fields → Use that entry's `table_name`, `category`, and `description` exactly

✅ **Output Format:**
   ```json
   {{
     "object_name": "table_name_from_selected_entry",
     "document_type": "category_value_from_selected_entry",
     "document_description": "description_value_from_selected_entry",
     "connector_name": "connector_name_from_selected_entry (only if field exists in entry)"
   }}
   ```

🔒 **CRITICAL CONSTRAINTS:**
   - `object_name` = Use the exact `table_name` value from the selected schema entry
   - `document_type` = Use the exact `category` value from the selected schema entry
   - `document_description` = Use the exact `description` value from the selected schema entry
   - `connector_name` = Use the exact `connector_name` value from the selected schema entry (ONLY if this field exists in the entry; omit the key entirely if it doesn't exist)
   - **DO NOT** use literal strings like "category", "table_name", or "description" - use the actual VALUES from the selected entry
   - **DO NOT** generate new descriptions like "Data from table X" or "SQL Data" - use the description from the schema list
   - **DO NOT** create new document types - use the category from the schema list
   - **DO NOT** modify, truncate, or add punctuation to any values

📋 **Schema List (Select ONE entry from below):**
   
{schema_list}"""
    
    @staticmethod
    def get_output_format_section() -> str:
        """
        Output format instructions (universal for all destinations)
        """
        return """2. 📤 **Output Format**
   - Output must be **flat JSON** (no nested objects or arrays inside each record).
   - If the data is multi-record (i.e., tabular), return a **list of objects**.
   - Include `"object_name"`, `"document_type"` and `"document_description"` in the **first data record** (not as a separate metadata record).
   - **Do NOT create a separate record just for metadata** - include metadata fields in the first actual data record."""
    
    @staticmethod
    def get_header_normalization_section() -> str:
        """
        Header normalization instructions (universal)
        """
        return """3. 🧹 **Header Normalization**
   - Convert short or informal headers (e.g., "FName", "DOB") into standardized formats:
     - `"FName"` → `"FirstName"`
     - `"DOB"` → `"DateOfBirth"`"""
    
    @staticmethod
    def get_boolean_fields_section() -> str:
        """
        Boolean fields handling (universal)
        """
        return """4. 📋 **Boolean Fields**
   - For checkboxes or radio buttons, include values **only if checked** (`true`).
   - Do not include fields with `false` values."""
    
    @staticmethod
    def get_filter_rules_section() -> str:
        """
        Filter rules section (universal for all destinations)
        """
        return """5. 🔍 **Filter Rules (REQUIRED - DO NOT SKIP THIS!)**
   - **Analyze if the user wants to filter/select specific records based on criteria**
   - **Examples of filtering requests:**
     - "Extract active contacts only", "Show me deals over $10,000", "Get records where Status is Active"
     - "Only California customers", "Amount greater than 100", "Closed deals"
     - "Remove records with empty email", "Exclude blank phone numbers", "Skip empty values"
     - "Only records with email addresses", "Show only filled phone numbers", "Records that have email"
     - "Exclude records with no email", "Filter out empty fields", "Only non-empty emails"
   - **If user wants filtering, provide filter_rules. If no filtering mentioned, set "filter_rules": null**
   - **Format:**
     ```json
     "filter_rules": {{
       "conditions": [
         {{
           "field": "Status",
           "operator": "equals",
           "value": "Active"
         }}
       ],
       "logic": "AND"
     }}
     ```
   - **Supported operators:**
     - `equals`, `not_equals` - Exact match (case-insensitive for strings, works for numbers too)
     - `greater_than`, `less_than`, `greater_than_or_equal`, `less_than_or_equal` - Numeric comparisons
     - `contains`, `not_contains` - String contains (case-insensitive)
     - `starts_with`, `ends_with` - String starts/ends with (case-insensitive)
     - `is_empty`, `is_not_empty` - Check if field is empty/null
     - `valid_email` - Field value is a syntactically valid email (use for "remove invalid emails" / "keep only valid emails"; same validation as data quality analysis). No `value` key needed.
     - `between` - Range check (value should be [min, max] array)
   - **Data cleanup / follow-up requests:** When the user asks to **remove invalid email addresses**, **drop bad emails**, **exclude malformed emails**, or **keep only valid emails** (often after extraction or in a cleanup turn), you **must** use `valid_email` on the email column. Do **not** use `is_not_empty` for that intent — non-empty strings can still be invalid emails.
   - **Logic:** Use "AND" for all conditions must match, "OR" for any condition matches
   - **Examples:**
     - User wants "active contacts":
       ```json
       {{"field": "Status", "operator": "equals", "value": "Active"}}
       ```
     - User wants "deals over $10,000":
       ```json
       {{"field": "Amount", "operator": "greater_than", "value": 10000}}
       ```
     - User wants "remove records with empty email" or "only records with email addresses":
       ```json
       {{"field": "Email", "operator": "is_not_empty"}}
       ```
    - User wants "remove invalid email addresses", "keep only valid emails", or "drop malformed emails" (especially during **cleanup** after extraction):
      ```json
      {{"field": "Email", "operator": "valid_email"}}
      ```
      - Use the **exact** column name from the sample data. Do **not** use `is_not_empty` or `contains` "@" for this intent — non-empty values can still be invalid.
      - If the user additionally requires a specific domain (e.g. only ".com"), add a second condition with `ends_with` on the same field.
     - User wants "exclude blank phone numbers":
       ```json
       {{"field": "Phone", "operator": "is_not_empty"}}
       ```
     - User wants "only records with no status" or "empty status field":
       ```json
       {{"field": "Status", "operator": "is_empty"}}
       ```
     - User wants "active contacts from California":
       ```json
       {{
         "conditions": [
           {{"field": "Status", "operator": "equals", "value": "Active"}},
           {{"field": "State", "operator": "equals", "value": "California"}}
         ],
         "logic": "AND"
       }}
       ```
     - User wants "active contacts with email addresses":
       ```json
       {{
         "conditions": [
           {{"field": "Status", "operator": "equals", "value": "Active"}},
           {{"field": "Email", "operator": "is_not_empty"}}
         ],
         "logic": "AND"
       }}
       ```
   - **IMPORTANT:** Filter rules work on ORIGINAL field names (before transformation)
   - **IMPORTANT:** If no filtering is requested, set "filter_rules": null"""
    
    @staticmethod
    def get_deduplication_rules_section() -> str:
        """
        Deduplication rules section (universal for all destinations)
        """
        return """6. 🔄 **Deduplication Rules (REQUIRED - DO NOT SKIP THIS!)**
   - **Analyze if user wants to remove duplicate records**
   - **Examples of deduplication requests:**
     - "Remove duplicates", "Deduplicate the data", "Remove duplicate records"
     - "Remove duplicates based on email", "Deduplicate by name and email"
     - "Keep only unique records", "Keep unique records by phone number"
     - "Remove duplicate contacts by email field", "Eliminate duplicates using name"
   - **If user wants deduplication, provide deduplication_rules. If no deduplication mentioned, set "deduplication_rules": null**
   - **Format:**
     ```json
     "deduplication_rules": {{
       "enabled": true,
       "fields": ["Email", "Name"],
       "strategy": "keep_first"
     }}
     ```
   - **Strategy Options (AI should detect from user's request):**
     - `keep_first`: Keep the first occurrence of duplicate (default if not specified)
       - Use when: "Remove duplicates", "Keep unique records", "Deduplicate"
     - `keep_last`: Keep the last/most recent occurrence
       - Use when: "Keep the latest", "Keep most recent duplicate", "Use last entry"
     - `merge`: Combine data from all duplicates into one record
       - Use when: "Merge duplicates", "Combine duplicate data", "Consolidate duplicates"
   - **Fields Detection:**
     - If user specifies fields: "Remove duplicates based on email, name" → `["Email", "Name"]`
     - If user says "Remove duplicates" without specifying fields → use ALL available fields
     - Field names should match ORIGINAL field names (before transformation)
   - **Examples:**
     - User: "Remove duplicate records based on email"
       ```json
       {{"enabled": true, "fields": ["Email"], "strategy": "keep_first"}}
       ```
     - User: "Deduplicate by name and email, keep the most recent"
       ```json
       {{"enabled": true, "fields": ["Name", "Email"], "strategy": "keep_last"}}
       ```
     - User: "Merge duplicate contacts by email"
       ```json
       {{"enabled": true, "fields": ["Email"], "strategy": "merge"}}
       ```
     - User: "Remove all duplicates" (no specific fields)
       ```json
       {{"enabled": true, "fields": ["Name", "Email", "Phone"], "strategy": "keep_first"}}
       ```
   - **IMPORTANT:** If no deduplication is requested, set "deduplication_rules": null"""
    
    @staticmethod
    def get_source_true_migrate_transform_section() -> str:
        """Minimal transform rules for extract → map → migrate (no destination reshaping)."""
        return """7. 🔧 **Transformation Rules (source-true migrate — REQUIRED behavior)**

   - **Omit** `transformation_rules` entirely when all source columns should pass through unchanged.
   - If the user only wants a subset of columns, use `exclude` for unwanted columns OR identity `keep` where **`output_field` equals the exact source column name** (e.g. `first_name` → `first_name`, not `Name`).
   - **Never** `merge`, `split_*`, or rename to destination headers (`Name`, `Email`, etc.) — mapping handles that later.
   - **Never** add `filter_rules`, `deduplication_rules`, or `data_cleanup_rules` unless the user explicitly asked in this message."""

    @staticmethod
    def get_transformation_rules_section(extraction_goals: str) -> str:
        """
        Transformation rules section (universal for all destinations)
        """
        return f"""7. 🔧 **Transformation Rules**
   - **Include "transformation_rules" when user's request requires field manipulation:**
     - Excluding specific columns/fields (e.g., "extract all except Phone", "exclude Address")
     - Splitting a single field into multiple fields **only when the user explicitly asks** (e.g., "split Name into first and last name")
     - **Do NOT** auto-split a source `Name` column to match a destination's First/Last columns during extraction
     - Combining/merging multiple fields into one (e.g., "First Name" + "Last Name" → "FullName")
     - Selective extraction (only specific fields, exclude others)
     - Renaming fields during extraction
   - **Use your judgment**: If user's intent requires any field manipulation, include transformation_rules. If no manipulation needed (e.g., "extract all data" with no exclusions), you may omit this field.
   - **Add transformation_rules as a field in the FIRST record only**
   
   - **Format:**
     ```json
     "transformation_rules": {{{{
       "fields": [
         {{{{
           "source_field": "column_name",  // Single field name (string) for keep/exclude/split
           "source_field": ["Field1", "Field2"],  // Array for merge actions
           "action": "keep|exclude|split_2|split_3|merge",
           "output_field": "NewFieldName",  // For keep/merge actions
           "output_fields": ["Field1", "Field2"],  // For split actions
           "split_char": " ",  // For split actions (default: space)
           "separator": " "  // For merge actions (default: space)
         }}}}
       ]
     }}}}
     ```
   
   - **Actions:**
     - `keep`: Keep field as-is (optional rename via "output_field")
     - `exclude`: Remove field from output (use when user wants to exclude columns or extract only specific fields)
     - `split_2`: Split into 2 parts (e.g., "John Doe" → FirstName: "John", LastName: "Doe")
     - `split_3`: Split into 3 parts (e.g., "John Michael Doe" → FirstName, MiddleName, LastName)
     - `merge`: Combine multiple fields into one. **`source_field` MUST be an array**, provide `output_field` and optional `separator` (default: space)
   
   - **CRITICAL: ONLY extract fields explicitly mentioned in user's goal: "{extraction_goals}"**
   - **When user wants specific fields ONLY or wants to exclude columns, include transformation_rules with "exclude" actions for unwanted fields**
   
   - **Examples:**
     - **Exclude columns**: User says "extract all except Phone and Address" from [Name, Email, Phone, Address]:
       ```json
       {{{{"source_field": "Name", "action": "keep", "output_field": "Name"}}}},
       {{{{"source_field": "Email", "action": "keep", "output_field": "Email"}}}},
       {{{{"source_field": "Phone", "action": "exclude"}}}},
       {{{{"source_field": "Address", "action": "exclude"}}}}
       ```
     
     - **Selective extraction**: User wants "Name and Email" from [Name, Email, Phone, Address]:
       ```json
       {{{{"source_field": "Name", "action": "keep", "output_field": "Name"}}}},
       {{{{"source_field": "Email", "action": "keep", "output_field": "Email"}}}},
       {{{{"source_field": "Phone", "action": "exclude"}}}},
       {{{{"source_field": "Address", "action": "exclude"}}}}
       ```
     
     - **Split field**: User wants "first name and last name" from column [Name]:
       ```json
       {{{{"source_field": "Name", "action": "split_2", "split_char": " ", "output_fields": ["FirstName", "LastName"]}}}}
       ```
     
     - **Merge fields**: User wants "FullName" by combining [First name] and [last name]:
       ```json
       {{{{"source_field": ["First name", "last name"], "action": "merge", "separator": " ", "output_field": "FullName"}}}}
       ```
     
     - **Merge with exclusion**: User says "extract only email and name (combination of First Name and Last Name)":
       ```json
         {{{{"source_field": ["First Name", "Last Name"], "action": "merge", "separator": " ", "output_field": "name"}}}},
         {{{{"source_field": "First Name", "action": "exclude"}}}},
         {{{{"source_field": "Last Name", "action": "exclude"}}}},
         {{{{"source_field": "Email", "action": "keep", "output_field": "Email"}}}}
       ```
     
     - **No transformation needed**: User says "extract all data" from [Name, Email, Phone]:
       → Omit transformation_rules field entirely
   
   - **If transformation is needed but exact rule cannot be determined, include empty rules with error:**
     ```json
     "transformation_rules": {{
       "fields": [],
       "error": "Missing instructions for how to combine First Name and Last Name"
     }}
     ```"""
    
    @staticmethod
    def get_mapping_context_summary_section() -> str:
        """
        Mapping context summary section - asks AI to generate a concise summary for mapping
        Focuses ONLY on destination object/table selection, not field-level details
        """
        return """8. 📝 **Mapping Context Summary (REQUIRED)**
   - **Generate a concise summary** that will help the mapping function select the correct destination object/table
   - **Include in the FIRST record only** as a field named `mapping_context_summary`
   - **Purpose**: This summary is used by the mapping function to prioritize the correct destination object/table when multiple options are available
   - **CRITICAL - Focus ONLY on Destination Selection**:
     - **ONLY include** specific destination objects/tables mentioned by the user (e.g., "Contact object", "Lead object", "Customer_Contact_Details__c")
     - **DO NOT include** field-level details (transformations, exclusions, filters) - these are handled separately
     - **DO NOT include** record limits, extraction counts, or other extraction-specific details
     - **DO NOT include** field names or data transformations (e.g., "split names", "remove phone numbers")
     - If the user did NOT mention a specific destination object/table, keep the summary very brief or just state the data type
   - **Format:**
     ```json
     "mapping_context_summary": "Brief summary focusing ONLY on destination object/table if mentioned by user. Example: 'User wants to migrate data to Salesforce Contact object' or 'User wants to extract customer contact data' (if no specific object mentioned)"
     ```
   - **Guidelines:**
     - Keep it very concise (1-2 sentences maximum)
     - **ONLY mention destination object/table names if explicitly mentioned by the user**
     - If no specific destination is mentioned, just describe the data type (e.g., "customer contact data", "employee records")
     - **DO NOT include**: field transformations, exclusions, record limits, or extraction details
   - **Examples:**
     - User says "extract customer contact data and map it to Contact object in Salesforce":
       ```json
       "mapping_context_summary": "User wants to migrate data to Salesforce Contact object"
       ```
     - User says "extract employee leave records" (no specific object mentioned):
       ```json
       "mapping_context_summary": "User wants to extract employee leave records"
       ```
     - User says "extract sales leads and map to Lead object":
       ```json
       "mapping_context_summary": "User wants to migrate data to Salesforce Lead object"
       ```
     - User says "extract data and split names, remove phone numbers" (no specific object mentioned):
       ```json
       "mapping_context_summary": "User wants to extract contact data"
       ```
       (Note: Field transformations like "split names" and "remove phone numbers" are NOT included)
   - **IMPORTANT**: Always include this field in the first record, even if the summary is brief. Only include destination object/table names if explicitly mentioned by the user."""
    
    @staticmethod
    async def build_extraction_prompt(
        destination: str,
        destination_name: Optional[str],
        extraction_goals: str,
        sample_data: List[Dict],
        plan_limit_instruction: str,
        user_id: str,
        source: str = "file",
        source_name: Optional[str] = None,
        extraction_profile: Optional[str] = None,
    ) -> str:
        """
        Build a complete extraction prompt for the specified destination.
        
        Args:
            destination: Target destination ("salesforce", "google_sheets", etc.)
            destination_name: Specific destination name (e.g., "Production Org", "Customer Database")
            extraction_goals: User's extraction goals/requirements
            sample_data: Sample data to include in prompt
            plan_limit_instruction: Plan limit instructions
            user_id: User ID (used to fetch destination-specific data like Salesforce categories)
            source: Data source type ("file", "salesforce", "google_sheets", "sql")
            source_name: Workbook/table/object name for the data being extracted (used for classification)
            
        Returns:
            Complete prompt string ready for OpenAI
        """
        # Build sections
        sections = []
        
        # 1. Base instructions (always included, source-aware)
        sections.append(PromptBuilder.get_base_instructions(extraction_goals, plan_limit_instruction, source))
        sections.append(
            "\n🏷️ **Source labeling:** `document_type` and `document_description` must describe the **source** "
            "data you extracted, not the migration destination org/database/spreadsheet.\n"
        )
        # Classify from SOURCE catalog when extracting from a connector (not migration destination)
        classification_name = destination_name
        if source in ("google_sheets", "sql", "salesforce") and source_name:
            classification_name = source_name
        # 2. Classification section (destination-specific) — skip when destination unknown (extract-only)
        dest_norm = (destination or "unknown").strip().lower()
        if dest_norm in ("", "unknown"):
            sections.append(PromptBuilder.get_extract_only_semantic_profile_section())
        elif destination == "salesforce":
            # Fetch Salesforce categories for this user
            try:
                # Lazy import to avoid circular dependency
                from utils.document_utils import get_dynamic_document_type_categories
                categories = await get_dynamic_document_type_categories(
                    user_id=user_id,
                    destination="salesforce",
                    destination_name=classification_name,
                )
                category_list = json.dumps(categories)
                sections.append(PromptBuilder.get_salesforce_classification_section(category_list))
            except Exception as e:
                print(f"⚠️ Warning: Could not fetch Salesforce categories: {e}")
                # Continue without categories - AI will do best-effort classification
        
        elif destination == "google_sheets":
            # Fetch Google Sheets schemas for this user
            try:
                from utils.document_utils import get_dynamic_document_type_categories
                schemas = await get_dynamic_document_type_categories(
                    user_id=user_id,
                    destination="google_sheets",
                    destination_name=classification_name,
                )
                schema_list = json.dumps(schemas, indent=2)
                sections.append(PromptBuilder.get_google_sheet_classification_section(schema_list))
            except Exception as e:
                print(f"⚠️ Warning: Could not fetch Google Sheets schemas: {e}")
                # Continue without schemas - AI will do best-effort classification
        
        elif destination == "sql":
            # Fetch SQL table schemas for this user
            try:
                from utils.document_utils import get_dynamic_document_type_categories
                schemas = await get_dynamic_document_type_categories(
                    user_id=user_id,
                    destination="sql",
                    destination_name=classification_name,
                )
                schema_list = json.dumps(schemas, indent=2)
                sections.append(PromptBuilder.get_sql_classification_section(schema_list))
            except Exception as e:
                print(f"⚠️ Warning: Could not fetch SQL schemas: {e}")
                # Continue without schemas - AI will do best-effort classification
        
        # 3. Universal sections (same for all destinations)
        sections.append(PromptBuilder.get_output_format_section())
        preserve_section = PromptBuilder.get_preserve_source_columns_section(source)
        if preserve_section:
            sections.append(preserve_section)
        else:
            sections.append(PromptBuilder.get_header_normalization_section())
        
        # Universal transformation and processing sections (work for all destinations)
        sections.append(PromptBuilder.get_boolean_fields_section())
        profile = (extraction_profile or "").strip().lower()
        if profile == "source_true_migrate":
            sections.append(PromptBuilder.get_source_true_migrate_transform_section())
        else:
            sections.append(PromptBuilder.get_filter_rules_section())
            sections.append(PromptBuilder.get_deduplication_rules_section())
            sections.append(PromptBuilder.get_transformation_rules_section(extraction_goals))
        sections.append(PromptBuilder.get_mapping_context_summary_section())
        
        # 4. Sample data (always at the end)
        sections.append(f"\n\n📦 Sample Data:\n{json.dumps(sample_data, indent=2)}")
        
        # Combine all sections
        return "\n\n".join(sections) + "\n"
    
    @staticmethod
    def build_plan_limit_instruction(extraction_limit: Dict[str, Any]) -> str:
        """
        Build plan limit instruction based on user's current usage
        
        Args:
            extraction_limit: Dictionary with can_extract, already_migrated, plan_type, etc.
            
        Returns:
            Plan limit instruction string
        """
        if extraction_limit.get("limit_reached"):
            return """
 **CREDIT LIMIT REACHED - IMPORTANT INSTRUCTION:**
- User has insufficient credits for extraction (balance too low)
- They can extract 0 more records until they add credits or upgrade
- **DO NOT extract any records** - return empty data
- Only classify the document type
"""
        elif extraction_limit.get("extraction_cap") is not None and extraction_limit.get("can_extract", 0) < extraction_limit.get("extraction_cap"):
            return f"""

📊 **CREDIT LIMIT APPLIED - IMPORTANT INSTRUCTION:**
- User can extract maximum {extraction_limit["can_extract"]} records (based on current credit balance)
- **Limit your extraction to {extraction_limit["can_extract"]} records maximum**
- If the document has more records, extract only the first {extraction_limit["can_extract"]}
"""
        else:
            return ""

