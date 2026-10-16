"""Column header resolution for create-spreadsheet flows (shared with mapping provenance)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

HEADER_SOURCE_FROM_EXTRACTION = "from_extraction"
HEADER_SOURCE_FROM_USER = "from_user"
HEADER_SOURCE_USER_SPECIFIED_INLINE = "user_specified_inline"

_VALID_HEADER_SOURCES = frozenset(
    {
        HEADER_SOURCE_FROM_EXTRACTION,
        HEADER_SOURCE_FROM_USER,
        HEADER_SOURCE_USER_SPECIFIED_INLINE,
    }
)

_ATTACHMENT_KEYS = frozenset({"Attachments", "_attachments", "attachments"})


def _normalize_headers(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        s = str(item or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def normalize_spreadsheet_header_source(raw: Any) -> str:
    src = (str(raw or "")).strip().lower()
    if src in _VALID_HEADER_SOURCES:
        return src
    return ""


def column_keys_from_record(record: Any) -> List[str]:
    if not isinstance(record, dict):
        return []
    return [k for k in record.keys() if k not in _ATTACHMENT_KEYS]


def resolve_pinned_headers(
    classification: Dict[str, Any],
    goal: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Headers pinned on classification or PLAN_SCOPE (user-specified or clar apply)."""
    g = dict(goal or {})
    headers = _normalize_headers((classification or {}).get("planner_spreadsheet_headers"))
    if not headers:
        headers = _normalize_headers(g.get("spreadsheet_headers"))
    return headers


def resolve_active_job_id(classification: Dict[str, Any]) -> str:
    return (
        (classification.get("active_job_id") or "")
        or (classification.get("parent_job_id") or "")
    ).strip()


def read_spreadsheet_header_source(
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> str:
    """
    PLAN_SCOPE LLM policy for create-sheet column provenance.

    When absent, infer from structural facts (extract in plan, active job, pinned headers).
    """
    from services.planning.plan_scope import read_plan_scope

    goal = dict(parsed_goal or read_plan_scope(classification) or {})
    explicit = normalize_spreadsheet_header_source(goal.get("spreadsheet_header_source"))
    if explicit:
        return explicit

    ops = [str(o).lower() for o in (goal.get("operations") or []) if str(o).strip()]
    tokens = _pipeline_tokens(classification, goal)
    create_flow = bool(
        goal.get("create_destination")
        or "create" in ops
        or classification.get("planner_create_spreadsheet")
    )

    pinned_cls = _normalize_headers((classification or {}).get("planner_spreadsheet_headers"))
    if pinned_cls:
        return HEADER_SOURCE_USER_SPECIFIED_INLINE

    if create_flow and ("extract" in ops or "extract" in tokens):
        return HEADER_SOURCE_FROM_EXTRACTION

    if create_flow and resolve_active_job_id(classification):
        return HEADER_SOURCE_FROM_EXTRACTION

    pinned_goal = _normalize_headers(goal.get("spreadsheet_headers"))
    if pinned_goal:
        return HEADER_SOURCE_USER_SPECIFIED_INLINE

    if create_flow:
        return HEADER_SOURCE_FROM_USER
    return ""


def _pipeline_tokens(
    classification: Dict[str, Any],
    goal: Dict[str, Any],
) -> List[str]:
    from services.planning.plan_scope import resolve_pipeline_tokens_from_goal

    if classification.get("_pipeline_tokens"):
        tokens = classification.get("_pipeline_tokens")
        if isinstance(tokens, list) and tokens:
            return [str(t).lower() for t in tokens if str(t).strip()]
    if goal:
        return resolve_pipeline_tokens_from_goal(classification, goal)
    return [
        str(o).lower() for o in (classification.get("operations") or []) if str(o).strip()
    ]


def create_headers_use_extraction(
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]] = None,
    pipeline_tokens: Optional[List[str]] = None,
) -> bool:
    """True when create execution should load column names from extract / active job."""
    from services.planning.plan_scope import read_plan_scope

    goal = dict(parsed_goal or read_plan_scope(classification) or {})
    source = read_spreadsheet_header_source(classification, goal)
    if source == HEADER_SOURCE_FROM_EXTRACTION:
        return True
    if source in (HEADER_SOURCE_FROM_USER, HEADER_SOURCE_USER_SPECIFIED_INLINE):
        return False

    tokens = pipeline_tokens or _pipeline_tokens(classification, goal)
    goal_ops = [str(o).lower() for o in (goal.get("operations") or []) if str(o).strip()]
    if "extract" in tokens or "extract" in goal_ops:
        return True
    if resolve_active_job_id(classification):
        return True
    return False


