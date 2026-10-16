"""
Salesforce Tooling API — CustomField read (source) and create/update (destination).
Used for metadata deploy: get field metadata from source org, create or update on destination org.
Phase 5 of Salesforce metadata/field deploy flow.
"""

from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

API_VERSION = "v59.0"

# Metadata keys that are safe to send on create/update (exclude read-only and internal).
# Picklist-type fields require one of: valueSet, picklist, or globalPicklist (per Salesforce).
ALLOWED_METADATA_KEYS = frozenset({
    "type", "label", "length", "required", "description", "inlineHelpText", "defaultValue",
    "precision", "scale", "unique", "externalId", "formula", "formulaTreatBlanksAs",
    "summaryForeignKey", "summaryOperation", "summaryFilterItems", "referenceTo",
    "relationshipName", "relationshipLabel", "relationshipOrder", "deleteConstraint",
    "writeRequiresMasterRead", "trackTrending", "trackHistory", "trackFeedHistory",
    "dependentPicklist", "valueSet", "picklist", "globalPicklist",
    "visibleLines", "maskType", "maskChar", "displayLocationInDecimal",
    "generated", "caseSensitive", "externalDeveloperName", "stripMarkup",
    "restrictedPicklist", "filterable", "sortable",
})


def _full_name(object_api_name: str, field_api_name: str) -> str:
    """Build Tooling API FullName: ObjectName.FieldName__c."""
    return f"{object_api_name}.{field_api_name}"


