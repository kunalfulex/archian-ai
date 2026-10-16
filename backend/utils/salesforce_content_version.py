"""
Salesforce ContentVersion Upload Utility

Handles uploading images/files to Salesforce as ContentVersion records.
ContentVersion is used to store files in Salesforce, which can then be
referenced in rich text fields.
"""

import base64
from typing import Dict, Any, List, Optional
import asyncio
from utils.salesforceapimigration import _new_session


async def upload_image_to_salesforce(
    image_bytes: bytes,
    filename: str,
    access_token: str,
    instance_url: str,
    title: Optional[str] = None,
    api_version: str = "v59.0",
    first_publish_location_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Upload an image to Salesforce as a ContentVersion record.
    
    Args:
        image_bytes: Binary image data
        filename: Filename for the image (e.g., "image.png")
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
        title: Optional title for the ContentVersion (defaults to filename)
        api_version: Salesforce API version (default: v59.0)
    
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
    if not image_bytes or not filename:
        return {
            "success": False,
            "content_document_id": None,
            "content_version_id": None,
            "salesforce_url": None,
            "error": "Missing image_bytes or filename"
        }
    
    # Validate file size (37.5 MB limit for JSON requests)
    max_size_json = 37.5 * 1024 * 1024  # 37.5 MB in bytes
    if len(image_bytes) > max_size_json:
        return {
            "success": False,
            "content_document_id": None,
            "content_version_id": None,
            "salesforce_url": None,
            "error": f"Image size ({len(image_bytes) / 1024 / 1024:.2f} MB) exceeds 37.5 MB limit for JSON requests"
        }
    
    # Encode image to base64
    try:
        base64_data = base64.b64encode(image_bytes).decode('utf-8')
    except Exception as e:
        print(f"❌ [CONTENT VERSION] Base64 encoding failed: {e}")
        return {
            "success": False,
            "content_document_id": None,
            "content_version_id": None,
            "salesforce_url": None,
            "error": f"Failed to encode image to base64: {str(e)}"
        }
    
    # Prepare payload
    payload = {
        "VersionData": base64_data,
        "PathOnClient": filename,
        "Title": title or filename
    }
    
    # FirstPublishLocationId is optional - if not set, file goes to user's personal library
    # We don't link files to records to keep records clean (files won't appear in record's Files section)
    if first_publish_location_id:
        payload["FirstPublishLocationId"] = first_publish_location_id
    
    # Upload to Salesforce
    url = f"{instance_url}/services/data/{api_version}/sobjects/ContentVersion"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    try:
        async with _new_session() as session:
            async with session.post(url, json=payload, headers=headers) as response:
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
                                    print(f"⚠️ [CONTENT VERSION] Failed to query ContentDocumentId: {query_response.status}")
                        except Exception as e:
                            print(f"⚠️ [CONTENT VERSION] Exception querying ContentDocumentId: {e}")
                    
                    # Build Salesforce URL for inline display in rich text fields
                    # Use relative URL format: /sfc/servlet.shepherd/version/download/{ContentVersionId}
                    # This URL works for inline display regardless of FirstPublishLocationId
                    if content_version_id:
                        salesforce_url = f"/sfc/servlet.shepherd/version/download/{content_version_id}"
                    else:
                        salesforce_url = None
                        print(f"⚠️ [CONTENT VERSION] ContentVersionId not available")
                    
                    print(f"✅ [CONTENT VERSION] Uploaded image: {filename} -> {content_version_id}")
                    
                    return {
                        "success": True,
                        "content_document_id": content_document_id,
                        "content_version_id": content_version_id,
                        "salesforce_url": salesforce_url,
                        "error": None
                    }
                else:
                    error_text = await response.text()
                    print(f"❌ [CONTENT VERSION] Failed to upload {filename}: {response.status} - {error_text[:200]}")
                    return {
                        "success": False,
                        "content_document_id": None,
                        "content_version_id": None,
                        "salesforce_url": None,
                        "error": f"Salesforce API error {response.status}: {error_text}"
                    }
    
    except Exception as e:
        print(f"❌ [CONTENT VERSION] Exception uploading {filename}: {e}")
        return {
            "success": False,
            "content_document_id": None,
            "content_version_id": None,
            "salesforce_url": None,
            "error": f"Exception: {str(e)}"
        }


async def upload_images_batch(
    images: List[Dict[str, Any]],
    access_token: str,
    instance_url: str,
    api_version: str = "v59.0",
    max_concurrent: int = 5,
    first_publish_location_id: Optional[str] = None
) -> Dict[str, Dict[str, Any]]:
    """
    Upload multiple images to Salesforce in parallel.
    
    Args:
        images: List of image dictionaries, each with:
            {
                "url": str,              # Original URL (for mapping)
                "image_bytes": bytes,     # Binary image data
                "filename": str,          # Filename
                "title": str (optional)   # Optional title
            }
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
        api_version: Salesforce API version (default: v59.0)
        max_concurrent: Maximum concurrent uploads (default: 5)
    
    Returns:
        Dictionary mapping original URLs to upload results:
        {
            original_url: {
                "salesforce_url": str,
                "content_document_id": str,
                "content_version_id": str
            }
        }
        Only includes successfully uploaded images.
    """
    if not images:
        return {}
    
    semaphore = asyncio.Semaphore(max_concurrent)
    image_mapping = {}
    
    async def upload_with_semaphore(image_data: Dict[str, Any]):
        async with semaphore:
            original_url = image_data.get("url")
            image_bytes = image_data.get("image_bytes")
            filename = image_data.get("filename", "image.png")
            title = image_data.get("title")
            
            if not image_bytes:
                print(f"⚠️ [CONTENT VERSION] Skipping {original_url}: no image_bytes")
                return
            
            result = await upload_image_to_salesforce(
                image_bytes=image_bytes,
                filename=filename,
                access_token=access_token,
                instance_url=instance_url,
                title=title,
                api_version=api_version,
                first_publish_location_id=first_publish_location_id
            )
            
            if result["success"] and result["salesforce_url"]:
                image_mapping[original_url] = {
                    "salesforce_url": result["salesforce_url"],
                    "content_document_id": result.get("content_document_id"),
                    "content_version_id": result.get("content_version_id")
                }
                print(f"✅ [CONTENT VERSION] Mapped {original_url} -> {result['salesforce_url']} (ContentDocument: {result.get('content_document_id')})")
            else:
                print(f"⚠️ [CONTENT VERSION] Failed to upload {original_url}: {result.get('error')}")
    
    # Upload all images concurrently
    tasks = [upload_with_semaphore(img) for img in images]
    await asyncio.gather(*tasks)
    
    print(f"📊 [CONTENT VERSION] Uploaded {len(image_mapping)}/{len(images)} images")
    return image_mapping


async def _download_content_version_via_rest_api(
    content_version_id: str,
    access_token: str,
    instance_url: str,
    api_version: str = "v59.0"
) -> Optional[bytes]:
    """
    Download ContentVersion file via REST API (works with Bearer token).
    The servlet URL (/sfc/servlet.shepherd/version/download/) returns HTML when
    called with Bearer token; REST API returns actual binary data.
    """
    url = f"{instance_url.rstrip('/')}/services/data/{api_version}/sobjects/ContentVersion/{content_version_id}/VersionData"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with _new_session() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    image_bytes = await response.read()
                    print(f"✅ [CONTENT VERSION] Downloaded via REST API: {len(image_bytes)} bytes (ContentVersion {content_version_id})")
                    return image_bytes
                else:
                    error_text = await response.text()
                    print(f"❌ [CONTENT VERSION] REST API download failed: {response.status} - {error_text[:200]}")
                    return None
    except Exception as e:
        print(f"❌ [CONTENT VERSION] Exception in REST API download: {e}")
        return None


def _extract_content_version_id_from_url(url: str) -> Optional[str]:
    """Extract ContentVersion ID from /sfc/servlet.shepherd/version/download/{id} URL."""
    if not url:
        return None
    prefix = "/sfc/servlet.shepherd/version/download/"
    if prefix in url:
        # Handle both relative and absolute URLs
        idx = url.find(prefix)
        rest = url[idx + len(prefix):]
        # ID is 18 chars (Salesforce ID), may have query params
        id_part = rest.split("?")[0].split("/")[0].strip()
        if id_part and len(id_part) in (15, 18):
            return id_part
    return None


async def download_salesforce_image(
    url: str,
    access_token: str,
    instance_url: str
) -> Optional[bytes]:
    """
    Download an image from Salesforce using the file download URL.
    
    For /sfc/servlet.shepherd/version/download/{ContentVersionId} URLs, uses REST API
    (servlet returns HTML with Bearer token). For other URLs, uses direct GET.
    
    Args:
        url: Salesforce file URL (e.g., /sfc/servlet.shepherd/version/download/...)
        access_token: Salesforce access token (should be SOURCE org token for downloading)
        instance_url: Salesforce instance URL (should be SOURCE org URL for downloading)
    
    Returns:
        Image bytes or None if download fails
    """
    if not url:
        return None
    
    # ContentVersion URLs: use REST API (Bearer token works; servlet returns HTML)
    content_version_id = _extract_content_version_id_from_url(url)
    if content_version_id:
        return await _download_content_version_via_rest_api(
            content_version_id, access_token, instance_url
        )
    
    # Build full URL for other endpoints (rtaImage on file.force.com, etc.)
    if url.startswith("http://") or url.startswith("https://"):
        full_url = url
    elif url.startswith("/"):
        full_url = f"{instance_url.rstrip('/')}{url}"
    else:
        full_url = f"{instance_url.rstrip('/')}/{url}"
    
    headers = {"Authorization": f"Bearer {access_token}"}
    
    try:
        async with _new_session() as session:
            async with session.get(full_url, headers=headers) as response:
                if response.status == 200:
                    image_bytes = await response.read()
                    print(f"✅ [CONTENT VERSION] Downloaded image: {len(image_bytes)} bytes")
                    
                    # Validate it's actually image data (check magic bytes)
                    if len(image_bytes) >= 4:
                        is_image = (
                            image_bytes[:4] == b'\x89PNG' or  # PNG
                            image_bytes[:3] == b'\xFF\xD8\xFF' or  # JPEG
                            image_bytes[:4] == b'GIF8' or  # GIF
                            (len(image_bytes) >= 12 and image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP')  # WebP
                        )
                        if not is_image:
                            try:
                                preview = image_bytes[:200].decode('utf-8', errors='ignore').lower()
                                if '<html' in preview or '<!doctype' in preview or 'error' in preview:
                                    print(f"❌ [CONTENT VERSION] Downloaded content is HTML/error page, not image")
                                    return None
                            except Exception:
                                pass
                            print(f"⚠️ [CONTENT VERSION] Downloaded content doesn't match image format")
                    
                    return image_bytes
                else:
                    error_text = await response.text()
                    print(f"❌ [CONTENT VERSION] Failed to download {url}: {response.status} - {error_text[:200]}")
                    return None
    
    except Exception as e:
        print(f"❌ [CONTENT VERSION] Exception downloading {url}: {e}")
        return None


async def download_rta_image_via_api(
    record_id: str,
    content_reference_id: str,
    access_token: str,
    instance_url: str,
    object_name: Optional[str] = None,
    field_name: Optional[str] = None,
    api_version: str = "v59.0"
) -> Optional[bytes]:
    """
    Download an image from Salesforce rich text field using the REST API endpoint.
    
    This is the correct way to download images from rtaImage URLs, which cannot be
    accessed directly via HTTP GET.
    
    Args:
        record_id: Salesforce record ID (eid from rtaImage URL)
        content_reference_id: ContentReference ID (refid from rtaImage URL)
        access_token: Salesforce access token (should be SOURCE org token for downloading)
        instance_url: Salesforce instance URL (should be SOURCE org URL for downloading)
        object_name: Object API name (e.g., "Contact"). If None, will try to detect from record_id prefix
        field_name: Field API name (e.g., "Description"). If None, will try common field names
        api_version: Salesforce API version (default: v59.0)
    
    Returns:
        Image bytes or None if download fails
    """
    # Detect object name from record ID prefix if not provided
    if not object_name:
        # Common Salesforce object prefixes
        prefix_map = {
            "001": "Account",
            "003": "Contact",
            "00Q": "Lead",
            "006": "Opportunity",
            "00T": "Task",
            "00U": "Event",
            "500": "Case",
            "00P": "Campaign"
        }
        prefix = record_id[:3] if len(record_id) >= 3 else None
        object_name = prefix_map.get(prefix, "Contact")  # Default to Contact
    
    # Build list of fields to try
    # Always try the provided field first, then fallback to common fields
    common_fields = []
    if field_name:
        common_fields.append(field_name)
    
    # Add common rich text field names as fallback
    fallback_fields = ["Description", "Body", "Rich_Text_Field__c", "Knowledge_Base__c", "xtagwordforce__Knowledge_Base__c"]
    for fallback_field in fallback_fields:
        if fallback_field not in common_fields:
            common_fields.append(fallback_field)
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "image/*"  # Accept any image format
    }
    
    # Try each field name until we find one that works
    for field in common_fields:
        try:
            # Use Salesforce REST API endpoint for rich text images
            # Format: /services/data/vXX.X/sobjects/{SObjectName}/{id}/richTextImageFields/{fieldName}/{contentReferenceId}
            api_url = f"{instance_url.rstrip('/')}/services/data/{api_version}/sobjects/{object_name}/{record_id}/richTextImageFields/{field}/{content_reference_id}"
            
            async with _new_session() as session:
                async with session.get(api_url, headers=headers) as response:
                    if response.status == 200:
                        image_bytes = await response.read()
                        print(f"✅ [CONTENT VERSION] Downloaded rtaImage via API: {len(image_bytes)} bytes (field: {field})")
                        
                        # Validate it's actually image data
                        if len(image_bytes) >= 4:
                            is_image = (
                                image_bytes[:4] == b'\x89PNG' or  # PNG
                                image_bytes[:3] == b'\xFF\xD8\xFF' or  # JPEG
                                image_bytes[:4] == b'GIF8' or  # GIF
                                (len(image_bytes) >= 12 and image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP')  # WebP
                            )
                            if is_image:
                                return image_bytes
                            else:
                                print(f"⚠️ [CONTENT VERSION] Downloaded content doesn't match image format (field: {field})")
                        else:
                            print(f"⚠️ [CONTENT VERSION] Downloaded content too small: {len(image_bytes)} bytes (field: {field})")
                    elif response.status == 404:
                        # Field not found or image doesn't exist in this field - try next field
                        continue
                    else:
                        error_text = await response.text()
                        print(f"⚠️ [CONTENT VERSION] API request failed for field {field}: {response.status}")
                        if response.status != 404:
                            # For non-404 errors, don't try other fields
                            break
        except Exception as e:
            print(f"⚠️ [CONTENT VERSION] Exception downloading rtaImage via API (field {field}): {e}")
            continue
    
    print(f"❌ [CONTENT VERSION] Failed to download rtaImage via API after trying {len(common_fields)} field(s)")
    return None