def create_headers_slot_satisfied(
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]] = None,
    pipeline_tokens: Optional[List[str]] = None,
) -> bool:
    """CREATE_HEADERS slot: satisfied when policy + pins say we must not ask the user."""
    from services.planning.create_metadata import _goal_has_create
    from services.planning.plan_scope import read_plan_scope

    goal = dict(parsed_goal or read_plan_scope(classification) or {})
    if not (_goal_has_create(goal) or classification.get("planner_create_spreadsheet")):
        return True

    source = read_spreadsheet_header_source(classification, goal)
    if source == HEADER_SOURCE_FROM_EXTRACTION:
        return True
    if resolve_pinned_headers(classification, goal):
        return True
    if source == HEADER_SOURCE_FROM_USER:
        return False
    if create_headers_use_extraction(classification, goal, pipeline_tokens=pipeline_tokens):
        return True
    return bool(resolve_pinned_headers(classification, goal))


def create_headers_from_extraction(
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]] = None,
    pipeline_tokens: Optional[List[str]] = None,
) -> bool:
    """Backward-compatible alias for create_headers_use_extraction."""
    return create_headers_use_extraction(
        classification, parsed_goal, pipeline_tokens=pipeline_tokens
    )


def should_require_create_headers_clarification(
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]] = None,
    pipeline_tokens: Optional[List[str]] = None,
) -> bool:
    """True when pick_create_headers should be emitted."""
    return not create_headers_slot_satisfied(
        classification, parsed_goal, pipeline_tokens=pipeline_tokens
    )


def filter_create_header_clarifications(
    clarifications: List[Any],
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> List[Any]:
    """Drop pick_create_headers when CREATE_HEADERS slot is satisfied."""
    if should_require_create_headers_clarification(classification, parsed_goal):
        return list(clarifications or [])

    def _ctype(clar: Any) -> str:
        if isinstance(clar, dict):
            return (clar.get("clarification_type") or "").strip()
        return (getattr(clar, "clarification_type", None) or "").strip()

    return [c for c in (clarifications or []) if _ctype(c) != "pick_create_headers"]


@dataclass
class CreateHeaderContract:
    use_extraction_headers: bool
    headers: List[str]
    spreadsheet_header_source: str
    extraction_job_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "use_extraction_headers": self.use_extraction_headers,
            "headers": list(self.headers),
            "spreadsheet_header_source": self.spreadsheet_header_source,
            "extraction_job_id": self.extraction_job_id,
        }


def build_create_header_contract(
    classification: Dict[str, Any],
    goal: Optional[Dict[str, Any]] = None,
    *,
    pipeline_tokens: Optional[List[str]] = None,
    active_job_id: Optional[str] = None,
) -> CreateHeaderContract:
    """Execution contract flags for google_sheets_create_from_prompt."""
    from services.planning.plan_scope import read_plan_scope

    g = dict(goal or read_plan_scope(classification))
    tokens = pipeline_tokens or _pipeline_tokens(classification, g)
    source = read_spreadsheet_header_source(classification, g)
    use_extraction = create_headers_use_extraction(
        classification, g, pipeline_tokens=tokens
    )
    headers = resolve_pinned_headers(classification, g) if not use_extraction else []
    job_id = (active_job_id or resolve_active_job_id(classification) or None) or None
    return CreateHeaderContract(
        use_extraction_headers=use_extraction,
        headers=headers,
        spreadsheet_header_source=source,
        extraction_job_id=job_id,
    )


async def load_job_column_headers(
    job_id: str,
    user_id: str,
) -> Optional[List[str]]:
    """Same column keys mapping uses from a completed extraction job."""
    from services.extraction_results_service import ExtractionResultsService

    row = await ExtractionResultsService.get_extraction_result(job_id, str(user_id))
    if not row:
        return None
    data = row.get("extracted_data") or []
    if isinstance(data, list) and data and isinstance(data[0], dict):
        keys = column_keys_from_record(data[0])
        return keys or None
    return None