def _filter_metadata_for_create_update(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Return only keys allowed for CustomField create/update; strip read-only."""
    if not metadata or not isinstance(metadata, dict):
        return {}
    return {k: v for k, v in metadata.items() if k in ALLOWED_METADATA_KEYS and v is not None}


def _sanitize_picklist_value_entry(entry: Any) -> Optional[Dict[str, Any]]:
    """
    Sanitize a single picklist value (valueSetDefinition.value[i]) so Tooling API accepts it.
    Required: fullName, label; default/isActive must be bool. Removes null description/color.
    """
    if not isinstance(entry, dict):
        return None
    full_name = entry.get("fullName")
    if full_name is None and entry.get("value") is not None:
        full_name = entry.get("value")
    if full_name is None or (isinstance(full_name, str) and not full_name.strip()):
        return None
    full_name = str(full_name).strip()
    label = entry.get("label")
    if label is None or (isinstance(label, str) and not label.strip()):
        label = full_name
    else:
        label = str(label).strip()
    out = {
        "fullName": full_name,
        "label": label,
        "default": bool(entry.get("default", False)),
        "isActive": bool(entry.get("isActive", True)),
    }
    if entry.get("description") is not None and str(entry.get("description")).strip():
        out["description"] = str(entry["description"]).strip()
    if entry.get("color") is not None and str(entry.get("color")).strip():
        out["color"] = str(entry["color"]).strip()
    return out


def _sanitize_value_set(value_set: Any) -> Optional[Dict[str, Any]]:
    """
    Sanitize valueSet for Picklist/MultiselectPicklist to avoid JSON_PARSER_ERROR.
    Handles: (1) valueSetDefinition.value[] with inline values, (2) valueSetName (global value set reference).
    """
    if not value_set or not isinstance(value_set, dict):
        return None
    out: Dict[str, Any] = {}
    if value_set.get("restricted") is not None:
        out["restricted"] = bool(value_set["restricted"])
    # Global value set reference: preserve valueSetName
    value_set_name = value_set.get("valueSetName")
    if value_set_name is not None and isinstance(value_set_name, str) and value_set_name.strip():
        out["valueSetName"] = value_set_name.strip()
    definition = value_set.get("valueSetDefinition")
    if definition and isinstance(definition, dict):
        sanitized_def: Dict[str, Any] = {}
        if definition.get("sorted") is not None:
            sanitized_def["sorted"] = bool(definition["sorted"])
        raw_values = definition.get("value") or definition.get("values") or []
        if isinstance(raw_values, list):
            values = []
            for item in raw_values:
                if item is None:
                    continue
                sanitized = _sanitize_picklist_value_entry(item)
                if sanitized is not None:
                    values.append(sanitized)
            if values:
                sanitized_def["value"] = values
                out["valueSetDefinition"] = sanitized_def
    # Return if we have either inline values or global reference (API requires one of valueSet/picklist/globalPicklist)
    if out.get("valueSetDefinition") or out.get("valueSetName"):
        return out
    return None


def _deep_strip_nulls(obj: Any) -> Any:
    """Recursively remove nulls from dict/list so API does not get VALUE_NULL. Preserves structure."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {k: _deep_strip_nulls(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_deep_strip_nulls(x) for x in obj if x is not None]
    return obj


def _sanitize_metadata_for_api(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Remove nulls and sanitize picklist structures so Tooling API accepts the payload.
    Picklist-type fields must keep one of valueSet, picklist, or globalPicklist (never drop).
    """
    if not metadata or not isinstance(metadata, dict):
        return metadata
    out = {}
    for k, v in metadata.items():
        if v is None:
            continue
        if k == "valueSet":
            sanitized_vs = _sanitize_value_set(v)
            if sanitized_vs is not None:
                out[k] = sanitized_vs
            else:
                # Preserve source valueSet with only null-stripping so we never drop picklist spec
                out[k] = _deep_strip_nulls(v)
            continue
        if k in ("picklist", "globalPicklist"):
            # API accepts these; sanitize recursively and always preserve
            out[k] = _sanitize_metadata_for_api(v) if isinstance(v, dict) else _deep_strip_nulls(v)
            continue
        if isinstance(v, list):
            v = [_sanitize_metadata_for_api(x) if isinstance(x, dict) else x for x in v if x is not None]
        elif isinstance(v, dict):
            v = _sanitize_metadata_for_api(v)
        out[k] = v
    return out


def _soql_escape(s: str) -> str:
    """Escape single quotes for SOQL string literal."""
    return (s or "").replace("\\", "\\\\").replace("'", "\\'")

def _parse_namespace_and_developer_name(field_api_name: str) -> Dict[str, Optional[str]]:
    """
    Parse a Salesforce custom field API name into NamespacePrefix + DeveloperName.

    Examples:
      - Field_Migration__c -> namespace=None, developer_name='Field_Migration'
      - xtagwordforce__CustomerPriority__c -> namespace='xtagwordforce', developer_name='CustomerPriority'

    Notes:
      - Tooling API `CustomField.DeveloperName` does NOT include the `__c` suffix.
      - For managed package fields, namespace is stored in `NamespacePrefix`.
    """
    api = (field_api_name or "").strip()
    if not api.endswith("__c"):
        return {"namespace": None, "developer_name": None}
    base = api[:-3]  # strip __c
    if "__" in base:
        # managed package: Namespace__FieldName
        ns, dev = base.split("__", 1)
        return {"namespace": ns or None, "developer_name": dev or None}
    return {"namespace": None, "developer_name": base or None}

async def _resolve_object_table_enum_or_id(
    access_token: str,
    base: str,
    object_api_name: str,
) -> str:
    """
    For custom objects (ending in __c), TableEnumOrId in CustomField SOQL requires
    the object's Tooling API DurableId (e.g. '01Ixxxxxxx'), NOT the API name string.
    For standard objects (e.g. 'Account'), the API name works directly.

    Returns the DurableId for custom objects, or the object_api_name for standard objects.
    """
    if not object_api_name.endswith("__c"):
        return object_api_name

    soql = f"SELECT DurableId FROM EntityDefinition WHERE QualifiedApiName = '{_soql_escape(object_api_name)}'"
    url = f"{base}/services/data/{API_VERSION}/tooling/query?q={quote(soql, safe='')}"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code == 200:
            records = (resp.json() or {}).get("records") or []
            if records:
                durable_id = records[0].get("DurableId") or records[0].get("durableId")
                if durable_id:
                    return durable_id
    except Exception:
        pass
    # Fall back to API name if lookup fails
    return object_api_name


async def _get_custom_field_metadata_via_query(
    access_token: str,
    instance_url: str,
    full_name: str,
    object_api_name: Optional[str] = None,
    field_api_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Query CustomField by DeveloperName + TableEnumOrId, then GET by Id for Metadata.

    Key Salesforce Tooling API rules:
    - TableEnumOrId for custom objects requires the DurableId (01Ixxxxxxx), NOT the API name.
    - TableEnumOrId for standard objects accepts the API name directly (e.g. 'Account').
    - NamespacePrefix = null is NOT supported in Tooling API SOQL; omit it for unmanaged fields.
    - WHERE FullName = '...' is not supported on CustomField.
    """
    base = (instance_url or "").rstrip("/")
    headers = {"Authorization": f"Bearer {access_token}"}

    async def _query_id_then_get(soql: str) -> Optional[Dict[str, Any]]:
        """Query for Id only, then GET /CustomField/{Id} for Metadata."""
        url = f"{base}/services/data/{API_VERSION}/tooling/query?q={quote(soql, safe='')}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return None
        data = resp.json()
        records = data.get("records") or []
        if len(records) != 1:
            return None
        field_id = records[0].get("Id") or records[0].get("id")
        if not field_id:
            return None
        get_url = f"{base}/services/data/{API_VERSION}/tooling/sobjects/CustomField/{field_id}"
        async with httpx.AsyncClient(timeout=30) as client:
            get_resp = await client.get(get_url, headers=headers)
        if get_resp.status_code != 200:
            return None
        get_data = get_resp.json()
        metadata = get_data.get("Metadata") or get_data.get("metadata")
        if not metadata or not isinstance(metadata, dict):
            return None
        filtered = _filter_metadata_for_create_update(metadata)
        if not filtered:
            return None
        return filtered

    if object_api_name and field_api_name:
        parsed = _parse_namespace_and_developer_name(field_api_name)
        developer_name = parsed.get("developer_name")
        namespace = parsed.get("namespace")
        if not developer_name:
            return None

        # For custom objects, TableEnumOrId must be the DurableId, not the API name
        table_id = await _resolve_object_table_enum_or_id(access_token, base, object_api_name)
        table_escaped = _soql_escape(table_id)
        dev_escaped = _soql_escape(developer_name)

        where_parts = [
            f"TableEnumOrId = '{table_escaped}'",
            f"DeveloperName = '{dev_escaped}'",
        ]
        if namespace:
            where_parts.append(f"NamespacePrefix = '{_soql_escape(namespace)}'")
        # NOTE: do NOT add "NamespacePrefix = null" — returns 0 rows in Tooling API SOQL

        soql_dev = "SELECT Id FROM CustomField WHERE " + " AND ".join(where_parts)
        result = await _query_id_then_get(soql_dev)
        if result is not None:
            return result

    return None


async def get_custom_field_metadata(
    access_token: str,
    instance_url: str,
    object_api_name: str,
    field_api_name: str,
) -> Optional[Dict[str, Any]]:
    """
    GET CustomField from org by FullName (ObjectName.FieldName__c).
    Returns Metadata dict suitable for create/update, or None if 404 (e.g. standard field).
    For namespaced (managed package) fields, GET by Name often returns 404; we fall back to SOQL query by FullName.
    """
    base = (instance_url or "").rstrip("/")
    full_name = _full_name(object_api_name, field_api_name)

    # Only custom fields exist as Tooling API CustomField records
    if not (field_api_name or "").endswith("__c"):
        return None

    # Query CustomField Id via SOQL, then GET by Id for Metadata.
    return await _get_custom_field_metadata_via_query(
        access_token,
        base,
        full_name,
        object_api_name=object_api_name,
        field_api_name=field_api_name,
    )


async def create_custom_field(
    access_token: str,
    instance_url: str,
    full_name: str,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """
    POST CustomField to org. Returns dict with id on success or error message.
    """
    base = (instance_url or "").rstrip("/")
    url = f"{base}/services/data/{API_VERSION}/tooling/sobjects/CustomField/"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    body = {"FullName": full_name, "Metadata": metadata}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=headers, json=body)
    if resp.status_code in (200, 201):
        return {"success": True, "id": resp.json().get("id")}
    return {"success": False, "error": resp.text, "status_code": resp.status_code}


async def update_custom_field(
    access_token: str,
    instance_url: str,
    field_id: str,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """
    PATCH CustomField on org by Id. Returns dict with success or error message.
    """
    base = (instance_url or "").rstrip("/")
    url = f"{base}/services/data/{API_VERSION}/tooling/sobjects/CustomField/{field_id}"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    body = {"Metadata": metadata}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.patch(url, headers=headers, json=body)
    if resp.status_code == 204 or resp.status_code == 200:
        return {"success": True}
    return {"success": False, "error": resp.text, "status_code": resp.status_code}


async def get_custom_field_id(
    access_token: str,
    instance_url: str,
    full_name: str,
    object_api_name: Optional[str] = None,
    field_api_name: Optional[str] = None,
) -> Optional[str]:
    """
    Resolve CustomField Id via SOQL (DeveloperName + TableEnumOrId + NamespacePrefix).
    Used to find existing field on destination for update. Returns Id or None.
    """
    base = (instance_url or "").rstrip("/")
    if not object_api_name or not field_api_name:
        if "." in (full_name or ""):
            object_api_name, field_api_name = full_name.split(".", 1)
        else:
            return None
    parsed = _parse_namespace_and_developer_name(field_api_name)
    developer_name = parsed.get("developer_name")
    namespace = parsed.get("namespace")
    if not developer_name:
        return None
    where_parts = [
        f"TableEnumOrId = '{_soql_escape(object_api_name)}'",
        f"DeveloperName = '{_soql_escape(developer_name)}'",
    ]
    if namespace:
        where_parts.append(f"NamespacePrefix = '{_soql_escape(namespace)}'")
    else:
        where_parts.append("NamespacePrefix = null")
    soql = "SELECT Id FROM CustomField WHERE " + " AND ".join(where_parts)
    url = f"{base}/services/data/{API_VERSION}/tooling/query?q={quote(soql, safe='')}"
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers)

    if resp.status_code != 200:
        return None
    data = resp.json()
    records = data.get("records") or []
    if len(records) != 1:
        return None
    return records[0].get("Id") or records[0].get("id")


def _is_duplicate_developer_name_error(resp_text: str) -> bool:
    """True if Salesforce returned DUPLICATE_DEVELOPER_NAME (field already exists)."""
    return "DUPLICATE_DEVELOPER_NAME" in (resp_text or "")


async def _get_current_user_profile_id(access_token: str, instance_url: str) -> Optional[str]:
    """
    Get the ProfileId of the user associated with the access token (for default FLS).
    Uses OAuth userinfo then User sobject. Returns None on any failure.
    """
    base = (instance_url or "").rstrip("/")
    if not base or not access_token:
        return None
    try:
        userinfo_url = f"{base}/services/oauth2/userinfo"
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=15) as client:
            ui_resp = await client.get(userinfo_url, headers=headers)
        if ui_resp.status_code != 200:
            return None
        user_id = (ui_resp.json() or {}).get("user_id")
        if not user_id:
            return None
        user_url = f"{base}/services/data/{API_VERSION}/sobjects/User/{user_id}?fields=ProfileId"
        async with httpx.AsyncClient(timeout=15) as client:
            u_resp = await client.get(user_url, headers=headers)
        if u_resp.status_code != 200:
            return None
        return (u_resp.json() or {}).get("ProfileId")
    except Exception as e:
        print(f"      [TOOLING-API] Could not get current user ProfileId for FLS: {e}")
        return None


async def _get_permission_set_id_for_profile(
    access_token: str, instance_url: str, profile_id: str
) -> Optional[str]:
    """
    FieldPermissions.ParentId must be a PermissionSet Id, not a Profile Id.
    Each profile has an associated PermissionSet (IsOwnedByProfile = true). Return its Id.
    """
    base = (instance_url or "").rstrip("/")
    if not base or not access_token or not profile_id:
        return None
    try:
        profile_escaped = _soql_escape(profile_id)
        soql = (
            f"SELECT Id FROM PermissionSet "
            f"WHERE IsOwnedByProfile = true AND ProfileId = '{profile_escaped}' LIMIT 1"
        )
        url = f"{base}/services/data/{API_VERSION}/query?q={quote(soql, safe='')}"
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return None
        data = resp.json()
        records = data.get("records") or []
        if not records:
            return None
        return records[0].get("Id") or records[0].get("id")
    except Exception as e:
        print(f"      [TOOLING-API] Could not get PermissionSet for profile: {e}")
        return None


async def set_default_field_level_security(
    access_token: str,
    instance_url: str,
    object_api_name: str,
    field_full_name: str,
    profile_id: Optional[str] = None,
) -> bool:
    """
    Create a FieldPermissions record so the field is readable and editable for the given profile.
    Uses standard REST API (sobjects/FieldPermissions). ParentId must be the profile's
    PermissionSet Id (not Profile Id). If profile_id is None, resolves the current user's
    profile from the token, then its PermissionSet Id. Returns True if FLS was set, False otherwise.
    """
    base = (instance_url or "").rstrip("/")
    if not base or not access_token:
        return False
    pid = profile_id
    if not pid:
        pid = await _get_current_user_profile_id(access_token, instance_url)
    if not pid:
        return False
    # FieldPermissions.ParentId must be a PermissionSet Id (profile's FLS permission set), not Profile Id
    permission_set_id = await _get_permission_set_id_for_profile(access_token, base, pid)
    if not permission_set_id:
        print(f"      [TOOLING-API] No PermissionSet found for profile {pid[:18]}..., skipping FLS")
        return False
    url = f"{base}/services/data/{API_VERSION}/sobjects/FieldPermissions"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    body = {
        "ParentId": permission_set_id,
        "SobjectType": object_api_name,
        "Field": field_full_name,
        "PermissionsRead": True,
        "PermissionsEdit": True,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, headers=headers, json=body)
        if resp.status_code in (200, 201):
            print(f"      [TOOLING-API] Default FLS set for {field_full_name}")
            return True
        if resp.status_code == 400 and "DUPLICATE" in (resp.text or ""):
            return True
        print(f"      [TOOLING-API] FLS create failed for {field_full_name}: {resp.status_code} {resp.text[:200]}")
        return False
    except Exception as e:
        print(f"      [TOOLING-API] FLS create error for {field_full_name}: {e}")
        return False


async def get_field_permissions_for_field(
    access_token: str,
    instance_url: str,
    object_api_name: str,
    field_full_name: str,
) -> List[Dict[str, Any]]:
    """
    Query FieldPermissions for a field on the given org (typically source).
    Returns a list of grants: each dict has profile_name (or None), permission_set_name (or None),
    is_profile_owned, PermissionsRead, PermissionsEdit. Use these to apply FLS on another org.
    """
    base = (instance_url or "").rstrip("/")
    if not base or not access_token:
        return []
    obj_escaped = _soql_escape(object_api_name)
    field_escaped = _soql_escape(field_full_name)
    soql = (
        "SELECT PermissionsRead, PermissionsEdit, Parent.IsOwnedByProfile, "
        "Parent.Profile.Name, Parent.Name "
        f"FROM FieldPermissions WHERE SobjectType = '{obj_escaped}' AND Field = '{field_escaped}'"
    )
    url = f"{base}/services/data/{API_VERSION}/query?q={quote(soql, safe='')}"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return []
        data = resp.json()
        records = data.get("records") or []
        grants: List[Dict[str, Any]] = []
        for rec in records:
            parent = rec.get("Parent") or {}
            profile_ref = parent.get("Profile") or {}
            profile_name = (profile_ref.get("Name") or "").strip() or None
            perm_set_name = (parent.get("Name") or "").strip() or None
            is_profile_owned = bool(parent.get("IsOwnedByProfile"))
            grants.append({
                "profile_name": profile_name if is_profile_owned else None,
                "permission_set_name": None if is_profile_owned else perm_set_name,
                "is_profile_owned": is_profile_owned,
                "PermissionsRead": bool(rec.get("PermissionsRead", False)),
                "PermissionsEdit": bool(rec.get("PermissionsEdit", False)),
            })
        return grants
    except Exception as e:
        print(f"      [TOOLING-API] get_field_permissions_for_field error: {e}")
        return []


async def get_permission_set_id_by_profile_name(
    access_token: str, instance_url: str, profile_name: str
) -> Optional[str]:
    """Resolve a profile by Name on the given org and return its FLS PermissionSet Id."""
    if not profile_name or not (access_token and instance_url):
        return None
    base = (instance_url or "").rstrip("/")
    name_escaped = _soql_escape(profile_name)
    soql = f"SELECT Id FROM Profile WHERE Name = '{name_escaped}' LIMIT 1"
    url = f"{base}/services/data/{API_VERSION}/query?q={quote(soql, safe='')}"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return None
        records = (resp.json() or {}).get("records") or []
        if not records:
            return None
        profile_id = records[0].get("Id") or records[0].get("id")
        if not profile_id:
            return None
        return await _get_permission_set_id_for_profile(access_token, base, profile_id)
    except Exception as e:
        print(f"      [TOOLING-API] get_permission_set_id_by_profile_name error: {e}")
        return None


async def get_permission_set_id_by_name(
    access_token: str, instance_url: str, permission_set_name: str
) -> Optional[str]:
    """Resolve a standalone PermissionSet by Name (API name) on the given org."""
    if not permission_set_name or not (access_token and instance_url):
        return None
    base = (instance_url or "").rstrip("/")
    name_escaped = _soql_escape(permission_set_name)
    soql = (
        f"SELECT Id FROM PermissionSet WHERE Name = '{name_escaped}' "
        "AND IsOwnedByProfile = false LIMIT 1"
    )
    url = f"{base}/services/data/{API_VERSION}/query?q={quote(soql, safe='')}"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return None
        records = (resp.json() or {}).get("records") or []
        if not records:
            return None
        return records[0].get("Id") or records[0].get("id")
    except Exception as e:
        print(f"      [TOOLING-API] get_permission_set_id_by_name error: {e}")
        return None


async def set_field_permissions_from_grants(
    access_token: str,
    instance_url: str,
    object_api_name: str,
    field_full_name: str,
    grants: List[Dict[str, Any]],
) -> int:
    """
    Create FieldPermissions on the given org for each grant. Each grant must have
    profile_name (for profile-owned) or permission_set_name (for standalone), and
    PermissionsRead, PermissionsEdit. Returns the number of FLS records created (or already present).
    """
    base = (instance_url or "").rstrip("/")
    if not base or not access_token or not grants:
        return 0
    url = f"{base}/services/data/{API_VERSION}/sobjects/FieldPermissions"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    created = 0
    for g in grants:
        parent_id = None
        if g.get("is_profile_owned") and g.get("profile_name"):
            parent_id = await get_permission_set_id_by_profile_name(
                access_token, base, g["profile_name"]
            )
        elif g.get("permission_set_name"):
            parent_id = await get_permission_set_id_by_name(
                access_token, base, g["permission_set_name"]
            )
        if not parent_id:
            continue
        body = {
            "ParentId": parent_id,
            "SobjectType": object_api_name,
            "Field": field_full_name,
            "PermissionsRead": bool(g.get("PermissionsRead", False)),
            "PermissionsEdit": bool(g.get("PermissionsEdit", False)),
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, headers=headers, json=body)
            if resp.status_code in (200, 201):
                created += 1
            elif resp.status_code == 400 and "DUPLICATE" in (resp.text or ""):
                created += 1  # already exists
        except Exception as e:
            print(f"      [TOOLING-API] FLS grant failed for {field_full_name}: {e}")
    return created


async def get_record_types_for_object(
    access_token: str,
    instance_url: str,
    object_api_name: str,
) -> List[Dict[str, Any]]:
    """
    Query all non-master record types for an object from the given org.
    Uses the standard REST API query endpoint (not Tooling API) because the Tooling API
    RecordType entity does not expose DeveloperName, IsActive, or IsPersonType via SOQL.
    Returns list of dicts: {Id, DeveloperName, Name, Description, IsActive, IsPersonType}.
    """
    base = (instance_url or "").rstrip("/")
    obj_escaped = _soql_escape(object_api_name)
    # Only Id, Name, DeveloperName, Description are safely queryable on RecordType via SOQL.
    # IsMaster, IsActive, IsPersonType are NOT filterable/selectable in SOQL -- filter in Python.
    # Master record type always has DeveloperName = 'Master'; exclude it after fetch.
    # IsPersonType is derived: DeveloperName = 'PersonAccount' means it is a Person Account type.
    soql = (
        "SELECT Id, DeveloperName, Name, Description "
        f"FROM RecordType WHERE SobjectType = '{obj_escaped}'"
    )
    url = f"{base}/services/data/{API_VERSION}/query?q={quote(soql, safe='')}"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            print(f"      [TOOLING-API] get_record_types_for_object failed: {resp.status_code} {resp.text[:200]}")
            return []
        records = (resp.json() or {}).get("records") or []
        result = []
        for r in records:
            dev_name = r.get("DeveloperName") or ""
            if not dev_name:
                continue
            # Skip the master record type -- it always has DeveloperName = 'Master'
            if dev_name.lower() == "master":
                continue
            result.append({
                "Id": r.get("Id"),
                "DeveloperName": dev_name,
                "Name": r.get("Name") or dev_name,
                "Description": r.get("Description") or "",
                "IsActive": True,
                "IsPersonType": dev_name == "PersonAccount",
            })
        return result
    except Exception as e:
        print(f"      [TOOLING-API] get_record_types_for_object error: {e}")
        return []


async def get_record_type_id_on_dest(
    access_token: str,
    instance_url: str,
    object_api_name: str,
    developer_name: str,
) -> Optional[str]:
    """
    Check if a RecordType with the given DeveloperName already exists on the destination org.
    Uses standard REST API (not Tooling API) -- DeveloperName is not filterable via Tooling SOQL.
    Returns the record Id if found, None otherwise.
    """
    base = (instance_url or "").rstrip("/")
    obj_escaped = _soql_escape(object_api_name)
    dev_escaped = _soql_escape(developer_name)
    soql = (
        "SELECT Id FROM RecordType "
        f"WHERE SobjectType = '{obj_escaped}' AND DeveloperName = '{dev_escaped}' LIMIT 1"
    )
    url = f"{base}/services/data/{API_VERSION}/query?q={quote(soql, safe='')}"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return None
        records = (resp.json() or {}).get("records") or []
        if not records:
            return None
        return records[0].get("Id") or records[0].get("id")
    except Exception as e:
        print(f"      [TOOLING-API] get_record_type_id_on_dest error: {e}")
        return None


async def create_or_update_record_type(
    access_token: str,
    instance_url: str,
    object_api_name: str,
    developer_name: str,
    name: str,
    description: str = "",
    is_active: bool = True,
    is_person_type: bool = False,
) -> Dict[str, Any]:
    """
    Create or update a RecordType on the destination org via Tooling API.
    FullName format: ObjectApiName.DeveloperName
    Returns dict with success, action ('created'|'updated'|'skipped'), id, error.

    Note: IsPersonType record types (Person Accounts) require the PersonAccounts feature
    to be enabled on the destination org. We attempt the deploy and surface the error if it fails.
    """
    base = (instance_url or "").rstrip("/")
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    full_name = f"{object_api_name}.{developer_name}"

    metadata: Dict[str, Any] = {
        "label": name,
        "active": is_active,
    }
    if description:
        metadata["description"] = description

    # Check if record type already exists on destination
    existing_id = await get_record_type_id_on_dest(access_token, instance_url, object_api_name, developer_name)

    if existing_id:
        # Update existing record type
        url = f"{base}/services/data/{API_VERSION}/tooling/sobjects/RecordType/{existing_id}"
        body = {"Metadata": metadata}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.patch(url, headers=headers, json=body)
            if resp.status_code in (200, 204):
                return {"success": True, "action": "updated", "id": existing_id}
            return {"success": False, "action": "update_failed", "id": existing_id, "error": resp.text[:500]}
        except Exception as e:
            return {"success": False, "action": "update_failed", "id": existing_id, "error": str(e)}
    else:
        # Create new record type
        url = f"{base}/services/data/{API_VERSION}/tooling/sobjects/RecordType/"
        body = {"FullName": full_name, "Metadata": metadata}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, headers=headers, json=body)
            if resp.status_code in (200, 201):
                new_id = (resp.json() or {}).get("id")
                return {"success": True, "action": "created", "id": new_id}
            # If destination doesn't have PersonAccounts enabled, surface a clear error
            err_text = resp.text[:500]
            if is_person_type and "INVALID_TYPE" in err_text:
                return {
                    "success": False,
                    "action": "skipped",
                    "error": f"Person Account record type '{developer_name}' skipped: PersonAccounts feature not enabled on destination org.",
                }
            return {"success": False, "action": "create_failed", "error": err_text}
        except Exception as e:
            return {"success": False, "action": "create_failed", "error": str(e)}



