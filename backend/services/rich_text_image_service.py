"""
Rich Text Image Service

Service for extracting and processing images from rich text fields.
Handles image extraction, categorization, downloading, and uploading.
"""

from typing import List, Dict, Any, Optional, Tuple
import hashlib
import asyncio
from utils.rich_text_handler import (
    extract_images_from_html,
    is_external_url,
    is_salesforce_internal_url,
    is_base64_image,
    extract_base64_data
)
from utils.salesforce_content_version import (
    download_salesforce_image,
    download_rta_image_via_api,
    upload_images_batch
)
from utils.salesforce_file_attachment import (
    upload_content_versions_bulk_binary,
    RICH_TEXT_BULK_UPLOAD_THRESHOLD,
)
from services.entitlements import get_entitlements


def extract_image_hash(image_bytes: bytes) -> str:
    """
    Generate a hash for an image to detect duplicates.
    
    Args:
        image_bytes: Binary image data
    
    Returns:
        SHA256 hash of the image
    """
    return hashlib.sha256(image_bytes).hexdigest()


def detect_image_format(image_bytes: bytes) -> Tuple[str, str]:
    """
    Detect image format from binary data using magic bytes/file signatures.
    
    Args:
        image_bytes: Binary image data
    
    Returns:
        Tuple of (mime_type, extension) or ("image/png", "png") as default
    """
    if not image_bytes or len(image_bytes) < 4:
        return "image/png", "png"
    
    # Check magic bytes (file signatures)
    # PNG: 89 50 4E 47
    if image_bytes[:4] == b'\x89PNG':
        return "image/png", "png"
    
    # JPEG: FF D8 FF
    if image_bytes[:3] == b'\xFF\xD8\xFF':
        return "image/jpeg", "jpg"
    
    # GIF: 47 49 46 38 (GIF8)
    if image_bytes[:4] == b'GIF8':
        return "image/gif", "gif"
    
    # WebP: RIFF...WEBP
    if len(image_bytes) >= 12 and image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP':
        return "image/webp", "webp"
    
    # SVG: Check for XML declaration or <svg tag
    try:
        text_start = image_bytes[:100].decode('utf-8', errors='ignore').strip()
        if text_start.startswith('<?xml') or text_start.startswith('<svg'):
            return "image/svg+xml", "svg"
    except:
        pass
    
    # Default to PNG if format cannot be determined
    return "image/png", "png"


