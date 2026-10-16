"""
Data Transformation Service

This service handles schema-aware data transformations for Salesforce migrations:
- Data type conversions based on destination field metadata
- Schema-aware name/address splitting
- Lookup transformation preparation
- Data cleaning and normalization
"""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from db.connection import database


class DataTransformationService:
    """Service for transforming data based on destination schema and field mappings"""

    @staticmethod
    def transform_value_for_destination(value: Any, data_type: str, is_lookup: bool = False) -> Any:
        """
        Transform a value based on the destination field's data type.
        
        Args:
            value: The value to transform
            data_type: Salesforce field data type (e.g., 'date', 'datetime', 'number', 'boolean', etc.)
            is_lookup: Whether this is a lookup/reference field
        
        Returns:
            Transformed value ready for Salesforce API
        """
        if value is None:
            return None
        
        # Handle lookup/reference fields - ensure they're strings (IDs)
        if is_lookup:
            return str(value).strip() if value else None
        
        # Convert data_type to lowercase for consistent comparison
        data_type_lower = data_type.lower() if data_type else ""
        
        try:
            # Boolean fields
            if data_type_lower == "boolean":
                if isinstance(value, bool):
                    return value
                if isinstance(value, str):
                    value_lower = value.strip().lower()
                    if value_lower in ("true", "1", "yes", "y", "on"):
                        return True
                    if value_lower in ("false", "0", "no", "n", "off", ""):
                        return False
                if isinstance(value, (int, float)):
                    return bool(value)
                return None
            
            # Number fields (int, double, currency, percent)
            elif data_type_lower in ("int", "integer", "double", "currency", "percent", "long", "decimal"):
                if isinstance(value, (int, float)):
                    return value
                if isinstance(value, str):
                    # Remove currency symbols, commas, percentage signs
                    cleaned = value.strip().replace(",", "").replace("$", "").replace("%", "").strip()
                    if not cleaned or cleaned == "":
                        return None
                    try:
                        # For percent, divide by 100 if it looks like a percentage (e.g., "50" -> 0.5)
                        if data_type_lower == "percent" and not cleaned.endswith("%"):
                            # If it's a percentage stored as "50" meaning 50%, convert to 0.5
                            num_value = float(cleaned)
                            # Check if it's likely a percentage value (0-100 range)
                            if 0 <= num_value <= 100:
                                return num_value / 100
                        return float(cleaned) if data_type_lower in ("double", "currency", "decimal", "percent") else int(float(cleaned))
                    except (ValueError, TypeError):
                        print(f"⚠️ Could not convert '{value}' to {data_type_lower}, returning None")
                        return None
                return None
            
            # Date fields (date only, no time)
            elif data_type_lower == "date":
                if isinstance(value, datetime):
                    return value.strftime("%Y-%m-%d")
                if isinstance(value, str):
                    value = value.strip()
                    if not value:
                        return None
                    # Try common date formats (including variations like "12 Aug 2026")
                    date_formats = [
                        "%Y-%m-%d",
                        "%m/%d/%Y",
                        "%d/%m/%Y",
                        "%Y/%m/%d",
                        "%m-%d-%Y",
                        "%d-%m-%Y",
                        "%B %d, %Y",    # "August 12, 2026"
                        "%b %d, %Y",    # "Aug 12, 2026"
                        "%d %B %Y",     # "12 August 2026"
                        "%d %b %Y",     # "12 Aug 2026" - handles the format from the error
                        "%d-%b-%Y",     # "12-Aug-2026"
                        "%d.%b.%Y",     # "12.Aug.2026"
                    ]
                    for fmt in date_formats:
                        try:
                            dt = datetime.strptime(value, fmt)
                            return dt.strftime("%Y-%m-%d")
                        except ValueError:
                            continue
                    print(f"⚠️ Could not parse date '{value}', returning as-is")
                    return value  # Return as-is and let Salesforce handle it
                return None
            
            # DateTime fields (date and time)
            elif data_type_lower == "datetime":
                if isinstance(value, datetime):
                    # Salesforce expects ISO 8601 format
                    return value.strftime("%Y-%m-%dT%H:%M:%S.000Z")
                if isinstance(value, str):
                    value = value.strip()
                    if not value:
                        return None
                    # Try common datetime formats
                    datetime_formats = [
                        "%Y-%m-%dT%H:%M:%S.%fZ",
                        "%Y-%m-%dT%H:%M:%SZ",
                        "%Y-%m-%d %H:%M:%S",
                        "%Y-%m-%dT%H:%M:%S",
                        "%m/%d/%Y %H:%M:%S",
                        "%d/%m/%Y %H:%M:%S",
                        "%Y-%m-%d %H:%M",
                    ]
                    for fmt in datetime_formats:
                        try:
                            dt = datetime.strptime(value, fmt)
                            return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
                        except ValueError:
                            continue
                    # If no format matches, try date parsing and add default time
                    try:
                        dt = datetime.strptime(value.split()[0], "%Y-%m-%d")
                        return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
                    except:
                        pass
                    print(f"⚠️ Could not parse datetime '{value}', returning as-is")
                    return value  # Return as-is and let Salesforce handle it
                return None
            
            # Time fields
            elif data_type_lower == "time":
                if isinstance(value, str):
                    value = value.strip()
                    if not value:
                        return None
                    # Salesforce time format: HH:MM:SS.000Z
                    # Try parsing various time formats
                    time_formats = ["%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M:%S %p"]
                    for fmt in time_formats:
                        try:
                            dt = datetime.strptime(value, fmt)
                            return dt.strftime("%H:%M:%S.000Z")
                        except ValueError:
                            continue
                    return value
                return value
            
            # Email fields - validate and clean
            elif data_type_lower == "email":
                if isinstance(value, str):
                    value = value.strip()
                    from utils.field_validation import is_valid_email

                    if is_valid_email(value):
                        return value
                    print(f"⚠️ Email format may be invalid: '{value}'")
                    return value
                return str(value).strip() if value else None
            
            # Phone fields - clean phone numbers
            elif data_type_lower == "phone":
                if isinstance(value, str):
                    # Remove common phone formatting characters but keep + for international
                    cleaned = re.sub(r'[^\d+]', '', value)
                    return cleaned if cleaned else value.strip()
                return str(value).strip() if value else None
            
            # URL fields
            elif data_type_lower == "url":
                if isinstance(value, str):
                    value = value.strip()
                    # Add http:// if no protocol
                    if value and not value.startswith(("http://", "https://", "ftp://")):
                        value = "http://" + value
                    return value
                return str(value).strip() if value else None
            
            # Text/String fields - trim whitespace
            elif data_type_lower in ("string", "textarea", "text", "picklist", "multipicklist", "combobox"):
                return str(value).strip() if value else None
            
            # Default: return as string, trimmed
            else:
                return str(value).strip() if value else None
                
        except Exception as e:
            print(f"⚠️ Error transforming value '{value}' for type '{data_type}': {e}")
            # Return as string as fallback
            return str(value).strip() if value else None

    @staticmethod
    def migrate_coerce_value_for_salesforce(value: Any, field_data_type: Optional[str]) -> Any:
        """
        Coerce values immediately before Salesforce REST/Bulk payloads so numeric fields are JSON
        numbers, not strings (e.g. scientific notation \"3.5E8\" from spreadsheets).
        Uses the same rules as transform_value_for_destination when field metadata exists.
        """
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value
        dt = (field_data_type or "").strip().lower()
        is_lookup = dt == "reference"
        if dt:
            return DataTransformationService.transform_value_for_destination(value, field_data_type, is_lookup)
        from utils.data_type_converter import parse_number
        parsed = parse_number(value, as_int=False)
        if parsed is not None:
            return parsed
        return value

    @staticmethod
    async def fetch_destination_field_metadata(
        user_id: str,
        org_id: Optional[str],
        mapping: Dict[str, Any]
    ) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """
        Fetch field metadata (data_type, is_lookup, etc.) for all fields in the mapping from the database.
        
        Returns a dictionary mapping (object, field) -> field metadata
        Format: {(object_name, field_name): {"data_type": "string", "is_lookup": False, ...}}
        """
        if not user_id or not mapping:
            return {}
        
        try:
            # Extract unique (object, field) pairs from mapping
            # Handle both single dict mappings and array mappings (for split fields)
            object_field_pairs = set()
            for mapping_info in mapping.values():
                if isinstance(mapping_info, list):
                    # Array case (split fields) - extract from each item in array
                    for item in mapping_info:
                        if isinstance(item, dict):
                            obj = item.get("object")
                            field = item.get("field")
                            if obj and field:
                                object_field_pairs.add((obj, field))
                elif isinstance(mapping_info, dict):
                    # Single dict case
                    obj = mapping_info.get("object")
                    field = mapping_info.get("field")
                    if obj and field:
                        object_field_pairs.add((obj, field))
            
            if not object_field_pairs:
                return {}
            
            # Build query conditions
            conditions = []
            params = {"user_id": user_id}
            if org_id:
                params["org_id"] = org_id
                org_filter = "AND org_id = :org_id"
            else:
                org_filter = ""
            
            for idx, (obj, field) in enumerate(object_field_pairs):
                obj_key = f"obj_{idx}"
                field_key = f"field_{idx}"
                conditions.append(f"(object_api_name = :{obj_key} AND field_api_name = :{field_key})")
                params[obj_key] = obj
                params[field_key] = field
            
            query = f"""
                SELECT object_api_name, field_api_name, data_type, is_lookup, is_required, lookup_object_api_name
                FROM salesforce_object_fields
                WHERE user_id = :user_id {org_filter}
                AND ({' OR '.join(conditions)})
            """
            
            fields_metadata = await database.fetch_all(query, values=params)
            
            # Build result dictionary
            result = {}
            for field_meta in fields_metadata:
                # Convert Record to dict if needed
                field_dict = dict(field_meta) if hasattr(field_meta, '_mapping') or hasattr(field_meta, 'keys') else field_meta
                key = (field_dict["object_api_name"], field_dict["field_api_name"])
                result[key] = {
                    "data_type": field_dict.get("data_type", "").lower(),
                    "is_lookup": field_dict.get("is_lookup", False),
                    "is_required": field_dict.get("is_required", False),
                    "lookup_object_api_name": field_dict.get("lookup_object_api_name"),
                }
            
            print(f"📋 Fetched metadata for {len(result)} fields")
            return result
            
        except Exception as e:
            print(f"⚠️ Error fetching field metadata: {e}")
            return {}
    
    @staticmethod
    def _build_metadata_from_table_schema(
        table_schema: Dict[str, Any],
        mapping: Dict[str, Any]
    ) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """
        Build field metadata dictionary from database table schema.
        
        Args:
            table_schema: Table schema dict with structure:
                {
                    "table_name": "users",
                    "columns": [
                        {"column_name": "first_name", "data_type": "VARCHAR", "is_nullable": True, ...},
                        ...
                    ],
                    "connector_type": "postgresql"  # Optional
                }
            mapping: Field mapping configuration
        
        Returns:
            Dictionary mapping (table, column) -> column metadata
            Format: {(table_name, column_name): {"data_type": "varchar", "is_nullable": True, ...}}
        """
        result = {}
        
        if not table_schema or "columns" not in table_schema:
            return result
        
        columns = table_schema.get("columns", [])
        table_name = table_schema.get("table_name", "")
        
        # Build metadata dictionary
        for col in columns:
            if isinstance(col, dict):
                col_name = col.get("column_name", "")
                if col_name:
                    key = (table_name, col_name)
                    result[key] = {
                        "data_type": col.get("data_type", "").lower(),
                        "is_nullable": col.get("is_nullable", True),
                        "is_primary_key": col.get("is_primary_key", False),
                        "column_default": col.get("column_default"),
                    }
        
        # Also check mapping for table names (in case mapping uses different tables)
        for mapping_info in mapping.values():
            if isinstance(mapping_info, list):
                # Array case (split fields)
                for item in mapping_info:
                    if isinstance(item, dict):
                        table = item.get("table")
                        column = item.get("column")
                        if table and column and (table, column) not in result:
                            # Try to find column in schema (might be in different table)
                            # For now, add with default metadata
                            result[(table, column)] = {
                                "data_type": "varchar",  # Default
                                "is_nullable": True,
                                "is_primary_key": False,
                            }
            elif isinstance(mapping_info, dict):
                # Single dict case
                table = mapping_info.get("table")
                column = mapping_info.get("column")
                if table and column and (table, column) not in result:
                    result[(table, column)] = {
                        "data_type": "varchar",  # Default
                        "is_nullable": True,
                        "is_primary_key": False,
                    }
        
        return result

    @staticmethod
    def split_full_name(full_name: str) -> Dict[str, Optional[str]]:
        """
        Split a full name into FirstName, MiddleName, and LastName.
        
        Args:
            full_name: Full name string (e.g., "John Michael Doe", "Jane Smith")
        
        Returns:
            Dictionary with FirstName, MiddleName, LastName keys
        """
        if not full_name or not isinstance(full_name, str):
            return {"FirstName": None, "MiddleName": None, "LastName": None}
        
        full_name = full_name.strip()
        if not full_name:
            return {"FirstName": None, "MiddleName": None, "LastName": None}
        
        parts = full_name.split()
        
        if len(parts) == 1:
            from utils.split_field_utils import split_value_into_parts

            split_parts = split_value_into_parts(
                parts[0],
                ["FirstName", "MiddleName", "LastName"],
                required_indices=[2],
            )
            return {
                "FirstName": split_parts[0] or None,
                "MiddleName": split_parts[1] or None,
                "LastName": split_parts[2] or None,
            }
        elif len(parts) == 2:
            return {"FirstName": parts[0], "MiddleName": None, "LastName": parts[1]}
        else:
            # 3 or more parts: first is FirstName, last is LastName, rest is MiddleName
            return {
                "FirstName": parts[0],
                "MiddleName": " ".join(parts[1:-1]) if len(parts) > 2 else None,
                "LastName": parts[-1]
            }

    @staticmethod
    def parse_address_string(address: str) -> Dict[str, Optional[str]]:
        """
        Parse an address string into components.
        
        This is a basic parser. For production, consider using a more sophisticated
        address parsing library like pyap or usaddress.
        
        Args:
            address: Full address string
        
        Returns:
            Dictionary with Street, City, State, PostalCode, Country keys
        """
        if not address or not isinstance(address, str):
            return {
                "Street": None,
                "City": None,
                "State": None,
                "PostalCode": None,
                "Country": None
            }
        
        address = address.strip()
        if not address:
            return {
                "Street": None,
                "City": None,
                "State": None,
                "PostalCode": None,
                "Country": None
            }
        
        # Basic parsing - split by commas or newlines
        parts = [p.strip() for p in re.split(r'[,\n]', address) if p.strip()]
        
        result = {
            "Street": None,
            "City": None,
            "State": None,
            "PostalCode": None,
            "Country": None
        }
        
        if len(parts) >= 1:
            result["Street"] = parts[0]
        if len(parts) >= 2:
            result["City"] = parts[1]
        if len(parts) >= 3:
            # Last part might be "State PostalCode" or "State PostalCode Country"
            last_part = parts[-1]
            # Try to extract postal code (usually 5 digits or 5+4 format)
            postal_match = re.search(r'\b\d{5}(-\d{4})?\b', last_part)
            if postal_match:
                result["PostalCode"] = postal_match.group(0)
                # Remove postal code from state
                state_part = last_part[:postal_match.start()].strip()
                if state_part:
                    result["State"] = state_part
            else:
                result["State"] = last_part
        if len(parts) >= 4:
            result["Country"] = parts[-1]
        
        return result

    @staticmethod
    async def determine_name_splitting_needed(
        mapping: Dict[str, Any],
        field_metadata: Dict[Tuple[str, str], Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Determine if name splitting is needed based on destination schema.
        
        Returns a dictionary mapping source_field -> splitting configuration
        """
        name_splitting_config = {}
        
        for source_field, mapping_info in mapping.items():
            obj = mapping_info.get("object")
            field = mapping_info.get("field")
            
            if not obj or not field:
                continue
            
            # Check if this field maps to a Name field that should be split
            # (e.g., maps to FirstName, LastName, or a full Name field exists)
            key = (obj, field)
            field_meta = field_metadata.get(key, {})
            data_type = field_meta.get("data_type", "")
            
            # Check if destination object has FirstName/LastName fields
            # We need to check the metadata to see if FirstName/LastName exist
            has_firstname = False
            has_lastname = False
            for (meta_obj, meta_field), meta_info in field_metadata.items():
                if meta_obj == obj:
                    if meta_field == "FirstName":
                        has_firstname = True
                    elif meta_field == "LastName":
                        has_lastname = True
            
            # If destination has FirstName/LastName and source maps to a name-like field,
            # we might need splitting (but we'll check the actual mapping later)
            # For now, if field is "Name" or contains "name", consider it for splitting
            if (field.lower() in ("name", "fullname", "full_name") or 
                "name" in field.lower()) and has_firstname and has_lastname:
                name_splitting_config[source_field] = {
                    "object": obj,
                    "needs_splitting": True,
                    "target_fields": ["FirstName", "LastName", "MiddleName"]
                }
        
        return name_splitting_config

    @staticmethod
    def _merge_mapping_lookup_of(
        target: Dict[str, Any],
        source: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Preserve user-edited lookup_of when transforming mapping entries."""
        if not source or not isinstance(source, dict):
            return target
        merged = dict(target)
        lookup = (source.get("lookup_of") or "").strip()
        if lookup and not (merged.get("lookup_of") or "").strip():
            merged["lookup_of"] = lookup
        return merged

    @staticmethod
    def transform_mapping_for_transformed_data(
        mapping: Dict[str, Any],
        transformation_rules: Dict[str, Any] = None,
        destination: str = "salesforce"
    ) -> Dict[str, Any]:
        """
        Transform mapping to match the transformed data structure.
        
        For split fields: Maps destination field names (from array mapping) to destination columns
        For combined fields: Maps combined destination field name to destination column
        For regular fields: Keeps as-is
        
        Args:
            mapping: Original mapping with source field names as keys
            transformation_rules: Transformation rules to identify split/combine scenarios
            destination: Destination type ("salesforce", "google_sheets", "sql", "database")
        
        Returns:
            Transformed mapping with field names matching the transformed data structure
        """
        if not transformation_rules or not transformation_rules.get("fields"):
            return mapping
        
        transformed_mapping = {}
        combine_rules_by_output_field = {}
        source_fields_by_combine_group = {}
        combine_source_fields_set = set()
        
        # Step 1: Build maps of combine rules
        for rule in transformation_rules.get("fields", []):
            if rule.get("action") == "combine":
                output_field = rule.get("output_field")
                source_fields = rule.get("source_fields", [])
                if output_field and source_fields:
                    combine_rules_by_output_field[output_field] = rule
                    # Track which source fields belong to this combine group
                    for src_field in source_fields:
                        source_fields_by_combine_group[src_field] = output_field
                        combine_source_fields_set.add(src_field)
        
        # Step 2: Programmatically detect combine fields by finding multiple source fields
        # mapping to the same destination column/field (fallback if is_combined_field flag is missing)
        destination_to_source_fields = {}
        for source_field, mapping_info in mapping.items():
            # Skip split fields (arrays) and already identified combine fields
            if isinstance(mapping_info, list) or source_field in combine_source_fields_set:
                continue
            
            # Get destination column/field name based on destination type
            if destination == "salesforce":
                dest_key = mapping_info.get("field")
            elif destination == "google_sheets":
                dest_key = mapping_info.get("column")
            elif destination in ["sql", "database"]:
                dest_key = mapping_info.get("column")
            else:
                dest_key = mapping_info.get("field") or mapping_info.get("column")
            
            if dest_key:
                if dest_key not in destination_to_source_fields:
                    destination_to_source_fields[dest_key] = []
                destination_to_source_fields[dest_key].append(source_field)
        
        # Step 3: Process each mapping entry
        processed_combine_groups = set()  # Track which combine groups we've already processed
        
        for source_field, mapping_info in mapping.items():
            # Check if this is a split field (array mapping)
            if isinstance(mapping_info, list):
                # Split field: Create mapping entries for each destination field
                # The transformed data will have destination field names as keys
                for mapping_item in mapping_info:
                    # Get destination field/column name based on destination type
                    if destination == "salesforce":
                        destination_field_name = mapping_item.get("field")
                    elif destination == "google_sheets":
                        destination_field_name = mapping_item.get("column")
                    elif destination in ["sql", "database"]:
                        destination_field_name = mapping_item.get("column")
                    else:
                        destination_field_name = mapping_item.get("field") or mapping_item.get("column")
                    
                    if destination_field_name:
                        # Map destination field name to destination column/field
                        # Remove is_split_field flag as it's no longer needed
                        clean_mapping_item = {
                            k: v for k, v in mapping_item.items() if k != "is_split_field"
                        }
                        clean_mapping_item = DataTransformationService._merge_mapping_lookup_of(
                            clean_mapping_item, mapping_item
                        )
                        transformed_mapping[destination_field_name] = clean_mapping_item
            
            # Check if this is a combined field (by flag or programmatic detection)
            elif source_field in combine_source_fields_set:
                # Combined field: The transformed data will have the output_field as the key
                # Get the output_field from the combine rule
                output_field = source_fields_by_combine_group.get(source_field)
                
                if output_field and output_field not in processed_combine_groups:
                    # Only add once per combine group (for the first field in the combine group)
                    processed_combine_groups.add(output_field)
                    
                    # Get the mapping info from the first source field in the combine group
                    # All source fields in a combine group should map to the same destination
                    first_source_field = source_field
                    mapping_info_to_use = mapping_info
                    
                    # Create a clean mapping entry without combine flags
                    clean_mapping_info = {
                        k: v for k, v in mapping_info_to_use.items() 
                        if k not in ["is_combined_field", "combine_group", "combine_order"]
                    }
                    clean_mapping_info = DataTransformationService._merge_mapping_lookup_of(
                        clean_mapping_info, mapping_info_to_use
                    )
                    
                    # Use output_field as the key (this matches the transformed data structure)
                    transformed_mapping[output_field] = clean_mapping_info
                    print(f"✅ Combined field mapping: {source_field} (and others) → {output_field}")
            
            # Regular field: Keep as-is (no transformation)
            elif isinstance(mapping_info, dict):
                # Check if this field is part of a programmatically detected combine
                # (multiple source fields mapping to same destination)
                if destination == "salesforce":
                    dest_key = mapping_info.get("field")
                elif destination == "google_sheets":
                    dest_key = mapping_info.get("column")
                elif destination in ["sql", "database"]:
                    dest_key = mapping_info.get("column")
                else:
                    dest_key = mapping_info.get("field") or mapping_info.get("column")
                
                # Check if multiple source fields map to this destination and we have a combine rule
                is_part_of_combine = False
                if dest_key and dest_key in destination_to_source_fields:
                    source_fields_for_dest = destination_to_source_fields[dest_key]
                    if len(source_fields_for_dest) > 1:
                        # Multiple source fields map to same destination - check if we have a matching combine rule
                        for output_field, rule in combine_rules_by_output_field.items():
                            rule_source_fields = set(rule.get("source_fields", []))
                            if set(source_fields_for_dest) == rule_source_fields:
                                # This is a combine scenario
                                if output_field not in processed_combine_groups:
                                    # Use output_field as key (this matches the transformed data structure)
                                    processed_combine_groups.add(output_field)
                                    clean_mapping_info = {
                                        k: v for k, v in mapping_info.items() 
                                        if k not in ["is_combined_field", "combine_group", "combine_order"]
                                    }
                                    transformed_mapping[output_field] = clean_mapping_info
                                    print(f"✅ Programmatically detected combine: {source_fields_for_dest} → {output_field}")
                                is_part_of_combine = True
                                break
                
                # Only add as regular field if it's not part of a combine group
                if not is_part_of_combine:
                    transformed_mapping[source_field] = mapping_info
        
        # Drop orphan keys that mirror combine output (e.g. stale "name" when first_name+last_name → Name).
        # Keep the canonical combine output key (output_field) added above — do not remove it here.
        for rule in transformation_rules.get("fields", []):
            if rule.get("action") != "combine":
                continue
            output_field = (rule.get("output_field") or "").strip()
            source_fields = set(rule.get("source_fields") or [])
            if not output_field or len(source_fields) < 2:
                continue
            for key in list(transformed_mapping.keys()):
                if key in source_fields:
                    continue
                if key.lower() != output_field.lower():
                    continue
                if output_field in processed_combine_groups and key == output_field:
                    continue
                transformed_mapping.pop(key, None)
        
        return transformed_mapping
    
    async def transform_data_for_migration(
        data: List[Dict[str, Any]],
        mapping: Dict[str, Any],
        transformation_rules: Dict[str, Any] = None,
        user_id: str = None,
        org_id: Optional[str] = None,
        destination: str = "salesforce",
        table_schema: Optional[Dict[str, Any]] = None
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
        """
        Main transformation function that applies all schema-aware transformations.
        
        Args:
            data: List of records to transform
            mapping: Field mapping configuration (can contain arrays for split fields)
            transformation_rules: Transformation rules for field splitting (e.g., {"fields": [...]})
            user_id: User ID for metadata lookup
            org_id: Salesforce org ID for metadata lookup (only for Salesforce)
            destination: Destination type ("salesforce", "google_sheets", "sql", "database")
            table_schema: Table schema for database destinations (optional)
        
        Returns:
            Tuple of (transformed_data, transformation_metadata, transformed_mapping)
            - transformed_data: Data with transformed field names
            - transformation_metadata: Info about what transformations were applied
            - transformed_mapping: Mapping transformed to match the transformed data structure
        """
        print(f"🔄 Starting data transformation for {len(data)} records (destination: {destination})...")
        
        transformation_rules = transformation_rules or {"fields": []}
        # Build split_fields_rules - only include rules with "source_field" (singular) for splitting
        # Combine rules use "source_fields" (plural) and are handled separately in Step 2b
        split_fields_rules = {
            rule["source_field"]: rule 
            for rule in transformation_rules.get("fields", []) 
            if "source_field" in rule and rule.get("action", "").startswith("split")
        }
        
        # Step 1: Fetch destination field metadata based on destination type
        field_metadata = {}
        if destination == "salesforce":
            field_metadata = await DataTransformationService.fetch_destination_field_metadata(
                user_id, org_id, mapping
            )
            print(f"✅ Fetched Salesforce metadata for {len(field_metadata)} destination fields")
        elif destination == "google_sheets":
            # Google Sheets doesn't need metadata - just column names
            field_metadata = {}
            print(f"✅ Google Sheets transformation (no metadata needed)")
        elif destination in ["sql", "database"]:
            # Build metadata from table schema
            field_metadata = DataTransformationService._build_metadata_from_table_schema(
                table_schema, mapping
            ) if table_schema else {}
            print(f"✅ Built database metadata for {len(field_metadata)} destination columns")
        
        # Step 2: Transform each record
        transformed_data = []
        transformation_metadata = {
            "field_metadata_count": len(field_metadata),
            "transformations_applied": []
        }
        
        for record_idx, record in enumerate(data):
            # Start with a copy of the original record
            transformed_record = dict(record)
            
            # Step 2a: Process split fields first (before regular fields)
            # For split fields, we need to:
            # 1. Get the source field value
            # 2. Split it according to transformation_rules
            # 3. Create new fields with destination field names (from mapping array)
            # 4. Remove the original source field
            
            for source_field, mapping_info in mapping.items():
                # Check if this is a split field (mapping_info is an array)
                if not isinstance(mapping_info, list):
                    continue  # Skip non-array mappings (handled in Step 2b)
                
                # Check if we have a transformation rule for this field
                if source_field not in split_fields_rules:
                    continue  # No transformation rule, skip splitting
                
                if source_field not in record:
                    continue  # Field not in record, skip
                
                rule = split_fields_rules[source_field]
                raw_value = record.get(source_field)
                
                if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
                    # Empty value, skip splitting but still remove the source field
                    transformed_record.pop(source_field, None)
                    continue
                
                # Split the value according to the rule
                split_char = rule.get("split_char", " ")
                action = rule.get("action", "split_2")
                output_fields = rule.get("output_fields", [])

                from utils.split_field_utils import (
                    metadata_keys_for_split_mapping,
                    resolve_split_required_indices,
                    split_value_into_parts,
                )

                meta_keys = metadata_keys_for_split_mapping(
                    mapping_info, output_fields, destination=destination
                )
                required_indices = resolve_split_required_indices(
                    output_fields,
                    mapping_items=mapping_info,
                    field_metadata=field_metadata,
                    metadata_keys=meta_keys,
                    rule=rule,
                )

                split_parts = split_value_into_parts(
                    raw_value if isinstance(raw_value, str) else str(raw_value),
                    output_fields,
                    split_char=split_char,
                    required_indices=required_indices,
                )
                
                # Create new fields using destination field names from mapping array
                # For Salesforce: [{field: "FirstName", object: "Contact"}, {field: "LastName", object: "Contact"}]
                # For Google Sheets: [{column: "First Name", sheet: "Sheet1"}, {column: "Last Name", sheet: "Sheet1"}]
                # For Database: [{column: "first_name", table: "users"}, {column: "last_name", table: "users"}]
                for idx, mapping_item in enumerate(mapping_info):
                    if idx >= len(output_fields):
                        break
                    
                    # Get destination field/column name based on destination type
                    if destination == "salesforce":
                        destination_field_name = mapping_item.get("field")
                        obj = mapping_item.get("object")
                        key = (obj, destination_field_name) if obj else None
                    elif destination == "google_sheets":
                        destination_field_name = mapping_item.get("column")
                        sheet = mapping_item.get("sheet")
                        key = (sheet, destination_field_name) if sheet else None
                    elif destination in ["sql", "database"]:
                        destination_field_name = mapping_item.get("column")
                        table = mapping_item.get("table")
                        key = (table, destination_field_name) if table else None
                    else:
                        # Fallback: try field or column
                        destination_field_name = mapping_item.get("field") or mapping_item.get("column")
                        key = None
                    
                    if not destination_field_name:
                        continue
                    
                    # Use the corresponding split part value
                    split_value = split_parts[idx] if idx < len(split_parts) else ""
                    
                    # Apply data type transformation to the split value based on destination
                    if destination == "salesforce":
                        field_meta = field_metadata.get(key, {}) if key else {}
                        data_type = field_meta.get("data_type", "")
                        is_lookup = field_meta.get("is_lookup", False)
                        transformed_split_value = DataTransformationService.transform_value_for_destination(
                            split_value, data_type, is_lookup
                        )
                    elif destination == "google_sheets":
                        from utils.data_type_converter import convert_value_for_sheets
                        transformed_split_value = convert_value_for_sheets(
                            split_value, column_name=destination_field_name
                        )
                    elif destination in ["sql", "database"]:
                        from utils.data_type_converter import convert_value_for_database
                        field_meta = field_metadata.get(key, {}) if key else {}
                        column_type = field_meta.get("data_type", "")
                        is_nullable = field_meta.get("is_nullable", True)
                        # Get connector type from table_schema if available
                        connector_type = table_schema.get("connector_type", "postgresql") if table_schema else "postgresql"
                        transformed_split_value = convert_value_for_database(
                            split_value, column_type, connector_type, is_nullable, destination_field_name
                        )
                    else:
                        # Fallback: use as-is
                        transformed_split_value = split_value
                    
                    # Add the new field with destination field/column name as key
                    transformed_record[destination_field_name] = transformed_split_value
                    
                    # Log the split transformation
                    destination_label = f"{obj}.{destination_field_name}" if destination == "salesforce" and obj else destination_field_name
                    transformation_entry = {
                        "record_idx": record_idx,
                        "source_field": source_field,
                        "destination_field": destination_label,
                        "transformation": f"split_{action}",
                        "original_value": str(raw_value),
                        "transformed_value": str(transformed_split_value),
                        "split_part": idx + 1,
                        "total_parts": len(output_fields)
                    }
                    # Preserve lookup_of if it exists in mapping_item
                    if mapping_item.get("lookup_of"):
                        transformation_entry["lookup_of"] = mapping_item.get("lookup_of")
                    transformation_metadata["transformations_applied"].append(transformation_entry)
                
                # Remove the original source field from transformed record
                transformed_record.pop(source_field, None)
            
            # Step 2b: Process combined fields
            # Combine multiple source fields into a single destination field
            if transformation_rules and transformation_rules.get("fields"):
                # Build a list of combine rules (don't rely on combine_group flag which may not be in database mappings)
                combine_rules = []
                for rule in transformation_rules.get("fields", []):
                    if rule.get("action") == "combine":
                        source_fields = rule.get("source_fields", [])
                        output_field = rule.get("output_field", "")
                        # Verify that all source fields exist in the mapping
                        if source_fields and output_field:
                            all_source_fields_exist = all(src_field in mapping for src_field in source_fields)
                            if all_source_fields_exist:
                                combine_rules.append(rule)
                
                # Process each combine rule
                for rule in combine_rules:
                    source_fields = rule.get("source_fields", [])
                    output_field = rule.get("output_field", "")
                    separator = rule.get("separator", " ")
                    
                    if not output_field or not source_fields:
                        continue
                    
                    # Collect values from all source fields (store original values for logging)
                    combined_values = []
                    original_values_dict = {}
                    for src_field in source_fields:
                        # Try both transformed_record and original record
                        value = None
                        if src_field in transformed_record:
                            value = transformed_record.get(src_field)
                        elif src_field in record:
                            value = record.get(src_field)
                            # Add it to transformed_record for combining
                            transformed_record[src_field] = value
                        
                        if value is not None:
                            original_values_dict[src_field] = str(value) if value is not None else ""
                            combined_values.append(str(value).strip())
                    
                    # Combine the values
                    if combined_values:
                        combined_value = separator.join(combined_values)
                        
                        # Get destination field/column name from first source field's mapping
                        # This works even if is_combined_field flag is missing (e.g., from database mappings)
                        first_source_field = source_fields[0]
                        destination_field_name = None
                        mapping_info = None
                        
                        if first_source_field in mapping:
                            mapping_info = mapping[first_source_field]
                            if isinstance(mapping_info, dict):
                                # Get destination field/column name based on destination type
                                if destination == "salesforce":
                                    destination_field_name = mapping_info.get("field")
                                elif destination == "google_sheets":
                                    destination_field_name = mapping_info.get("column")
                                elif destination in ["sql", "database"]:
                                    destination_field_name = mapping_info.get("column")
                                else:
                                    destination_field_name = output_field
                        
                        # Fallback to output_field if destination_field_name not found
                        if not destination_field_name:
                            destination_field_name = output_field
                        
                        if destination_field_name:
                            # Apply data type transformation to the combined value
                            if destination == "salesforce":
                                obj = mapping_info.get("object") if mapping_info else None
                                key = (obj, destination_field_name) if obj else None
                                field_meta = field_metadata.get(key, {}) if key else {}
                                data_type = field_meta.get("data_type", "")
                                is_lookup = field_meta.get("is_lookup", False)
                                transformed_combined_value = DataTransformationService.transform_value_for_destination(
                                    combined_value, data_type, is_lookup
                                )
                            elif destination == "google_sheets":
                                from utils.data_type_converter import convert_value_for_sheets
                                transformed_combined_value = convert_value_for_sheets(
                                    combined_value, column_name=destination_field_name
                                )
                            elif destination in ["sql", "database"]:
                                from utils.data_type_converter import convert_value_for_database
                                table = mapping_info.get("table") if mapping_info else None
                                key = (table, destination_field_name) if table else None
                                field_meta = field_metadata.get(key, {}) if key else {}
                                column_type = field_meta.get("data_type", "")
                                is_nullable = field_meta.get("is_nullable", True)
                                connector_type = table_schema.get("connector_type", "postgresql") if table_schema else "postgresql"
                                transformed_combined_value = convert_value_for_database(
                                    combined_value, column_type, connector_type, is_nullable, destination_field_name
                                )
                            else:
                                transformed_combined_value = combined_value
                            
                            # Add the combined field with destination field/column name as key
                            transformed_record[destination_field_name] = transformed_combined_value
                            
                            # Remove the original source fields from transformed record
                            # IMPORTANT: Don't remove a source field if it's the same as the destination field
                            # (e.g., when combining Name + CompanyName -> Name, don't remove Name)
                            for src_field in source_fields:
                                if src_field != destination_field_name:
                                    transformed_record.pop(src_field, None)
                            
                            # Log the combine transformation
                            obj = mapping_info.get("object") if mapping_info and destination == "salesforce" else None
                            destination_label = f"{obj}.{destination_field_name}" if destination == "salesforce" and obj else destination_field_name
                            transformation_entry = {
                                "record_idx": record_idx,
                                "source_fields": source_fields,
                                "destination_field": destination_label,
                                "transformation": "combine",
                                "original_values": original_values_dict,
                                "transformed_value": str(transformed_combined_value),
                                "separator": separator,
                                "output_field": output_field
                            }
                            # Preserve lookup_of if it exists in mapping_info
                            if mapping_info and mapping_info.get("lookup_of"):
                                transformation_entry["lookup_of"] = mapping_info.get("lookup_of")
                            transformation_metadata["transformations_applied"].append(transformation_entry)
            
            # Step 2c: Process regular (non-split, non-combined) fields
            # Apply transformations for fields that are NOT split fields or combined fields
            for source_field, mapping_info in mapping.items():
                # Skip if this is a split field (array) - already handled in Step 2a
                if isinstance(mapping_info, list):
                    continue
                
                # Skip if this is a combined field - already handled in Step 2b
                # Check if this field is part of any combine rule (don't rely on is_combined_field flag)
                is_combined_field = False
                if transformation_rules and transformation_rules.get("fields"):
                    for rule in transformation_rules.get("fields", []):
                        if rule.get("action") == "combine":
                            source_fields = rule.get("source_fields", [])
                            if source_field in source_fields:
                                is_combined_field = True
                                break
                
                if is_combined_field:
                    continue
                
                if source_field not in record:
                    continue
                
                # Skip if this field was already removed (was a split field)
                if source_field not in transformed_record:
                    continue
                
                raw_value = transformed_record.get(source_field)
                
                # Skip None or empty strings (empty values should not be included in migration)
                if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
                    continue
                
                # Get destination field/column name and metadata based on destination type
                if destination == "salesforce":
                    obj = mapping_info.get("object")
                    field = mapping_info.get("field")
                    key = (obj, field) if obj and field else None
                    field_meta = field_metadata.get(key, {}) if key else {}
                    data_type = field_meta.get("data_type", "")
                    is_lookup = field_meta.get("is_lookup", False)
                    transformed_value = DataTransformationService.transform_value_for_destination(
                        raw_value, data_type, is_lookup
                    )
                elif destination == "google_sheets":
                    column = mapping_info.get("column")
                    from utils.data_type_converter import convert_value_for_sheets
                    transformed_value = convert_value_for_sheets(
                        raw_value, column_name=column
                    )
                elif destination in ["sql", "database"]:
                    column = mapping_info.get("column")
                    table = mapping_info.get("table")
                    key = (table, column) if table and column else None
                    field_meta = field_metadata.get(key, {}) if key else {}
                    column_type = field_meta.get("data_type", "")
                    is_nullable = field_meta.get("is_nullable", True)
                    connector_type = table_schema.get("connector_type", "postgresql") if table_schema else "postgresql"
                    from utils.data_type_converter import convert_value_for_database
                    transformed_value = convert_value_for_database(
                        raw_value, column_type, connector_type, is_nullable, column
                    )
                else:
                    # Fallback: use as-is
                    transformed_value = raw_value
                
                # IMPORTANT: Keep source field name as key, but use transformed value
                # The mapping will handle converting source_field -> destination object.field/column
                transformed_record[source_field] = transformed_value
                
                # Log transformation if value actually changed
                if transformed_value != raw_value:
                    # Build destination field label based on destination type
                    if destination == "salesforce":
                        destination_label = f"{obj}.{field}" if obj and field else field or source_field
                    elif destination == "google_sheets":
                        column = mapping_info.get("column")
                        destination_label = column or source_field
                    elif destination in ["sql", "database"]:
                        table = mapping_info.get("table")
                        column = mapping_info.get("column")
                        destination_label = f"{table}.{column}" if table and column else column or source_field
                    else:
                        destination_label = source_field
                    
                    transformation_entry = {
                        "record_idx": record_idx,
                        "source_field": source_field,
                        "destination_field": destination_label,
                        "transformation": "data_type_conversion",
                        "original_value": str(raw_value),
                        "transformed_value": str(transformed_value),
                        "data_type": field_meta.get("data_type", "") if destination == "salesforce" else (field_meta.get("data_type", "") if destination in ["sql", "database"] else "")
                    }
                    # Preserve lookup_of if it exists in mapping_info
                    if mapping_info.get("lookup_of"):
                        transformation_entry["lookup_of"] = mapping_info.get("lookup_of")
                    transformation_metadata["transformations_applied"].append(transformation_entry)
            
            transformed_data.append(transformed_record)
        
        # Step 3: Transform mapping to match the transformed data structure
        # This ensures the mapping keys match the field names in transformed_data
        transformed_mapping = DataTransformationService.transform_mapping_for_transformed_data(
            mapping=mapping,
            transformation_rules=transformation_rules,
            destination=destination
        )
        
        print(f"✅ Transformation complete. Applied {len(transformation_metadata['transformations_applied'])} transformations")
        print(f"✅ Mapping transformed: {len(mapping)} entries -> {len(transformed_mapping)} entries")
        return transformed_data, transformation_metadata, transformed_mapping
