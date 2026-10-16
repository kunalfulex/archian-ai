"""
Google Sheets Service
Handles all Google Sheets API operations for reading, writing, and managing spreadsheets.
"""

import asyncio
import os
from typing import List, Dict, Any, Optional
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.exceptions import RefreshError
from datetime import datetime, timedelta


class GoogleSheetsAuthError(Exception):
    """OAuth token expired, revoked, or invalid — user must reconnect Google Sheets."""

    pass


class GoogleSheetsRateLimitError(Exception):
    """Custom exception for Google Sheets API rate limit errors (429)"""
    def __init__(self, message: str, retry_after: Optional[int] = None):
        self.retry_after = retry_after
        super().__init__(message)


class GoogleSheetsAPIError(Exception):
    """Custom exception for Google Sheets API errors"""
    def __init__(self, message: str, status_code: Optional[int] = None, error_details: Optional[str] = None):
        self.status_code = status_code
        self.error_details = error_details
        super().__init__(message)


def _is_google_oauth_reauth_failure(exc: BaseException) -> bool:
    """True if exception or its chain indicates invalid_grant / refresh token failure."""
    if isinstance(exc, RefreshError):
        return True
    s = str(exc).lower()
    if "invalid_grant" in s:
        return True
    depth = 0
    c: Optional[BaseException] = exc
    while c is not None and depth < 8:
        if type(c).__name__ == "RefreshError":
            return True
        if "invalid_grant" in str(c).lower():
            return True
        c = c.__cause__
        depth += 1
    return False


