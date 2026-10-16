"""
Data Type Conversion Utility

This module provides reusable functions for converting data values to appropriate types
for different destinations: Database (PostgreSQL, MySQL, Oracle, BigQuery), Salesforce, and Google Sheets.

Key Features:
- Handles empty strings → NULL for timestamp/date columns
- Converts string values to appropriate types (numbers, booleans, dates)
- Destination-specific type handling
- Graceful error handling (returns None or original value on failure)
"""

from datetime import datetime, date
from typing import Any, Optional, Dict
import re


def parse_datetime(value: Any, format_hint: Optional[str] = None) -> Optional[datetime]:
    """
    Parse a datetime value from various string formats.
    
    Args:
        value: The value to parse (string, datetime, or None)
        format_hint: Optional hint for the expected format
        
    Returns:
        datetime object or None if parsing fails
    """
    if value is None:
        return None
    
    # If already a datetime object, return as-is
    if isinstance(value, datetime):
        return value
    
    # If it's a date object, convert to datetime
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    
    # Convert to string if not already
    if not isinstance(value, str):
        value = str(value)
    
    # Strip whitespace
    value = value.strip()
    
    if not value or value == '':
        return None
    
    # Common datetime formats to try
    formats = [
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M:%S.%f',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M:%S.%f',
        '%Y-%m-%dT%H:%M:%SZ',
        '%Y-%m-%dT%H:%M:%S.%fZ',
        '%Y-%m-%d',
        '%m/%d/%Y %H:%M:%S',
        '%m/%d/%Y',
        '%d/%m/%Y %H:%M:%S',
        '%d/%m/%Y',
        '%d-%m-%Y',
        '%Y/%m/%d',
    ]
    
    # Add format_hint to the beginning if provided
    if format_hint:
        formats.insert(0, format_hint)
    
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except (ValueError, TypeError):
            continue
    
    # Try ISO format parsing (more lenient)
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        pass
    
    return None


