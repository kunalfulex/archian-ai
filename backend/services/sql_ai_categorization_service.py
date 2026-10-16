"""Service for AI-powered SQL table categorization.

Deprecated: use EntityLabelingService via schedule_entity_labeling instead.
"""
import json
import logging
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from datetime import datetime, date, time
from decimal import Decimal
from uuid import UUID

load_dotenv()

logger = logging.getLogger(__name__)
DEPRECATION_MSG = (
    "SQLAICategorizationService is deprecated; use EntityLabelingService instead."
)


class DateTimeEncoder(json.JSONEncoder):
    """Custom JSON encoder for common DB scalar types."""
    def default(self, obj):
        if isinstance(obj, (datetime, date, time)):
            return obj.isoformat()
        elif isinstance(obj, Decimal):
            return float(obj)
        elif isinstance(obj, UUID):
            return str(obj)
        return super().default(obj)


class SQLAICategorizationService:
    """Service for AI-powered SQL table categorization"""
    
    def generate_fake_data_from_schema(self, columns: List[Dict[str, Any]], count: int = 5) -> List[Dict[str, Any]]:
        """
        Generate fake data based on table schema for AI categorization
        This is enterprise-safe as no real data is sent to OpenAI
        """
        if not columns:
            return []
        
        fake_data = []
        
        for i in range(count):
            fake_record = {}
            for col in columns:
                field_name = col.get('column_name', '')
                data_type = col.get('data_type', '').lower()
                
                if 'varchar' in data_type or 'text' in data_type or 'char' in data_type:
                    if any(keyword in field_name.lower() for keyword in ['email', 'mail']):
                        fake_record[field_name] = f"user{i+1}@example.com"
                    elif any(keyword in field_name.lower() for keyword in ['phone', 'mobile', 'tel']):
                        fake_record[field_name] = f"555-{str(i+1).zfill(4)}"
                    elif any(keyword in field_name.lower() for keyword in ['name', 'first', 'last']):
                        fake_record[field_name] = f"Sample {field_name.replace('_', ' ').title()} {i+1}"
                    elif any(keyword in field_name.lower() for keyword in ['address', 'street', 'city']):
                        fake_record[field_name] = f"123 Sample {field_name.replace('_', ' ').title()} St"
                    elif any(keyword in field_name.lower() for keyword in ['company', 'org']):
                        fake_record[field_name] = f"Sample Company {i+1}"
                    else:
                        fake_record[field_name] = f"Sample {field_name.replace('_', ' ').title()} {i+1}"
                elif 'int' in data_type or 'integer' in data_type:
                    fake_record[field_name] = i + 1
                elif 'float' in data_type or 'decimal' in data_type or 'numeric' in data_type:
                    fake_record[field_name] = round(100.0 + i * 10.5, 2)
                elif 'bool' in data_type:
                    fake_record[field_name] = i % 2 == 0
                elif 'date' in data_type or 'timestamp' in data_type:
                    fake_record[field_name] = f"2024-01-{str(i+1).zfill(2)}"
                else:
                    fake_record[field_name] = f"Sample {field_name.replace('_', ' ').title()} {i+1}"
            
            fake_data.append(fake_record)
        
        return fake_data

    async def categorize_sql_table_with_sample_data(self, table_name: str, columns: List[Dict[str, Any]], sample_data: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        logger.warning("%s categorize_sql_table_with_sample_data(%s)", DEPRECATION_MSG, table_name)
        """
        Send table name, column details, and real data to OpenAI for categorization
        Data is only sent for analysis, not stored, and data sharing with OpenAI is disabled
        """
        try:
            # Prepare column information for AI
            column_info = []
            for col in columns:
                column_info.append({
                    'name': col.get('column_name', ''),
                    'type': col.get('data_type', ''),
                    'description': col.get('column_comment', ''),
                    'is_nullable': col.get('is_nullable', True),
                    'is_primary_key': col.get('is_primary_key', False)
                })
            
            # Use real data if provided, limit to 3 records for token management
            # Data is only sent for analysis, not stored, and data sharing with OpenAI is disabled
            if sample_data and len(sample_data) > 0:
                real_data = sample_data[:3] if len(sample_data) > 3 else sample_data
            else:
                # Fallback: no sample data available, use empty list
                real_data = []
            
            prompt = f"""
            Analyze this SQL table and categorize it for data discovery purposes:
            
            Table Name: {table_name}
            Columns: {json.dumps(column_info, indent=2, cls=DateTimeEncoder)}
            Sample Data: {json.dumps(real_data, indent=2, cls=DateTimeEncoder) if real_data else "No sample data available"}
            
            Based on the table structure and real data provided, please categorize this table for data discovery:
            1. document_type: Category of data (e.g., "Customer Records", "Product Data", "Transaction Records", "User Management", "Financial Data", "Inventory Data", "Log Data")
            2. document_description: Detailed description of what this table contains based on the schema and sample data structure
            3. confidence_score: How confident you are in this categorization (0-100)
            
            This categorization will be used to help users discover which tables contain the data they want to extract.
            Do NOT provide field mappings - those will be handled separately when data is actually extracted.
            
            Return your response as a JSON object with the following structure:
            {{
                "document_type": "Customer Records", 
                "document_description": "This table contains customer account information including contact details, company information, and account status. Based on the sample data structure, it stores user profiles with email addresses, names, and creation timestamps.",
                "confidence_score": 85
            }}
            """
            import asyncio
            from services.architect_agent import async_openai_call
            from services.llm import tasks as llm_tasks

            response = await async_openai_call(
                None,
                task=llm_tasks.SCHEMA_CATEGORIZATION,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1500,
            )
            
            ai_response = response.choices[0].message.content.strip()
            
            # Parse JSON response
            try:
                # Extract JSON from response (handle markdown and explanatory text)
                if '```json' in ai_response:
                    # Extract content between ```json and ```
                    start = ai_response.find('```json') + 7
                    end = ai_response.find('```', start)
                    if end != -1:
                        ai_response = ai_response[start:end].strip()
                elif '```' in ai_response:
                    # Extract content between ``` and ```
                    start = ai_response.find('```') + 3
                    end = ai_response.find('```', start)
                    if end != -1:
                        ai_response = ai_response[start:end].strip()
                elif '{' in ai_response and '}' in ai_response:
                    # Extract JSON object from text
                    start = ai_response.find('{')
                    end = ai_response.rfind('}') + 1
                    ai_response = ai_response[start:end]
                
                categorization = json.loads(ai_response)
                
                # Validate required fields
                required_fields = ['document_type', 'document_description']
                for field in required_fields:
                    if field not in categorization:
                        categorization[field] = "Unknown"
                
                # Add confidence score if not provided
                if 'confidence_score' not in categorization:
                    categorization['confidence_score'] = 50
                
                return categorization
                
            except json.JSONDecodeError as e:
                print(f"Error parsing AI response: {e}")
                print(f"Raw response: {ai_response}")
                
                # Return fallback categorization
                return {
                    "object_name": "Custom_Object__c",
                    "document_type": "Database Table",
                    "document_description": f"SQL table: {table_name}",
                    "confidence_score": 30
                }
                
        except Exception as e:
            print(f"Error in AI categorization: {e}")
            return {
                "object_name": "Custom_Object__c",
                "document_type": "Database Table", 
                "document_description": f"SQL table: {table_name}",
                "confidence_score": 20
            }

    async def categorize_sql_table(self, table_name: str, columns: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Send table name and column details to OpenAI for categorization
        Similar to how Salesforce objects are categorized
        """
        try:
            # Prepare column information for AI
            column_info = []
            for col in columns:
                column_info.append({
                    'name': col.get('column_name', ''),
                    'type': col.get('data_type', ''),
                    'description': col.get('column_comment', ''),
                    'is_nullable': col.get('is_nullable', True),
                    'is_primary_key': col.get('is_primary_key', False)
                })
            
            prompt = f"""
            Analyze this SQL table and categorize it for Salesforce migration:
            
            Table Name: {table_name}
            Columns: {json.dumps(column_info, indent=2)}
            
            Please categorize this table and provide:
            1. object_name: Suggested Salesforce object name (e.g., "Account", "Contact", "Opportunity")
            2. document_type: Category of data (e.g., "Customer Records", "Product Data", "Transaction Records")
            3. document_description: Detailed description of what this table contains
            4. suggested_mappings: Suggested field mappings from SQL columns to Salesforce fields
            5. data_type_conversions: Any data type conversions needed
            
            Return your response as a JSON object with the following structure:
            {{
                "object_name": "Account",
                "document_type": "Customer Records", 
                "document_description": "This table contains customer account information including contact details, company information, and account status.",
                "suggested_mappings": {{
                    "id": "Id",
                    "company_name": "Name",
                    "email": "Email__c",
                    "phone": "Phone",
                    "created_date": "CreatedDate"
                }},
                "data_type_conversions": {{
                    "id": "Text",
                    "company_name": "Text",
                    "email": "Email",
                    "phone": "Phone",
                    "created_date": "DateTime"
                }}
            }}
            """
            import asyncio
            from services.architect_agent import async_openai_call
            from services.llm import tasks as llm_tasks

            response = await async_openai_call(
                None,
                task=llm_tasks.SCHEMA_CATEGORIZATION,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1000,
            )
            
            ai_response = response.choices[0].message.content.strip()
            
            # Parse JSON response
            try:
                # Extract JSON from response (handle markdown and explanatory text)
                if '```json' in ai_response:
                    # Extract content between ```json and ```
                    start = ai_response.find('```json') + 7
                    end = ai_response.find('```', start)
                    if end != -1:
                        ai_response = ai_response[start:end].strip()
                elif '```' in ai_response:
                    # Extract content between ``` and ```
                    start = ai_response.find('```') + 3
                    end = ai_response.find('```', start)
                    if end != -1:
                        ai_response = ai_response[start:end].strip()
                elif '{' in ai_response and '}' in ai_response:
                    # Extract JSON object from text
                    start = ai_response.find('{')
                    end = ai_response.rfind('}') + 1
                    ai_response = ai_response[start:end]
                
                categorization = json.loads(ai_response)
                
                # Validate required fields
                required_fields = ['object_name', 'document_type', 'document_description']
                for field in required_fields:
                    if field not in categorization:
                        categorization[field] = "Unknown"
                
                return categorization
                
            except json.JSONDecodeError as e:
                print(f"Error parsing AI response: {e}")
                print(f"Raw response: {ai_response}")
                
                # Return fallback categorization
                return {
                    "object_name": "Custom_Object__c",
                    "document_type": "Database Table",
                    "document_description": f"SQL table: {table_name}",
                    "suggested_mappings": {},
                    "data_type_conversions": {}
                }
                
        except Exception as e:
            print(f"Error in AI categorization: {e}")
            return {
                "object_name": "Custom_Object__c",
                "document_type": "Database Table", 
                "document_description": f"SQL table: {table_name}",
                "suggested_mappings": {},
                "data_type_conversions": {}
            }
    
    async def categorize_multiple_tables(self, tables_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Categorize multiple tables in batch
        """
        results = []
        
        for table_data in tables_data:
            table_name = table_data['table_name']
            columns = table_data['columns']
            
            categorization = await self.categorize_sql_table(table_name, columns)
            
            results.append({
                'table_name': table_name,
                'schema_data': table_data,
                'ai_categorization': categorization
            })
        
        return results
    
    async def suggest_table_for_prompt(self, user_prompt: str, categorized_tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Suggest which tables match the user's prompt
        """
        try:
            # Prepare table summaries for AI
            table_summaries = []
            for table in categorized_tables:
                table_summaries.append({
                    'table_name': table['table_name'],
                    'object_name': table['ai_categorization'].get('object_name', ''),
                    'document_type': table['ai_categorization'].get('document_type', ''),
                    'document_description': table['ai_categorization'].get('document_description', '')
                })
            
            prompt = f"""
            User prompt: "{user_prompt}"
            
            Available tables with their AI categorizations:
            {json.dumps(table_summaries, indent=2)}
            
            Based on the user's prompt, suggest which tables are most relevant for data extraction.
            Return a JSON array of table names ordered by relevance (most relevant first).
            
            Example response:
            ["users", "customers", "accounts"]
            """
            import asyncio
            from services.architect_agent import async_openai_call
            from services.llm import tasks as llm_tasks

            response = await async_openai_call(
                None,
                task=llm_tasks.SCHEMA_CATEGORIZATION,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=500,
            )
            
            ai_response = response.choices[0].message.content.strip()
            
            try:
                # Extract JSON from response (handle markdown and explanatory text)
                if '```json' in ai_response:
                    # Extract content between ```json and ```
                    start = ai_response.find('```json') + 7
                    end = ai_response.find('```', start)
                    if end != -1:
                        ai_response = ai_response[start:end].strip()
                elif '```' in ai_response:
                    # Extract content between ``` and ```
                    start = ai_response.find('```') + 3
                    end = ai_response.find('```', start)
                    if end != -1:
                        ai_response = ai_response[start:end].strip()
                elif '[' in ai_response and ']' in ai_response:
                    # Extract JSON array from text
                    start = ai_response.find('[')
                    end = ai_response.rfind(']') + 1
                    ai_response = ai_response[start:end]
                
                suggested_tables = json.loads(ai_response)
                
                # Filter and return only tables that exist in our categorized_tables
                available_table_names = [t['table_name'] for t in categorized_tables]
                filtered_suggestions = [t for t in suggested_tables if t in available_table_names]
                
                return filtered_suggestions
                
            except json.JSONDecodeError as e:
                print(f"Error parsing AI table suggestion response: {e}")
                return []
                
        except Exception as e:
            print(f"Error in table suggestion: {e}")
            return []
