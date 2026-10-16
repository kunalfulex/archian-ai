"""
Rich Text Field Handler

Utilities for processing Salesforce rich text fields, including:
- Detecting rich text field types
- Categorizing image URLs (external, Salesforce internal, base64)
- Extracting images from HTML content
- Updating HTML with Salesforce image URLs
"""

import re
from typing import List, Dict, Any, Optional, Tuple
from html.parser import HTMLParser
from db.connection import database


def is_rich_text_field(data_type: str, field_value: Optional[str] = None) -> bool:
    """
    Check if a Salesforce field type is a rich text field.
    
    Note: RichTextArea fields are stored as "textarea" (lowercase) in the database,
    so we also check if the field value contains HTML content.
    
    Args:
        data_type: Salesforce field data type (e.g., "richtextarea", "html", "textarea", "string")
                  Note: data_type from database is lowercase
        field_value: Optional field value to check for HTML content
    
    Returns:
        True if the field is a rich text field type or contains HTML content
    """
    if not data_type:
        return False
    
    # Convert to lowercase for comparison (database stores in lowercase)
    data_type_lower = data_type.lower()
    
    # Explicit rich text types (check lowercase)
    rich_text_types = ["richtextarea", "html"]
    if data_type_lower in rich_text_types:
        return True
    
    # TextArea and LongTextArea might be rich text - check content if provided
    if data_type_lower in ["textarea", "longtextarea"]:
        if field_value:
            # Check if content contains HTML tags
            html_pattern = re.compile(r'<[a-z][\s\S]*>', re.IGNORECASE)
            has_html = html_pattern.search(field_value)
            if has_html:
                return True
        # If no value provided, assume it could be rich text (safer to process)
        return True
    
    return False


def categorize_image_url(url: str) -> str:
    """
    Categorize image URL to determine if upload is needed.
    
    Note: In Salesforce, files are always stored internally, so we only
    encounter external URLs, Salesforce internal URLs, or base64 data URIs.
    
    Args:
        url: Image URL or data URI
    
    Returns:
        - "external": External URL (http/https), keep as-is
        - "salesforce_internal": Salesforce org-specific URL, needs upload
        - "base64": Base64 data URI, needs upload
    """
    if not url or not isinstance(url, str):
        return "external"
    
    url = url.strip()
    
    # Check for base64 data URI
    if url.startswith("data:image/"):
        return "base64"
    
    # Check for HTTP/HTTPS URLs
    if url.startswith("http://") or url.startswith("https://"):
        is_salesforce_domain = (
            ".salesforce.com" in url or
            ".force.com" in url or
            "salesforce.com" in url or
            "force.com" in url
        )
        
        if is_salesforce_domain:
            salesforce_file_patterns = [
                "/sfc/servlet.shepherd/",
                "/servlet/servlet.FileDownload",
                "/servlet/rtaImage",
                "/servlet/servlet.ImageServer",
                "/servlet/servlet.FilePreview",
                "/file.force.com",
                "/content.force.com"
            ]
            if any(pattern in url for pattern in salesforce_file_patterns):
                return "salesforce_internal"
        
        return "external"
    
    # Salesforce internal URLs (relative paths within Salesforce)
    if url.startswith("/sfc/servlet.shepherd/"):
        return "salesforce_internal"
    if url.startswith("/servlet/rtaImage") or "/servlet/rtaImage" in url:
        return "salesforce_internal"
    if url.startswith("/servlet/servlet.") or "/servlet/servlet." in url:
        return "salesforce_internal"
    if url.startswith("/servlet/") or url.startswith("/sfc/"):
        return "salesforce_internal"
    
    return "external"


def is_external_url(url: str) -> bool:
    """
    Check if URL is an external URL (should be kept as-is).
    
    Args:
        url: Image URL
    
    Returns:
        True if URL is external and should not be processed
    """
    return categorize_image_url(url) == "external"