def extract_filename_from_url_or_alt(url: str, alt_text: str = "", image_bytes: bytes = None) -> str:
    """
    Extract or generate a proper filename from URL, alt text, or image format.
    
    Args:
        url: Image URL
        alt_text: Alt text from img tag
        image_bytes: Binary image data (for format detection)
    
    Returns:
        Proper filename with extension
    """
    # Try to use alt text if it looks like a filename
    if alt_text:
        # Check if alt text contains a file extension
        if '.' in alt_text and any(alt_text.lower().endswith(f'.{ext}') for ext in ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg']):
            # Clean up alt text (remove path, keep just filename)
            filename = alt_text.split('/')[-1].split('\\')[-1]
            # Ensure it has a valid extension
            if '.' in filename:
                return filename
    
    # Try to extract from URL (but clean it up)
    if url:
        # Remove query parameters
        url_clean = url.split('?')[0]
        # Get last part of path
        url_part = url_clean.split('/')[-1]
        # Check if it looks like a filename with extension
        if '.' in url_part and any(url_part.lower().endswith(f'.{ext}') for ext in ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg']):
            return url_part
    
    # Detect format from image bytes if available
    if image_bytes:
        mime_type, extension = detect_image_format(image_bytes)
        # Use alt text as base name if available, otherwise use generic name
        if alt_text:
            base_name = alt_text.split('/')[-1].split('\\')[-1].split('.')[0]
            # Clean base name (remove invalid characters)
            base_name = ''.join(c for c in base_name if c.isalnum() or c in (' ', '-', '_'))[:50]
            return f"{base_name}.{extension}" if base_name else f"image.{extension}"
        else:
            return f"image.{extension}"
    
    # Last resort: generic PNG filename
    return "image.png"


async def process_rich_text_images(
    html_content: str,
    access_token: str,
    instance_url: str,
    source_access_token: Optional[str] = None,
    source_instance_url: Optional[str] = None,
    target_record_id: Optional[str] = None,
    source_object_name: Optional[str] = None,
    source_field_name: Optional[str] = None,
    user_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Process images in rich text HTML content.
    
    Extracts images, downloads/uploads as needed, and returns mapping
    of original URLs to Salesforce URLs.
    
    Args:
        html_content: HTML content with image tags
        access_token: Destination Salesforce access token
        instance_url: Destination Salesforce instance URL
        source_access_token: Source Salesforce access token (if migrating from Salesforce)
        source_instance_url: Source Salesforce instance URL (if migrating from Salesforce)
        user_id: User ID for plan-based upload restrictions (optional)
    
    Returns:
        Dictionary with:
        {
            "updated_html": str,              # HTML with updated image URLs
            "image_mapping": Dict[str, str],  # Original URL -> Salesforce URL mapping
            "images_processed": int,          # Number of images processed
            "images_uploaded": int,           # Number of images uploaded
            "images_skipped": int             # Number of external URLs skipped
        }
    """
    if not html_content:
        return {
            "updated_html": html_content,
            "image_mapping": {},
            "images_processed": 0,
            "images_uploaded": 0,
            "images_skipped": 0,
            "rich_text_image_ids": []
        }
    
    # Extract images from HTML
    images = extract_images_from_html(html_content)
    
    if not images:
        return {
            "updated_html": html_content,
            "image_mapping": {},
            "images_processed": 0,
            "images_uploaded": 0,
            "images_skipped": 0,
            "rich_text_image_ids": []
        }
    
    print(f"🖼️  [RICH TEXT IMAGE] Found {len(images)} images in HTML")
    
    # Separate images by category
    images_to_upload = []
    external_urls = []
    image_hash_map = {}  # For deduplication: hash -> Salesforce URL
    rich_text_image_ids = []  # Track content_document_ids for rollback
    url_downloads: List[Tuple[Dict, str]] = []  # (img, download_url) for parallel download
    rta_downloads: List[Tuple[Dict, str, str]] = []  # (img, eid, refid) for parallel rta download
    
    for img in images:
        url = img["url"]
        
        if is_external_url(url):
            external_urls.append(url)
            continue
        
        if is_base64_image(url):
            # Base64 image - extract and prepare for upload
            image_bytes, _, _ = extract_base64_data(url)
            if image_bytes:
                # Generate hash for deduplication
                img_hash = extract_image_hash(image_bytes)
                
                # Use alt text if available, otherwise use detected format
                alt_text = img.get("alt", "")
                filename = extract_filename_from_url_or_alt(url, alt_text, image_bytes)
                
                if img_hash in image_hash_map:
                    # Reuse existing upload
                    print(f"   ♻️  [RICH TEXT IMAGE] Reusing existing upload for base64 image")
                    images_to_upload.append({
                        "url": url,
                        "image_bytes": None,  # Will use mapping
                        "filename": filename,
                        "hash": img_hash,
                        "reuse_url": image_hash_map[img_hash]
                    })
                else:
                    images_to_upload.append({
                        "url": url,
                        "image_bytes": image_bytes,
                        "filename": filename,
                        "hash": img_hash
                    })
            else:
                print(f"   ⚠️  [RICH TEXT IMAGE] Failed to extract base64 data from: {url[:50]}...")
        
        elif is_salesforce_internal_url(url):
            # Salesforce internal URL - collect for parallel download (Phase 3.3.1)
            if not source_access_token or not source_instance_url:
                print(f"   ⚠️  [RICH TEXT IMAGE] Salesforce URL found but no source credentials: {url[:50]}...")
                continue
            if source_instance_url == instance_url:
                print(f"   ⚠️  [RICH TEXT IMAGE] WARNING: Source and destination instance URLs are the same - may be using wrong token")
            
            if "/servlet/rtaImage" in url:
                import urllib.parse
                parsed_url = urllib.parse.urlparse(url)
                params = urllib.parse.parse_qs(parsed_url.query)
                eid = params.get("eid", [None])[0]
                refid = params.get("refid", [None])[0]
                if eid and refid:
                    rta_downloads.append((img, eid, refid))
                else:
                    print(f"⚠️ [RICH TEXT IMAGE] Could not extract eid/refid from rtaImage URL")
            else:
                download_url = url if (url.startswith("http://") or url.startswith("https://")) else (f"{source_instance_url.rstrip('/')}{url}" if url.startswith("/") else f"{source_instance_url.rstrip('/')}/{url}")
                url_downloads.append((img, download_url))
    
    # Phase 3.3.1: Parallel download for Salesforce URL and rtaImage
    if url_downloads or rta_downloads:
        url_results = await asyncio.gather(*[
            download_salesforce_image(download_url, source_access_token, source_instance_url)
            for (_, download_url) in url_downloads
        ]) if url_downloads else []
        rta_results = await asyncio.gather(*[
            download_rta_image_via_api(
                eid, refid, source_access_token, source_instance_url,
                source_object_name, source_field_name
            )
            for (_, eid, refid) in rta_downloads
        ]) if rta_downloads else []
        
        def _validate_and_append(img: Dict, url: str, image_bytes: Optional[bytes]) -> None:
            if not image_bytes:
                print(f"   ⚠️  [RICH TEXT IMAGE] Failed to download Salesforce image: {url[:50]}...")
                return
            if len(image_bytes) < 100:
                try:
                    content_preview = image_bytes[:200].decode('utf-8', errors='ignore').lower()
                    if '<html' in content_preview or '<!doctype' in content_preview or 'error' in content_preview:
                        print(f"❌ [RICH TEXT IMAGE] Downloaded content is HTML/error page, not image data")
                        return
                except Exception:
                    pass
            if len(image_bytes) >= 4:
                is_valid_image = (
                    image_bytes[:4] == b'\x89PNG' or image_bytes[:3] == b'\xFF\xD8\xFF' or
                    image_bytes[:4] == b'GIF8' or (len(image_bytes) >= 12 and image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP')
                )
                if not is_valid_image:
                    try:
                        content_preview = image_bytes[:500].decode('utf-8', errors='ignore')
                        if '<html' in content_preview.lower() or '<!doctype' in content_preview.lower():
                            return
                    except Exception:
                        pass
                    return
            img_hash = extract_image_hash(image_bytes)
            alt_text = img.get("alt", "")
            filename = extract_filename_from_url_or_alt(url, alt_text, image_bytes)
            if img_hash in image_hash_map:
                images_to_upload.append({
                    "url": url, "image_bytes": None, "filename": filename, "hash": img_hash,
                    "reuse_url": image_hash_map[img_hash]
                })
            else:
                images_to_upload.append({"url": url, "image_bytes": image_bytes, "filename": filename, "hash": img_hash})
        
        for (img, download_url), image_bytes in zip(url_downloads, url_results):
            _validate_and_append(img, img["url"], image_bytes)
        for (img, _, _), image_bytes in zip(rta_downloads, rta_results):
            _validate_and_append(img, img["url"], image_bytes)
        
        if url_downloads or rta_downloads:
            print(f"   📥 [RICH TEXT IMAGE] Parallel download: {len(url_downloads) + len(rta_downloads)} images")
    
    # Upload images to destination Salesforce
    image_mapping = {}
    
    if images_to_upload:
        # Check user plan and validate file attachment uploads
        images_allowed = []
        images_restricted = []
        
        if user_id:
            user_plan = await get_entitlements().get_user_plan(user_id)
            if user_plan:
                # Validate each image individually
                for img in images_to_upload:
                    original_url = img["url"]
                    image_bytes = img.get("image_bytes")
                    
                    if not image_bytes:
                        # Reuse case - already uploaded, allow (don't count against limit)
                        images_allowed.append(img)
                        continue
                    
                    # Calculate file size
                    file_size_mb = len(image_bytes) / (1024 * 1024)
                    
                    # Validate file attachment upload (general validation for ContentVersion)
                    is_valid, message, plan_info = await get_entitlements().validate_file_attachment_upload(
                        user_id=user_id,
                        file_size_mb=file_size_mb,
                        file_count=1
                    )
                    
                    if is_valid:
                        images_allowed.append(img)
                    else:
                        # Limit reached or size exceeded - skip upload
                        print(f"   ⚠️  [FILE ATTACHMENT] {message}")
                        images_restricted.append(img)
            else:
                # No plan found - treat as free (restrict all)
                images_restricted = images_to_upload
                images_allowed = []
        else:
            # No user_id - allow all (backward compatibility)
            images_allowed = images_to_upload
        
        # Update images_to_upload with only allowed images
        if images_restricted:
            print(f"   ⚠️  [FILE ATTACHMENT] Skipping upload of {len(images_restricted)} files (limit reached or size exceeded)")
            # Keep original URLs for restricted images
            for img in images_restricted:
                original_url = img["url"]
                image_mapping[original_url] = original_url  # Keep original URL
        
        images_to_upload = images_allowed
        
        # Separate images that need upload vs reuse
        images_for_upload = [img for img in images_to_upload if img.get("image_bytes") is not None]
        images_for_reuse = [img for img in images_to_upload if img.get("reuse_url")]
        
        # Handle reuse first
        for img in images_for_reuse:
            original_url = img["url"]
            reuse_url = img["reuse_url"]
            image_mapping[original_url] = reuse_url
            print(f"   ♻️  [RICH TEXT IMAGE] Reusing: {original_url[:50]}... -> {reuse_url}")
        
        # Upload new images (Phase 3.3.2: bulk path when count >= threshold)
        if images_for_upload:
            print(f"   📤 [RICH TEXT IMAGE] Uploading {len(images_for_upload)} images to Salesforce...")
            
            if len(images_for_upload) >= RICH_TEXT_BULK_UPLOAD_THRESHOLD:
                # Bulk path: upload_content_versions_bulk_binary (no link to record)
                payload = [
                    {
                        "file_bytes": img.get("image_bytes"),
                        "filename": img.get("filename", "image.png"),
                        "title": img.get("filename"),
                        "linked_entity_id": None,
                    }
                    for img in images_for_upload
                ]
                bulk_results = await upload_content_versions_bulk_binary(
                    files=payload,
                    access_token=access_token,
                    instance_url=instance_url,
                    user_id=user_id,
                )
                upload_mapping = {}
                for img, res in zip(images_for_upload, bulk_results):
                    original_url = img["url"]
                    if res.get("success") and res.get("content_version_id"):
                        salesforce_url = f"/sfc/servlet.shepherd/version/download/{res['content_version_id']}"
                        upload_mapping[original_url] = {
                            "salesforce_url": salesforce_url,
                            "content_document_id": res.get("content_document_id"),
                            "content_version_id": res.get("content_version_id"),
                        }
            else:
                upload_mapping = await upload_images_batch(
                    images=images_for_upload,
                    access_token=access_token,
                    instance_url=instance_url,
                    first_publish_location_id=None
                )
            
            # Update hash map with new uploads
            successful_uploads = []
            for img in images_for_upload:
                original_url = img["url"]
                img_hash = img["hash"]
                if original_url in upload_mapping:
                    upload_result = upload_mapping[original_url]
                    salesforce_url = upload_result.get("salesforce_url") if isinstance(upload_result, dict) else upload_result
                    image_hash_map[img_hash] = salesforce_url
                    image_mapping[original_url] = salesforce_url
                    successful_uploads.append(img)
                    if isinstance(upload_result, dict) and upload_result.get("content_document_id"):
                        rich_text_image_ids.append({
                            "content_document_id": upload_result["content_document_id"],
                            "title": img.get("filename", "image"),
                            "status": "uploaded"
                        })
            
            # Track successful file attachment uploads (bulk path already tracks in upload_content_versions_bulk_binary)
            if user_id and successful_uploads and len(images_for_upload) < RICH_TEXT_BULK_UPLOAD_THRESHOLD:
                total_size_mb = sum(
                    len(img.get("image_bytes", b"")) / (1024 * 1024)
                    for img in successful_uploads
                    if img.get("image_bytes")
                )
                upload_count = len(successful_uploads)
                await get_entitlements().track_file_attachment_upload(
                    user_id=user_id,
                    file_count=upload_count,
                    total_size_mb=total_size_mb
                )
                print(f"   📊 [FILE ATTACHMENT] Tracked {upload_count} file uploads ({total_size_mb:.2f} MB)")
            
            # Update reuse mapping for future images
            for img in images_for_reuse:
                img_hash = img["hash"]
                if img_hash in image_hash_map:
                    img["reuse_url"] = image_hash_map[img_hash]
                    image_mapping[img["url"]] = image_hash_map[img_hash]
    
    # Update HTML with Salesforce URLs
    from utils.rich_text_handler import update_html_with_salesforce_images
    updated_html = update_html_with_salesforce_images(html_content, image_mapping)
    
    return {
        "updated_html": updated_html,
        "image_mapping": image_mapping,
        "images_processed": len(images),
        "images_uploaded": len([img for img in images_to_upload if img.get("image_bytes")]),
        "images_skipped": len(external_urls),
        "rich_text_image_ids": rich_text_image_ids  # ContentDocument IDs for rollback
    }


async def process_rich_text_field(
    field_value: str,
    field_data_type: str,
    access_token: str,
    instance_url: str,
    source_access_token: Optional[str] = None,
    source_instance_url: Optional[str] = None,
    target_record_id: Optional[str] = None,
    source_object_name: Optional[str] = None,
    source_field_name: Optional[str] = None,
    user_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Process a rich text field value, extracting and uploading images if needed.
    
    Args:
        field_value: Rich text field value (HTML content)
        field_data_type: Salesforce field data type
        access_token: Destination Salesforce access token
        instance_url: Destination Salesforce instance URL
        source_access_token: Source Salesforce access token (if migrating from Salesforce)
        source_instance_url: Source Salesforce instance URL (if migrating from Salesforce)
        user_id: User ID for plan-based upload restrictions (optional)
    
    Returns:
        Dictionary with processed field value and metadata
    """
    from utils.rich_text_handler import is_rich_text_field
    
    # Check if field is rich text (pass field_value to check for HTML content)
    if not is_rich_text_field(field_data_type, field_value):
        return {
            "updated_value": field_value,
            "is_rich_text": False,
            "images_processed": 0
        }
    
    # Process images in rich text
    result = await process_rich_text_images(
        html_content=field_value,
        access_token=access_token,
        instance_url=instance_url,
        source_access_token=source_access_token,
        source_instance_url=source_instance_url,
        target_record_id=target_record_id,
        source_object_name=source_object_name,
        source_field_name=source_field_name,
        user_id=user_id
    )
    
    return {
        "updated_value": result["updated_html"],
        "is_rich_text": True,
        "images_processed": result["images_processed"],
        "images_uploaded": result["images_uploaded"],
        "images_skipped": result["images_skipped"],
        "image_mapping": result["image_mapping"],
        "rich_text_image_ids": result.get("rich_text_image_ids", [])  # ContentDocument IDs for rollback
    }
