"""Shared Salesforce SOQL/query/clarification helpers."""

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

def _clarification_stream_chunks(message: str):
    """Yield SSE clarification + human-readable line for agent runtime meta parsing."""
    msg = (message or "").strip()
    if not msg:
        return
    payload = json.dumps({"status": "clarification", "message": msg})
    yield f"data: {payload}\n\n"
    yield f"{msg}\n"


def _sanitize_soql_query(soql: str) -> str:
    """
    Sanitize SOQL query to fix common syntax issues.
    
    Fixes:
    - Removes SQL-style string concatenation using '+' operator
    - Removes 'AS' aliases (SOQL doesn't support aliases)
    - Removes trailing semicolons
    """
    if not soql:
        return soql
    
    # Remove trailing semicolon
    soql = soql.rstrip().rstrip(';')

    # Remove 'AS alias' patterns (SOQL doesn't support aliases)
    # Match: "Field AS Alias" but be careful not to match "AS" in other contexts
    # Use regex to find AS in SELECT clause only
    select_pattern = r'(SELECT\s+)(.*?)(\s+FROM)'
    match = re.search(select_pattern, soql, re.IGNORECASE | re.DOTALL)
    if match:
        select_clause = match.group(2)
        # Remove AS aliases from SELECT clause
        # Pattern: field AS alias (case insensitive)
        cleaned_select = re.sub(r'\s+AS\s+\w+', '', select_clause, flags=re.IGNORECASE)
        soql = soql[:match.start(2)] + cleaned_select + soql[match.end(2):]
    
    return soql.strip()