def _parse_custom_object_developer_name(object_api_name: str) -> Dict[str, Optional[str]]:
    """
    Parse a Salesforce custom object API name into NamespacePrefix + DeveloperName.

    Examples:
      - Leave__c       -> namespace=None, developer_name='Leave'
      - ns__Leave__c   -> namespace='ns', developer_name='Leave'

    Returns {"namespace": ..., "developer_name": ...} or both None if not a custom object.
    """
    api = (object_api_name or "").strip()
    if not api.endswith("__c"):
        return {"namespace": None, "developer_name": None}
    base = api[:-3]
    if "__" in base:
        ns, dev = base.split("__", 1)
        return {"namespace": ns or None, "developer_name": dev or None}
    return {"namespace": None, "developer_name": base or None}


async def get_custom_object_metadata(
    access_token: str,
    instance_url: str,
    object_api_name: str,
) -> Optional[Dict[str, Any]]:
    """
    Fetch custom object metadata from the source org using EntityDefinition
    (Tooling API SOQL). EntityDefinition exposes Label, Description,
    ExternalSharingModel, InternalSharingModel, DeploymentStatus and is
    queryable by QualifiedApiName — no null-namespace issues.

    Returns a dict suitable for create_or_update_custom_object, or None on failure.
    """
    base = (instance_url or "").rstrip("/")
    if not (object_api_name or "").endswith("__c"):
        print(f"      [TOOLING-API] get_custom_object_metadata: {object_api_name} is not a custom object")
        return None

    parsed = _parse_custom_object_developer_name(object_api_name)
    developer_name = parsed.get("developer_name") or object_api_name

    soql = (
        "SELECT QualifiedApiName, Label, Description, "
        "ExternalSharingModel, InternalSharingModel, DeploymentStatus, NamespacePrefix "
        f"FROM EntityDefinition WHERE QualifiedApiName = '{_soql_escape(object_api_name)}'"
    )
    url = f"{base}/services/data/{API_VERSION}/tooling/query?q={quote(soql, safe='')}"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)
        print(f"      [TOOLING-API] get_custom_object_metadata status={resp.status_code} for {object_api_name}")
        if resp.status_code != 200:
            print(f"      [TOOLING-API] get_custom_object_metadata failed: {resp.text[:300]}")
            return None
        records = (resp.json() or {}).get("records") or []
        if not records:
            print(f"      [TOOLING-API] get_custom_object_metadata: no EntityDefinition found for {object_api_name}")
            return None
        rec = records[0]
        label = rec.get("Label") or developer_name
        # ExternalSharingModel is the OWD for external users; InternalSharingModel for internal.
        # For the create payload Salesforce expects a single "sharingModel" — use internal.
        sharing_model = rec.get("InternalSharingModel") or rec.get("ExternalSharingModel") or "ReadWrite"
        deployment_status = rec.get("DeploymentStatus") or "Deployed"
        description = rec.get("Description") or ""

        result: Dict[str, Any] = {
            "label": label,
            "pluralLabel": label + "s",
            "sharingModel": sharing_model,
            "deploymentStatus": deployment_status,
            "nameField": {"label": label + " Name", "type": "Text"},
        }
        if description:
            result["description"] = description

        print(f"      [TOOLING-API] get_custom_object_metadata: found {object_api_name} label='{label}' sharingModel='{sharing_model}'")
        return result
    except Exception as e:
        print(f"      [TOOLING-API] get_custom_object_metadata error: {e}")
        return None


