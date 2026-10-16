"""
Salesforce Metadata API — retrieve (source) and deploy (destination) for CustomObject + fields.
Also supports unpackaged retrieve for DuplicateRule + MatchingRule (org rules cache for data migration UX).

Used when the object does not exist in destination: retrieve from source, deploy zip to destination.
Phase 6 of Salesforce metadata/field deploy flow.
"""

import asyncio
import base64
import io
import json
import re
import zipfile
from typing import Any, Dict, List, Optional, Tuple

import httpx

API_VERSION = "v59.0"
VERSION_NUM = "59.0"

# package.xml namespace and structure for Metadata API
PACKAGE_XML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<Package xmlns="http://soap.sforce.com/2006/04/metadata">
  <types>
    <members>{object_api_name}</members>
    <name>CustomObject</name>
  </types>
  <version>{version}</version>
</Package>"""

# Minimal package.xml for destructive-only deploy (no types, just version)
PACKAGE_XML_MINIMAL = """<?xml version="1.0" encoding="UTF-8"?>
<Package xmlns="http://soap.sforce.com/2006/04/metadata">
  <version>{version}</version>
</Package>"""

# destructiveChanges.xml: list CustomField full names to delete (ObjectName.FieldName__c)
DESTRUCTIVE_CHANGES_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<Package xmlns="http://soap.sforce.com/2006/04/metadata">
  <types>
{members}
    <name>CustomField</name>
  </types>
  <version>{version}</version>
</Package>"""

# destructiveChanges.xml for deleting a CustomObject
DESTRUCTIVE_CHANGES_OBJECT_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<Package xmlns="http://soap.sforce.com/2006/04/metadata">
  <types>
    <members>{object_api_name}</members>
    <name>CustomObject</name>
  </types>
  <version>{version}</version>
