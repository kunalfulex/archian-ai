"""
Google Sheets Handler Module

This module contains all Google Sheets related functions for:
- Data extraction from Google Sheets
- Information analysis of Google Sheets
- Spreadsheet detection and metadata handling
- Background processing for Google Sheets operations
"""

import asyncio
import json
import re
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List
from googleapiclient.errors import HttpError

from utils.extraction_sse_payload import extraction_setup_progress_sse_line

# In-memory storage for pending update confirmations
# In production, this should be in Redis or database with TTL
pending_google_sheets_updates = {}


async def classify_google_sheets_info_request(prompt: str, conversation_history: list = None) -> dict:
    """
    Use OpenAI to classify the type of Google Sheets info request
    
    Returns:
        {
            "request_type": "basic_info" | "specific_search",
            "reasoning": "Brief explanation of classification"
        }
    """
    try:
        
        
        # Build conversation context if available
        conversation_context = ""
        if conversation_history:
            # Lazy import to avoid circular dependency
            from services.architect_agent import get_full_conversation_context
            conversation_context = get_full_conversation_context(conversation_history, max_tokens=5000)
        
        classification_prompt = f"""
Analyze the user's request about a Google Spreadsheet and classify it into ONE of these types:

**Request Types:**

1. **basic_info** - User wants general information ABOUT the spreadsheet:
   - Structure (columns, headers, data types)
   - Record count (how many rows/records)
   - Data type analysis (what kind of data)
   - Spreadsheet overview/summary
   
   Examples:
   - "How many columns does Archian Test have?"
   - "What type of data is stored in Customer Database?"
   - "Show me the headers from Sales Report"
   - "How many records are in Leads spreadsheet?"
   - "Tell me about the Q4 Report structure"
   - "What's in the Customer Database?"

2. **specific_search** - User wants to FIND/SEARCH for specific records:
   - Search by name, email, company, or any field
   - Filter records by criteria
   - Find records matching conditions
   
   Examples:
   - "Find John Smith in Archian Test"
   - "Show me all records from California"
   - "Search for customers with @gmail.com"
   - "Get records where status is Active"
   - "Find all Tesla employees"
   - "Show me contacts added in January"

{conversation_context}

Current user message: "{prompt}"

Return your response in this EXACT JSON format:
{{
    "request_type": "basic_info" or "specific_search",
    "reasoning": "Brief explanation of why you chose this classification"
}}

Return ONLY the JSON, no other text.
"""
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.GOOGLE_SHEETS_DETECTION,
            messages=[{"role": "user", "content": classification_prompt}],
            max_tokens=500,
            temperature=0.1,
        )
        
        ai_response = response.choices[0].message.content.strip()
        
        # Use centralized JSON parsing function (handles markdown code blocks, JSON fixing, etc.)
        from utils.openai_helpers import parse_openai_json_response
        try:
            result = parse_openai_json_response(ai_response)
        except (ValueError, json.JSONDecodeError):
                # Fallback to basic_info
                result = {"request_type": "basic_info", "reasoning": "Failed to parse, defaulting to basic info"}
        
        return result
        
    except Exception as e:
        print(f"❌ Error classifying info request: {e}")
        # Default to basic_info on error
        return {"request_type": "basic_info", "reasoning": "Classification error, defaulting to basic info"}


