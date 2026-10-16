#!/usr/bin/env python3
"""Fail CI when keyword routing or hardcoded clarification copy appears on hot paths."""
from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
HOT_DIRS = (
    BACKEND / "services" / "intent",
    BACKEND / "services" / "planning",
)

CONTINUATION_MODULES = (
    "services/intent/continuation_planning.py",
    "services/planning/continuation_destination.py",
    "services/planning/continuation_policy.py",
    "services/planning/planning_kernel.py",
)

FORBIDDEN_IMPORTS = (
    "prompt_requests_fresh_extraction",
    "prompt_is_compound_extract_then_migrate",
    "prompt_requests_migrate_destination",
    "resolve_prompt_route",
    "_fallback_decision",
)

FORBIDDEN_KEYWORD_SYMBOLS = (
    "find_google_sheet_destination_in_prompt",
    "find_salesforce_destination_in_prompt",
    "find_sql_connection_in_prompt",
    "apply_resource_hints",
    "apply_all_resource_hints",
    "apply_resource_hints_from_current_prompt",
    "latest_user_stated_map_destination",
    "_legacy_user_named_different_destination",
    "should_skip_keyword_resource_hints",
    "should_skip_legacy_keyword_fallbacks",
)

KEYWORD_RESOLVER_SYMBOLS = FORBIDDEN_KEYWORD_SYMBOLS

ALLOWLIST = {
    "services/planning/conversation_session.py",
    "services/planning/endpoint_resolver.py",
    "services/planning/agent_runtime_enrichment.py",
    "services/intent/enrich.py",
    "services/planning/goal_parser.py",
    "services/planning/orchestrator.py",
    "services/planning/multi_source.py",
}

INTENT_KEYWORD_ALLOWLIST = {
    "services/intent/route_turn.py",
    "services/intent/steps/bind_resources.py",
}

FORBIDDEN_PATTERN_ALLOWLIST = {
    "services/planning/conversation_session.py",
    "services/planning/goal_parser.py",
    "services/planning/agent_runtime_enrichment.py",
    "services/planning/clarification_compose.py",
    "services/planning/resource_resolver.py",
    "services/planning/endpoint_resolver.py",
    "services/planning/google_sheets_destination.py",
    "services/planning/connector_endpoint_confirm.py",
    "handlers/google_sheets/detection.py",
}

AGENT_DIR = BACKEND / "agent"

FORBIDDEN_RUNTIME_KEYWORD_IMPORTS: tuple[str, ...] = ()

PRE_PLANNER_FORBIDDEN_IMPORTS = (
    "classification_is_general_turn",
    "prompt_requests_same_workbook_destination",
)

FORBIDDEN_LEGACY_PIPELINE_SYMBOLS = (
    "resolve_pipeline_action",
    "resolve_continuation_pipeline_action",
    "pipeline_action_from_session_decision",
    "guardrail_pipeline_action",
    "legacy_template_label",
    "_derive_plan_template",
)

LEGACY_PIPELINE_FIELD_PATTERNS = (
    (r'\.get\(\s*["\']pipeline_action["\']\s*\)', "pipeline_action field read"),
    (r'classification\.get\(\s*["\']pipeline_action["\']\s*\)', "pipeline_action field read"),
    (r'metadata\.get\(\s*["\']template["\']\s*\)', "metadata.template field read"),
    (r'\["pipeline_action"\]', "pipeline_action field read"),
)

LEGACY_PIPELINE_FIELD_ALLOWLIST = {
    "services/planning/observability.py",
    "services/planning/recovery_planner.py",
}

PROMPT_INSTANCE_CONFIRM_ALLOWLIST = {
    "services/planning/connector_endpoint_confirm.py",
}

PROMPT_WORKBOOK_CONFIRM_ALLOWLIST = {
    "handlers/google_sheets/detection.py",
}

FORBIDDEN_PATTERNS = (
    (r"prompt_is_compound_extract_then_migrate\s*\(", "compound keyword call"),
    (r"prompt_requests_migrate_destination\s*\(", "migrate keyword call"),
)

PROMPT_CONFIRM_PATTERNS = (
    (r"connector_instance_name_explicit_in_prompt\s*\(", "prompt instance confirm"),
    (r"spreadsheet_name_explicit_in_prompt\s*\(", "prompt workbook confirm"),
)

# Create/clarification slots that must use LLM compose (not fixed user-facing strings).
COMPOSE_SLOT_IDS = ("CREATE_TITLE", "CREATE_HEADERS")

# Legacy fixed create-clarification copy — only allowed in clarification_compose fallbacks.
HARDCODED_CLARIFICATION_ALLOWLIST = {
    "services/planning/clarification_compose.py",
    "services/intent/gaps.py",
}

