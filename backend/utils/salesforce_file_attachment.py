"""
Salesforce File Attachment Utility

Unified functions for downloading and uploading file attachments to/from Salesforce.
Generalizes the image-specific code from salesforce_content_version.py to handle any file type.

Phase 1: Bulk API 2.0 for attachment metadata, parallel binary download.
Phase 2: High-concurrency REST upload + batch ContentDocumentLink.
Phase 3: Configurable thresholds (env), 429/503 backoff.
Single-file multipart: files > 37.5 MB use REST multipart/form-data (up to 50 MB).
"""

import base64
import json
import os
from typing import Dict, Any, List, Optional
import asyncio
from utils.salesforceapimigration import _new_session

# Salesforce limits: JSON/base64 ~37.5 MB; multipart/form-data up to 50 MB per file
MAX_FILE_SIZE_JSON_MB = 37.5
MAX_FILE_SIZE_MULTIPART_MB = 50


def _env_int(key: str, default: int) -> int:
    """Read integer from environment with fallback."""
    try:
        val = os.environ.get(key)
        return int(val) if val is not None else default
    except ValueError:
        return default


# Phase 3: Configurable thresholds (env); fallback to defaults
# Use Bulk API 2.0 for attachment metadata when record count >= this threshold
BULK_ATTACHMENT_METADATA_THRESHOLD = _env_int("BULK_ATTACHMENT_METADATA_THRESHOLD", 20)
# Max record IDs per Bulk Query IN clause (Salesforce limit)
BULK_ATTACHMENT_QUERY_CHUNK_SIZE = _env_int("BULK_ATTACHMENT_QUERY_CHUNK_SIZE", 200)
# Max concurrent binary GETs when downloading files in parallel
MAX_PARALLEL_DOWNLOADS = _env_int("MAX_PARALLEL_DOWNLOADS", 30)
# Use bulk upload path (high-concurrency REST + batch link) when file count >= this
BULK_UPLOAD_THRESHOLD = _env_int("BULK_UPLOAD_THRESHOLD", 10)
# Max concurrent REST uploads when using bulk upload path
MAX_PARALLEL_UPLOADS = _env_int("MAX_PARALLEL_UPLOADS", 20)
# Max concurrent ContentDocumentLink POSTs when creating links in batch
MAX_PARALLEL_LINKS = _env_int("MAX_PARALLEL_LINKS", 15)
# Rich-text: use bulk upload when image count >= this (Phase 3.3)
RICH_TEXT_BULK_UPLOAD_THRESHOLD = _env_int("RICH_TEXT_BULK_UPLOAD_THRESHOLD", 50)


async def download_file_from_content_version(
    content_version_id: str,
    access_token: str,
    instance_url: str,
    api_version: str = "v59.0"
) -> Optional[bytes]:
    """
    Download a file from Salesforce using ContentVersion ID.
    
    Args:
        content_version_id: ContentVersion ID
        access_token: Salesforce access token (should be SOURCE org token for downloading)
        instance_url: Salesforce instance URL (should be SOURCE org URL for downloading)
        api_version: Salesforce API version (default: v59.0)
    
    Returns:
        File bytes or None if download fails
    """
    if not content_version_id:
        return None
    
    url = f"{instance_url}/services/data/{api_version}/sobjects/ContentVersion/{content_version_id}/VersionData"
    headers = {
        "Authorization": f"Bearer {access_token}"
    }
    
    try:
        async with _new_session() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    file_bytes = await response.read()
                    print(f"✅ [FILE ATTACHMENT] Downloaded file: {len(file_bytes)} bytes")
                    return file_bytes
                else:
                    error_text = await response.text()
                    print(f"❌ [FILE ATTACHMENT] Failed to download ContentVersion {content_version_id}: {response.status} - {error_text[:200]}")
                    return None
    
    except Exception as e:
        print(f"❌ [FILE ATTACHMENT] Exception downloading ContentVersion {content_version_id}: {e}")
        return None