def is_salesforce_internal_url(url: str) -> bool:
    """
    Check if URL is a Salesforce internal URL (needs download and upload).
    
    Args:
        url: Image URL
    
    Returns:
        True if URL is Salesforce internal and needs processing
    """
    return categorize_image_url(url) == "salesforce_internal"


def is_base64_image(url: str) -> bool:
    """
    Check if URL is a base64 data URI (needs extraction and upload).
    
    Args:
        url: Image URL or data URI
    
    Returns:
        True if URL is a base64 data URI
    """
    return categorize_image_url(url) == "base64"


class ImageExtractor(HTMLParser):
    """
    HTML Parser to extract image sources from HTML content.
    """
    
    def __init__(self):
        super().__init__()
        self.images: List[Dict[str, Any]] = []
    
    def handle_starttag(self, tag, attrs):
        if tag == "img":
            attrs_dict = dict(attrs)
            src = attrs_dict.get("src") or attrs_dict.get("data-src")
            if src:
                self.images.append({
                    "url": src,
                    "alt": attrs_dict.get("alt", ""),
                })


def extract_images_from_html(html_content: str) -> List[Dict[str, Any]]:
    """
    Extract all image sources from HTML content.
    
    Args:
        html_content: HTML string containing image tags
    
    Returns:
        List of dicts with "url" and "alt" per image. Category is determined
        later by the service via categorize_image_url.
    """
    if not html_content or not isinstance(html_content, str):
        return []
    
    parser = ImageExtractor()
    try:
        parser.feed(html_content)
    except Exception as e:
        print(f"⚠️ [RICH TEXT] Error parsing HTML: {e}")
        return []
    
    return parser.images


def update_html_with_salesforce_images(
    html_content: str,
    image_mapping: Dict[str, str]
) -> str:
    """
    Update HTML content by replacing image URLs with Salesforce URLs.
    
    Only replaces images that were uploaded (in image_mapping).
    External URLs are preserved unchanged.
    
    Args:
        html_content: Original HTML content
        image_mapping: Dictionary mapping original URLs to Salesforce URLs
                       Format: {original_url: salesforce_url}
    
    Returns:
        Updated HTML content with Salesforce image URLs
    """
    if not html_content or not isinstance(html_content, str):
        return html_content
    
    if not image_mapping:
        return html_content
    
    updated_html = html_content
    
    # Replace each image URL that was uploaded
    for original_url, salesforce_url in image_mapping.items():
        # Try HTML-decoded version (e.g., &amp; -> &)
        import html
        html_decoded_url = html.unescape(original_url)
        if html_decoded_url != original_url and html_decoded_url in updated_html:
            original_url = html_decoded_url
        
        # Escape special regex characters in the URL
        escaped_url = re.escape(original_url)
        
        # Also try HTML-entity-encoded version of the URL (e.g., & -> &amp;)
        # HTMLParser decodes entities when extracting, but the original HTML might have encoded entities
        html_encoded_url = original_url.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
        
        # Try both encoded and decoded versions
        for url_to_match in [original_url, html_encoded_url]:
            escaped_url = re.escape(url_to_match)
            
            # Replace in src attribute
            pattern1 = f'src=["\']?{escaped_url}["\']?'
            before_replace = updated_html
            updated_html = re.sub(pattern1, f'src="{salesforce_url}"', updated_html, flags=re.IGNORECASE)
            
            if before_replace != updated_html:
                break  # Successfully replaced, no need to try other variants
        
        # Also try data-src with both encoded and decoded
        for url_to_match in [original_url, html_encoded_url]:
            escaped_url = re.escape(url_to_match)
            
            # Replace in data-src attribute (lazy loading)
            pattern2 = f'data-src=["\']?{escaped_url}["\']?'
            before_replace_data_src = updated_html
            updated_html = re.sub(pattern2, f'data-src="{salesforce_url}"', updated_html, flags=re.IGNORECASE)
            
            if before_replace_data_src != updated_html:
                break
    
    return updated_html


