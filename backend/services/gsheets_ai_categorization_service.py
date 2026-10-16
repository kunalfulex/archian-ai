"""
Google Sheets AI Categorization Service

Deprecated: use EntityLabelingService via schedule_entity_labeling instead.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)
DEPRECATION_MSG = (
    "GoogleSheetsAICategorizationService is deprecated; use EntityLabelingService instead."
)


class GoogleSheetsAICategorizationService:
    """Service for AI-powered Google Sheets categorization"""
    
    async def categorize_google_sheet(
        self, 
        spreadsheet_name: str,
        sheet_name: str, 
        headers: List[Dict[str, Any]],
        sample_data: Optional[List[Dict]] = None,
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Categorize Google Sheet based on headers and optional sample data
        
        Args:
            spreadsheet_name: Name of the spreadsheet
            sheet_name: Name of the sheet within the spreadsheet
            headers: List of header definitions [{"name": "Email", "type": "string", "position": 0}, ...]
            sample_data: Optional sample data (first few rows) for better categorization
            user_id: User ID (not used in simplified version)
            
        Returns:
            Dict containing:
            - document_type: Document category (e.g., "Customer Records")
            - document_description: Description of the sheet content
            - confidence_score: Confidence level (0-100)
        """
        
        logger.warning("%s categorize_google_sheet(%s/%s)", DEPRECATION_MSG, spreadsheet_name, sheet_name)
        try:
            # Extract just header names for the prompt
            header_names = [h['name'] for h in headers]
            
            # Build sample data preview
            sample_preview = ""
            if sample_data and len(sample_data) > 0:
                sample_preview = f"\n**Sample Data (first 3 rows):**\n"
                for i, row in enumerate(sample_data[:3], 1):
                    sample_preview += f"Row {i}: {json.dumps(row, indent=2)}\n"
            
            categorization_prompt = f"""
Analyze this Google Spreadsheet and categorize it for data discovery purposes:

**Spreadsheet Information:**
- Spreadsheet Name: "{spreadsheet_name}"
- Sheet Name: "{sheet_name}"
- Headers: {header_names}
{sample_preview}

Based on the sheet structure and sample data, please categorize this sheet for data discovery:
1. document_type: Category of data (e.g., "Customer Records", "Product Data", "Transaction Records", "User Management", "Financial Data", "Inventory Data", "Log Data")
2. document_description: Detailed description of what this sheet contains based on the headers and sample data structure
3. confidence_score: How confident you are in this categorization (0-100)

This categorization will be used to help users discover which sheets contain the data they want to extract.
Do NOT provide field mappings - those will be handled separately when data is actually extracted.

Return your response as a JSON object with the following structure:
{{
    "document_type": "Customer Records", 
    "document_description": "This sheet contains customer account information including contact details, company information, and account status. Based on the sample data structure, it stores user profiles with email addresses, names, and creation timestamps.",
    "confidence_score": 85
}}
"""
            # Call OpenAI for categorization (run in thread to avoid blocking event loop)
            from services.architect_agent import async_openai_call
            from services.llm import tasks as llm_tasks

            response = await async_openai_call(
                None,
                task=llm_tasks.SCHEMA_CATEGORIZATION,
                messages=[{"role": "user", "content": categorization_prompt}],
                response_format={"type": "json_object"},
                temperature=0.3,
            )
            
            ai_response = response.choices[0].message.content
            categorization = json.loads(ai_response)
            
            print(f"✅ AI Categorization for {sheet_name}: {categorization.get('document_type')}")
            
            return categorization
            
        except json.JSONDecodeError as e:
            print(f"❌ Error parsing AI response: {e}")
            print(f"Raw response: {ai_response if 'ai_response' in locals() else 'N/A'}")
            
            # Return fallback categorization
            return {
                "document_type": "Spreadsheet Data",
                "document_description": f"Google Sheet: {sheet_name}",
                "confidence_score": 50
            }
            
        except Exception as e:
            print(f"❌ Error in AI categorization: {e}")
            return {
                "document_type": "Spreadsheet Data",
                "document_description": f"Google Sheet: {sheet_name}",
                "confidence_score": 50
            }
    
    async def categorize_multiple_sheets(
        self, 
        sheets_data: List[Dict[str, Any]],
        user_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Categorize multiple sheets in batch
        
        Args:
            sheets_data: List of sheet data dictionaries containing:
                - spreadsheet_id
                - spreadsheet_name
                - sheet_name
                - headers
                - sample_data (optional)
            user_id: User ID for fetching categories
            
        Returns:
            List of categorization results
        """
        results = []
        
        for sheet_data in sheets_data:
            categorization = await self.categorize_google_sheet(
                spreadsheet_name=sheet_data['spreadsheet_name'],
                sheet_name=sheet_data['sheet_name'],
                headers=sheet_data['headers'],
                sample_data=sheet_data.get('sample_data'),
                user_id=user_id
            )
            
            results.append({
                'spreadsheet_id': sheet_data['spreadsheet_id'],
                'spreadsheet_name': sheet_data['spreadsheet_name'],
                'sheet_name': sheet_data['sheet_name'],
                'header_schema': {'headers': sheet_data['headers']},
                'ai_categorization': categorization
            })
        
        return results
    
    async def suggest_sheet_for_prompt(
        self, 
        user_prompt: str, 
        categorized_sheets: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Suggest which sheet(s) match the user's prompt
        Similar to SQL table suggestion
        
        Args:
            user_prompt: User's natural language request
            categorized_sheets: List of categorized sheets with ai_categorization
            
        Returns:
            Ranked list of matching sheets
        """
        
        try:
            # Build sheet summaries
            sheet_summaries = []
            for sheet in categorized_sheets:
                ai_cat = sheet.get('ai_categorization', {})
                sheet_summaries.append({
                    'spreadsheet_name': sheet['spreadsheet_name'],
                    'sheet_name': sheet['sheet_name'],
                    'object_name': ai_cat.get('object_name', 'Unknown'),
                    'document_type': ai_cat.get('document_type', 'Unknown'),
                    'headers': [h['name'] for h in sheet.get('header_schema', {}).get('headers', [])]
                })
            
            suggestion_prompt = f"""
Given this user request: "{user_prompt}"

And these available Google Sheets:
{json.dumps(sheet_summaries, indent=2)}

Which sheet(s) best match the user's request? Return a ranked list with confidence scores.

Return JSON:
{{
    "suggested_sheets": [
        {{"spreadsheet_name": "...", "sheet_name": "...", "confidence": 95, "reason": "..."}}
    ]
}}
"""
            from services.architect_agent import async_openai_call
            from services.llm import tasks as llm_tasks

            response = await async_openai_call(
                None,
                task=llm_tasks.GOOGLE_SHEETS_DETECTION,
                messages=[{"role": "user", "content": suggestion_prompt}],
                response_format={"type": "json_object"},
                temperature=0.3,
            )
            
            result = json.loads(response.choices[0].message.content)
            return result.get('suggested_sheets', [])
            
        except Exception as e:
            print(f"❌ Error suggesting sheets: {e}")
            return []