async def extract_data_from_prompt_with_ai(prompt: str, conversation_history: list = None, headers: list = None) -> dict:
    """
    Use OpenAI to extract structured data from natural language prompt.
    Supports both SINGLE record and BULK (multiple records) extraction.
    
    Args:
        prompt: User's natural language input (e.g., "Add John Smith with email john@test.com")
        conversation_history: Recent conversation for context
        headers: Expected headers/columns for the spreadsheet (REQUIRED for accurate mapping)
    
    Returns:
        Single record:
        {
            "data": {"Name": "John Smith", "Email": "john@test.com"},
            "is_bulk": false,
            "record_count": 1,
            "confidence": 0.9
        }
        
        Multiple records:
        {
            "data": [
                {"Name": "John Smith", "Email": "john@test.com"},
                {"Name": "Jane Doe", "Email": "jane@test.com"}
            ],
            "is_bulk": true,
            "record_count": 2,
            "confidence": 0.9
        }
    """
    try:
        
        
        # Build conversation context if available
        conversation_context = ""
        if conversation_history:
            from services.architect_agent import get_full_conversation_context
            conversation_context = get_full_conversation_context(conversation_history, max_tokens=3000)
        
        headers_info = ""
        if headers:
            headers_info = f"\n**Target Spreadsheet Columns (use EXACT names):** {', '.join(headers)}"
        
        extraction_prompt = f"""
Extract structured data from the user's natural language request.
The user may be adding ONE record OR MULTIPLE records at once.

{conversation_context}

Current user message: "{prompt}"
{headers_info}

**Your task:**
1. Determine if user is adding SINGLE or MULTIPLE records
2. Extract ONLY the data fields and their values from the user's message
3. Map the values to the EXACT column names from the target spreadsheet columns above
4. Do NOT extract spreadsheet name (handled separately)

**SINGLE RECORD Examples:**

Example 1:
User: "Add John Smith, john@test.com, 555-1234"
Columns: ["Name", "Email", "Phone"]
Result: {{"data": {{"Name": "John Smith", "Email": "john@test.com", "Phone": "555-1234"}}, "is_bulk": false, "record_count": 1, "confidence": 0.95}}

Example 2:
User: "Add Name=Jane Doe, Email is jane@example.com, City: New York"
Columns: ["Name", "Email", "City", "Status"]
Result: {{"data": {{"Name": "Jane Doe", "Email": "jane@example.com", "City": "New York"}}, "is_bulk": false, "record_count": 1, "confidence": 0.9}}

**MULTIPLE RECORDS Examples:**

Example 3:
User: "Add the following records: 
       John, john@test.com, 555-1234
       Jane, jane@test.com, 555-5678"
Columns: ["Name", "Email", "Phone"]
Result: {{
    "data": [
        {{"Name": "John", "Email": "john@test.com", "Phone": "555-1234"}},
        {{"Name": "Jane", "Email": "jane@test.com", "Phone": "555-5678"}}
    ],
    "is_bulk": true,
    "record_count": 2,
    "confidence": 0.9
}}

Example 4:
User: "Add these three tasks:
       Date: 2025-10-22, Task: Meeting, Hours: 2, Person: John
       Date: 2025-10-23, Task: Coding, Hours: 5, Person: Jane
       Date: 2025-10-24, Task: Testing, Hours: 3, Person: Mike"
Columns: ["Date", "Task", "Hours", "Person"]
Result: {{
    "data": [
        {{"Date": "2025-10-22", "Task": "Meeting", "Hours": "2", "Person": "John"}},
        {{"Date": "2025-10-23", "Task": "Coding", "Hours": "5", "Person": "Jane"}},
        {{"Date": "2025-10-24", "Task": "Testing", "Hours": "3", "Person": "Mike"}}
    ],
    "is_bulk": true,
    "record_count": 3,
    "confidence": 0.95
}}

Example 5: Markdown Table Format (tab/space separated)
User: "Add the following data:
       Initialized Capital    Garry Tan    partners@initialized.com    AI + SaaS + infra    Tier 2
       Village Global    Anne Dwane    anne@villageglobal.vc    Early stage SaaS    Tier 2"
Columns: ["VC Firm", "Partner Name", "Email", "Fund Thesis Match", "Priority"]
Result: {{
    "data": [
        {{"VC Firm": "Initialized Capital", "Partner Name": "Garry Tan", "Email": "partners@initialized.com", "Fund Thesis Match": "AI + SaaS + infra", "Priority": "Tier 2"}},
        {{"VC Firm": "Village Global", "Partner Name": "Anne Dwane", "Email": "anne@villageglobal.vc", "Fund Thesis Match": "Early stage SaaS", "Priority": "Tier 2"}}
    ],
    "is_bulk": true,
    "record_count": 2,
    "confidence": 0.9
}}

**Return ONLY this JSON format:**

SINGLE RECORD:
{{
    "data": {{"Column1": "value1", "Column2": "value2"}},
    "is_bulk": false,
    "record_count": 1,
    "confidence": 0.0 to 1.0
}}

MULTIPLE RECORDS:
{{
    "data": [
        {{"Column1": "value1", "Column2": "value2"}},
        {{"Column1": "value3", "Column2": "value4"}}
    ],
    "is_bulk": true,
    "record_count": 2,
    "confidence": 0.0 to 1.0
}}

Use EXACT column names from the target columns list. If no columns provided, infer reasonable field names.

**CRITICAL JSON FORMATTING REQUIREMENTS:**
- Return ONLY valid JSON, no markdown, no code blocks, no explanations
- ALL string values MUST be properly escaped:
  * Use \\" for double quotes inside strings: "Field": "Value with \\"quotes\\""
  * DO NOT escape single quotes - they are valid in JSON strings: "Field": "We're done" (NOT "We\\'re done")
  * Escape backslashes: use \\\\ for a single backslash
- For data with quotes, special characters, or URLs, ensure they are properly escaped in JSON
- Example with apostrophe: {{"data": [{{"Field": "We're done"}}]}} (single quote NOT escaped)

Return ONLY valid JSON, no other text.
"""
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.FILE_EXTRACTION,
            messages=[{"role": "user", "content": extraction_prompt}],
            max_tokens=4000,
            temperature=0.1,
        )
        
        ai_response = response.choices[0].message.content.strip()
        
        # Use centralized JSON parsing function (handles markdown code blocks, JSON fixing, etc.)
        from utils.openai_helpers import parse_openai_json_response
        try:
            result = parse_openai_json_response(ai_response)
        except (ValueError, json.JSONDecodeError) as e:
            print(f"⚠️ JSON parsing failed. Error: {e}")
            print(f"⚠️ AI Response (first 500 chars): {ai_response[:500]}")
            # Fallback: Return empty result
            result = {"data": {}, "is_bulk": False, "record_count": 0, "confidence": 0.0}
        
        if not result:
            result = {"data": {}, "is_bulk": False, "record_count": 0, "confidence": 0.0}
        
        # Ensure required fields exist
        if "is_bulk" not in result:
            # Detect if data is array (bulk) or object (single)
            if isinstance(result.get("data"), list):
                result["is_bulk"] = True
                result["record_count"] = len(result["data"])
            else:
                result["is_bulk"] = False
                result["record_count"] = 1 if result.get("data") else 0
        
        if "record_count" not in result:
            if result.get("is_bulk"):
                result["record_count"] = len(result.get("data", []))
            else:
                result["record_count"] = 1 if result.get("data") else 0
        
        return result
        
    except Exception as e:
        print(f"❌ Error extracting data from prompt: {e}")
        return {"data": {}, "is_bulk": False, "record_count": 0, "confidence": 0.0}