</Package>"""


async def _metadata_retrieve_post_and_poll(
    access_token: str,
    instance_url: str,
    body: Dict[str, Any],
    poll_interval_seconds: float = 2.0,
    max_wait_seconds: float = 180.0,
) -> Tuple[bool, Optional[bytes], Optional[str]]:
    """
    Submit Metadata REST retrieveRequest with JSON body, poll until zip is ready.
    Returns (success, zip_bytes, error_message).
    """
    base = (instance_url or "").rstrip("/")
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    url = f"{base}/services/data/{API_VERSION}/metadata/retrieveRequest"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, headers=headers, json=body)
    if resp.status_code not in (200, 201):
        return False, None, f"Retrieve submit failed: {resp.status_code} {resp.text[:500]}"
    try:
        data = resp.json()
    except Exception as e:
        return False, None, f"Retrieve response not JSON: {e}"
    job_id = data.get("id") or data.get("Id")
    if not job_id:
        return False, None, "Retrieve response missing id"

    status_url = f"{base}/services/data/{API_VERSION}/metadata/retrieveRequest/{job_id}"
    waited = 0.0
    while waited < max_wait_seconds:
        await asyncio.sleep(poll_interval_seconds)
        waited += poll_interval_seconds
        async with httpx.AsyncClient(timeout=30) as client:
            status_resp = await client.get(status_url, headers=headers)
        if status_resp.status_code != 200:
            return False, None, f"Retrieve status failed: {status_resp.status_code}"
        try:
            status_data = status_resp.json()
        except Exception as e:
            return False, None, f"Retrieve status not JSON: {e}"
        status = (status_data.get("status") or status_data.get("Status") or "").lower()
        if status == "done" or status == "succeeded":
            zip_b64 = status_data.get("zipFile")
            if zip_b64:
                try:
                    zip_bytes = base64.b64decode(zip_b64)
                    return True, zip_bytes, None
                except Exception as e:
                    return False, None, f"Decode zip: {e}"
            success = (status_data.get("success") or status_data.get("Success")) is not False
            if success and status == "done":
                return False, None, "Retrieve done but zip not in response (check API)"
        if status in ("failed", "error", "cancelled", "canceled"):
            msg = status_data.get("error") or status_data.get("message") or status_data.get("errorMessage") or status
            return False, None, f"Retrieve failed: {msg}"
        continue
    return False, None, f"Retrieve timed out after {max_wait_seconds}s"


def extract_metadata_zip_files_as_text(zip_bytes: bytes) -> Dict[str, str]:
    """
    Expand a Metadata retrieve result zip to path -> UTF-8 text.
    Skips package.xml; used for DuplicateRule / MatchingRule XML (parse in a later phase).
    """
    out: Dict[str, str] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in zf.namelist():
                if name.endswith("/") or name == "package.xml":
                    continue
                try:
                    raw = zf.read(name)
                    out[name] = raw.decode("utf-8", errors="replace")
                except Exception:
                    continue
    except Exception:
        return out
    return out


def _escape_xml_text(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _build_soap_unpackaged_types_xml(types_members: List[Tuple[str, List[str]]]) -> str:
    blocks: List[str] = []
    for type_name, members in types_members:
        if not members:
            continue
        mem_lines = "\n".join(
            f"          <met:members>{_escape_xml_text(m)}</met:members>" for m in members
        )
        blocks.append(
            f"""        <met:types>
{mem_lines}
          <met:name>{_escape_xml_text(type_name)}</met:name>
        </met:types>"""
        )
    return "\n".join(blocks)


def _soap_first_local_tag_text(xml_text: str, local: str) -> Optional[str]:
    """First element whose local name matches (handles met:id, id, etc.)."""
    if not xml_text:
        return None
    m = re.search(
        rf"<(?:[^/>: ]+:)?{re.escape(local)}>([^<]*)</(?:[^/>: ]+:)?{re.escape(local)}>",
        xml_text,
        re.IGNORECASE | re.DOTALL,
    )
    return m.group(1).strip() if m else None


def _soap_retrieve_async_id(xml_text: str) -> Optional[str]:
    """Async process id from retrieveResponse (prefer id inside result/retrieveResult)."""
    if not xml_text:
        return None
    m = re.search(
        r"<(?:[^:>]+:)?(?:retrieveResult|result)[^>]*>.*?<(?:[^:>]+:)?id>([a-zA-Z0-9]{15,18})</(?:[^:>]+:)?id>",
        xml_text,
        re.DOTALL | re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    return _soap_first_local_tag_text(xml_text, "id")


def _soap_zip_file_base64(xml_text: str) -> Optional[str]:
    m = re.search(
        r"<(?:[^:>]+:)?zipFile>([\s\S]*?)</(?:[^:>]+:)?zipFile>",
        xml_text,
        re.IGNORECASE,
    )
    if not m:
        return None
    return re.sub(r"\s+", "", m.group(1).strip())


async def _metadata_retrieve_soap_unpackaged(
    access_token: str,
    instance_url: str,
    types_members: List[Tuple[str, List[str]]],
    poll_interval_seconds: float = 2.0,
    max_wait_seconds: float = 180.0,
) -> Tuple[bool, Optional[bytes], Optional[str]]:
    """
    Metadata SOAP retrieve + checkRetrieveStatus (works when REST /metadata/retrieveRequest returns 404,
    e.g. many Developer Edition orgs — same pattern as create_custom_object_via_soap).
    """
    filtered = [(n, m) for n, m in types_members if m]
    if not filtered:
        return True, None, None

    base = (instance_url or "").rstrip("/")
    soap_url = f"{base}/services/Soap/m/{VERSION_NUM}"
    headers = {
        "Content-Type": "text/xml; charset=UTF-8",
        "SOAPAction": '""',
    }
    unpack_xml = _build_soap_unpackaged_types_xml(filtered)
    retrieve_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope
    xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:met="http://soap.sforce.com/2006/04/metadata">
  <soapenv:Header>
    <met:CallOptions/>
    <met:SessionHeader>
      <met:sessionId>{access_token}</met:sessionId>
    </met:SessionHeader>
  </soapenv:Header>
  <soapenv:Body>
    <met:retrieve>
      <met:retrieveRequest>
        <met:apiVersion>{VERSION_NUM}</met:apiVersion>
        <met:singlePackage>true</met:singlePackage>
        <met:unpackaged>
{unpack_xml}
        </met:unpackaged>
      </met:retrieveRequest>
    </met:retrieve>
  </soapenv:Body>
</soapenv:Envelope>"""

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(soap_url, headers=headers, content=retrieve_body.encode("utf-8"))
    except Exception as e:
        return False, None, f"SOAP retrieve POST failed: {e}"

    if resp.status_code not in (200, 201):
        return False, None, f"SOAP retrieve HTTP {resp.status_code}: {resp.text[:600]}"
    text = resp.text or ""
    fault = _extract_soap_fault(text)
    if fault:
        return False, None, f"SOAP retrieve fault: {fault}"

    async_id = _soap_retrieve_async_id(text)
    if not async_id:
        return False, None, "SOAP retrieve: could not parse async id from response"

    waited = 0.0
    while waited < max_wait_seconds:
        await asyncio.sleep(poll_interval_seconds)
        waited += poll_interval_seconds

        check_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope
    xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:met="http://soap.sforce.com/2006/04/metadata">
  <soapenv:Header>
    <met:SessionHeader>
      <met:sessionId>{access_token}</met:sessionId>
    </met:SessionHeader>
  </soapenv:Header>
  <soapenv:Body>
    <met:checkRetrieveStatus>
      <met:asyncProcessId>{async_id}</met:asyncProcessId>
    </met:checkRetrieveStatus>
  </soapenv:Body>
