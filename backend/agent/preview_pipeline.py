"""Build dynamic operation-preview step lists from agent plans (not a fixed extract-only template)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent.plan import ExecutionPlan, PlanStep
from agent.planner import _ARCHITECT_TOOL_LABELS
from agent.session import AgentSession
from utils.operation_labels import label_for_operation, pipeline_step_for_operation

# Tools that stream granular setup milestones (connect → catalog → plan → extract → preview).
_EXTRACT_TOOL_IDS = frozenset(
    {
        "extract_salesforce",
        "extract_sql",
        "extract_google_sheets",
        "extract_file",
        "file_reextract",
    }
)

_EXTRACT_SETUP_STEPS: List[tuple[str, str]] = [
    ("connect_source", "Connecting to source"),
    ("inspect_catalog", "Reviewing fields and structure"),
    ("build_extraction_plan", "Building extraction plan"),
    ("extract_records", "Extracting records"),
    ("prepare_preview", "Preparing data preview"),
]

def _source_display(source: Optional[str]) -> str:
    s = (source or "").strip().lower()
    if s in ("google_sheets", "gsheets"):
        return "Google Sheets"
    if s == "salesforce":
        return "Salesforce"
    if s == "sql":
        return "SQL database"
    if s == "file":
        return "File"
    return (source or "source").replace("_", " ").title()


def connect_source_label(source: Optional[str], source_name: Optional[str]) -> str:
    src = _source_display(source)
    sn = (source_name or "").strip()
    return f"Connecting to {src} ({sn})" if sn else f"Connecting to {src}"


def _destination_is_known_snapshot(snap: Dict[str, Any]) -> bool:
    dest = (snap.get("destination") or "").strip().lower()
    dest_name = (snap.get("destination_name") or "").strip().lower()
    return bool(dest and dest != "unknown" and dest_name and dest_name != "unknown")


def _is_data_ops_classification(snap: Dict[str, Any]) -> bool:
    mode = (snap.get("mode") or "").strip().lower()
    intent = (snap.get("intent") or "").strip().lower()
    ops = snap.get("operations") or []
    if mode == "data_ops" or (isinstance(ops, list) and ops):
        return True
    return intent in ("transform", "filter", "dedupe", "validate", "multi", "cleanup")


def _classification_prelude(snap: Dict[str, Any]) -> List[Dict[str, str]]:
    """Route identification only — prompt parsing is shown in the planning loader."""
    if _is_data_ops_classification(snap):
        return []
    if _destination_is_known_snapshot(snap):
        return [{"id": "identify_route", "label": "Identifying source and destination"}]
    return [{"id": "identify_route", "label": "Identifying source"}]


def _extract_step_count(plan: ExecutionPlan) -> int:
    return sum(
        1 for s in plan.steps if (s.tool_id or "").strip() in _EXTRACT_TOOL_IDS
    )


def build_agent_preview_pipeline(
    plan: ExecutionPlan, session: AgentSession
) -> List[Dict[str, str]]:
    """
    Ordered UI steps for this turn: classification prelude + plan tools / extract substeps / data-ops ops.
    """
    snap = session.classification_snapshot or {}
    source = snap.get("source") or session.source
    source_name = snap.get("source_name") or session.source_name
    multi_extract = _extract_step_count(plan) > 1

    out: List[Dict[str, str]] = _classification_prelude(snap)

    for step in plan.steps:
        out.extend(
            _steps_for_plan_step(
                step,
                source=source,
                source_name=source_name,
                multi_extract=multi_extract,
            )
        )

    return out


def _steps_for_plan_step(
    step: PlanStep,
    *,
    source: Optional[str],
    source_name: Optional[str],
    multi_extract: bool = False,
) -> List[Dict[str, str]]:
    tool_id = (step.tool_id or "").strip()
    label = (step.label or _ARCHITECT_TOOL_LABELS.get(tool_id) or tool_id).strip()

    if tool_id == "run_data_ops_plan":
        steps_out: List[Dict[str, str]] = []
        for sub in (step.arguments or {}).get("steps") or []:
            if not isinstance(sub, dict):
                continue
            op_id = (sub.get("operation_id") or sub.get("tool_id") or "").strip()
            if not op_id:
                continue
            step_def = pipeline_step_for_operation(
                op_id,
                label=sub.get("label"),
            )
            steps_out.append({"id": step_def["id"], "label": step_def["label"]})
        if not steps_out:
            ops = (step.arguments or {}).get("operations") or []
            for op in ops:
                if isinstance(op, str) and op.strip():
                    s = pipeline_step_for_operation(op)
                    steps_out.append({"id": s["id"], "label": s["label"]})
        return steps_out or [
            pipeline_step_for_operation("transform"),
        ]

    if tool_id in _EXTRACT_TOOL_IDS:
        if multi_extract:
            return [{"id": tool_id, "label": label}]
        connect_label = connect_source_label(source, source_name)
        return [
            {"id": "connect_source", "label": connect_label},
            *[{"id": pid, "label": plabel} for pid, plabel in _EXTRACT_SETUP_STEPS[1:]],
        ]

    if tool_id == "start_migration":
        if "unknown" in label.lower():
            return []
        return [{"id": "start_migration", "label": label or "Migrating records"}]

    if tool_id == "map_fields":
        return [{"id": "map_fields", "label": label or "Mapping fields"}]

    if tool_id == "google_sheets_create_from_prompt":
        return [{"id": "create_spreadsheet", "label": label or "Create spreadsheet"}]

    if tool_id == "gsheets_execute_operation":
        op = ((step.arguments or {}).get("operation") or "").strip().lower()
        if op:
            step_def = pipeline_step_for_operation(op, label=label)
            return [{"id": step_def["id"], "label": step_def["label"]}]
        return [{"id": "gsheets_execute_operation", "label": label or "Run Sheets operation"}]

    if tool_id == "google_sheets_update_metadata":
        return [{"id": "update_spreadsheet_metadata", "label": label or "Update spreadsheet metadata"}]

    if tool_id == "metadata_deploy_salesforce":
        return [{"id": "deploy_schema", "label": label or "Deploy Salesforce metadata"}]

    if tool_id == "load_flow":
        return [{"id": "load_flow", "label": label or "Load saved flow"}]

    if tool_id == "save_flow":
        return [{"id": "save_flow", "label": label or "Save as flow"}]

    return [{"id": tool_id, "label": label}]