class GoogleSheetsService:
    """Service class for Google Sheets API operations"""
    
    def __init__(self, access_token: str, refresh_token: str, expires_at: datetime = None):
        """
        Initialize Google Sheets service with OAuth credentials
        
        Args:
            access_token: OAuth access token
            refresh_token: OAuth refresh token
            expires_at: Token expiration timestamp
        """
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_at = expires_at
        self.client_id = os.getenv("GOOGLE_CLIENT_ID")
        self.client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        
        # Create credentials object
        self.credentials = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self.client_id,
            client_secret=self.client_secret
        )
        
        # Build the Google Sheets API service
        self.service = build('sheets', 'v4', credentials=self.credentials)
    
    async def get_all_sheet_names(self, spreadsheet_id: str) -> List[Dict[str, Any]]:
        """
        Get all sheet names and IDs from a Google Spreadsheet
        
        Args:
            spreadsheet_id: The ID of the spreadsheet
        
        Returns:
            List of dictionaries with sheet_name and sheet_id
        """
        try:
            # Get spreadsheet metadata with sheet properties
            spreadsheet = await asyncio.to_thread(
                lambda: self.service.spreadsheets().get(
                    spreadsheetId=spreadsheet_id,
                    fields='properties,sheets.properties'
                ).execute()
            )
            
            sheets_info = []
            for sheet in spreadsheet.get('sheets', []):
                sheet_props = sheet.get('properties', {})
                sheets_info.append({
                    'sheet_name': sheet_props.get('title', 'Sheet1'),
                    'sheet_id': str(sheet_props.get('sheetId', 0))
                })
            
            return sheets_info
            
        except HttpError as e:
            print(f"Error getting sheet names: {e}")
            return []
        except Exception as e:
            print(f"Unexpected error getting sheet names: {e}")
            return []

    async def search_records(self, spreadsheet_id: str, search_term: str, sheet_name: str = 'Sheet1', max_results: int = 5) -> Dict[str, Any]:
        """
        Search for records matching a search term across all columns
        
        Args:
            spreadsheet_id: The ID of the spreadsheet
            search_term: The term to search for
            sheet_name: The name of the sheet to search in
            max_results: Maximum number of matching records to return
        
        Returns:
            Dictionary with matching records and metadata
        """
        try:
            # First get all data from the sheet
            result = await self.read_spreadsheet(
                spreadsheet_id=spreadsheet_id,
                range_name='A1:ZZ1000',  # Get a large range
                sheet_name=sheet_name
            )
            
            if not result.get('success') or not result.get('data'):
                return {
                    'success': False,
                    'error': 'Could not read sheet data',
                    'matching_records': []
                }
            
            headers = result.get('headers', [])
            all_data = result.get('data', [])
            
            # Search through all records
            matching_records = []
            search_lower = search_term.lower()
            search_words = search_lower.split()  # Split into individual words
            
            for record in all_data:
                # Check if search term matches any field value (exact or partial match)
                is_match = False
                
                # First try exact phrase matching
                for field_value in record.values():
                    if search_lower in str(field_value).lower():
                        is_match = True
                        break
                
                # If no exact phrase match, try matching individual words
                if not is_match and len(search_words) > 1:
                    # Check if all search words appear somewhere in the record
                    record_text = ' '.join(str(v).lower() for v in record.values())
                    if all(word in record_text for word in search_words):
                        is_match = True
                
                # If still no match, try partial word matching
                if not is_match:
                    for field_value in record.values():
                        field_lower = str(field_value).lower()
                        for word in search_words:
                            if word in field_lower:
                                is_match = True
                                break
                        if is_match:
                            break
                
                if is_match:
                    matching_records.append(record)
                
                if len(matching_records) >= max_results:
                    break
            
            return {
                'success': True,
                'search_term': search_term,
                'matching_records': matching_records,
                'total_matches_found': len(matching_records),
                'headers': headers
            }
            
        except HttpError as e:
            print(f"Error searching records: {e}")
            return {
                'success': False,
                'error': str(e),
                'matching_records': []
            }
        except Exception as e:
            print(f"Unexpected error searching records: {e}")
            return {
                'success': False,
                'error': str(e),
                'matching_records': []
            }
    
    async def search_records_with_ai(self, spreadsheet_id: str, search_query: str, headers: List[str], all_records: List[Dict], max_results: int = 5) -> Dict[str, Any]:
        """
        Use AI to generate intelligent search criteria based on spreadsheet schema and user query.
        
        Args:
            spreadsheet_id: The ID of the spreadsheet
            search_query: The user's natural language search query
            headers: List of column headers from the spreadsheet
            all_records: All records from the spreadsheet
            max_results: Maximum number of matching records to return
        
        Returns:
            Dictionary with matching records and search criteria used
        """
        import json  # Import at function level to avoid UnboundLocalError
        
        try:
            import asyncio
            
            
            # Build schema context for OpenAI
            schema_str = ", ".join(headers)
            
            ai_prompt = f"""
            You are Archian AI, a data search expert. Given a spreadsheet with columns: {schema_str}
            
            The user is searching for: "{search_query}"
            
            Generate search criteria in JSON format to find matching records. Return a JSON object with:
            - "search_fields": array of column names to search in
            - "search_terms": array of search terms extracted from the query
            - "match_condition": "AND" or "OR" - determines how search terms should be matched
              * Use "AND" (default) when user wants all terms to match (e.g., "Kunal Fule" = both must match)
              * Use "OR" when user explicitly uses "or" or wants any term to match (e.g., "California or New York")
            - "explanation": brief explanation of what we're searching for and why this match condition was chosen
            
            Examples:
            
            Query: "Kunal Fule"
            {{
                "search_fields": ["First Name", "Last Name"],
                "search_terms": ["Kunal", "Fule"],
                "match_condition": "AND",
                "explanation": "Searching for records where both 'Kunal' and 'Fule' are present (full name match)"
            }}
            
            Query: "contacts from California or New York"
            {{
                "search_fields": ["State", "Location"],
                "search_terms": ["California", "New York"],
                "match_condition": "OR",
                "explanation": "Searching for records in either California or New York"
            }}
            
            Query: "John Smith"
            {{
                "search_fields": ["First Name", "Last Name"],
                "search_terms": ["John", "Smith"],
                "match_condition": "AND",
                "explanation": "Searching for full name match - both first and last name must be present"
            }}
            
            IMPORTANT: Default to "AND" unless the user explicitly uses "or" or clearly wants any term to match.
            Be smart about interpreting the user's intent. Extract relevant search terms.
            Return ONLY valid JSON, no other text.
            """
            
            # Use asyncio.to_thread for async execution
            from services.architect_agent import async_openai_call
            from services.llm import tasks as llm_tasks

            response = await async_openai_call(
                None,
                task=llm_tasks.GOOGLE_SHEETS_DETECTION,
                messages=[{"role": "user", "content": ai_prompt}],
                temperature=0,
            )
            
            # Parse the response
            ai_response = response.choices[0].message.content.strip()
            search_criteria = json.loads(ai_response)
            
            print(f"🤖 AI Search Criteria: {search_criteria}")
            
            # Now search using the AI-generated criteria
            matching_records = []
            search_fields = search_criteria.get("search_fields", headers)
            search_terms = search_criteria.get("search_terms", [])
            match_condition = search_criteria.get("match_condition", "AND").upper()  # Default to AND
            
            if not search_terms:
                return {
                    'success': False,
                    'error': 'Could not extract search terms',
                    'matching_records': []
                }
            
            # Search through records
            for record in all_records:
                is_match = False
                
                if match_condition == "OR":
                    # OR logic: Any search term matches any field
                    for field in search_fields:
                        if field not in record:
                            continue
                        
                        field_value = str(record[field]).lower()
                        
                        # Check if any search term is in this field
                        for term in search_terms:
                            if term.lower() in field_value:
                                is_match = True
                                break
                        
                        if is_match:
                            break
                else:
                    # AND logic (default): All search terms must match somewhere in the record
                    # Build a combined text from all search fields for this record
                    record_text = ' '.join(
                        str(record.get(field, '')).lower() 
                        for field in search_fields 
                        if field in record
                    )
                    
                    # Check if ALL search terms are present in the record
                    all_terms_match = all(
                        term.lower() in record_text 
                        for term in search_terms
                    )
                    
                    if all_terms_match:
                        is_match = True
                
                if is_match:
                    matching_records.append(record)
                
                if len(matching_records) >= max_results:
                    break
            
            return {
                'success': True,
                'search_query': search_query,
                'search_criteria': search_criteria,
                'matching_records': matching_records,
                'total_matches_found': len(matching_records),
                'headers': headers
            }
            
        except json.JSONDecodeError as e:
            print(f"Error parsing AI search criteria: {e}")
            return {
                'success': False,
                'error': f'Failed to parse search criteria: {str(e)}',
                'matching_records': []
            }
        except Exception as e:
            print(f"Error in AI search: {e}")
            return {
                'success': False,
                'error': str(e),
                'matching_records': []
            }
    
    async def read_spreadsheet(
        self,
        spreadsheet_id: str,
        range_name: str = 'A1:ZZ',
        sheet_name: str = None,
        max_rows: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Read data from a Google Spreadsheet
        
        Args:
            spreadsheet_id: The ID of the spreadsheet (from URL)
            range_name: The A1 notation range to read (default: all columns)
            sheet_name: Optional sheet/tab name (default: first sheet)
            max_rows: Optional maximum number of data rows to read (excludes header row).
                     If specified, the range will be modified to only read this many rows.
        
        Returns:
            Dictionary with spreadsheet data and metadata
        """
        try:
            import re
            
            # Apply max_rows limit to range if specified
            if max_rows is not None and max_rows > 0:
                # Parse the range to extract start and end cells
                # Handle ranges like: 'A1:ZZ', 'A1:ZZ10000', 'Sheet1!A1:ZZ', etc.
                
                # Extract sheet prefix if present
                sheet_prefix = ""
                range_part = range_name
                if '!' in range_name:
                    parts = range_name.split('!', 1)
                    sheet_prefix = parts[0] + '!'
                    range_part = parts[1]
                
                # Parse range_part (e.g., 'A1:ZZ' or 'A1:ZZ10000')
                if ':' in range_part:
                    start_cell, end_cell = range_part.split(':', 1)
                    
                    # Extract start row number (usually 1 for headers)
                    start_row_match = re.search(r'(\d+)$', start_cell)
                    start_row = int(start_row_match.group(1)) if start_row_match else 1
                    
                    # Extract column letters from end_cell (e.g., 'ZZ' from 'ZZ10000')
                    end_col_match = re.match(r'^([A-Z]+)', end_cell)
                    end_col = end_col_match.group(1) if end_col_match else 'ZZ'
                    
                    # Calculate end row: start_row (header) + max_rows (data rows)
                    # If start_row is 1, end_row = 1 + max_rows
                    # If start_row is 2 (data starts), end_row = 2 + max_rows - 1
                    if start_row == 1:
                        # Header row included, so end_row = 1 + max_rows
                        end_row = 1 + max_rows
                    else:
                        # Data starts at start_row, so end_row = start_row + max_rows - 1
                        end_row = start_row + max_rows - 1
                    
                    # Build new range with limit applied (preserve sheet prefix if present)
                    range_name = f"{sheet_prefix}{start_cell}:{end_col}{end_row}"
                else:
                    # No range specified, use default with limit (preserve sheet prefix if present)
                    range_name = f"{sheet_prefix}A1:ZZ{1 + max_rows}"
            
            # Build base range and sheet prefix
            if sheet_name:
                if '!' in range_name:
                    sheet_prefix = range_name.split('!', 1)[0] + '!'
                    range_part = range_name.split('!', 1)[1]
                else:
                    sheet_prefix = f"{sheet_name}!"
                    range_part = range_name
            else:
                sheet_prefix = ""
                range_part = range_name

            # Get spreadsheet metadata (run in thread to avoid blocking event loop)
            spreadsheet = await asyncio.to_thread(
                lambda: self.service.spreadsheets().get(
                    spreadsheetId=spreadsheet_id
                ).execute()
            )

            try:
                from utils.batch_config import GSHEETS_CHUNK_ROWS
                chunk_rows = GSHEETS_CHUNK_ROWS
            except ImportError:
                chunk_rows = 10000

            # Chunked reads for ranges that would exceed chunk_rows (avoids timeouts/memory limits)
            def _parse_range(rpart: str) -> tuple:
                if ':' not in rpart:
                    return 1, 999999, 'A', 'ZZ'
                start_cell, end_cell = rpart.split(':', 1)
                start_row_m = re.search(r'(\d+)$', start_cell)
                start_row = int(start_row_m.group(1)) if start_row_m else 1
                end_row_m = re.search(r'(\d+)$', end_cell)
                end_row = int(end_row_m.group(1)) if end_row_m else 999999
                end_col_m = re.match(r'^([A-Z]+)', end_cell)
                end_col = end_col_m.group(1) if end_col_m else 'ZZ'
                start_col_m = re.match(r'^([A-Z]+)', start_cell)
                start_col = start_col_m.group(1) if start_col_m else 'A'
                return start_row, end_row, start_col, end_col

            start_row, end_row, start_col, end_col = _parse_range(range_part)
            effective_end = end_row
            if max_rows is not None and max_rows > 0 and start_row == 1:
                effective_end = min(end_row, 1 + max_rows)

            all_values = []
            headers = []
            row_offset = start_row
            while row_offset <= effective_end:
                chunk_end = min(row_offset + chunk_rows - 1, effective_end)
                chunk_range = f"{start_col}{row_offset}:{end_col}{chunk_end}"
                full_range = f"{sheet_prefix}{chunk_range}" if sheet_prefix else chunk_range
                result = await asyncio.to_thread(
                    lambda fr=full_range: self.service.spreadsheets().values().get(
                        spreadsheetId=spreadsheet_id,
                        range=fr,
                        valueRenderOption='UNFORMATTED_VALUE',
                        dateTimeRenderOption='FORMATTED_STRING'
                    ).execute()
                )
                chunk_values = result.get('values', [])
                if not chunk_values:
                    break
                if not headers and row_offset == start_row:
                    headers = chunk_values[0] if chunk_values else []
                    all_values.extend(chunk_values)
                else:
                    all_values.extend(chunk_values)
                if len(chunk_values) < chunk_rows:
                    break
                row_offset = chunk_end + 1
            
            values = all_values
            
            if not values:
                return {
                    'success': False,
                    'error': 'No data found in the specified range',
                    'spreadsheet_name': spreadsheet.get('properties', {}).get('title', 'Unknown'),
                    'data': []
                }
            
            # Convert to list of dictionaries (assuming first row is headers)
            headers = values[0] if values else []
            data_rows = values[1:] if len(values) > 1 else []
            
            structured_data = []
            for row in data_rows:
                # Pad row with empty strings if it's shorter than headers
                padded_row = row + [''] * (len(headers) - len(row))
                row_dict = {headers[i]: padded_row[i] for i in range(len(headers))}
                structured_data.append(row_dict)
            
            effective_range = f"{sheet_prefix}{start_col}{start_row}:{end_col}{effective_end}" if sheet_prefix else f"{start_col}{start_row}:{end_col}{effective_end}"
            return {
                'success': True,
                'spreadsheet_id': spreadsheet_id,
                'spreadsheet_name': spreadsheet.get('properties', {}).get('title', 'Unknown'),
                'sheet_name': sheet_name or spreadsheet.get('sheets', [{}])[0].get('properties', {}).get('title', 'Sheet1'),
                'range': effective_range,
                'headers': headers,
                'data': structured_data,
                'total_rows': len(structured_data)
            }
            
        except HttpError as e:
            error_message = str(e)
            if '404' in error_message:
                return {
                    'success': False,
                    'error': 'Spreadsheet not found. Please check the spreadsheet ID or URL.',
                    'data': []
                }
            elif '403' in error_message:
                return {
                    'success': False,
                    'error': 'I don\'t have access to this spreadsheet. To connect it:\n1. Open the spreadsheet in Google Sheets\n2. Click the "Share" button\n3. Share it with your Archian AI connected Google account\n4. Give "Editor" or "Viewer" permissions\n5. Try again',
                    'data': [],
                    'permission_denied': True
                }
            else:
                return {
                    'success': False,
                    'error': f'Google Sheets API error: {error_message}',
                    'data': []
                }
        except Exception as e:
            return {
                'success': False,
                'error': f'Unexpected error reading spreadsheet: {str(e)}',
                'data': []
            }
    
    async def create_spreadsheet(
        self,
        title: str,
        data: List[Dict[str, Any]],
        sheet_name: str = 'Sheet1'
    ) -> Dict[str, Any]:
        """
        Create a new Google Spreadsheet with data
        
        Args:
            title: Title for the new spreadsheet
            data: List of dictionaries containing the data OR List of lists (headers + data rows)
            sheet_name: Name for the first sheet (default: 'Sheet1')
        
        Returns:
            Dictionary with spreadsheet info and URL
        """
        try:
            if not data:
                return {
                    'success': False,
                    'error': 'No data provided to create spreadsheet'
                }
            
            # Create the spreadsheet
            spreadsheet = {
                'properties': {
                    'title': title
                },
                'sheets': [{
                    'properties': {
                        'title': sheet_name
                    }
                }]
            }
            
            spreadsheet = await asyncio.to_thread(
                lambda: self.service.spreadsheets().create(
                    body=spreadsheet,
                    fields='spreadsheetId,spreadsheetUrl,properties.title'
                ).execute()
            )
            
            spreadsheet_id = spreadsheet.get('spreadsheetId')
            spreadsheet_url = spreadsheet.get('spreadsheetUrl')
            
            # Check if data is already in list format (headers + rows)
            if data and isinstance(data[0], list):
                values = data
            else:
                # Prepare data for writing
                headers = list(data[0].keys())
                values = [headers]
                
                for row in data:
                    values.append([row.get(header, '') for header in headers])
            
            # Write data to the spreadsheet
            body = {
                'values': values
            }
            
            await asyncio.to_thread(
                lambda: self.service.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range=f'{sheet_name}!A1',
                    valueInputOption='USER_ENTERED',
                    body=body
                ).execute()
            )
            
            return {
                'success': True,
                'spreadsheet_id': spreadsheet_id,
                'spreadsheet_url': spreadsheet_url,
                'spreadsheet_name': title,
                'sheet_name': sheet_name,
                'rows_written': len(data)
            }
            
        except HttpError as e:
            return {
                'success': False,
                'error': f'Google Sheets API error: {str(e)}'
            }
        except Exception as e:
            return {
                'success': False,
                'error': f'Unexpected error creating spreadsheet: {str(e)}'
            }
    
    async def append_to_spreadsheet(
        self,
        spreadsheet_id: str,
        data: List[Dict[str, Any]],
        sheet_name: str = None
    ) -> Dict[str, Any]:
        """
        Append data to an existing Google Spreadsheet
        
        Args:
            spreadsheet_id: The ID of the spreadsheet
            data: List of dictionaries containing the data to append OR List of lists
            sheet_name: Optional sheet/tab name (default: first sheet)
        
        Returns:
            Dictionary with operation result
        """
        try:
            if not data:
                return {
                    'success': False,
                    'error': 'No data provided to append'
                }
            
            # Get spreadsheet info
            spreadsheet = await asyncio.to_thread(
                lambda: self.service.spreadsheets().get(
                    spreadsheetId=spreadsheet_id
                ).execute()
            )
            
            # Determine sheet name
            if not sheet_name:
                sheet_name = spreadsheet.get('sheets', [{}])[0].get('properties', {}).get('title', 'Sheet1')
            
            # Check if data is already in list format or dict format
            if data and isinstance(data[0], list):
                values = data
            else:
                # Prepare data
                headers = list(data[0].keys())
                values = []
                
                for row in data:
                    values.append([row.get(header, '') for header in headers])
            
            # Append data
            body = {
                'values': values
            }
            
            result = await asyncio.to_thread(
                lambda: self.service.spreadsheets().values().append(
                    spreadsheetId=spreadsheet_id,
                    range=f'{sheet_name}!A1',
                    valueInputOption='USER_ENTERED',
                    insertDataOption='INSERT_ROWS',
                    body=body
                ).execute()
            )
            
            return {
                'success': True,
                'spreadsheet_id': spreadsheet_id,
                'spreadsheet_name': spreadsheet.get('properties', {}).get('title', 'Unknown'),
                'sheet_name': sheet_name,
                'rows_appended': len(data),
                'updated_range': result.get('updates', {}).get('updatedRange', '')
            }
            
        except HttpError as e:
            error_message = str(e)
            if '404' in error_message:
                return {
                    'success': False,
                    'error': 'Spreadsheet not found. Please check the spreadsheet ID or URL.'
                }
            elif '403' in error_message:
                return {
                    'success': False,
                    'error': 'I don\'t have write access to this spreadsheet. To connect it:\n1. Open the spreadsheet in Google Sheets\n2. Click the "Share" button\n3. Share it with your Archian AI connected Google account\n4. Give "Editor" permissions (not just Viewer)\n5. Try again',
                    'permission_denied': True
                }
            else:
                return {
                    'success': False,
                    'error': f'Google Sheets API error: {error_message}'
                }
        except Exception as e:
            return {
                'success': False,
                'error': f'Unexpected error appending to spreadsheet: {str(e)}'
            }
    
    async def update_spreadsheet(
        self,
        spreadsheet_id: str,
        data: List[Dict[str, Any]],
        sheet_name: str = None,
        start_row: int = 2  # Default to row 2 (after headers)
    ) -> Dict[str, Any]:
        """
        Update existing data in a Google Spreadsheet (overwrites from start_row)
        
        Args:
            spreadsheet_id: The ID of the spreadsheet
            data: List of dictionaries containing the data
            sheet_name: Optional sheet/tab name (default: first sheet)
            start_row: Row number to start writing (default: 2, after headers)
        
        Returns:
            Dictionary with operation result
        """
        try:
            if not data:
                return {
                    'success': False,
                    'error': 'No data provided to update'
                }
            
            # Get spreadsheet info
            spreadsheet = await asyncio.to_thread(
                lambda: self.service.spreadsheets().get(
                    spreadsheetId=spreadsheet_id
                ).execute()
            )
            
            # Determine sheet name
            if not sheet_name:
                sheet_name = spreadsheet.get('sheets', [{}])[0].get('properties', {}).get('title', 'Sheet1')
            
            # Check if data is already in list format or dict format
            if data and isinstance(data[0], list):
                values = data
            else:
                # Prepare data from dicts
                headers = list(data[0].keys())
                values = []
                
                for row in data:
                    values.append([row.get(header, '') for header in headers])
            
            # Update data
            body = {
                'values': values
            }
            
            range_name = f'{sheet_name}!A{start_row}'
            
            result = await asyncio.to_thread(
                lambda rn=range_name: self.service.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range=rn,
                    valueInputOption='USER_ENTERED',
                    body=body
                ).execute()
            )
            
            return {
                'success': True,
                'spreadsheet_id': spreadsheet_id,
                'spreadsheet_name': spreadsheet.get('properties', {}).get('title', 'Unknown'),
                'sheet_name': sheet_name,
                'rows_updated': len(data),
                'updated_range': result.get('updatedRange', '')
            }
            
        except HttpError as e:
            status_code = e.resp.status if hasattr(e, 'resp') and hasattr(e.resp, 'status') else None
            error_message = str(e)
            
            # Check for rate limit (429)
            if status_code == 429:
                retry_after = None
                if hasattr(e, 'resp') and hasattr(e.resp, 'headers') and 'Retry-After' in e.resp.headers:
                    try:
                        retry_after = int(e.resp.headers['Retry-After'])
                    except (ValueError, TypeError):
                        retry_after = None
                
                print(f"❌ Google Sheets API rate limit exceeded (429) in update_spreadsheet")
                if retry_after:
                    print(f"   Retry after: {retry_after} seconds")
                
                # For update_spreadsheet, return dict but also raise exception for consistency
                # This allows callers to handle it either way
                raise GoogleSheetsRateLimitError(
                    f"Google Sheets API rate limit exceeded. Please wait before retrying.",
                    retry_after=retry_after
                )
            
            if '404' in error_message or status_code == 404:
                return {
                    'success': False,
                    'error': 'Spreadsheet not found. Please check the spreadsheet ID or URL.'
                }
            elif '403' in error_message or status_code == 403:
                return {
                    'success': False,
                    'error': 'I don\'t have write access to this spreadsheet. To connect it:\n1. Open the spreadsheet in Google Sheets\n2. Click the "Share" button\n3. Share it with your Archian AI connected Google account\n4. Give "Editor" permissions (not just Viewer)\n5. Try again',
                    'permission_denied': True
                }
            else:
                return {
                    'success': False,
                    'error': f'Google Sheets API error: {error_message}',
                    'status_code': status_code
                }
        except Exception as e:
            return {
                'success': False,
                'error': f'Unexpected error updating spreadsheet: {str(e)}'
            }
    
    async def clear_sheet(
        self,
        spreadsheet_id: str,
        sheet_name: str = None,
        range_name: str = None
    ) -> Dict[str, Any]:
        """
        Clear data from a Google Spreadsheet
        
        Args:
            spreadsheet_id: The ID of the spreadsheet
            sheet_name: Optional sheet/tab name (default: first sheet)
            range_name: Optional specific range to clear (default: all data)
        
        Returns:
            Dictionary with operation result
        """
        try:
            # Get spreadsheet info
            spreadsheet = await asyncio.to_thread(
                lambda: self.service.spreadsheets().get(
                    spreadsheetId=spreadsheet_id
                ).execute()
            )
            
            # Determine sheet name
            if not sheet_name:
                sheet_name = spreadsheet.get('sheets', [{}])[0].get('properties', {}).get('title', 'Sheet1')
            
            # Build range
            if range_name:
                full_range = f'{sheet_name}!{range_name}'
            else:
                full_range = sheet_name
            
            # Clear the range
            result = await asyncio.to_thread(
                lambda fr=full_range: self.service.spreadsheets().values().clear(
                    spreadsheetId=spreadsheet_id,
                    range=fr
                ).execute()
            )
            
            return {
                'success': True,
                'spreadsheet_id': spreadsheet_id,
                'spreadsheet_name': spreadsheet.get('properties', {}).get('title', 'Unknown'),
                'sheet_name': sheet_name,
                'cleared_range': result.get('clearedRange', '')
            }
            
        except HttpError as e:
            error_message = str(e)
            if '404' in error_message:
                return {
                    'success': False,
                    'error': 'Spreadsheet not found. Please check the spreadsheet ID or URL.'
                }
            elif '403' in error_message:
                return {
                    'success': False,
                    'error': 'I don\'t have write access to this spreadsheet. To connect it:\n1. Open the spreadsheet in Google Sheets\n2. Click the "Share" button\n3. Share it with your Archian AI connected Google account\n4. Give "Editor" permissions (not just Viewer)\n5. Try again',
                    'permission_denied': True
                }
            else:
                return {
                    'success': False,
                    'error': f'Google Sheets API error: {error_message}'
                }
        except Exception as e:
            return {
                'success': False,
                'error': f'Unexpected error clearing sheet: {str(e)}'
            }
    
    def extract_spreadsheet_id_from_url(self, url: str) -> Optional[str]:
        """
        Extract spreadsheet ID from a Google Sheets URL
        
        Args:
            url: Google Sheets URL
        
        Returns:
            Spreadsheet ID or None if not found
        """
        import re
        
        # Pattern to match Google Sheets URLs
        pattern = r'/spreadsheets/d/([a-zA-Z0-9-_]+)'
        match = re.search(pattern, url)
        
        if match:
            return match.group(1)
        
        # If no match, assume the string itself might be the ID
        if len(url) > 30 and '/' not in url:
            return url
        
        return None

    async def get_spreadsheet_metadata(self, spreadsheet_id: str) -> Dict[str, Any]:
        """
        Get spreadsheet metadata including title and properties
        
        Args:
            spreadsheet_id: The ID of the spreadsheet
            
        Returns:
            Dict containing spreadsheet metadata
            
        Note:
            Works ONLY for spreadsheets created through Archian AI.
            For spreadsheets created directly in Google Drive (even by the user),
            will return 404 due to drive.file scope limitations.
        """
        try:
            # Get spreadsheet metadata from Sheets API
            print(f"📊 Fetching spreadsheet metadata for: {spreadsheet_id}")
            
            spreadsheet = await asyncio.to_thread(
                lambda: self.service.spreadsheets().get(
                    spreadsheetId=spreadsheet_id,
                    fields='properties,sheets.properties'
                ).execute()
            )
            
            print(f"   ✅ Successfully retrieved spreadsheet metadata")
            return spreadsheet
            
        except RefreshError as e:
            print(f"❌ Google OAuth refresh failed (reconnect required): {e}")
            raise GoogleSheetsAuthError(
                "Google Sheets access expired or was revoked. Reconnect your Google account on the Connections page."
            ) from e
        except HttpError as error:
            print(f"❌ Error getting spreadsheet metadata: {error}")
            # Re-raise HttpError so handlers can catch it and handle 403/404 errors properly
            raise error
        except GoogleSheetsAuthError:
            raise
        except Exception as e:
            print(f"❌ Unexpected error getting spreadsheet metadata: {e}")
            if _is_google_oauth_reauth_failure(e):
                raise GoogleSheetsAuthError(
                    "Google Sheets access expired or was revoked. Reconnect your Google account on the Connections page."
                ) from e
            raise Exception(f"Unexpected error getting spreadsheet metadata: {e}") from e
    
    async def write_data(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: List[List[Any]]
    ) -> Dict[str, Any]:
        """
        Write data to a specific range in the spreadsheet
        
        Args:
            spreadsheet_id: The ID of the spreadsheet
            range_name: The range to write to (e.g., "'Sheet1'!A1")
            values: 2D list of values to write
            
        Returns:
            Update result from Google Sheets API
        """
        try:
            body = {
                'values': values
            }
            
            result = await asyncio.to_thread(
                lambda: self.service.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range=range_name,
                    valueInputOption='USER_ENTERED',
                    body=body
                ).execute()
            )
            
            return result
            
        except HttpError as error:
            status_code = error.resp.status if hasattr(error, 'resp') and hasattr(error.resp, 'status') else None
            
            # Check for rate limit (429)
            if status_code == 429:
                retry_after = None
                if hasattr(error, 'resp') and hasattr(error.resp, 'headers') and 'Retry-After' in error.resp.headers:
                    try:
                        retry_after = int(error.resp.headers['Retry-After'])
                    except (ValueError, TypeError):
                        retry_after = None
                
                print(f"❌ Google Sheets API rate limit exceeded (429)")
                if retry_after:
                    print(f"   Retry after: {retry_after} seconds")
                
                raise GoogleSheetsRateLimitError(
                    f"Google Sheets API rate limit exceeded. Please wait before retrying.",
                    retry_after=retry_after
                )
            
            # Other HTTP errors
            print(f"❌ Error writing data: {error}")
            raise GoogleSheetsAPIError(
                f"Google Sheets API error: {str(error)}",
                status_code=status_code,
                error_details=str(error)
            )
        except Exception as e:
            print(f"❌ Unexpected error writing data: {e}")
            raise Exception(f"Unexpected error writing data: {e}")
    
    async def batch_write_data(
        self,
        spreadsheet_id: str,
        updates: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Batch write multiple ranges in a single API call.
        
        This method allows writing to multiple ranges in one API request,
        significantly reducing the number of API calls and helping avoid rate limits.
        
        Args:
            spreadsheet_id: The ID of the spreadsheet
            updates: List of dicts with keys:
                - 'range': Range to write to (e.g., "'Sheet1'!A2")
                - 'values': 2D list of values to write (e.g., [["Value1", "Value2"]])
        
        Returns:
            Dictionary with:
                - 'success': True if successful
                - 'totalUpdatedCells': Total number of cells updated
                - 'responses': List of individual update responses
        
        Example:
            ```python
            updates = [
                {'range': "'Sheet1'!A2", 'values': [["John", "Doe"]]},
                {'range': "'Sheet1'!A3", 'values': [["Jane", "Smith"]]}
            ]
            result = await sheets_service.batch_write_data(spreadsheet_id, updates)
            ```
        """
        try:
            data = []
            for update in updates:
                if 'range' not in update or 'values' not in update:
                    raise ValueError("Each update must have 'range' and 'values' keys")
                data.append({
                    'range': update['range'],
                    'values': update['values']
                })
            
            body = {
                'valueInputOption': 'USER_ENTERED',
                'data': data
            }
            
            result = await asyncio.to_thread(
                lambda: self.service.spreadsheets().values().batchUpdate(
                    spreadsheetId=spreadsheet_id,
                    body=body
                ).execute()
            )
            
            return {
                'success': True,
                'totalUpdatedCells': result.get('totalUpdatedCells', 0),
                'responses': result.get('responses', [])
            }
            
        except HttpError as error:
            status_code = error.resp.status if hasattr(error, 'resp') and hasattr(error.resp, 'status') else None
            
            # Check for rate limit (429)
            if status_code == 429:
                retry_after = None
                if hasattr(error, 'resp') and hasattr(error.resp, 'headers') and 'Retry-After' in error.resp.headers:
                    try:
                        retry_after = int(error.resp.headers['Retry-After'])
                    except (ValueError, TypeError):
                        retry_after = None
                
                print(f"❌ Google Sheets API rate limit exceeded (429) in batch_write_data")
                if retry_after:
                    print(f"   Retry after: {retry_after} seconds")
                
                raise GoogleSheetsRateLimitError(
                    f"Google Sheets API rate limit exceeded during batch write.",
                    retry_after=retry_after
                )
            
            # Other HTTP errors
            print(f"❌ Error during batch write: {error}")
            raise GoogleSheetsAPIError(
                f"Google Sheets API error during batch write: {str(error)}",
                status_code=status_code,
                error_details=str(error)
            )
        except Exception as e:
            print(f"❌ Unexpected error during batch write: {e}")
            raise GoogleSheetsAPIError(f"Unexpected error during batch write: {str(e)}")
    
    async def append_data(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: List[List[Any]]
    ) -> Dict[str, Any]:
        """
        Append data to a range in the spreadsheet
        
        Args:
            spreadsheet_id: The ID of the spreadsheet
            range_name: The range to append to (e.g., "'Sheet1'!A:Z")
            values: 2D list of values to append
            
        Returns:
            Append result from Google Sheets API
        """
        try:
            body = {
                'values': values
            }
            
            result = await asyncio.to_thread(
                lambda: self.service.spreadsheets().values().append(
                    spreadsheetId=spreadsheet_id,
                    range=range_name,
                    valueInputOption='USER_ENTERED',
                    insertDataOption='INSERT_ROWS',
                    body=body
                ).execute()
            )
            
            return result
            
        except HttpError as error:
            status_code = error.resp.status if hasattr(error, 'resp') and hasattr(error.resp, 'status') else None
            
            # Check for rate limit (429)
            if status_code == 429:
                retry_after = None
                if hasattr(error, 'resp') and hasattr(error.resp, 'headers') and 'Retry-After' in error.resp.headers:
                    try:
                        retry_after = int(error.resp.headers['Retry-After'])
                    except (ValueError, TypeError):
                        retry_after = None
                
                print(f"❌ Google Sheets API rate limit exceeded (429)")
                if retry_after:
                    print(f"   Retry after: {retry_after} seconds")
                
                raise GoogleSheetsRateLimitError(
                    f"Google Sheets API rate limit exceeded. Please wait before retrying.",
                    retry_after=retry_after
                )
            
            # Other HTTP errors
            print(f"❌ Error appending data: {error}")
            raise GoogleSheetsAPIError(
                f"Google Sheets API error: {str(error)}",
                status_code=status_code,
                error_details=str(error)
            )
        except Exception as e:
            print(f"❌ Unexpected error appending data: {e}")
            raise Exception(f"Unexpected error appending data: {e}")
    
    async def clear_data(
        self,
        spreadsheet_id: str,
        range_name: str
    ) -> Dict[str, Any]:
        """
        Clear data from a range in the spreadsheet
        
        Args:
            spreadsheet_id: The ID of the spreadsheet
            range_name: The range to clear (e.g., "'Sheet1'!A:Z")
            
        Returns:
            Clear result from Google Sheets API
        """
        try:
            result = await asyncio.to_thread(
                lambda: self.service.spreadsheets().values().clear(
                    spreadsheetId=spreadsheet_id,
                    range=range_name,
                    body={}
                ).execute()
            )
            
            return result
            
        except HttpError as error:
            status_code = error.resp.status if hasattr(error, 'resp') and hasattr(error.resp, 'status') else None
            
            # Check for rate limit (429)
            if status_code == 429:
                retry_after = None
                if hasattr(error, 'resp') and hasattr(error.resp, 'headers') and 'Retry-After' in error.resp.headers:
                    try:
                        retry_after = int(error.resp.headers['Retry-After'])
                    except (ValueError, TypeError):
                        retry_after = None
                
                print(f"❌ Google Sheets API rate limit exceeded (429)")
                if retry_after:
                    print(f"   Retry after: {retry_after} seconds")
                
                raise GoogleSheetsRateLimitError(
                    f"Google Sheets API rate limit exceeded. Please wait before retrying.",
                    retry_after=retry_after
                )
            
            # Other HTTP errors
            print(f"❌ Error clearing data: {error}")
            raise GoogleSheetsAPIError(
                f"Google Sheets API error: {str(error)}",
                status_code=status_code,
                error_details=str(error)
            )
        except Exception as e:
            print(f"❌ Unexpected error clearing data: {e}")
            raise Exception(f"Unexpected error clearing data: {e}")
    
    async def update_spreadsheet_metadata(
        self,
        spreadsheet_id: str,
        title: str = None
    ) -> Dict[str, Any]:
        """
        Update spreadsheet metadata (e.g., title/name)
        
        Args:
            spreadsheet_id: The ID of the spreadsheet
            title: New title/name for the spreadsheet (optional)
        
        Returns:
            Dictionary with operation result including updated metadata
        
        Note:
            Works ONLY for spreadsheets created through Archian AI.
            Requires full write permissions to the spreadsheet.
        """
        try:
            if not title:
                return {
                    'success': False,
                    'error': 'No title provided to update'
                }
            
            print(f"📝 Updating spreadsheet metadata for: {spreadsheet_id}")
            print(f"   New title: {title}")
            
            # Prepare batch update request
            requests = []
            
            if title:
                requests.append({
                    "updateSpreadsheetProperties": {
                        "properties": {
                            "title": title
                        },
                        "fields": "title"
                    }
                })
            
            if not requests:
                return {
                    'success': False,
                    'error': 'No metadata fields provided to update'
                }
            
            # Execute batch update
            result = await asyncio.to_thread(
                lambda: self.service.spreadsheets().batchUpdate(
                    spreadsheetId=spreadsheet_id,
                    body={
                        "requests": requests
                    }
                ).execute()
            )
            
            # Get updated spreadsheet info
            spreadsheet = await asyncio.to_thread(
                lambda: self.service.spreadsheets().get(
                    spreadsheetId=spreadsheet_id,
                    fields='spreadsheetId,spreadsheetUrl,properties.title'
                ).execute()
            )
            
            print(f"   ✅ Successfully updated spreadsheet metadata")
            
            return {
                'success': True,
                'spreadsheet_id': spreadsheet_id,
                'spreadsheet_url': spreadsheet.get('spreadsheetUrl'),
                'spreadsheet_name': spreadsheet.get('properties', {}).get('title', title),
                'updated_fields': ['title'] if title else [],
                'replies': result.get('replies', [])
            }
            
        except HttpError as e:
            error_message = str(e)
            if '404' in error_message:
                return {
                    'success': False,
                    'error': 'Spreadsheet not found. Please check the spreadsheet ID.'
                }
            elif '403' in error_message:
                return {
                    'success': False,
                    'error': 'I don\'t have write access to this spreadsheet. This operation only works for spreadsheets created through Archian AI.',
                    'permission_denied': True
                }
            else:
                return {
                    'success': False,
                    'error': f'Google Sheets API error: {error_message}'
                }
        except Exception as e:
            return {
                'success': False,
                'error': f'Unexpected error updating spreadsheet metadata: {str(e)}'
            }