async def fetch_field_data_types(
    mapping: Dict[str, Any],
    user_id: str,
    org_id: Optional[str] = None
) -> Dict[tuple, str]:
    """
    Fetch data types for all fields in the mapping.
    
    Args:
        mapping: Field mapping dictionary
        user_id: User ID
        org_id: Optional org ID
    
    Returns:
        Dictionary mapping (object_name, field_name) to data_type
    """
    field_data_types = {}
    
    try:
        # Collect all (object, field) pairs from mapping
        object_field_pairs = set()
        
        for source_field, mapping_info in mapping.items():
            if isinstance(mapping_info, list):
                # Array mapping (split fields)
                for mapping_item in mapping_info:
                    if isinstance(mapping_item, dict):
                        obj = mapping_item.get("object")
                        field = mapping_item.get("field")
                        if obj and field:
                            object_field_pairs.add((obj, field))
            elif isinstance(mapping_info, dict):
                # Simple mapping
                obj = mapping_info.get("object")
                field = mapping_info.get("field")
                if obj and field:
                    object_field_pairs.add((obj, field))
        
        if not object_field_pairs:
            return field_data_types
        
        # Build query to fetch data types
        params = {"user_id": user_id}
        org_filter = ""
        if org_id:
            org_filter = "AND org_id = :org_id"
            params["org_id"] = org_id
        
        conditions = []
        for idx, (obj, field) in enumerate(object_field_pairs):
            obj_key = f"obj_{idx}"
            field_key = f"field_{idx}"
            conditions.append(f"(object_api_name = :{obj_key} AND field_api_name = :{field_key})")
            params[obj_key] = obj
            params[field_key] = field
        
        query = f"""
            SELECT object_api_name, field_api_name, data_type
            FROM salesforce_object_fields
            WHERE user_id = :user_id {org_filter}
            AND ({' OR '.join(conditions)})
        """
        
        fields_metadata = await database.fetch_all(query, values=params)
        
        for field_meta in fields_metadata:
            field_dict = dict(field_meta) if hasattr(field_meta, '_mapping') or hasattr(field_meta, 'keys') else field_meta
            key = (field_dict["object_api_name"], field_dict["field_api_name"])
            field_data_types[key] = field_dict.get("data_type", "")
        
        print(f"📋 [RICH TEXT] Fetched data types for {len(field_data_types)} fields")
        
    except Exception as e:
        print(f"⚠️ [RICH TEXT] Error fetching field data types: {e}")
    
    return field_data_types


def extract_base64_data(data_uri: str) -> Tuple[Optional[bytes], Optional[str], Optional[str]]:
    """
    Extract binary data, MIME type, and filename from base64 data URI.
    
    Args:
        data_uri: Base64 data URI (e.g., "data:image/png;base64,iVBORw0KGgo...")
    
    Returns:
        Tuple of (image_bytes, mime_type, filename) or (None, None, None) if invalid
    """
    if not data_uri or not data_uri.startswith("data:image/"):
        return None, None, None
    
    try:
        # Parse data URI: data:image/png;base64,<data>
        match = re.match(r'data:image/([^;]+);base64,(.+)', data_uri)
        if not match:
            return None, None, None
        
        mime_type = match.group(1)
        base64_data = match.group(2)
        
        # Decode base64
        import base64
        image_bytes = base64.b64decode(base64_data)
        
        # Generate filename from MIME type
        extension_map = {
            "png": "png",
            "jpeg": "jpg",
            "jpg": "jpg",
            "gif": "gif",
            "webp": "webp",
            "svg+xml": "svg"
        }
        extension = extension_map.get(mime_type.lower(), "png")
        filename = f"image.{extension}"
        
        return image_bytes, mime_type, filename
    
    except Exception as e:
        print(f"⚠️ [RICH TEXT] Error extracting base64 data: {e}")
        return None, None, None