HARDCODED_CLARIFICATION_PATTERNS = (
    r"What should we name the new Google Sheet\?",
    r"We'll use the column headers from your extract",
    r"_CREATE_WORKBOOK_PROMPT",
    r"Please choose an option to continue",
    r"Which Google account should we use\?",
    r"Which system should we extract from\?",
    r"Where should we migrate the data\?",
    r"Where should we send the data\?",
    r"Which Salesforce org should we extract from\?",
    r"Which SQL connection should we extract from\?",
)


def audit_forbidden_imports() -> list[str]:
    violations: list[str] = []
    for base in HOT_DIRS:
        for path in base.rglob("*.py"):
            rel = str(path.relative_to(BACKEND)).replace("\\", "/")
            if rel in ALLOWLIST:
                continue
            text = path.read_text(encoding="utf-8")
            for sym in FORBIDDEN_IMPORTS:
                if re.search(rf"\b{re.escape(sym)}\b", text):
                    violations.append(f"{rel}: references {sym}")
    return violations


def audit_keyword_resolver_imports() -> list[str]:
    """Keyword workbook/endpoint resolvers must not appear in intent or continuation modules."""
    violations: list[str] = []
    intent_dir = BACKEND / "services" / "intent"
    for path in intent_dir.rglob("*.py"):
        rel = str(path.relative_to(BACKEND)).replace("\\", "/")
        if rel in INTENT_KEYWORD_ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8")
        for sym in KEYWORD_RESOLVER_SYMBOLS:
            if re.search(rf"\b{re.escape(sym)}\b", text):
                violations.append(f"{rel}: keyword resolver {sym}")

    for rel in CONTINUATION_MODULES:
        path = BACKEND / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for sym in KEYWORD_RESOLVER_SYMBOLS:
            if re.search(rf"\b{re.escape(sym)}\b", text):
                violations.append(f"{rel}: keyword resolver {sym}")
    return violations


def audit_compose_slots() -> list[str]:
    """CREATE_TITLE / CREATE_HEADERS must map to <compose> in op_requirements."""
    path = BACKEND / "services" / "planning" / "op_requirements.py"
    text = path.read_text(encoding="utf-8")
    violations: list[str] = []
    for slot_id in COMPOSE_SLOT_IDS:
        marker = f"{slot_id}:"
        idx = text.find(marker)
        if idx == -1:
            violations.append(f"op_requirements.py: missing {slot_id} slot mapping")
            continue
        chunk = text[idx : idx + 220]
        if '"<compose>"' not in chunk:
            violations.append(
                f"op_requirements.py: {slot_id} message must be '<compose>'"
            )
    return violations


def audit_hardcoded_clarification_copy() -> list[str]:
    violations: list[str] = []
    for base in HOT_DIRS:
        for path in base.rglob("*.py"):
            rel = str(path.relative_to(BACKEND)).replace("\\", "/")
            if rel in HARDCODED_CLARIFICATION_ALLOWLIST:
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in HARDCODED_CLARIFICATION_PATTERNS:
                if re.search(pattern, text):
                    violations.append(f"{rel}: hardcoded clarification copy ({pattern})")
    return violations


def audit_forbidden_patterns() -> list[str]:
    violations: list[str] = []
    for base in HOT_DIRS:
        for path in base.rglob("*.py"):
            rel = str(path.relative_to(BACKEND)).replace("\\", "/")
            if rel in FORBIDDEN_PATTERN_ALLOWLIST:
                continue
            text = path.read_text(encoding="utf-8")
            for pattern, label in FORBIDDEN_PATTERNS:
                if re.search(pattern, text):
                    violations.append(f"{rel}: {label} ({pattern})")
    return violations


def audit_prompt_confirm_patterns() -> list[str]:
    """Prompt substring confirmation must stay in legacy allowlisted modules only."""
    violations: list[str] = []
    for base in HOT_DIRS:
        for path in base.rglob("*.py"):
            rel = str(path.relative_to(BACKEND)).replace("\\", "/")
            text = path.read_text(encoding="utf-8")
            for pattern, label in PROMPT_CONFIRM_PATTERNS:
                if not re.search(pattern, text):
                    continue
                if pattern.startswith(r"connector_instance") and rel in PROMPT_INSTANCE_CONFIRM_ALLOWLIST:
                    continue
                if pattern.startswith(r"spreadsheet_name") and rel in PROMPT_WORKBOOK_CONFIRM_ALLOWLIST:
                    continue
                violations.append(f"{rel}: {label} ({pattern})")
    return violations