def parse_date(value: Any) -> Optional[date]:
    """
    Parse a date value from various string formats.
    
    Args:
        value: The value to parse (string, date, datetime, or None)
        
    Returns:
        date object or None if parsing fails
    """
    if value is None:
        return None
    
    # If already a date object, return as-is
    if isinstance(value, date):
        return value
    
    # If it's a datetime object, extract the date part
    if isinstance(value, datetime):
        return value.date()
    
    # Convert to string if not already
    if not isinstance(value, str):
        value = str(value)
    
    # Strip whitespace
    value = value.strip()
    
    if not value or value == '':
        return None
    
    # Common date formats to try
    formats = [
        '%Y-%m-%d',
        '%m/%d/%Y',
        '%d/%m/%Y',
        '%d-%m-%Y',
        '%Y/%m/%d',
        '%m-%d-%Y',
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date()
        except (ValueError, TypeError):
            continue
    
    # Try parsing as datetime and extract date
    dt = parse_datetime(value)
    if dt:
        return dt.date()
    
    return None


def parse_boolean(value: Any) -> Optional[bool]:
    """
    Parse a boolean value from various formats.
    
    Args:
        value: The value to parse (string, bool, int, or None)
        
    Returns:
        bool or None if value is empty/invalid
    """
    if value is None:
        return None
    
    # If already a boolean, return as-is
    if isinstance(value, bool):
        return value
    
    # If it's a number, convert 0/1 to False/True
    if isinstance(value, (int, float)):
        return bool(value)
    
    # Convert to string if not already
    if not isinstance(value, str):
        value = str(value)
    
    # Strip whitespace and convert to lowercase
    value = value.strip().lower()
    
    if not value or value == '':
        return None
    
    # True values
    if value in ('true', '1', 'yes', 'y', 'on', 't'):
        return True
    
    # False values
    if value in ('false', '0', 'no', 'n', 'off', 'f'):
        return False
    
    return None


def parse_number(value: Any, as_int: bool = False) -> Optional[float]:
    """
    Parse a number from a string value.
    
    Args:
        value: The value to parse (string, int, float, or None)
        as_int: If True, return int; otherwise return float
        
    Returns:
        int or float, or None if parsing fails
    """
    if value is None:
        return None
    
    # If already a number, return as-is (with type conversion if needed)
    if isinstance(value, (int, float)):
        if as_int:
            return int(value)
        return float(value)
    
    # Convert to string if not already
    if not isinstance(value, str):
        value = str(value)
    
    # Strip whitespace
    value = value.strip()
    
    if not value or value == '':
        return None
    
    # Remove common formatting (commas, currency symbols, etc.)
    value = value.replace(',', '').replace('$', '').replace('€', '').replace('£', '').strip()
    
    try:
        num = float(value)
        if as_int:
            return int(num)
        return num
    except (ValueError, TypeError):
        return None


def convert_value_for_database(
    value: Any,
    column_type: str,
    connector_type: str,
    is_nullable: bool = True,
    column_name: Optional[str] = None
) -> Any:
    """
    Convert a value to the appropriate type for database insertion.
    
    Args:
        value: The value to convert
        column_type: The database column type (e.g., 'timestamp', 'integer', 'boolean')
        connector_type: The database connector type ('postgresql', 'mysql', 'mssql', 'oracle', 'bigquery')
        is_nullable: Whether the column allows NULL values
        column_name: Optional column name for better error messages
        
    Returns:
        Converted value appropriate for the database column type, or None for NULL
    """
    # Handle None and empty strings
    if value is None or (isinstance(value, str) and value.strip() == ''):
        # For timestamp/date/time types, always return None (NULL)
        column_type_lower = column_type.lower()
        if any(t in column_type_lower for t in ['timestamp', 'date', 'time']):
            return None
        # For other nullable columns, return None
        if is_nullable:
            return None
        # For non-nullable columns, return appropriate default based on type
        if 'int' in column_type_lower:
            return 0
        if 'bool' in column_type_lower:
            return False
        if any(t in column_type_lower for t in ['numeric', 'decimal', 'float', 'double']):
            return 0.0
        if 'char' in column_type_lower or 'text' in column_type_lower or 'varchar' in column_type_lower:
            return ''
        return None
    
    column_type_lower = column_type.lower()
    
    # Timestamp/Date/Time handling
    if any(t in column_type_lower for t in ['timestamp', 'timestamptz', 'datetime']):
        if isinstance(value, (datetime, date)):
            return value
        parsed_dt = parse_datetime(value)
        if parsed_dt:
            return parsed_dt
        # If parsing fails and column is nullable, return None
        return None if is_nullable else datetime.now()
    
    if 'date' in column_type_lower and 'time' not in column_type_lower:
        if isinstance(value, date):
            return value
        if isinstance(value, datetime):
            return value.date()
        parsed_date = parse_date(value)
        if parsed_date:
            return parsed_date
        # If parsing fails and column is nullable, return None
        return None if is_nullable else date.today()
    
    if 'time' in column_type_lower and 'date' not in column_type_lower and 'timestamp' not in column_type_lower:
        # Time-only fields - for now, return as string or None
        if isinstance(value, str) and value.strip():
            return value
        return None if is_nullable else '00:00:00'
    
    # Integer handling
    if any(t in column_type_lower for t in ['int', 'bigint', 'smallint', 'tinyint', 'serial', 'bigserial']):
        if isinstance(value, (int, float)):
            return int(value)
        parsed_num = parse_number(value, as_int=True)
        if parsed_num is not None:
            return parsed_num
        # If parsing fails and column is nullable, return None
        return None if is_nullable else 0
    
    # Numeric/Decimal/Float handling
    if any(t in column_type_lower for t in ['numeric', 'decimal', 'float', 'double', 'real', 'money']):
        if isinstance(value, (int, float)):
            return float(value)
        parsed_num = parse_number(value, as_int=False)
        if parsed_num is not None:
            return parsed_num
        # If parsing fails and column is nullable, return None
        return None if is_nullable else 0.0
    
    # Boolean handling
    if 'bool' in column_type_lower:
        if isinstance(value, bool):
            return value
        parsed_bool = parse_boolean(value)
        if parsed_bool is not None:
            return parsed_bool
        # If parsing fails and column is nullable, return None
        return None if is_nullable else False
    
    # For other types (varchar, text, char, json, jsonb, etc.)
    # Strip whitespace from string values to prevent duplicate key issues
    # This ensures consistent data storage and proper matching in WHERE clauses
    if isinstance(value, str):
        return value.strip()
    
    return value


def convert_value_for_salesforce(
    value: Any,
    field_type: Optional[str] = None,
    field_name: Optional[str] = None
) -> Any:
    """
    Convert a value to the appropriate type for Salesforce API.
    
    Args:
        value: The value to convert
        field_type: Optional Salesforce field type (e.g., 'Date', 'DateTime', 'Boolean', 'Number')
        field_name: Optional field name for better type detection
        
    Returns:
        Converted value appropriate for Salesforce API
    """
    # Handle None and empty strings
    if value is None or (isinstance(value, str) and value.strip() == ''):
        return None
    
    # If field_type is provided, use it for conversion
    if field_type:
        field_type_lower = field_type.lower()
        
        # Date handling (Salesforce expects YYYY-MM-DD format)
        if field_type_lower == 'date':
            parsed_date = parse_date(value)
            if parsed_date:
                return parsed_date.strftime('%Y-%m-%d')
            return None
        
        # DateTime handling (Salesforce expects ISO 8601 format: YYYY-MM-DDTHH:MM:SSZ)
        if field_type_lower in ['datetime', 'datetime2']:
            parsed_dt = parse_datetime(value)
            if parsed_dt:
                # Salesforce expects ISO 8601 format with timezone
                # If no timezone info, assume UTC
                if parsed_dt.tzinfo is None:
                    return parsed_dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')
                return parsed_dt.isoformat()
            return None
        
        # Boolean handling
        if field_type_lower == 'boolean':
            parsed_bool = parse_boolean(value)
            if parsed_bool is not None:
                return parsed_bool
            return False  # Default to False for Salesforce
        
        # Number handling
        if field_type_lower in ['number', 'double', 'int', 'currency', 'percent']:
            if isinstance(value, (int, float)):
                return value
            parsed_num = parse_number(value, as_int=False)
            if parsed_num is not None:
                return parsed_num
            return None
    
    # If no field_type, try to infer from field_name
    if field_name:
        field_name_lower = field_name.lower()
        
        # Date fields
        if any(pattern in field_name_lower for pattern in ['_date', 'date', '_at', 'created', 'updated', 'modified']):
            # Check if it's a datetime field (has _at suffix)
            if '_at' in field_name_lower or field_name_lower.endswith('at'):
                parsed_dt = parse_datetime(value)
                if parsed_dt:
                    if parsed_dt.tzinfo is None:
                        return parsed_dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')
                    return parsed_dt.isoformat()
            else:
                # Date field
                parsed_date = parse_date(value)
                if parsed_date:
                    return parsed_date.strftime('%Y-%m-%d')
            return None
        
        # Boolean fields
        if any(pattern in field_name_lower for pattern in ['is_', 'has_', 'can_', '_flag', 'active', 'enabled', 'deleted']):
            parsed_bool = parse_boolean(value)
            if parsed_bool is not None:
                return parsed_bool
            return False
    
    # For string fields or unknown types, return as-is (Salesforce API handles strings)
    return value


def convert_value_for_sheets(
    value: Any,
    column_name: Optional[str] = None
) -> Any:
    """
    Convert a value to the appropriate type for Google Sheets.
    Google Sheets is more lenient, but we still want to convert for better data quality.
    
    Args:
        value: The value to convert
        column_name: Optional column name for better type detection
        
    Returns:
        Converted value appropriate for Google Sheets
    """
    # Handle None and empty strings
    if value is None or (isinstance(value, str) and value.strip() == ''):
        return ''  # Google Sheets can handle empty strings
    
    # If already a number or boolean, return as-is
    if isinstance(value, (int, float, bool)):
        return value
    
    # If it's a date/datetime object, format it appropriately
    if isinstance(value, (datetime, date)):
        if isinstance(value, datetime):
            return value.strftime('%Y-%m-%d %H:%M:%S')
        return value.strftime('%Y-%m-%d')
    
    # Convert to string if not already
    if not isinstance(value, str):
        value = str(value)
    
    # Detect date columns by name
    is_date_column = column_name and any(
        pattern in column_name.lower()
        for pattern in ['_date', 'date', '_at', 'created', 'updated', 'modified', 'time']
    )
    
    # Try to parse as date if column name suggests it
    if is_date_column:
        # Check if it's a datetime field (has _at suffix)
        if column_name and ('_at' in column_name.lower() or column_name.lower().endswith('at')):
            parsed_dt = parse_datetime(value)
            if parsed_dt:
                return parsed_dt.strftime('%Y-%m-%d %H:%M:%S')
        else:
            # Date field
            parsed_date = parse_date(value)
            if parsed_date:
                return parsed_date.strftime('%Y-%m-%d')
    
    # Try to parse as boolean
    parsed_bool = parse_boolean(value)
    if parsed_bool is not None:
        return parsed_bool
    
    # Try to parse as number (only if it looks like a number)
    # Don't convert strings that might be IDs or codes
    if re.match(r'^-?\d+\.?\d*$', value.strip()):
        parsed_num = parse_number(value, as_int=False)
        if parsed_num is not None:
            # Return as int if it's a whole number, otherwise float
            if parsed_num == int(parsed_num):
                return int(parsed_num)
            return parsed_num
    
    # For everything else, return as string
    return value


def get_column_type_category(column_type: str) -> str:
    """
    Categorize a database column type into a general category.
    
    Args:
        column_type: The database column type string
        
    Returns:
        Category: 'timestamp', 'date', 'time', 'integer', 'numeric', 'boolean', 'text', 'other'
    """
    column_type_lower = column_type.lower()
    
    if any(t in column_type_lower for t in ['timestamp', 'timestamptz', 'datetime']):
        return 'timestamp'
    if 'date' in column_type_lower and 'time' not in column_type_lower:
        return 'date'
    if 'time' in column_type_lower and 'date' not in column_type_lower and 'timestamp' not in column_type_lower:
        return 'time'
    if any(t in column_type_lower for t in ['int', 'bigint', 'smallint', 'tinyint', 'serial', 'bigserial']):
        return 'integer'
    if any(t in column_type_lower for t in ['numeric', 'decimal', 'float', 'double', 'real', 'money']):
        return 'numeric'
    if 'bool' in column_type_lower:
        return 'boolean'
    if any(t in column_type_lower for t in ['char', 'text', 'varchar', 'string']):
        return 'text'
    
    return 'other'

