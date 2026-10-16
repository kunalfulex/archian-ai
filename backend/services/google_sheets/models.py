"""Data models for Google Sheets executor v2."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class OperationType(str, Enum):
    CREATE = "create"
    UPSERT = "upsert"
    SEARCH = "search"
    RENAME = "rename"
    EXTRACT = "extract"
    FORMULA = "formula"
    UNSUPPORTED = "unsupported"


@dataclass
class SpreadsheetContext:
    found: bool
    spreadsheet_id: Optional[str] = None
    spreadsheet_name: Optional[str] = None
    sheet_name: Optional[str] = "Sheet1"
    headers: List[str] = field(default_factory=list)
    key_columns: List[str] = field(default_factory=list)
    source: str = ""
    error: Optional[str] = None
    message: Optional[str] = None
    estimated_row_count: Optional[int] = None

    google_sheets_connection_id: Optional[str] = None
    google_account_email: Optional[str] = None
    candidates: List[Dict[str, Any]] = field(default_factory=list)

    def to_detection_dict(self) -> Dict[str, Any]:
        """Shape compatible with legacy detect_google_sheet_from_prompt."""
        if not self.found:
            out: Dict[str, Any] = {
                "found": False,
                "message": self.message or "Could not determine spreadsheet.",
                "error": self.error,
            }
            if self.candidates:
                out["candidates"] = self.candidates
            return out
        return {
            "found": True,
            "spreadsheet_id": self.spreadsheet_id,
            "spreadsheet_name": self.spreadsheet_name,
            "sheet_name": self.sheet_name,
            "source": self.source,
            "google_sheets_connection_id": self.google_sheets_connection_id,
            "google_account_email": self.google_account_email,
        }


@dataclass
class OperationPlan:
    operation: OperationType
    records: List[Dict[str, Any]] = field(default_factory=list)
    is_bulk: bool = False
    match_keys: List[str] = field(default_factory=list)
    search_query: Optional[str] = None
    rename: Optional[Dict[str, str]] = None
    unsupported_reason: Optional[str] = None
    confidence: float = 1.0
    # Join: second workbook/tab (same spreadsheet_id allowed, different sheet_name)
    join_right_workbook: Optional[str] = None
    join_right_sheet: Optional[str] = None
    join_left_key: Optional[str] = None
    join_right_key: Optional[str] = None
    join_how: str = "left"
    join_output_sheet: Optional[str] = None


@dataclass
class UpsertResult:
    operation: str = "upsert"
    rows_inserted: int = 0
    rows_updated: int = 0
    processed_records: int = 0