def audit_catalog_sanitize_entrypoint() -> list[str]:
    """PLAN_SCOPE sanitize must route through catalog-only connector_endpoint_confirm."""
    path = BACKEND / "services" / "planning" / "connector_endpoint_confirm.py"
    text = path.read_text(encoding="utf-8")
    violations: list[str] = []
    required = ("sanitize_unconfirmed_connector_endpoints_catalog",)
    for sym in required:
        if sym not in text:
            violations.append(
                f"services/planning/connector_endpoint_confirm.py: missing {sym}"
            )
    return violations


def audit_removed_keyword_symbols() -> list[str]:
    """Legacy keyword resolver symbols must not appear on planning hot paths."""
    violations: list[str] = []
    for base in HOT_DIRS:
        for path in base.rglob("*.py"):
            rel = str(path.relative_to(BACKEND)).replace("\\", "/")
            if rel in ALLOWLIST:
                continue
            text = path.read_text(encoding="utf-8")
            for sym in FORBIDDEN_KEYWORD_SYMBOLS:
                if re.search(rf"\b{re.escape(sym)}\b", text):
                    violations.append(f"{rel}: removed keyword symbol {sym}")
    return violations


def audit_runtime_keyword_imports() -> list[str]:
    violations: list[str] = []
    for path in AGENT_DIR.rglob("*.py"):
        rel = str(path.relative_to(BACKEND)).replace("\\", "/")
        text = path.read_text(encoding="utf-8")
        for sym in FORBIDDEN_RUNTIME_KEYWORD_IMPORTS:
            if re.search(rf"\b{re.escape(sym)}\b", text):
                violations.append(f"{rel}: runtime keyword import {sym}")
    return violations


def audit_pre_planner_keyword_imports() -> list[str]:
    """Pre-planner hooks must not import keyword routing helpers."""
    targets = (
        BACKEND / "services" / "intent" / "connector_enrichment.py",
        BACKEND / "services" / "planning" / "agent_runtime_enrichment.py",
        BACKEND / "services" / "planning" / "endpoint_resolver.py",
    )
    violations: list[str] = []
    for path in targets:
        if not path.exists():
            continue
        rel = str(path.relative_to(BACKEND)).replace("\\", "/")
        text = path.read_text(encoding="utf-8")
        for sym in PRE_PLANNER_FORBIDDEN_IMPORTS:
            if re.search(rf"\b{re.escape(sym)}\b", text):
                violations.append(f"{rel}: pre-planner keyword import {sym}")
    return violations


def audit_removed_legacy_pipeline_symbols() -> list[str]:
    """Legacy pipeline_action routers and template metadata must not return on hot paths."""
    violations: list[str] = []
    for base in HOT_DIRS:
        for path in base.rglob("*.py"):
            rel = str(path.relative_to(BACKEND)).replace("\\", "/")
            if rel in ALLOWLIST:
                continue
            text = path.read_text(encoding="utf-8")
            for sym in FORBIDDEN_LEGACY_PIPELINE_SYMBOLS:
                if re.search(rf"\b{re.escape(sym)}\b", text):
                    violations.append(f"{rel}: legacy pipeline symbol {sym}")
    return violations


def audit_legacy_pipeline_field_reads() -> list[str]:
    violations: list[str] = []
    for base in HOT_DIRS:
        for path in base.rglob("*.py"):
            rel = str(path.relative_to(BACKEND)).replace("\\", "/")
            if rel in LEGACY_PIPELINE_FIELD_ALLOWLIST:
                continue
            text = path.read_text(encoding="utf-8")
            for pattern, label in LEGACY_PIPELINE_FIELD_PATTERNS:
                if re.search(pattern, text):
                    violations.append(f"{rel}: {label} ({pattern})")
    return violations


def main() -> int:
    violations: list[str] = []
    violations.extend(audit_forbidden_imports())
    violations.extend(audit_keyword_resolver_imports())
    violations.extend(audit_removed_keyword_symbols())
    violations.extend(audit_removed_legacy_pipeline_symbols())
    violations.extend(audit_legacy_pipeline_field_reads())
    violations.extend(audit_runtime_keyword_imports())
    violations.extend(audit_pre_planner_keyword_imports())
    violations.extend(audit_forbidden_patterns())
    violations.extend(audit_prompt_confirm_patterns())
    violations.extend(audit_catalog_sanitize_entrypoint())
    violations.extend(audit_compose_slots())
    violations.extend(audit_hardcoded_clarification_copy())
    if violations:
        print("Keyword / clarification routing audit failed:")
        for v in sorted(violations):
            print(f"  - {v}")
        return 1
    print("Keyword routing audit passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
