# Backend timezone utilities for schedule and user display

from datetime import datetime
import pytz
from typing import Optional, Tuple

def parse_timezone_string(timezone_str: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse timezone string and return (iana_timezone, display_name).
    Supports two formats:
    1. IANA format: "America/Los_Angeles|Los Angeles, USA" -> ("America/Los_Angeles", "Los Angeles, USA")
    2. Legacy UTC offset format: "UTC-8 (Pacific Time)" -> (None, "UTC-8 (Pacific Time)")
    
    Returns:
        Tuple of (iana_timezone, display_name_or_legacy_format)
    """
    if not timezone_str:
        return None, None
    
    # Check if it's IANA format (contains | separator)
    if "|" in timezone_str:
        parts = timezone_str.split("|", 1)
        iana_tz = parts[0].strip()
        display_name = parts[1].strip() if len(parts) > 1 else iana_tz
        return iana_tz, display_name
    
    # Check if it's legacy UTC offset format
    if timezone_str.startswith("UTC"):
        return None, timezone_str
    
    # If it's just an IANA timezone without display name, use it as-is
    try:
        # Validate it's a valid IANA timezone
        pytz.timezone(timezone_str)
        return timezone_str, timezone_str
    except pytz.exceptions.UnknownTimeZoneError:
        # Not a valid IANA timezone, treat as legacy format
        return None, timezone_str

def get_timezone_object(timezone_str: str) -> pytz.BaseTzInfo:
    """
    Get pytz timezone object from timezone string.
    Supports both IANA timezones and legacy UTC offset format.
    
    Args:
        timezone_str: Timezone string (IANA format or legacy UTC offset format)
    
    Returns:
        pytz timezone object
    """
    iana_tz, _ = parse_timezone_string(timezone_str)
    
    # Mapping for invalid timezones that may exist in the database (backward compatibility)
    # These were previously shown in the UI but are not valid IANA timezones
    INVALID_TIMEZONE_MAPPING = {
        "America/San_Francisco": "America/Los_Angeles",  # Same timezone as Los Angeles
        "America/Seattle": "America/Los_Angeles",  # Same timezone as Los Angeles
        "America/Calgary": "America/Denver",  # Mountain Time
        "America/Dallas": "America/Chicago",  # Central Time
        "America/Miami": "America/New_York",  # Eastern Time
        "America/Atlanta": "America/New_York",  # Eastern Time
        "Asia/Islamabad": "Asia/Karachi",  # Same timezone
        "Asia/Hanoi": "Asia/Ho_Chi_Minh",  # Same timezone
    }
    
    if iana_tz:
        # Check if it's an invalid timezone that needs mapping
        if iana_tz in INVALID_TIMEZONE_MAPPING:
            mapped_tz = INVALID_TIMEZONE_MAPPING[iana_tz]
            print(f"⚠️ Invalid IANA timezone '{iana_tz}' mapped to '{mapped_tz}' (backward compatibility)")
            iana_tz = mapped_tz
        
        # Use IANA timezone (supports DST automatically)
        try:
            return pytz.timezone(iana_tz)
        except pytz.exceptions.UnknownTimeZoneError:
            print(f"⚠️ Unknown IANA timezone: {iana_tz}, falling back to America/Los_Angeles")
            return pytz.timezone("America/Los_Angeles")
    else:
        # Legacy format: parse UTC offset
        offset_hours = parse_timezone_offset(timezone_str)
        offset_minutes = int(offset_hours * 60)
        return pytz.FixedOffset(offset_minutes)

def parse_timezone_offset(timezone_str: str) -> float:
    """
    Parse timezone string like "UTC-8 (Pacific Time)" or "UTC+5:30 (India)" and return offset in hours
    Returns the UTC offset in hours as a float (e.g., -8.0 for UTC-8, +5.5 for UTC+5:30)
    """
    try:
        # Extract UTC offset from string like "UTC-8 (Pacific Time)" or "UTC+5:30 (India)"
        if "UTC+" in timezone_str:
            offset_str = timezone_str.split("UTC+")[1].split(" ")[0]
            # Handle offsets with minutes like "5:30" -> 5.5 hours
            if ":" in offset_str:
                hours, minutes = map(int, offset_str.split(":"))
                return hours + (minutes / 60.0)
            else:
                return float(offset_str)
        elif "UTC-" in timezone_str:
            offset_str = timezone_str.split("UTC-")[1].split(" ")[0]
            # Handle offsets with minutes like "5:30" -> -5.5 hours
            if ":" in offset_str:
                hours, minutes = map(int, offset_str.split(":"))
                return -(hours + (minutes / 60.0))
            else:
                return -float(offset_str)
        else:
            # Try to parse as IANA timezone
            iana_tz, _ = parse_timezone_string(timezone_str)
            if iana_tz:
                try:
                    tz_obj = pytz.timezone(iana_tz)
                    # Get current offset for this timezone
                    now = datetime.now(tz_obj)
                    offset_seconds = now.utcoffset().total_seconds()
                    return offset_seconds / 3600.0  # Convert to hours
                except:
                    return -8.0  # Default fallback
            return -8.0  # Default to Pacific Time if parsing fails
    except (ValueError, IndexError) as e:
        print(f"⚠️ Failed to parse timezone '{timezone_str}': {e}, defaulting to UTC-8")
        return -8.0  # Default to Pacific Time if parsing fails