def _dedupe_preserve_order(values: List[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for value in values:
        if value and value not in seen:
            ordered.append(value)
            seen.add(value)
    return ordered


def _validate_soql_fields(
    object_result: Dict[str, Any],
    referenced_fields: List[str],
) -> Tuple[bool, List[str], List[str]]:
    """
    Validate that all referenced_fields exist on the object according to field_metadata.
    Supports relationship paths (e.g. Account.Name). Returns (valid, invalid_fields, available_fields).
    """
    from services.planning.soql_extract_contract import validate_soql_referenced_fields

    return validate_soql_referenced_fields(referenced_fields, object_result)


async def _build_invalid_field_clarification(
    invalid_fields: List[str],
    available_fields: List[str],
    object_result: Dict[str, Any],
    org_result: Dict[str, Any],
    soql: Optional[str] = None,
    prompt: Optional[str] = None,
) -> str:
    """
    Build a clarification message when invalid fields are detected.
    Lists available fields, asks for correction, and optionally suggests a fixed SOQL.
    """
    conn = org_result.get("connection_name") or "your Salesforce org"
    obj_label = object_result.get("object_label") or object_result.get("object_api_name") or "the object"
    invalid_str = ", ".join(f"'{f}'" for f in invalid_fields[:10])
    if len(invalid_fields) > 10:
        invalid_str += f" and {len(invalid_fields) - 10} more"
    avail_preview = ", ".join(available_fields[:30])
    if len(available_fields) > 30:
        avail_preview += f", ... ({len(available_fields)} total)"

    msg = (
        f"The field(s) {invalid_str} are not available on {obj_label} in {conn}.\n\n"
        f"**Available fields:** {avail_preview}\n\n"
        f"Which field would you like to use instead? Reply with the correct field name(s), and I'll rerun the extraction."
    )

    # Optional: suggest a corrected SOQL via LLM (non-blocking, short timeout)
    suggestion = None
    try:
        from services.llm import tasks as llm_tasks
        from services.llm.service import llm_complete_openai_compat, task_has_api_key

        if soql and prompt and await task_has_api_key(llm_tasks.SQL_QUERY_GENERATION):

            async def _suggest():
                resp = await llm_complete_openai_compat(
                    llm_tasks.SQL_QUERY_GENERATION,
                    messages=[{
                        "role": "user",
                        "content": f"""The user's SOQL query uses invalid field(s): {invalid_fields}.
Available fields on this object: {available_fields[:40]}

User's SOQL: {soql}
User's request: {prompt[:300]}

Suggest a corrected SOQL using only available fields. If the invalid field was in a WHERE clause, suggest a sensible replacement (e.g. Id if filtering by ID, or remove the filter if no good replacement).
Reply with ONLY the corrected SOQL, no explanation. If you cannot suggest one, reply with exactly: NONE""",
                    }],
                    max_tokens=200,
                    temperature=0,
                )
                text = (resp.choices[0].message.content or "").strip()
                if text and text.upper() != "NONE":
                    return text
                return None

            suggestion = await asyncio.wait_for(_suggest(), timeout=4.0)
    except Exception:
        pass

    if suggestion:
        msg += f"\n\n**Suggested query:** `{suggestion}`"

    return msg + "\n"


async def _salesforce_clarification_response(message: str):
    async def generate():
        yield f"{message}\n"

    return generate()


def _serialize_salesforce_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        cleaned = {k: v for k, v in value.items() if k != "attributes"}
        try:
            return json.dumps(cleaned)
        except TypeError:
            return str(cleaned)
    if isinstance(value, list):
        try:
            return json.dumps(value)
        except TypeError:
            return str(value)
    return value


def _clean_salesforce_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Clean and normalize Salesforce records.
    - Removes 'attributes' metadata
    - Flattens relationship fields (nested objects) into flat fields
    - Splits compound address fields (MailingAddress, BillingAddress, etc.) into separate components
    - Serializes complex values
    
    Relationship fields from SOQL queries (e.g., Account.Name, Account.Website) come back nested under
    the relationship key:
      Account: {"Name": "...", "Website": "...", "attributes": {...}}

    We flatten these to separate keys so multiple relationship fields don't overwrite each other:
      AccountName: "..."
      AccountWebsite: "..."
    
    Compound address fields (e.g., MailingAddress) come back as:
      MailingAddress: {"city": "San Francisco", "country": "USA", "street": "123 Main St", ...}
    
    We split these into separate fields:
      MailingCity: "San Francisco"
      MailingCountry: "USA"
      MailingStreet: "123 Main St"
      etc.
    
    This keeps the rest of the pipeline "flat" and supports N relationship fields dynamically.
    """
    cleaned_records: List[Dict[str, Any]] = []
    
    # Common compound address field prefixes
    ADDRESS_FIELD_PREFIXES = ["Mailing", "Billing", "Shipping", "Other"]
    
    for record_idx, record in enumerate(records):
        cleaned_record: Dict[str, Any] = {}
        for key, value in record.items():
            if key == "attributes":
                continue
            
            # Handle compound address fields (e.g., MailingAddress, BillingAddress)
            # These come as dicts with keys: city, country, street, postalCode, state, latitude, longitude
            if isinstance(value, dict) and not ("attributes" in value):
                # Check if this is a compound address field
                is_address_field = (
                    key.endswith("Address") and 
                    any(key.startswith(prefix) for prefix in ADDRESS_FIELD_PREFIXES)
                ) or (
                    # Also handle generic address fields
                    "city" in value or "country" in value or "street" in value or "postalCode" in value
                )
                
                if is_address_field:
                    # Split compound address into separate fields
                    address_prefix = key.replace("Address", "")  # e.g., "Mailing" from "MailingAddress"
                    
                    # Map Salesforce address field names to our field names
                    address_field_mapping = {
                        "city": f"{address_prefix}City",
                        "country": f"{address_prefix}Country",
                        "street": f"{address_prefix}Street",
                        "postalCode": f"{address_prefix}PostalCode",
                        "state": f"{address_prefix}State",
                        "latitude": f"{address_prefix}Latitude",
                        "longitude": f"{address_prefix}Longitude",
                        "geocodeAccuracy": f"{address_prefix}GeocodeAccuracy"
                    }
                    
                    # Extract each address component
                    for sf_key, field_name in address_field_mapping.items():
                        if sf_key in value:
                            cleaned_record[field_name] = _serialize_salesforce_value(value[sf_key])
                    
                    # Don't include the original compound field (we've split it)
                    continue
            
            # Handle relationship fields (nested objects from SOQL relationship queries)
            # Example: Account: {"Name": "Edge Communications", "Id": "001..."}
            # Convert to: Account: "Edge Communications"
            if isinstance(value, dict):
                # Relationship objects from SOQL include an "attributes" key.
                # We expand them into flat keys: <RelationshipKey><FieldApiName>
                if "attributes" in value:
                    for rel_field, rel_val in value.items():
                        if rel_field == "attributes":
                            continue
                        # Avoid double-prefixing when the related field already contains the relationship name.
                        # Example: key="Account", rel_field="AccountNumber" -> keep "AccountNumber" (not "AccountAccountNumber")
                        if isinstance(rel_field, str) and rel_field.lower().startswith(key.lower()):
                            flat_key = rel_field
                        else:
                            flat_key = f"{key}{rel_field}"
                        cleaned_record[flat_key] = _serialize_salesforce_value(rel_val)
                else:
                    # Regular nested dict - serialize it
                    cleaned_record[key] = _serialize_salesforce_value(value)
            else:
                # Regular field - serialize normally
                cleaned_record[key] = _serialize_salesforce_value(value)
        
        cleaned_records.append(cleaned_record)
    
    return cleaned_records


async def _execute_salesforce_query(access_token: str, instance_url: str, soql: str) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{instance_url}/services/data/v59.0/query"
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(url, headers=headers, params={"q": soql})
        
        # Handle SOQL syntax errors with helpful error messages
        if response.status_code == 400:
            error_message = "Invalid SOQL query"
            try:
                error_response = response.json()
                if isinstance(error_response, list) and len(error_response) > 0:
                    error_message = error_response[0].get("message", error_message)
                elif isinstance(error_response, dict):
                    error_message = error_response.get("message") or error_response.get(
                        "error", error_message
                    )
            except (json.JSONDecodeError, KeyError, IndexError, AttributeError, TypeError) as e:
                print(f"⚠️ [SOQL ERROR] Failed to parse error response: {e}")
                response.raise_for_status()

            if "+" in soql and "CONCAT" not in soql.upper():
                raise ValueError(
                    f"SOQL syntax error: String concatenation using '+' operator is not supported in SOQL. "
                    f"Use CONCAT() function instead, or return fields separately. "
                    f"Salesforce error: {error_message}"
                )
            if " AS " in soql.upper():
                raise ValueError(
                    f"SOQL syntax error: Field aliases using 'AS' keyword are not supported in SOQL. "
                    f"Use exact field API names only. Salesforce error: {error_message}"
                )
            if "ContentDocumentLink" in error_message and "semi join" in error_message.lower():
                raise ValueError(
                    f"SOQL syntax error: Subqueries on ContentDocumentLink are not supported. "
                    f"Attachments are extracted after fetching records, not via SOQL filters. "
                    f"Salesforce error: {error_message}"
                )
            raise ValueError(f"SOQL syntax error: {error_message}")
        
        response.raise_for_status()
        return response.json()


async def _fetch_salesforce_records(access_token: str, instance_url: str, soql: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    response = await _execute_salesforce_query(access_token, instance_url, soql)
    records.extend(response.get("records", []))

    while not response.get("done"):
        next_url = response.get("nextRecordsUrl")
        if not next_url:
            break
        async with httpx.AsyncClient(timeout=60) as client:
            next_response = await client.get(f"{instance_url}{next_url}", headers={"Authorization": f"Bearer {access_token}"})
            next_response.raise_for_status()
            response = next_response.json()
            records.extend(response.get("records", []))

    return records


def _sanitize_soql_for_bulk_api(soql: str) -> str:
    """
    Bulk API 2.0 query has stricter SELECT support than REST /query.
    In particular, it rejects selecting certain compound fields (e.g., MailingAddress).

    If we detect those known compound fields in the SELECT list, we remove them
    while preserving the rest of the query (FROM/WHERE/ORDER BY/LIMIT...).

    This is applied ONLY to the Bulk API query path.
    """
    if not soql or not isinstance(soql, str):
        return soql

    # Capture: SELECT <fields> FROM <rest...>
    m = re.search(r"^\s*SELECT\s+(?P<select>.+?)\s+FROM\s+(?P<rest>.+)$", soql.strip(), re.IGNORECASE)
    if not m:
        return soql

    select_part = m.group("select").strip()
    rest_part = m.group("rest").strip()

    # Known compound address fields that Bulk Query rejects
    compound_address_fields = {"MailingAddress", "OtherAddress", "BillingAddress", "ShippingAddress"}

    # Basic, safe split: SOQL field lists here are simple comma-separated identifiers
    raw_fields = [f.strip() for f in select_part.split(",") if f.strip()]
    if not raw_fields:
        return soql

    def is_disallowed_compound(field: str) -> bool:
        # Remove surrounding quotes just in case (SOQL generally doesn't quote identifiers)
        field = field.strip().strip('"').strip("'")
        # Handle relationship paths like Account.BillingAddress
        last_segment = field.split(".")[-1] if field else field
        return last_segment in compound_address_fields

    filtered_fields = [f for f in raw_fields if not is_disallowed_compound(f)]

    # If nothing changed, return original
    if len(filtered_fields) == len(raw_fields):
        return soql

    # Never produce an invalid SELECT
    if not filtered_fields:
        return soql

    return f"SELECT {', '.join(filtered_fields)} FROM {rest_part}"


def _parse_soql_from_prompt(prompt: str) -> Optional[tuple]:
    """
    Extract SOQL SELECT fields and object from prompt text.
    Returns (fields_list, object_api_name) or None if no SOQL pattern found.
    Handles prompts like: "run this as extraction query: select Id, firstname, lastname from contact where ..."
    """
    if not prompt or not isinstance(prompt, str):
        return None
    # Match SELECT ... FROM object (object is word before WHERE/GROUP/ORDER/LIMIT or end)
    m = re.search(
        r"\bselect\s+(.+?)\s+from\s+(\w+)(?:\s+where|\s+group\s+by|\s+order\s+by|\s+limit|$)",
        prompt,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return None
    select_part = m.group(1).strip()
    object_name = m.group(2).strip()
    # Parse field names (comma-separated, trim, normalize case for standard fields)
    raw = [f.strip() for f in select_part.split(",") if f.strip()]
    if not raw:
        return None
    # Normalize: Id, FirstName, LastName etc. (Salesforce is case-insensitive for standard fields)
    std_map = {"id": "Id", "firstname": "FirstName", "lastname": "LastName", "name": "Name", "email": "Email", "phone": "Phone"}
    fields = []
    for f in raw:
        clean = f.strip().strip('"').strip("'")
        if not clean:
            continue
        fields.append(std_map.get(clean.lower(), clean))
    return (fields, object_name) if fields else None


def _is_field_related_clarification(message: str) -> bool:
    """True if the clarification message is about an invalid/unavailable field."""
    if not message:
        return False
    msg_lower = message.lower()
    return any(
        phrase in msg_lower
        for phrase in [
            "not listed among",
            "not in selected",
            "not available",
            "does not exist",
            "doesn't exist",
            "not among the selected",
            "unavailable",
            "not exist on",
        ]
    )


def _extract_field_names_from_clarification(message: str) -> List[str]:
    """Extract field API names from a clarification message (e.g. 'ex_Id__c' from \"field 'ex_Id__c'\").
    Returns list of potential field names found in quotes."""
    if not message:
        return []
    # Match 'fieldname' or "fieldname" - prefer patterns that look like field identifiers
    matches = re.findall(r"['\"]([a-zA-Z_][a-zA-Z0-9_.]*(?:__c)?)['\"]", message)
    return list(dict.fromkeys(matches))  # dedupe preserve order


def _parse_invalid_field_from_soql_error(error_message: str) -> Optional[str]:
    """Extract the invalid field API name from a Salesforce SOQL error message."""
    if not error_message:
        return None
    m = _NO_SUCH_COLUMN_RE.search(error_message)
    return m.group(1).strip() if m else None


def _remove_field_from_soql(soql: str, field_name: str) -> Optional[str]:
    """
    Remove a single field from the SELECT list of a SOQL query.
    Used when the org does not have that field (e.g. stale metadata or different org).
    Returns the new SOQL string, or None if the field was not found (no change).
    """
    if not soql or not field_name:
        return None
    m = re.search(r"^\s*SELECT\s+(?P<select>.+?)\s+FROM\s+(?P<rest>.+)$", soql.strip(), re.IGNORECASE)
    if not m:
        return None
    select_part = m.group("select").strip()
    rest_part = m.group("rest").strip()
    raw_fields = [f.strip() for f in select_part.split(",") if f.strip()]
    # Match field exactly (Salesforce API names are case-sensitive)
    filtered = [f for f in raw_fields if f != field_name]
    if len(filtered) == len(raw_fields):
        return None  # field not in list
    if not filtered:
        return soql  # do not produce empty SELECT
    return f"SELECT {', '.join(filtered)} FROM {rest_part}"


def _normalize_bulk_api_query_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Bulk API 2.0 query results come back as CSV -> Dict[str, str] with flat keys.

    REST /query results (what the rest of this module was originally built around) can include:
    - relationship fields nested under their relationship key (Account: {Name: ...})
    - compound address dicts (MailingAddress: {city: ..., ...})

    Bulk CSV flattens relationship + compound fields into dotted column names like:
    - Account.Name
    - Project__r.Status__c
    - MailingAddress.city

    To keep downstream behavior consistent (especially `_clean_salesforce_records`),
    we normalize dotted keys into the same "flattened" style the cleaner produces:
    - Account.Name -> AccountName
    - Project__r.Status__c -> Project__rStatus__c
    - MailingAddress.city -> MailingCity
    """
    if not records:
        return []

    address_prefixes = {"Mailing", "Billing", "Shipping", "Other"}
    address_component_map = {
        "city": "City",
        "country": "Country",
        "street": "Street",
        "postalCode": "PostalCode",
        "state": "State",
        "latitude": "Latitude",
        "longitude": "Longitude",
        "geocodeAccuracy": "GeocodeAccuracy",
    }

    normalized: List[Dict[str, Any]] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        out: Dict[str, Any] = {}
        for raw_key, value in rec.items():
            if raw_key is None:
                continue
            key = str(raw_key).strip()
            if not key:
                continue

            # Normalize compound address dotted columns into split fields
            # Example: MailingAddress.city -> MailingCity
            if ". " in key:
                # Defensive: strip accidental spaces around dot
                key = key.replace(". ", ".")
            if " ." in key:
                key = key.replace(" .", ".")

            if ". " in key or " ." in key:
                # just in case any remain
                key = key.replace(" ", "")

            if "." in key:
                left, right = key.split(".", 1)
                if left.endswith("Address"):
                    prefix = left.replace("Address", "")
                    if prefix in address_prefixes and right in address_component_map:
                        key = f"{prefix}{address_component_map[right]}"
                    else:
                        # Fall back to removing dots: MailingAddress.city -> MailingAddresscity
                        key = "".join([p for p in key.split(".") if p])
                else:
                    # Relationship field path: Account.Name -> AccountName
                    # Multi-level relationship: Account.Owner.Name -> AccountOwnerName
                    key = "".join([p for p in key.split(".") if p])

            out[key] = value
        normalized.append(out)

    return normalized


async def _fetch_salesforce_records_with_bulk_api(
    access_token: str,
    instance_url: str,
    soql: str,
    object_api_name: str,
) -> List[Dict[str, Any]]:
    """
    Fetch Salesforce records using Bulk API 2.0 query jobs.

    Returns a List[Dict] (same as REST query fetch) so the rest of the pipeline remains unchanged.
    """
    from utils.soql_extraction_sanitize import fix_duplicate_soql_select_aliases

    soql_for_bulk = fix_duplicate_soql_select_aliases(_sanitize_soql_for_bulk_api(soql))

    job_id = await create_bulk_query_job(
        object_name=object_api_name,
        query=soql_for_bulk,
        access_token=access_token,
        instance_url=instance_url,
    )
    await wait_for_bulk_query_job_completion(
        job_id=job_id,
        access_token=access_token,
        instance_url=instance_url,
    )
    records = await get_bulk_query_results(
        job_id=job_id,
        access_token=access_token,
        instance_url=instance_url,
    )
    return _normalize_bulk_api_query_records(records)