</soapenv:Envelope>"""

        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(soap_url, headers=headers, content=check_body.encode("utf-8"))
        except Exception as e:
            return False, None, f"checkRetrieveStatus failed: {e}"

        if resp.status_code not in (200, 201):
            return False, None, f"checkRetrieveStatus HTTP {resp.status_code}"

        ct = resp.text or ""
        fault = _extract_soap_fault(ct)
        if fault:
            return False, None, f"checkRetrieveStatus fault: {fault}"

        done_raw = _soap_first_local_tag_text(ct, "done")
        if not done_raw or done_raw.lower() != "true":
            continue

        status_raw = (_soap_first_local_tag_text(ct, "status") or "").lower()
        if status_raw in ("failed", "error"):
            err_msg = (
                _soap_first_local_tag_text(ct, "errorMessage")
                or _soap_first_local_tag_text(ct, "message")
                or status_raw
            )
            return False, None, f"Retrieve status failed: {err_msg}"

        zf_b64 = _soap_zip_file_base64(ct)
        if zf_b64:
            try:
                return True, base64.b64decode(zf_b64), None
            except Exception as e:
                return False, None, f"Decode zip from SOAP: {e}"
        return False, None, "Retrieve done but zipFile empty"

    return False, None, f"SOAP retrieve timed out after {max_wait_seconds}s"


def _rest_metadata_not_available(err: Optional[str]) -> bool:
    if not err:
        return False
    return "404" in err and ("NOT_FOUND" in err or "does not exist" in err.lower())


async def metadata_retrieve_unpackaged_types(
    access_token: str,
    instance_url: str,
    types_members: List[Tuple[str, List[str]]],
    poll_interval_seconds: float = 2.0,
    max_wait_seconds: float = 180.0,
) -> Tuple[bool, Optional[Dict[str, str]], Optional[str]]:
    """
    Retrieve arbitrary metadata types (e.g. DuplicateRule, MatchingRule) in one job.
    types_members: [("DuplicateRule", ["Account.MyRule", ...]), ("MatchingRule", ["Account.MyMatch", ...])]
    Returns (success, path_to_xml_text, error_message).

    Uses Metadata REST first; on 404 (common in Developer Edition), falls back to SOAP retrieve.
    """
    version_num = API_VERSION.replace("v", "")
    types_payload: List[Dict[str, Any]] = []
    for type_name, members in types_members:
        if members:
            types_payload.append({"name": type_name, "members": members})
    if not types_payload:
        return True, {}, None

    body = {
        "singlePackage": True,
        "unpackaged": {
            "types": types_payload,
            "version": version_num,
        },
    }
    ok, zip_bytes, err = await _metadata_retrieve_post_and_poll(
        access_token,
        instance_url,
        body,
        poll_interval_seconds=poll_interval_seconds,
        max_wait_seconds=max_wait_seconds,
    )

    if (not ok or not zip_bytes) and _rest_metadata_not_available(err):
        ok2, zip_bytes2, err2 = await _metadata_retrieve_soap_unpackaged(
            access_token,
            instance_url,
            types_members,
            poll_interval_seconds=poll_interval_seconds,
            max_wait_seconds=max_wait_seconds,
        )
        if ok2 and zip_bytes2:
            files = extract_metadata_zip_files_as_text(zip_bytes2)
            return True, files, None
        return False, None, f"REST Metadata unavailable ({err}); SOAP fallback failed: {err2}"

    if not ok or not zip_bytes:
        return False, None, err or "Retrieve failed"
    files = extract_metadata_zip_files_as_text(zip_bytes)
    return True, files, None


def build_retrieve_zip(object_api_name: str, version: str = "59.0") -> bytes:
    """Build a zip containing only package.xml at root (for retrieve request)."""
    version_num = (version or "59").replace("v", "")
    package_xml = PACKAGE_XML_TEMPLATE.format(object_api_name=object_api_name, version=version_num)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("package.xml", package_xml)
    return buf.getvalue()


def build_destructive_changes_zip(custom_field_full_names: List[str], version: str = VERSION_NUM) -> bytes:
    """
    Build a zip for Metadata API destructive deploy to delete CustomFields.
    Zip contains package.xml (minimal) and destructiveChanges.xml with CustomField members.
    full_names format: ['Account.Lead__c', 'Account.Date_Time__c']
    """
    version_num = (version or "59").replace("v", "")
    members_xml = "\n".join(f"    <members>{fn}</members>" for fn in custom_field_full_names)
    destructive_xml = DESTRUCTIVE_CHANGES_TEMPLATE.format(members=members_xml, version=version_num)
    package_xml = PACKAGE_XML_MINIMAL.format(version=version_num)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("package.xml", package_xml)
        zf.writestr("destructiveChanges.xml", destructive_xml)
    return buf.getvalue()


async def metadata_retrieve(
    access_token: str,
    instance_url: str,
    object_api_name: str,
    poll_interval_seconds: float = 2.0,
    max_wait_seconds: float = 120.0,
) -> Tuple[bool, Optional[bytes], Optional[str]]:
    """
    Retrieve CustomObject metadata from org: submit retrieve request, poll until done, return zip bytes.
    Returns (success, zip_bytes, error_message).
    Uses REST: POST retrieveRequest with unpackaged JSON body; poll GET retrieveRequest/{id}; fetch result zip.
    """
    version_num = API_VERSION.replace("v", "")
    body = {
        "singlePackage": True,
        "unpackaged": {
            "types": [{"name": "CustomObject", "members": [object_api_name]}],
            "version": version_num,
        },
    }
    return await _metadata_retrieve_post_and_poll(
        access_token,
        instance_url,
        body,
        poll_interval_seconds=poll_interval_seconds,
        max_wait_seconds=max_wait_seconds,
    )


async def metadata_retrieve_with_zip(
    access_token: str,
    instance_url: str,
    object_api_name: str,
    poll_interval_seconds: float = 2.0,
    max_wait_seconds: float = 120.0,
) -> Tuple[bool, Optional[bytes], Optional[str]]:
    """
    Alternative: submit retrieve using a zip that contains only package.xml.
    Some REST implementations accept multipart with zip for retrieve.
    """
    zip_bytes = build_retrieve_zip(object_api_name)
    base = (instance_url or "").rstrip("/")
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{base}/services/data/{API_VERSION}/metadata/retrieveRequest"
    files = {"file": ("package.zip", zip_bytes, "application/zip")}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, headers=headers, files=files)
    if resp.status_code not in (200, 201):
        return False, None, f"Retrieve (zip) failed: {resp.status_code} {resp.text[:500]}"
    try:
        data = resp.json()
    except Exception as e:
        return False, None, f"Retrieve response not JSON: {e}"
    job_id = data.get("id") or data.get("Id")
    if not job_id:
        return False, None, "Retrieve response missing id"
    # Poll (same as metadata_retrieve)
    status_url = f"{base}/services/data/{API_VERSION}/metadata/retrieveRequest/{job_id}"
    waited = 0.0
    while waited < max_wait_seconds:
        await asyncio.sleep(poll_interval_seconds)
        waited += poll_interval_seconds
        async with httpx.AsyncClient(timeout=30) as client:
            status_resp = await client.get(status_url, headers=headers)
        if status_resp.status_code != 200:
            return False, None, f"Retrieve status failed: {status_resp.status_code}"
        try:
            status_data = status_resp.json()
        except Exception as e:
            return False, None, f"Retrieve status not JSON: {e}"
        status = (status_data.get("status") or status_data.get("Status") or "").lower()
        if status == "done" or status == "succeeded":
            zip_b64 = status_data.get("zipFile")
            if zip_b64:
                try:
                    return True, base64.b64decode(zip_b64), None
                except Exception as e:
                    return False, None, f"Decode zip: {e}"
        if status in ("failed", "error", "cancelled", "canceled"):
            msg = status_data.get("error") or status_data.get("message") or status
            return False, None, f"Retrieve failed: {msg}"
        continue
    return False, None, f"Retrieve timed out after {max_wait_seconds}s"


async def metadata_deploy(
    access_token: str,
    instance_url: str,
    zip_bytes: bytes,
    rollback_on_error: bool = True,
    poll_interval_seconds: float = 2.0,
    max_wait_seconds: float = 300.0,
) -> Dict[str, Any]:
    """
    Deploy metadata zip to org. Uses REST multipart: entity_content (JSON) + file (zip).
    Returns dict with success, status, numberDeployed, numberFailed, details, error.
    """
    base = (instance_url or "").rstrip("/")
    url = f"{base}/services/data/{API_VERSION}/metadata/deployRequest"
    headers = {"Authorization": f"Bearer {access_token}"}
    # REST API expects entity_content with deployOptions wrapper
    entity_content = {"deployOptions": {"rollbackOnError": rollback_on_error, "singlePackage": True}}
    files = [
        ("entity_content", (None, json.dumps(entity_content).encode("utf-8"), "application/json")),
        ("file", ("deploy.zip", zip_bytes, "application/zip")),
    ]
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, headers=headers, files=files)
    if resp.status_code not in (200, 201):
        return {"success": False, "error": resp.text[:500], "status_code": resp.status_code}
    try:
        data = resp.json()
    except Exception as e:
        return {"success": False, "error": str(e)}
    job_id = data.get("id") or data.get("Id")
    if not job_id:
        return {"success": False, "error": "Deploy response missing id"}

    status_url = f"{base}/services/data/{API_VERSION}/metadata/deployRequest/{job_id}"
    waited = 0.0
    while waited < max_wait_seconds:
        await asyncio.sleep(poll_interval_seconds)
        waited += poll_interval_seconds
        async with httpx.AsyncClient(timeout=30) as client:
            status_resp = await client.get(status_url, headers=headers)
        if status_resp.status_code != 200:
            return {"success": False, "error": f"Deploy status failed: {status_resp.status_code}"}
        try:
            status_data = status_resp.json()
        except Exception as e:
            return {"success": False, "error": f"Deploy status not JSON: {e}"}
        # REST returns deploy outcome under "deployResult" (v59+); older use "result" or top-level
        result = status_data.get("deployResult") or status_data.get("result") or status_data
        status = (result.get("status") or result.get("Status") or status_data.get("status") or status_data.get("Status") or "").lower()
        if status in ("succeeded", "done"):
            return {
                "success": True,
                "status": status,
                "numberDeployed": result.get("numberComponentsDeployed") or result.get("numberDeployed") or 0,
                "numberFailed": result.get("numberComponentErrors") or result.get("numberFailed") or 0,
                "details": result.get("details") or result if isinstance(result, dict) else status_data,
            }
        if status in ("failed", "error", "cancelled", "canceled"):
            return {
                "success": False,
                "status": status,
                "error": result.get("error") or result.get("message") or result.get("errorMessage") or status,
                "details": result.get("details") or result if isinstance(result, dict) else status_data,
            }
        continue
    return {"success": False, "error": f"Deploy timed out after {max_wait_seconds}s"}


async def create_custom_object_via_soap(
    access_token: str,
    instance_url: str,
    object_api_name: str,
    label: str,
    plural_label: str,
    sharing_model: str = "ReadWrite",
    deployment_status: str = "Deployed",
    description: str = "",
) -> Dict[str, Any]:
    """
    Create a custom object on the destination org using the Metadata SOAP API
    (createMetadata call at /services/Soap/m/{version}).

    This is the only approach that works across all org types including Developer Edition,
    where the Metadata REST API (/services/data/vXX/metadata/retrieveRequest) returns 404.

    Returns dict with success, action, error.
    """
    base = (instance_url or "").rstrip("/")
    soap_url = f"{base}/services/Soap/m/{VERSION_NUM}"
    headers = {
        "Content-Type": "text/xml; charset=UTF-8",
        "SOAPAction": '""',
    }

    name_field_label = f"{label} Name"
    description_xml = f"<description>{description}</description>" if description else ""

    soap_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope
    xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:met="http://soap.sforce.com/2006/04/metadata">
  <soapenv:Header>
    <met:CallOptions/>
    <met:SessionHeader>
      <met:sessionId>{access_token}</met:sessionId>
    </met:SessionHeader>
  </soapenv:Header>
  <soapenv:Body>
    <met:createMetadata>
      <met:metadata xsi:type="met:CustomObject"
          xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
        <met:fullName>{object_api_name}</met:fullName>
        <met:label>{label}</met:label>
        <met:pluralLabel>{plural_label}</met:pluralLabel>
        <met:deploymentStatus>{deployment_status}</met:deploymentStatus>
        <met:sharingModel>{sharing_model}</met:sharingModel>
        {description_xml}
        <met:nameField>
          <met:label>{name_field_label}</met:label>
          <met:type>Text</met:type>
        </met:nameField>
      </met:metadata>
    </met:createMetadata>
  </soapenv:Body>
</soapenv:Envelope>"""

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(soap_url, headers=headers, content=soap_body.encode("utf-8"))
        print(f"      [METADATA-SOAP] create_custom_object status={resp.status_code} for {object_api_name}")

        if resp.status_code not in (200, 201):
            # SOAP faults come back as 500 with XML body
            body_text = resp.text or ""
            fault = _extract_soap_fault(body_text)
            return {"success": False, "action": "create_failed", "error": fault or body_text[:300]}

        body_text = resp.text or ""
        # Check for SOAP fault in 200 response (Salesforce sometimes returns 200 with fault)
        fault = _extract_soap_fault(body_text)
        if fault:
            return {"success": False, "action": "create_failed", "error": fault}

        # Check for success indicator in response
        if "<success>true</success>" in body_text:
            print(f"      [METADATA-SOAP] ✅ {object_api_name} created successfully")
            return {"success": True, "action": "created"}

        # If no explicit success/fault, treat as success if status 200
        print(f"      [METADATA-SOAP] ✅ {object_api_name} created (status 200, no fault)")
        return {"success": True, "action": "created"}

    except Exception as e:
        return {"success": False, "action": "create_failed", "error": str(e)}