async def find_potential_duplicates_with_ai(new_data: dict, existing_records: list, headers: list) -> list:
    """
    Use OpenAI to intelligently find potential duplicate records.
    
    Args:
        new_data: New record to check (e.g., {"Name": "John Smith", "Email": "john@test.com"})
        existing_records: List of dicts with "row_index" and "data" keys
                         [{"row_index": 2, "data": {...}}, ...]
        headers: Column headers
    
    Returns:
        List of potential duplicate records with similarity scores
        [
            {"record": {...}, "row_index": 5, "similarity_score": 0.95, "match_reason": "Exact name and email match"},
            ...
        ]
    """
    try:
        
        
        # Limit to first 100 records for performance
        sample_records = existing_records[:100]
        
        # Format records for AI with row numbers
        formatted_records = []
        for record in sample_records:
            formatted_records.append({
                "row": record["row_index"],
                "data": record["data"]
            })
        
        duplicate_check_prompt = f"""
You are Archian AI, a duplicate detection expert. Find potential duplicates for this new record.

**New Record to Add:**
{json.dumps(new_data, indent=2)}

**Existing Records (first {len(sample_records)} of {len(existing_records)} total):**
Each record shows its row number in the spreadsheet.
{json.dumps(formatted_records, indent=2)}

**Matching Criteria:**
1. **Exact matches** - Same name, email, phone, ID
2. **Similar names** - "John Smith" vs "J. Smith", "Jonathan Smith"
3. **Same email** - Strong indicator of duplicate
4. **Same phone** - Strong indicator of duplicate
5. **Partial matches** - Same company + similar name
6. **Fuzzy matches** - Typos, case differences

Return ONLY this JSON format:
{{
    "duplicates": [
        {{
            "row_index": 4,
            "similarity_score": 0.95,
            "match_reason": "Exact email match",
            "matched_record": {{"Name": "John", "Email": "john@test.com", ...}}
        }}
    ]
}}

**IMPORTANT:**
- Use the "row" number from the existing records as the "row_index" in your response
- For example, if the match is in record with "row": 4, set "row_index": 4
- This row_index is the actual Google Sheets row number
- For "matched_record", include ONLY the "data" part, NOT the "row" wrapper
- Example: if record is {{"row": 4, "data": {{"Name": "John"}}}}, use "matched_record": {{"Name": "John"}}

Rules:
- Only return records with similarity_score >= 0.6
- Sort by similarity_score (highest first)
- Limit to top 5 matches
- If no matches, return empty array
"""
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.GENERAL,
            messages=[{"role": "user", "content": duplicate_check_prompt}],
            max_tokens=1000,
            temperature=0.1,
        )
        
        ai_response = response.choices[0].message.content.strip()
        
        # Use centralized JSON parsing function (handles markdown code blocks, JSON fixing, etc.)
        from utils.openai_helpers import parse_openai_json_response
        try:
            result = parse_openai_json_response(ai_response)
            duplicates = result.get("duplicates", [])
        except (ValueError, json.JSONDecodeError):
                duplicates = []
        
        # Clean up matched_record if it has the wrong structure
        # AI might return {"row": 4, "data": {...}} instead of just {...}
        cleaned_duplicates = []
        for dup in duplicates:
            matched_record = dup.get('matched_record', {})
            
            # If matched_record has "row" and "data" keys, extract just the data
            if isinstance(matched_record, dict) and 'row' in matched_record and 'data' in matched_record:
                matched_record = matched_record['data']
            
            cleaned_duplicates.append({
                'row_index': dup.get('row_index'),
                'similarity_score': dup.get('similarity_score', 0),
                'match_reason': dup.get('match_reason', ''),
                'matched_record': matched_record
            })
        
        return cleaned_duplicates
        
    except Exception as e:
        print(f"❌ Error finding duplicates: {e}")
        return []


