"""User-facing copy when automated mapping cannot resolve a destination target."""

from typing import Optional

_CLARIFICATION_MARKER = "Please be more specific about where in the destination"
_SPREADSHEET_PICKER_MARKERS = (
    "could not find a matching spreadsheet",
    "could not determine which spreadsheet",
    "could not resolve spreadsheet",
    "which google sheet should we use",
    "which google sheet should receive",
)


def is_mapping_destination_clarification(message: Optional[str]) -> bool:
    msg = (message or "").lower()
    if _CLARIFICATION_MARKER.lower() in msg:
        return True
    return any(marker in msg for marker in _SPREADSHEET_PICKER_MARKERS)


def mapping_destination_clarification_message(
    *,
    destination_name: Optional[str] = None,
    platform: Optional[str] = None,
    document_type: Optional[str] = None,
) -> str:
    """
    Ask the user to name a concrete target inside the destination (object/table/sheet).
    """
    _ = document_type  # reserved for logging; omit from user copy
    platform_l = (platform or "").strip().lower()
    dest = (destination_name or "").strip()

    if platform_l == "salesforce" and dest:
        return (
            f"I couldn't tell which Salesforce object in {dest} to use for this data. "
            f"{_CLARIFICATION_MARKER} you'd like it—for example Contact, Account, or Lead."
        )
    if platform_l in ("google_sheets", "gsheets") and dest:
        return (
            f"I couldn't tell which sheet or tab in {dest} to use for this data. "
            f"{_CLARIFICATION_MARKER} you'd like it—for example a workbook name and tab."
        )
    if platform_l == "sql" and dest:
        return (
            f"I couldn't tell which table in {dest} to use for this data. "
            f"{_CLARIFICATION_MARKER} you'd like it—for example a table name."
        )
    if dest:
        return (
            f"I couldn't match this data to a target in {dest}. "
            f"{_CLARIFICATION_MARKER} you'd like it."
        )
    return (
        "I couldn't match this data to a destination. "
        f"{_CLARIFICATION_MARKER} you'd like it—for example a Salesforce object, "
        "database table, or spreadsheet."
    )