async def download_files_parallel(
    content_version_ids: List[str],
    access_token: str,
    instance_url: str,
    api_version: str = "v59.0",
    max_concurrent: int = MAX_PARALLEL_DOWNLOADS,
    max_retries: int = 2,
) -> Dict[str, Optional[bytes]]:
    """
    Download multiple files from Salesforce by ContentVersion ID in parallel.
    Returns a map content_version_id -> file_bytes (or None if download failed).
    
    Args:
        content_version_ids: List of ContentVersion IDs
        access_token: Salesforce access token (source org)
        instance_url: Salesforce instance URL (source org)
        api_version: Salesforce API version (default: v59.0)
        max_concurrent: Max concurrent GETs (default: MAX_PARALLEL_DOWNLOADS)
        max_retries: Retries per request on failure (default: 2)
    
    Returns:
        Dict mapping content_version_id -> bytes or None
    """
    if not content_version_ids:
        return {}
    
    result: Dict[str, Optional[bytes]] = {cv_id: None for cv_id in content_version_ids}
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def download_one(cv_id: str) -> None:
        async with semaphore:
            for attempt in range(max_retries + 1):
                try:
                    url = f"{instance_url}/services/data/{api_version}/sobjects/ContentVersion/{cv_id}/VersionData"
                    headers = {"Authorization": f"Bearer {access_token}"}
                    async with _new_session() as session:
                        async with session.get(url, headers=headers) as response:
                            if response.status == 200:
                                result[cv_id] = await response.read()
                                return
                            if response.status in (429, 503) and attempt < max_retries:
                                retry_after = response.headers.get("Retry-After")
                                delay = int(retry_after) if retry_after and retry_after.isdigit() else (2 ** attempt)
                                await asyncio.sleep(min(delay, 60))
                                continue
                            result[cv_id] = None
                            return
                except Exception as e:
                    if attempt < max_retries:
                        await asyncio.sleep(2 ** attempt)
                    else:
                        print(f"❌ [FILE ATTACHMENT] Parallel download failed for {cv_id}: {e}")
                        result[cv_id] = None
                        return
    
    await asyncio.gather(*[download_one(cv_id) for cv_id in content_version_ids])
    success = sum(1 for b in result.values() if b is not None)
    print(f"✅ [FILE ATTACHMENT] Parallel download: {success}/{len(content_version_ids)} files")
    return result