def _extract_soap_fault(xml_text: str) -> Optional[str]:
    """Extract faultstring from a SOAP fault response body."""
    import re
    match = re.search(r"<faultstring[^>]*>(.*?)</faultstring>", xml_text, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"<message[^>]*>(.*?)</message>", xml_text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


async def delete_custom_object_via_metadata_api(
    access_token: str,
    instance_url: str,
    object_api_name: str,
    poll_interval_seconds: float = 3.0,
    max_wait_seconds: float = 300.0,
) -> Dict[str, Any]:
    """
    Delete a CustomObject using Metadata API destructive deploy.
    All fields on the object must be deleted first before calling this.
    Uses purgeOnDelete=True for permanent deletion (bypasses recycle bin).
    Returns dict with success, error.
    """
    if not (object_api_name or "").endswith("__c"):
        return {"success": False, "error": f"{object_api_name} is not a custom object"}

    version_num = (VERSION_NUM or "59").replace("v", "")
    destructive_xml = DESTRUCTIVE_CHANGES_OBJECT_TEMPLATE.format(
        object_api_name=object_api_name,
        version=version_num,
    )
    package_xml = PACKAGE_XML_MINIMAL.format(version=version_num)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("package.xml", package_xml)
        zf.writestr("destructiveChanges.xml", destructive_xml)
    zip_bytes = buf.getvalue()

    base = (instance_url or "").rstrip("/")
    url = f"{base}/services/data/{API_VERSION}/metadata/deployRequest"
    headers = {"Authorization": f"Bearer {access_token}"}
    entity_content = {
        "deployOptions": {
            "rollbackOnError": False,
            "singlePackage": True,
            "purgeOnDelete": True,
        }
    }
    files = [
        ("entity_content", (None, json.dumps(entity_content).encode("utf-8"), "application/json")),
        ("file", ("deploy.zip", zip_bytes, "application/zip")),
    ]
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(url, headers=headers, files=files)
        if resp.status_code not in (200, 201):
            return {"success": False, "error": resp.text[:500], "status_code": resp.status_code}
        data = resp.json()
    except Exception as e:
        return {"success": False, "error": str(e)}

    job_id = data.get("id") or data.get("Id")
    if not job_id:
        return {"success": False, "error": "Deploy response missing id"}

    status_url = f"{base}/services/data/{API_VERSION}/metadata/deployRequest/{job_id}"
    waited = 0.0
    while waited < max_wait_seconds:
        await asyncio.sleep(poll_interval_seconds)
        waited += poll_interval_seconds
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                status_resp = await client.get(status_url, headers=headers)
            if status_resp.status_code != 200:
                return {"success": False, "error": f"Deploy status failed: {status_resp.status_code}"}
            status_data = status_resp.json()
        except Exception as e:
            return {"success": False, "error": f"Deploy status error: {e}"}

        result = status_data.get("deployResult") or status_data.get("result") or status_data
        status = (result.get("status") or result.get("Status") or
                  status_data.get("status") or status_data.get("Status") or "").lower()
        done = result.get("done") if isinstance(result.get("done"), bool) else status_data.get("done")

        if done is True or status in ("succeeded", "done"):
            failed = result.get("numberComponentErrors") or result.get("numberFailed") or 0
            if failed == 0:
                return {"success": True}
            err = (result.get("error") or result.get("message") or
                   result.get("errorMessage") or "Deploy completed with errors")
            return {"success": False, "error": str(err)}

        if status in ("failed", "error", "cancelled", "canceled"):
            err = (result.get("error") or result.get("message") or
                   result.get("errorMessage") or status_data.get("error") or status)
            return {"success": False, "error": str(err)}

    return {"success": False, "error": f"Delete custom object timed out after {max_wait_seconds}s"}


async def delete_custom_fields_via_metadata_api(
    access_token: str,
    instance_url: str,
    full_names: List[str],
    poll_interval_seconds: float = 3.0,
    max_wait_seconds: float = 300.0,
) -> Dict[str, Any]:
    """
    Delete CustomFields using Metadata API destructive deploy (Tooling API does not support DELETE on CustomField).
    full_names: e.g. ['Account.Lead__c', 'Account.Date_Time__c'].
    Returns dict with success, numberDeployed, numberFailed, error, details.
    """
    if not full_names:
        return {"success": True, "numberDeployed": 0, "numberFailed": 0}
    zip_bytes = build_destructive_changes_zip(full_names)
    base = (instance_url or "").rstrip("/")
    url = f"{base}/services/data/{API_VERSION}/metadata/deployRequest"
    headers = {"Authorization": f"Bearer {access_token}"}
    # REST API expects entity_content with deployOptions wrapper (top-level deployOptions key)
    entity_content = {
        "deployOptions": {
            "rollbackOnError": False,
            "singlePackage": True,
            "purgeOnDelete": True,
        }
    }
    files = [
        ("entity_content", (None, json.dumps(entity_content).encode("utf-8"), "application/json")),
        ("file", ("deploy.zip", zip_bytes, "application/zip")),
    ]
    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.post(url, headers=headers, files=files)
    if resp.status_code not in (200, 201):
        return {"success": False, "error": resp.text[:500], "status_code": resp.status_code, "numberDeployed": 0, "numberFailed": len(full_names)}
    try:
        data = resp.json()
    except Exception as e:
        return {"success": False, "error": str(e), "numberDeployed": 0, "numberFailed": len(full_names)}
    job_id = data.get("id") or data.get("Id")
    if not job_id:
        return {"success": False, "error": "Deploy response missing id", "numberDeployed": 0, "numberFailed": len(full_names)}

    status_url = f"{base}/services/data/{API_VERSION}/metadata/deployRequest/{job_id}"
    waited = 0.0
    while waited < max_wait_seconds:
        await asyncio.sleep(poll_interval_seconds)
        waited += poll_interval_seconds
        async with httpx.AsyncClient(timeout=30) as client:
            status_resp = await client.get(status_url, headers=headers)
        if status_resp.status_code != 200:
            return {"success": False, "error": f"Deploy status failed: {status_resp.status_code}", "numberDeployed": 0, "numberFailed": len(full_names)}
        try:
            status_data = status_resp.json()
        except Exception as e:
            return {"success": False, "error": f"Deploy status not JSON: {e}", "numberDeployed": 0, "numberFailed": len(full_names)}
        # REST returns deploy outcome under "deployResult" (v59+); older/docs use "result" or top-level
        result = status_data.get("deployResult") or status_data.get("result") or status_data
        status = (result.get("status") or result.get("Status") or status_data.get("status") or status_data.get("Status") or "").lower()
        done = result.get("done") if isinstance(result.get("done"), bool) else status_data.get("done")
        if done is True or status in ("succeeded", "done"):
            # Completed: read counts from result/status_data
            deployed = result.get("numberComponentsDeployed") or result.get("numberDeployed") or status_data.get("numberComponentsDeployed") or status_data.get("numberDeployed") or 0
            failed = result.get("numberComponentErrors") or result.get("numberFailed") or status_data.get("numberComponentErrors") or status_data.get("numberFailed") or 0
            is_success = status not in ("failed", "error", "cancelled", "canceled") and failed == 0
            err_msg = result.get("error") or result.get("message") or result.get("errorMessage") or status_data.get("error") or status_data.get("message") or ""
            return {
                "success": is_success,
                "status": status or ("succeeded" if is_success else "failed"),
                "numberDeployed": deployed,
                "numberFailed": failed,
                "error": None if is_success else (err_msg or "Deploy failed"),
                "details": result.get("details") or result if isinstance(result, dict) else status_data,
            }
        if status in ("failed", "error", "cancelled", "canceled"):
            return {
                "success": False,
                "status": status,
                "error": result.get("error") or result.get("message") or result.get("errorMessage") or status_data.get("error") or status_data.get("message") or status,
                "details": result.get("details") or result if isinstance(result, dict) else status_data,
                "numberDeployed": result.get("numberComponentsDeployed") or 0,
                "numberFailed": result.get("numberComponentErrors") or len(full_names),
            }
        continue
    return {"success": False, "error": f"Deploy timed out after {max_wait_seconds}s", "numberDeployed": 0, "numberFailed": len(full_names)}