async def create_or_update_custom_field(
    access_token: str,
    instance_url: str,
    full_name: str,
    metadata: Dict[str, Any],
    object_api_name: Optional[str] = None,
    field_api_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create or update CustomField on destination. If field exists (by Id or DUPLICATE_DEVELOPER_NAME), PATCH to update (e.g. new picklist values).
    Returns dict with success, action ('created'|'updated'), id (if created), and optional error.
    """
    metadata = _sanitize_metadata_for_api(metadata)
    # Resolve existing field Id on destination via SOQL
    field_id = await get_custom_field_id(
        access_token, instance_url, full_name,
        object_api_name=object_api_name,
        field_api_name=field_api_name,
    )
    if field_id:
        result = await update_custom_field(access_token, instance_url, field_id, metadata)
        result["action"] = "updated"
        result["id"] = field_id
        return result
    result = await create_custom_field(access_token, instance_url, full_name, metadata)
    if result.get("success"):
        result["action"] = "created"
        # FLS is applied by the caller: copy from source or set default (see metadata_deploy_background_service)
        return result
    # Field may already exist; retry as update (e.g. to sync picklist values)
    if _is_duplicate_developer_name_error(result.get("error") or ""):
        field_id = await get_custom_field_id(
            access_token, instance_url, full_name,
            object_api_name=object_api_name,
            field_api_name=field_api_name,
        )
        if field_id:
            result = await update_custom_field(access_token, instance_url, field_id, metadata)
            result["action"] = "updated"
            result["id"] = field_id
            return result
    result["action"] = "create_failed"
    return result
