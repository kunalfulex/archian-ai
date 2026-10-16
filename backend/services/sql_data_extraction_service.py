import asyncio
import json
from typing import Dict, List, Any, Optional
from dotenv import load_dotenv
from services.sql_connector_service import SQLConnectorFactory, SQLConnectorService
from db.connection import database

load_dotenv()


class SQLDataExtractionService:
    """Service for extracting data from SQL tables with AI-powered field mapping"""
    
    def __init__(self):
        self.connector_service = SQLConnectorService()
    
    async def extract_data_with_ai_mapping(
        self, 
        connector_id: str, 
        table_name: str, 
        user_prompt: str, 
        user_id: str,
        limit: int = 100
    ) -> Dict[str, Any]:
        """
        Extract data from SQL table and get AI-powered field mapping for Salesforce
        This is similar to the existing document extraction flow
        """
        try:
            # Get connector details
            connector = await self.get_connector_details(connector_id, user_id)
            if not connector:
                return {
                    "status": "error",
                    "message": "Connector not found"
                }
            
            # Create connector instance
            connector_config = self.prepare_connector_config(connector)
            connector_instance = SQLConnectorFactory.create_connector(
                connector['connector_type'], 
                connector_config
            )
            
            # Extract data based on user prompt
            extracted_data = await self.extract_data_from_table(
                connector_instance, 
                table_name, 
                user_prompt, 
                limit
            )
            
            if not extracted_data:
                return {
                    "status": "error",
                    "message": "No data found matching your criteria"
                }
            
            # Get AI categorization and field mapping
            ai_result = await self.get_ai_categorization_and_mapping(
                table_name, 
                extracted_data, 
                user_prompt
            )
            
            # Close connection
            await connector_instance.close_connection()
            
            return {
                "status": "success",
                "message": f"Successfully extracted {len(extracted_data)} records from {table_name}",
                "data": extracted_data,
                "table_name": table_name,
                "total_records": len(extracted_data),
                "ai_categorization": ai_result
            }
            
        except Exception as e:
            print(f"Error in SQL data extraction: {e}")
            return {
                "status": "error",
                "message": f"Extraction failed: {str(e)}"
            }
    
    async def extract_data_from_table(
        self, 
        connector_instance, 
        table_name: str, 
        user_prompt: str, 
        limit: int
    ) -> List[Dict[str, Any]]:
        """Extract data from table based on user prompt"""
        try:
            # For now, we'll extract all data and let AI filter it
            # In the future, we could build dynamic SQL queries based on the prompt
            if hasattr(connector_instance, 'config') and connector_instance.config.get('connector_type') == 'oracle':
                query = f"SELECT * FROM {table_name} WHERE ROWNUM <= {limit}"
            else:
                query = f"SELECT * FROM {table_name} LIMIT {limit}"
            
            return await connector_instance.execute_query(query)
        except Exception as e:
            print(f"Error extracting data from {table_name}: {e}")
            return []
    
    def generate_fake_data(self, real_data: List[Dict[str, Any]], count: int = 5) -> List[Dict[str, Any]]:
        """
        Generate fake data that matches the structure of real data
        This is enterprise-safe as no real data is sent to OpenAI
        """
        if not real_data:
            return []
        
        fake_data = []
        sample_record = real_data[0]
        
        for i in range(min(count, len(real_data))):
            fake_record = {}
            for field, value in sample_record.items():
                if isinstance(value, str):
                    if any(keyword in field.lower() for keyword in ['email', 'mail']):
                        fake_record[field] = f"user{i+1}@example.com"
                    elif any(keyword in field.lower() for keyword in ['phone', 'mobile', 'tel']):
                        fake_record[field] = f"555-{str(i+1).zfill(4)}"
                    elif any(keyword in field.lower() for keyword in ['name', 'first', 'last']):
                        fake_record[field] = f"Sample {field.replace('_', ' ').title()} {i+1}"
                    elif any(keyword in field.lower() for keyword in ['address', 'street', 'city']):
                        fake_record[field] = f"123 Sample {field.replace('_', ' ').title()} St"
                    elif any(keyword in field.lower() for keyword in ['company', 'org']):
                        fake_record[field] = f"Sample Company {i+1}"
                    else:
                        fake_record[field] = f"Sample {field.replace('_', ' ').title()} {i+1}"
                elif isinstance(value, int):
                    fake_record[field] = i + 1
                elif isinstance(value, float):
                    fake_record[field] = round(100.0 + i * 10.5, 2)
                elif isinstance(value, bool):
                    fake_record[field] = i % 2 == 0
                else:
                    fake_record[field] = f"Sample {field.replace('_', ' ').title()} {i+1}"
            
            fake_data.append(fake_record)
        
        return fake_data

    async def get_ai_categorization_and_mapping(
        self, 
        table_name: str, 
        extracted_data: List[Dict[str, Any]], 
        user_prompt: str
    ) -> Dict[str, Any]:
        """
        Send real data with user prompt to OpenAI for categorization and field mapping
        Data is only sent for analysis, not stored, and data sharing with OpenAI is disabled
        """
        try:
            # Use real data for OpenAI analysis (limit to 5 records for token management)
            # Data is only sent for analysis, not stored, and data sharing with OpenAI is disabled
            real_data = extracted_data[:5] if len(extracted_data) > 5 else extracted_data
            
            prompt = f"""
            Analyze this extracted SQL data and provide Salesforce categorization and field mapping:
            
            Table Name: {table_name}
            User Prompt: "{user_prompt}"
            Sample Data: {json.dumps(real_data, indent=2)}
            
            Based on the user's prompt and the real data provided, please provide:
            1. object_name: Suggested Salesforce object name (e.g., "Account", "Contact", "Opportunity")
            2. document_type: Category of data (e.g., "Customer Records", "Product Data", "Transaction Records")
            3. document_description: Detailed description of what this data represents
            4. field_mappings: Suggested field mappings from SQL columns to Salesforce fields
            5. data_type_conversions: Data type conversions needed for Salesforce
            
            Return your response as a JSON object with the following structure:
            {{
                "object_name": "Account",
                "document_type": "Customer Records",
                "document_description": "This data contains customer account information extracted based on the user's request for customer data.",
                "field_mappings": {{
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
            from services.architect_agent import async_openai_call
            from services.llm import tasks as llm_tasks

            response = await async_openai_call(
                None,
                task=llm_tasks.SQL_EXTRACTION,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1500,
            )
            
            ai_response = response.choices[0].message.content.strip()
            
            # Parse JSON response
            try:
                # Remove any markdown formatting if present
                if ai_response.startswith('```json'):
                    ai_response = ai_response[7:]
                if ai_response.endswith('```'):
                    ai_response = ai_response[:-3]
                
                result = json.loads(ai_response)
                
                # Validate required fields
                required_fields = ['object_name', 'document_type', 'document_description']
                for field in required_fields:
                    if field not in result:
                        result[field] = "Unknown"
                
                return result
                
            except json.JSONDecodeError as e:
                print(f"Error parsing AI response: {e}")
                print(f"Raw response: {ai_response}")
                
                # Return fallback result
                return {
                    "object_name": "Custom_Object__c",
                    "document_type": "Database Table",
                    "document_description": f"SQL data from {table_name}",
                    "field_mappings": {},
                    "data_type_conversions": {}
                }
                
        except Exception as e:
            print(f"Error in AI categorization and mapping: {e}")
            return {
                "object_name": "Custom_Object__c",
                "document_type": "Database Table",
                "document_description": f"SQL data from {table_name}",
                "field_mappings": {},
                "data_type_conversions": {}
            }
    
    async def get_connector_details(self, connector_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Get connector details from database"""
        try:
            result = await database.fetch_one(
                """
                SELECT * FROM sql_connectors 
                WHERE id = :connector_id AND user_id = :user_id
                """,
                {"connector_id": connector_id, "user_id": user_id}
            )
            return dict(result) if result else None
        except Exception as e:
            print(f"Error getting connector details: {e}")
            return None
    
    def prepare_connector_config(self, connector: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare connector configuration with decrypted password"""
        config = {
            'host': connector.get('host'),
            'port': connector.get('port'),
            'database_name': connector.get('database_name'),
            'username': connector.get('username'),
            'ssl_enabled': connector.get('ssl_enabled', False),
            'additional_config': connector.get('additional_config', {})
        }
        
        # Decrypt password if available
        if connector.get('password_encrypted'):
            config['password'] = self.connector_service.decrypt_password(
                connector['password_encrypted']
            )
        
        # Handle connection string for complex connections
        if connector.get('connection_string'):
            config['connection_string'] = connector['connection_string']
        
        # Handle BigQuery service account key
        if connector.get('connector_type') == 'bigquery' and connector.get('additional_config'):
            config['service_account_key'] = connector['additional_config'].get('service_account_key')
        
        return config