async def upload_file_to_salesforce(
    file_bytes: bytes,
    filename: str,
    access_token: str,
    instance_url: str,
    title: Optional[str] = None,
    api_version: str = "v59.0",
    first_publish_location_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Upload a file to Salesforce as a ContentVersion record.
    Generalizes upload_image_to_salesforce() to handle any file type.
    
    Args:
        file_bytes: Binary file data
        filename: Filename for the file (e.g., "document.pdf")
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
        title: Optional title for the ContentVersion (defaults to filename)
        api_version: Salesforce API version (default: v59.0)
        first_publish_location_id: Optional record ID to link file to (if None, goes to user's library)
    
    Returns:
        Dictionary with:
        {
            "success": bool,
            "content_document_id": str | None,
            "content_version_id": str | None,
            "salesforce_url": str | None,
            "error": str | None
        }
    """
    if not file_bytes or not filename:
        return {
            "success": False,
            "content_document_id": None,
            "content_version_id": None,
            "salesforce_url": None,
            "error": "Missing file_bytes or filename"
        }
    
    file_size_mb = len(file_bytes) / (1024 * 1024)
    max_size_multipart = MAX_FILE_SIZE_MULTIPART_MB * 1024 * 1024
    max_size_json = MAX_FILE_SIZE_JSON_MB * 1024 * 1024
    
    if len(file_bytes) > max_size_multipart:
        return {
            "success": False,
            "content_document_id": None,
            "content_version_id": None,
            "salesforce_url": None,
            "error": f"File size ({file_size_mb:.2f} MB) exceeds Salesforce limit ({MAX_FILE_SIZE_MULTIPART_MB} MB) for ContentVersion upload."
        }
    
    use_multipart = len(file_bytes) > max_size_json
    
    url = f"{instance_url}/services/data/{api_version}/sobjects/ContentVersion"
    headers = {"Authorization": f"Bearer {access_token}"}
    
    try:
        async with _new_session() as session:
            if use_multipart:
                # REST multipart/form-data: entity_content (JSON) + VersionData (binary) — supports up to 50 MB
                print(f"   📤 [FILE ATTACHMENT] Using multipart upload for large file ({file_size_mb:.2f} MB)")
                import aiohttp
                entity_content = {
                    "PathOnClient": filename,
                    "Title": title or filename
                }
                if first_publish_location_id:
                    entity_content["FirstPublishLocationId"] = first_publish_location_id
                form = aiohttp.FormData()
                form.add_field("entity_content", json.dumps(entity_content), content_type="application/json")
                form.add_field("VersionData", file_bytes, filename=filename, content_type="application/octet-stream")
                # Let aiohttp set Content-Type (multipart boundary); only pass Authorization
                response = await session.post(url, data=form, headers=headers)
            else:
                # JSON + base64 (up to 37.5 MB)
                try:
                    base64_data = base64.b64encode(file_bytes).decode('utf-8')
                except Exception as e:
                    print(f"❌ [FILE ATTACHMENT] Base64 encoding failed: {e}")
                    return {
                        "success": False,
                        "content_document_id": None,
                        "content_version_id": None,
                        "salesforce_url": None,
                        "error": f"Failed to encode file to base64: {str(e)}"
                    }
                payload = {
                    "VersionData": base64_data,
                    "PathOnClient": filename,
                    "Title": title or filename
                }
                if first_publish_location_id:
                    payload["FirstPublishLocationId"] = first_publish_location_id
                headers["Content-Type"] = "application/json"
                response = await session.post(url, json=payload, headers=headers)
            
            async with response:
                if response.status == 201:
                    result = await response.json()
                    content_version_id = result.get("id")
                    content_document_id = result.get("ContentDocumentId")
                    
                    # If ContentDocumentId is not in the response, query for it
                    if not content_document_id and content_version_id:
                        try:
                            query_url = f"{instance_url}/services/data/{api_version}/sobjects/ContentVersion/{content_version_id}"
                            query_headers = {
                                "Authorization": f"Bearer {access_token}",
                                "Content-Type": "application/json"
                            }
                            async with session.get(query_url, headers=query_headers) as query_response:
                                if query_response.status == 200:
                                    query_result = await query_response.json()
                                    content_document_id = query_result.get("ContentDocumentId")
                                else:
                                    error_text = await query_response.text()
                                    print(f"⚠️ [FILE ATTACHMENT] Failed to query ContentDocumentId: {query_response.status}")
                        except Exception as e:
                            print(f"⚠️ [FILE ATTACHMENT] Exception querying ContentDocumentId: {e}")
                    
                    # Build Salesforce URL for reference
                    if content_version_id:
                        salesforce_url = f"/sfc/servlet.shepherd/version/download/{content_version_id}"
                    else:
                        salesforce_url = None
                        print(f"⚠️ [FILE ATTACHMENT] ContentVersionId not available")
                    
                    print(f"✅ [FILE ATTACHMENT] Uploaded file: {filename} -> {content_version_id}")
                    
                    return {
                        "success": True,
                        "content_document_id": content_document_id,
                        "content_version_id": content_version_id,
                        "salesforce_url": salesforce_url,
                        "error": None
                    }
                else:
                    error_text = await response.text()
                    print(f"❌ [FILE ATTACHMENT] Failed to upload {filename}: {response.status} - {error_text[:200]}")
                    return {
                        "success": False,
                        "content_document_id": None,
                        "content_version_id": None,
                        "salesforce_url": None,
                        "error": f"Salesforce API error {response.status}: {error_text}"
                    }
    
    except Exception as e:
        print(f"❌ [FILE ATTACHMENT] Exception uploading {filename}: {e}")
        return {
            "success": False,
            "content_document_id": None,
            "content_version_id": None,
            "salesforce_url": None,
            "error": f"Exception: {str(e)}"
        }


async def upload_files_batch(
    files: List[Dict[str, Any]],
    access_token: str,
    instance_url: str,
    api_version: str = "v59.0",
    max_concurrent: int = 5,
    first_publish_location_id: Optional[str] = None
) -> Dict[str, Dict[str, Any]]:
    """
    Upload multiple files to Salesforce in parallel.
    Generalizes upload_images_batch() to handle any file type.
    
    Args:
        files: List of file dictionaries, each with:
            {
                "file_bytes": bytes,     # Binary file data
                "filename": str,          # Filename
                "title": str (optional), # Optional title
                "key": str (optional)    # Optional key for mapping (e.g., content_version_id from source)
            }
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
        api_version: Salesforce API version (default: v59.0)
        max_concurrent: Maximum concurrent uploads (default: 5)
        first_publish_location_id: Optional record ID to link files to
    
    Returns:
        Dictionary mapping file keys (or indices) to upload results:
        {
            "key_or_index": {
                "success": bool,
                "content_document_id": str | None,
                "content_version_id": str | None,
                "error": str | None
            }
        }
    """
    if not files:
        return {}
    
    semaphore = asyncio.Semaphore(max_concurrent)
    upload_results = {}
    
    async def upload_with_semaphore(file_data: Dict[str, Any], index: int):
        async with semaphore:
            file_bytes = file_data.get("file_bytes")
            filename = file_data.get("filename", f"file_{index}")
            title = file_data.get("title")
            file_key = file_data.get("key", str(index))
            
            if not file_bytes:
                print(f"⚠️ [FILE ATTACHMENT] Skipping {filename}: no file_bytes")
                upload_results[file_key] = {
                    "success": False,
                    "content_document_id": None,
                    "content_version_id": None,
                    "error": "Missing file_bytes"
                }
                return
            
            result = await upload_file_to_salesforce(
                file_bytes=file_bytes,
                filename=filename,
                access_token=access_token,
                instance_url=instance_url,
                title=title,
                api_version=api_version,
                first_publish_location_id=first_publish_location_id
            )
            
            upload_results[file_key] = result
    
    # Upload all files concurrently
    tasks = [upload_with_semaphore(file_data, idx) for idx, file_data in enumerate(files)]
    await asyncio.gather(*tasks)
    
    successful = sum(1 for r in upload_results.values() if r.get("success"))
    print(f"📊 [FILE ATTACHMENT] Uploaded {successful}/{len(files)} files")
    return upload_results


async def create_content_document_link(
    content_document_id: str,
    linked_entity_id: str,
    access_token: str,
    instance_url: str,
    share_type: str = "V",
    visibility: str = "AllUsers",
    api_version: str = "v59.0"
) -> Dict[str, Any]:
    """
    Create a ContentDocumentLink to link a file to a Salesforce record.
    
    Args:
        content_document_id: ContentDocument ID (from ContentVersion)
        linked_entity_id: Record ID to link the file to
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
        share_type: Share type - 'V' (Viewer), 'C' (Collaborator), 'I' (Inferred)
        visibility: Visibility - 'AllUsers' or 'InternalUsers'
        api_version: Salesforce API version (default: v59.0)
    
    Returns:
        Dictionary with:
        {
            "success": bool,
            "link_id": str | None,
            "error": str | None
        }
    """
    if not content_document_id or not linked_entity_id:
        return {
            "success": False,
            "link_id": None,
            "error": "Missing content_document_id or linked_entity_id"
        }
    
    payload = {
        "ContentDocumentId": content_document_id,
        "LinkedEntityId": linked_entity_id,
        "ShareType": share_type,
        "Visibility": visibility
    }
    
    url = f"{instance_url}/services/data/{api_version}/sobjects/ContentDocumentLink"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    try:
        async with _new_session() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status == 201:
                    result = await response.json()
                    link_id = result.get("id")
                    print(f"✅ [FILE ATTACHMENT] Created ContentDocumentLink: {link_id} (file {content_document_id} -> record {linked_entity_id})")
                    return {
                        "success": True,
                        "link_id": link_id,
                        "error": None
                    }
                else:
                    error_text = await response.text()
                    print(f"❌ [FILE ATTACHMENT] Failed to create ContentDocumentLink: {response.status} - {error_text[:200]}")
                    return {
                        "success": False,
                        "link_id": None,
                        "error": f"Salesforce API error {response.status}: {error_text}"
                    }
    
    except Exception as e:
        print(f"❌ [FILE ATTACHMENT] Exception creating ContentDocumentLink: {e}")
        return {
            "success": False,
            "link_id": None,
            "error": f"Exception: {str(e)}"
        }


async def create_content_document_links_batch(
    links: List[Dict[str, Any]],
    access_token: str,
    instance_url: str,
    api_version: str = "v59.0",
    max_concurrent: int = MAX_PARALLEL_LINKS,
) -> List[Dict[str, Any]]:
    """
    Create multiple ContentDocumentLinks in parallel (REST POST per link).
    
    Args:
        links: List of dicts with content_document_id, linked_entity_id, share_type? (default V), visibility? (default AllUsers)
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
        api_version: Salesforce API version (default: v59.0)
        max_concurrent: Max concurrent POSTs (default: MAX_PARALLEL_LINKS)
    
    Returns:
        List of { content_document_id, linked_entity_id, success, link_id?, error? } in same order as links
    """
    if not links:
        return []
    
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def create_one(link: Dict[str, Any]) -> Dict[str, Any]:
        async with semaphore:
            content_document_id = link.get("content_document_id")
            linked_entity_id = link.get("linked_entity_id")
            share_type = link.get("share_type", "V")
            visibility = link.get("visibility", "AllUsers")
            if not content_document_id or not linked_entity_id:
                return {
                    "content_document_id": content_document_id,
                    "linked_entity_id": linked_entity_id,
                    "success": False,
                    "link_id": None,
                    "error": "Missing content_document_id or linked_entity_id",
                }
            r = await create_content_document_link(
                content_document_id=content_document_id,
                linked_entity_id=linked_entity_id,
                access_token=access_token,
                instance_url=instance_url,
                share_type=share_type,
                visibility=visibility,
                api_version=api_version,
            )
            return {
                "content_document_id": content_document_id,
                "linked_entity_id": linked_entity_id,
                "success": r.get("success", False),
                "link_id": r.get("link_id"),
                "error": r.get("error"),
            }
    
    result = await asyncio.gather(*[create_one(link) for link in links])
    success_count = sum(1 for r in result if r.get("success"))
    print(f"✅ [FILE ATTACHMENT] Batch links: {success_count}/{len(links)} created")
    return list(result)


async def upload_content_versions_bulk_binary(
    files: List[Dict[str, Any]],
    access_token: str,
    instance_url: str,
    api_version: str = "v59.0",
    user_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Upload many ContentVersion records in parallel (REST) and create ContentDocumentLinks in batch.
    Phase 2 bulk upload path: high-concurrency upload + batch link (no Bulk API 2.0 binary ingest).
    
    Args:
        files: List of dicts, each with:
            file_bytes: bytes
            filename: str
            title?: str (defaults to filename)
            linked_entity_id?: str (record to link file to; if absent, file stays in library)
            share_type?: str (default V)
            visibility?: str (default AllUsers)
            key?: str (for mapping results; default index)
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
        api_version: Salesforce API version (default: v59.0)
        user_id: Optional; if set, validates/tracks against plan limits
    
    Returns:
        List of results in same order as files: { content_document_id, content_version_id, linked_entity_id?, success, error? }
    """
    if not files:
        return []
    
    total_size_mb = sum(len(f.get("file_bytes") or b"") for f in files) / (1024 * 1024)
    file_count = len(files)
    
    if user_id:
        from services.entitlements import get_entitlements
        is_valid, message, _ = await get_entitlements().validate_file_attachment_upload(
            user_id=user_id,
            file_size_mb=total_size_mb,
            file_count=file_count,
        )
        if not is_valid:
            return [
                {"content_document_id": None, "content_version_id": None, "success": False, "error": message}
                for _ in files
            ]
    
    upload_list = [
        {
            "file_bytes": f.get("file_bytes"),
            "filename": f.get("filename", f"file_{i}"),
            "title": f.get("title"),
            "key": f.get("key", i),
        }
        for i, f in enumerate(files)
    ]
    upload_results = await upload_files_batch(
        upload_list,
        access_token=access_token,
        instance_url=instance_url,
        api_version=api_version,
        max_concurrent=MAX_PARALLEL_UPLOADS,
        first_publish_location_id=None,
    )
    
    links_to_create: List[Dict[str, Any]] = []
    link_source_index: List[int] = []
    for i, f in enumerate(files):
        key = f.get("key", i)
        r = upload_results.get(key) or upload_results.get(str(key))
        if not r or not r.get("success") or not r.get("content_document_id"):
            continue
        if f.get("linked_entity_id"):
            links_to_create.append({
                "content_document_id": r["content_document_id"],
                "linked_entity_id": f["linked_entity_id"],
                "share_type": f.get("share_type", "V"),
                "visibility": f.get("visibility", "AllUsers"),
            })
            link_source_index.append(i)
    
    link_results: List[Dict[str, Any]] = []
    if links_to_create:
        link_results = await create_content_document_links_batch(
            links_to_create,
            access_token=access_token,
            instance_url=instance_url,
            api_version=api_version,
        )
    
    link_success_by_index = {
        link_source_index[k]: link_results[k].get("success", False)
        for k in range(len(link_results))
    } if link_results else {}
    
    per_file_results: List[Dict[str, Any]] = []
    successful_count = 0
    successful_size_mb = 0.0
    for i, f in enumerate(files):
        key = f.get("key", i)
        r = upload_results.get(key) or upload_results.get(str(key)) or {}
        upload_ok = r.get("success", False)
        link_ok = link_success_by_index.get(i, True) if not f.get("linked_entity_id") else link_success_by_index.get(i, False)
        success = upload_ok and link_ok
        if success and r.get("content_document_id"):
            successful_count += 1
            successful_size_mb += len(f.get("file_bytes") or b"") / (1024 * 1024)
        per_file_results.append({
            "content_document_id": r.get("content_document_id"),
            "content_version_id": r.get("content_version_id"),
            "linked_entity_id": f.get("linked_entity_id"),
            "success": success,
            "error": r.get("error") if not upload_ok else (None if link_ok else "Link creation failed"),
        })
    
    if user_id and successful_count:
        from services.entitlements import get_entitlements
        await get_entitlements().track_file_attachment_upload(
            user_id=user_id,
            file_count=successful_count,
            total_size_mb=successful_size_mb,
        )
    
    print(f"📊 [FILE ATTACHMENT] Bulk upload: {successful_count}/{len(files)} files uploaded and linked")
    return per_file_results


def _bulk_row_get(row: Dict[str, Any], *keys: str) -> Any:
    """Get first present key from a Bulk API 2.0 CSV row (handles sf__ prefix)."""
    for k in keys:
        v = row.get(k)
        if v is not None and v != "":
            return v
    return None


async def fetch_attachments_for_records_bulk(
    record_ids: List[str],
    access_token: str,
    instance_url: str,
    api_version: str = "v59.0"
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Fetch attachment metadata via Bulk API 2.0 Query (ContentDocumentLink + ContentVersion).
    Use for large batches (e.g. 200+ records) to avoid many REST queries.
    
    Returns same shape as fetch_attachments_for_records: record_id -> list of attachment metadata.
    """
    from utils.salesforce_bulk_api import (
        create_bulk_query_job,
        wait_for_bulk_query_job_completion,
        get_bulk_query_results,
    )
    
    if not record_ids:
        return {}
    
    all_links: List[Dict[str, Any]] = []
    
    for i in range(0, len(record_ids), BULK_ATTACHMENT_QUERY_CHUNK_SIZE):
        chunk = record_ids[i : i + BULK_ATTACHMENT_QUERY_CHUNK_SIZE]
        ids_str = "', '".join(chunk)
        soql_links = (
            "SELECT ContentDocumentId, LinkedEntityId, ShareType, Visibility "
            f"FROM ContentDocumentLink WHERE LinkedEntityId IN ('{ids_str}')"
        )
        try:
            job_id = await create_bulk_query_job(
                "ContentDocumentLink",
                soql_links,
                access_token,
                instance_url,
                api_version,
            )
            await wait_for_bulk_query_job_completion(
                job_id, access_token, instance_url, api_version=api_version
            )
            links = await get_bulk_query_results(
                job_id, access_token, instance_url, api_version
            )
            all_links.extend(links)
        except Exception as e:
            print(f"⚠️ [FILE ATTACHMENT BULK] ContentDocumentLink query failed for chunk: {e}")
            continue
    
    if not all_links:
        print(f"📊 [FILE ATTACHMENT BULK] No ContentDocumentLinks found for {len(record_ids)} records")
        return {}
    
    content_document_ids = list(
        set(
            _bulk_row_get(link, "ContentDocumentId") or link.get("ContentDocumentId")
            for link in all_links
        )
    )
    content_document_ids = [x for x in content_document_ids if x]
    
    doc_to_version: Dict[str, Dict[str, Any]] = {}
    
    for i in range(0, len(content_document_ids), BULK_ATTACHMENT_QUERY_CHUNK_SIZE):
        chunk = content_document_ids[i : i + BULK_ATTACHMENT_QUERY_CHUNK_SIZE]
        ids_str = "', '".join(chunk)
        soql_versions = (
            "SELECT Id, ContentDocumentId, Title, FileExtension, ContentSize "
            f"FROM ContentVersion WHERE ContentDocumentId IN ('{ids_str}') AND IsLatest = true"
        )
        try:
            job_id = await create_bulk_query_job(
                "ContentVersion",
                soql_versions,
                access_token,
                instance_url,
                api_version,
            )
            await wait_for_bulk_query_job_completion(
                job_id, access_token, instance_url, api_version=api_version
            )
            versions = await get_bulk_query_results(
                job_id, access_token, instance_url, api_version
            )
            for row in versions:
                cdoc_id = _bulk_row_get(row, "ContentDocumentId") or row.get("ContentDocumentId")
                if cdoc_id:
                    doc_to_version[cdoc_id] = row
        except Exception as e:
            print(f"⚠️ [FILE ATTACHMENT BULK] ContentVersion query failed for chunk: {e}")
            continue
    
    all_attachments: Dict[str, List[Dict[str, Any]]] = {}
    for link in all_links:
        record_id = _bulk_row_get(link, "LinkedEntityId") or link.get("LinkedEntityId")
        content_doc_id = _bulk_row_get(link, "ContentDocumentId") or link.get("ContentDocumentId")
        if not record_id or not content_doc_id:
            continue
        version = doc_to_version.get(content_doc_id)
        if not version:
            continue
        cv_id = _bulk_row_get(version, "Id") or version.get("Id")
        if not cv_id:
            continue
        if record_id not in all_attachments:
            all_attachments[record_id] = []
        all_attachments[record_id].append({
            "content_document_id": content_doc_id,
            "content_version_id": cv_id,
            "title": _bulk_row_get(version, "Title") or version.get("Title") or "Untitled",
            "file_extension": _bulk_row_get(version, "FileExtension") or version.get("FileExtension") or "",
            "file_size": _bulk_row_get(version, "ContentSize") or version.get("ContentSize") or 0,
            "share_type": _bulk_row_get(link, "ShareType") or link.get("ShareType") or "V",
            "visibility": _bulk_row_get(link, "Visibility") or link.get("Visibility") or "AllUsers",
        })
    
    print(f"📊 [FILE ATTACHMENT BULK] Found attachments for {len(all_attachments)}/{len(record_ids)} records via Bulk API 2.0")
    return all_attachments


async def fetch_attachments_for_records(
    record_ids: List[str],
    access_token: str,
    instance_url: str,
    api_version: str = "v59.0"
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Query ContentDocumentLink and ContentVersion to find attachments for given record IDs.
    Uses Bulk API 2.0 for large batches (>= BULK_ATTACHMENT_METADATA_THRESHOLD), REST otherwise.
    
    Args:
        record_ids: List of Salesforce record IDs
        access_token: Salesforce access token (source org)
        instance_url: Salesforce instance URL (source org)
        api_version: Salesforce API version (default: v59.0)
    
    Returns:
        Dictionary mapping record_id to list of attachment metadata:
        {
            "record_id": [
                {
                    "content_document_id": str,
                    "content_version_id": str,
                    "title": str,
                    "file_extension": str,
                    "file_size": int,
                    "share_type": str,
                    "visibility": str
                },
                ...
            ]
        }
    """
    if not record_ids:
        return {}
    
    if len(record_ids) >= BULK_ATTACHMENT_METADATA_THRESHOLD:
        return await fetch_attachments_for_records_bulk(
            record_ids, access_token, instance_url, api_version
        )
    
    # REST path: batch record IDs (Salesforce IN clause limit is ~2000 characters)
    # Estimate: each ID is ~18 chars, so ~100 IDs per query is safe
    BATCH_SIZE = 100
    all_attachments = {}
    
    for i in range(0, len(record_ids), BATCH_SIZE):
        batch_ids = record_ids[i:i + BATCH_SIZE]
        ids_str = "', '".join(batch_ids)
        
        # Query ContentDocumentLink
        soql_links = f"""
            SELECT ContentDocumentId, LinkedEntityId, ShareType, Visibility
            FROM ContentDocumentLink
            WHERE LinkedEntityId IN ('{ids_str}')
        """
        
        try:
            async with _new_session() as session:
                # Execute query
                query_url = f"{instance_url}/services/data/{api_version}/query"
                headers = {"Authorization": f"Bearer {access_token}"}
                params = {"q": soql_links}
                
                async with session.get(query_url, headers=headers, params=params) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        print(f"⚠️ [FILE ATTACHMENT] Failed to query ContentDocumentLink: {response.status} - {error_text[:200]}")
                        continue
                    
                    result = await response.json()
                    links = result.get("records", [])
                    
                    # Handle pagination
                    while not result.get("done"):
                        next_url = result.get("nextRecordsUrl")
                        if not next_url:
                            break
                        async with session.get(f"{instance_url}{next_url}", headers=headers) as next_response:
                            if next_response.status == 200:
                                result = await next_response.json()
                                links.extend(result.get("records", []))
                            else:
                                break
                    
                    if not links:
                        continue
                    
                    # Get ContentDocumentIds
                    content_document_ids = list(set([link.get("ContentDocumentId") for link in links if link.get("ContentDocumentId")]))
                    
                    if not content_document_ids:
                        continue
                    
                    # Query ContentVersion for file details (get latest version)
                    doc_ids_str = "', '".join(content_document_ids)
                    soql_versions = f"""
                        SELECT Id, ContentDocumentId, Title, FileExtension, ContentSize
                        FROM ContentVersion
                        WHERE ContentDocumentId IN ('{doc_ids_str}')
                        AND IsLatest = true
                    """
                    
                    params_versions = {"q": soql_versions}
                    async with session.get(query_url, headers=headers, params=params_versions) as version_response:
                        if version_response.status != 200:
                            error_text = await version_response.text()
                            print(f"⚠️ [FILE ATTACHMENT] Failed to query ContentVersion: {version_response.status} - {error_text[:200]}")
                            continue
                        
                        version_result = await version_response.json()
                        versions = version_result.get("records", [])
                        
                        # Handle pagination
                        while not version_result.get("done"):
                            next_url = version_result.get("nextRecordsUrl")
                            if not next_url:
                                break
                            async with session.get(f"{instance_url}{next_url}", headers=headers) as next_response:
                                if next_response.status == 200:
                                    version_result = await next_response.json()
                                    versions.extend(version_result.get("records", []))
                                else:
                                    break
                        
                        # Build mapping: ContentDocumentId -> ContentVersion
                        doc_to_version = {v.get("ContentDocumentId"): v for v in versions if v.get("ContentDocumentId")}
                        
                        # Build mapping: LinkedEntityId -> attachments
                        for link in links:
                            record_id = link.get("LinkedEntityId")
                            content_doc_id = link.get("ContentDocumentId")
                            
                            if not record_id or not content_doc_id:
                                continue
                            
                            version = doc_to_version.get(content_doc_id)
                            if not version:
                                continue
                            
                            if record_id not in all_attachments:
                                all_attachments[record_id] = []
                            
                            all_attachments[record_id].append({
                                "content_document_id": content_doc_id,
                                "content_version_id": version.get("Id"),
                                "title": version.get("Title") or "Untitled",
                                "file_extension": version.get("FileExtension") or "",
                                "file_size": version.get("ContentSize") or 0,
                                "share_type": link.get("ShareType") or "V",
                                "visibility": link.get("Visibility") or "AllUsers"
                            })
        
        except Exception as e:
            print(f"❌ [FILE ATTACHMENT] Exception fetching attachments for batch: {e}")
            continue
    
    print(f"📊 [FILE ATTACHMENT] Found attachments for {len(all_attachments)}/{len(record_ids)} records")
    return all_attachments


async def check_duplicate_attachment_by_filename(
    record_id: str,
    filename: str,
    access_token: str,
    instance_url: str,
    api_version: str = "v59.0"
) -> Optional[str]:
    """
    Check if a file with the same filename already exists for a record.
    Uses filename (Title + FileExtension) comparison to detect duplicates.
    
    Args:
        record_id: Salesforce record ID to check attachments for
        filename: Filename to check (e.g., "document.pdf" or "Dec 1 Post Analytics.pdf")
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
        api_version: Salesforce API version (default: v59.0)
    
    Returns:
        ContentDocumentId if duplicate found, None otherwise
    """
    if not record_id or not filename:
        return None
    
    try:
        # Parse filename into title and extension
        # Handle cases like "document.pdf" or "Dec 1 Post Analytics.pdf"
        if "." in filename:
            parts = filename.rsplit(".", 1)
            title = parts[0]
            extension = parts[1].lower() if len(parts) > 1 else ""
        else:
            title = filename
            extension = ""
        
        async with _new_session() as session:
            # Query ContentDocumentLink for this record
            soql_links = f"""
                SELECT ContentDocumentId
                FROM ContentDocumentLink
                WHERE LinkedEntityId = '{record_id}'
            """
            
            query_url = f"{instance_url}/services/data/{api_version}/query"
            headers = {"Authorization": f"Bearer {access_token}"}
            params = {"q": soql_links}
            
            async with session.get(query_url, headers=headers, params=params) as response:
                if response.status != 200:
                    # If query fails, assume no duplicates and proceed with upload
                    return None
                
                result = await response.json()
                links = result.get("records", [])
                
                # Handle pagination
                while not result.get("done"):
                    next_url = result.get("nextRecordsUrl")
                    if not next_url:
                        break
                    async with session.get(f"{instance_url}{next_url}", headers=headers) as next_response:
                        if next_response.status == 200:
                            result = await next_response.json()
                            links.extend(result.get("records", []))
                        else:
                            break
                
                if not links:
                    return None
                
                # Get ContentDocumentIds
                content_document_ids = [link.get("ContentDocumentId") for link in links if link.get("ContentDocumentId")]
                if not content_document_ids:
                    return None
                
                # Query ContentVersion for file details (latest version only)
                doc_ids_str = "', '".join(content_document_ids)
                soql_versions = f"""
                    SELECT Id, ContentDocumentId, Title, FileExtension
                    FROM ContentVersion
                    WHERE ContentDocumentId IN ('{doc_ids_str}')
                    AND IsLatest = true
                """
                
                params_versions = {"q": soql_versions}
                async with session.get(query_url, headers=headers, params=params_versions) as version_response:
                    if version_response.status != 200:
                        return None
                    
                    version_result = await version_response.json()
                    versions = version_result.get("records", [])
                    
                    # Handle pagination
                    while not version_result.get("done"):
                        next_url = version_result.get("nextRecordsUrl")
                        if not next_url:
                            break
                        async with session.get(f"{instance_url}{next_url}", headers=headers) as next_response:
                            if next_response.status == 200:
                                version_result = await next_response.json()
                                versions.extend(version_result.get("records", []))
                            else:
                                break
                    
                    # Check each version for filename match
                    for version in versions:
                        version_title = version.get("Title") or ""
                        version_extension = (version.get("FileExtension") or "").lower()
                        
                        # Compare title (case-insensitive)
                        if version_title.lower() != title.lower():
                            continue
                        
                        # Compare extension (case-insensitive)
                        if extension:
                            if version_extension != extension:
                                continue
                        else:
                            # If no extension in filename, check if version also has no extension
                            if version_extension:
                                continue
                        
                        # Match found! Return the ContentDocumentId
                        content_doc_id = version.get("ContentDocumentId")
                        if content_doc_id:
                            print(f"🔍 [DUPLICATE CHECK] Found existing file '{filename}' for record {record_id} -> ContentDocumentId: {content_doc_id}")
                            return content_doc_id
                    
                    # No match found
                    return None
    
    except Exception as e:
        print(f"⚠️ [DUPLICATE CHECK] Error checking for duplicate file '{filename}': {e}")
        # On error, assume no duplicates and proceed with upload
        return None
