"""LLM-composed clarification prompts (one contextual question per gap)."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from services.planning.schemas import Clarification, ClarificationOption

logger = logging.getLogger("edison.clarification_compose")

_GAP_LABELS = {
    "pick_source_connector": "which system to extract from",
    "pick_destination_connector": "where to send or store the data",
    "pick_source_entity": "which table, object, or sheet tab to read from",
    "pick_destination_entity": "which destination table, object, or sheet tab",
    "pick_extract_fields": "which columns or fields to include in the extract",
    "pick_create_headers": "which column headers the new spreadsheet should have",
    "pick_workbook": "which Google Sheet workbook to use",
    "pick_source_workbook": "which Google Sheet workbook to read from",
    "pick_google_account": "which Google account",
    "pick_salesforce_org": "which Salesforce org",
    "pick_sql_connector": "which database connection",
    "pick_tab": "which sheet tab",
    "create_workbook_name": "what to name the new spreadsheet",
}


def _options_summary(options: List[ClarificationOption], *, limit: int = 8) -> str:
    lines = []
    for opt in (options or [])[:limit]:
        if getattr(opt, "create_new", False):
            lines.append(f"- {opt.id}: {opt.label} (create new)")
        else:
            lines.append(f"- {opt.id}: {opt.label}")
    return "\n".join(lines) if lines else "(user may reply in chat)"


def _understood_context(
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    scope = dict(parsed_goal or {})
    headers = list(classification.get("planner_spreadsheet_headers") or [])
    if not headers:
        headers = list(scope.get("spreadsheet_headers") or [])
    from services.planning.source_column_headers import read_spreadsheet_header_source

    return {
        "source_type": classification.get("source"),
        "source_name": classification.get("source_name"),
        "destination_type": classification.get("destination"),
        "destination_name": classification.get("destination_name"),
        "pipeline_ops": scope.get("pipeline_ops") or scope.get("operations"),
        "needs_destination": scope.get("needs_destination"),
        "source_phrase": scope.get("source_phrase"),
        "destination_phrase": scope.get("destination_phrase"),
        "intent": classification.get("intent"),
        "spreadsheet_headers": headers,
        "spreadsheet_header_source": read_spreadsheet_header_source(classification, scope),
        "suggested_spreadsheet_title": classification.get("planner_suggested_spreadsheet_title"),
        "title_confirmed": bool(classification.get("planner_spreadsheet_title_confirmed")),
    }


def _suggestions_to_options(
    clar: Clarification,
    suggestions: List[Dict[str, Any]],
) -> List[ClarificationOption]:
    """Map LLM suggestions to ClarificationOption when catalog did not supply options."""
    if clar.options:
        return list(clar.options)
    ctype = (clar.clarification_type or "").strip()
    out: List[ClarificationOption] = []
    seen: set[str] = set()
    for item in suggestions or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("text") or "").strip()
        if not label or label.lower() in seen:
            continue
        seen.add(label.lower())
        opt_id = str(item.get("option_id") or "").strip()
        if not opt_id:
            if ctype == "pick_create_headers":
                opt_id = f"header:{label}"
            else:
                opt_id = f"text:{label}"
        out.append(ClarificationOption(id=opt_id, label=label))
    if ctype == "pick_create_headers" and out:
        out.append(ClarificationOption(id="fields_custom", label="Type a custom list in chat"))
    return out[:8]


def _fallback_prompt(
    clar: Clarification,
    *,
    classification: Dict[str, Any],
    user_prompt: str,
) -> str:
    """Structured fallback when compose LLM is unavailable."""
    gap = _GAP_LABELS.get(clar.clarification_type, clar.clarification_type)
    src = (classification.get("source_name") or classification.get("source") or "").strip()
    dest = (classification.get("destination_name") or classification.get("destination") or "").strip()
    opts = clar.options or []
    opt_line = ""
    if opts:
        labels = [o.label for o in opts[:5]]
        opt_line = f" Options: {', '.join(labels)}."
        if len(opts) > 5:
            opt_line += f" (+{len(opts) - 5} more)"

    if clar.clarification_type == "pick_create_headers":
        return (
            f"To create your new Google Sheet, {gap}.{opt_line} "
            "List the column headers in chat (comma-separated is fine)."
        ).strip()

    if clar.clarification_type == "create_workbook_name":
        headers = classification.get("planner_spreadsheet_headers") or []
        header_hint = ""
        if headers:
            header_hint = f" Columns so far: {', '.join(str(h) for h in headers[:6])}."
        return (
            f"What should we name the new Google Sheet?{header_hint}{opt_line} "
            "Pick a suggestion below or type a name in chat."
        ).strip()

    if clar.clarification_type == "pick_extract_fields":
        entity = (
            classification.get("source_object_api_name")
            or classification.get("source_table_name")
            or classification.get("source_sheet_name")
            or "the source"
        )
        where = f" from {src}" if src else ""
        return (
            f"To extract from **{entity}**{where}, {gap}.{opt_line} "
            "Pick below or type field names in chat."
        ).strip()

    if clar.clarification_type in ("pick_destination_connector", "pick_destination_entity"):
        ctx = f" after reading from **{src}**" if src else ""
        return (
            f"I understand you want to work with data{ctx}. "
            f"To finish the plan, I need to know {gap}.{opt_line}"
        ).strip()

    if clar.clarification_type in ("pick_source_entity", "pick_source_connector"):
        return (
            f"For your request: \"{(user_prompt or '')[:120]}\", "
            f"I need to confirm {gap}.{opt_line}"
        ).strip()

    if clar.prompt and clar.prompt.strip():
        return clar.prompt.strip()
    return f"To continue, please clarify {gap}.{opt_line}"


def clarification_prompt_needs_compose(prompt: Optional[str]) -> bool:
    """True when blocking clar still needs LLM compose (empty or sentinel)."""
    p = (prompt or "").strip()
    return not p or p == "<compose>"


def resolve_clarification_display_message(
    clarifications: List[Any],
    *,
    classification: Optional[Dict[str, Any]] = None,
    user_prompt: str = "",
    message: Optional[str] = None,
) -> str:
    """
    Sync display copy for SSE/chat persistence.

    Compose should run before emit; this uses composed prompt or LLM-failure fallback only.
    """
    if message and str(message).strip():
        return str(message).strip()
    if not clarifications:
        return (
            "I need one more detail to continue — "
            "please use the options below or reply in chat."
        )
    first = clarifications[0]
    if isinstance(first, Clarification):
        clar = first
    elif isinstance(first, dict):
        prompt = str(first.get("prompt") or "").strip()
        if prompt and not clarification_prompt_needs_compose(prompt):
            return prompt
        try:
            clar = Clarification.model_validate(first)
        except Exception:
            return (
                "I need one more detail to continue — "
                "please use the options below or reply in chat."
            )
    else:
        return (
            "I need one more detail to continue — "
            "please use the options below or reply in chat."
        )
    if clar.prompt and not clarification_prompt_needs_compose(clar.prompt):
        return clar.prompt.strip()
    fb = _fallback_prompt(
        clar,
        classification=dict(classification or {}),
        user_prompt=user_prompt,
    )
    return fb or (
        "I need one more detail to continue — "
        "please use the options below or reply in chat."
    )


def _export_flow_policy_block(export_create: bool) -> str:
    if not export_create:
        return ""
    return (
        "\nExport-flow policy (column headers come from the extract step):\n"
        "- Do NOT ask for spreadsheet column headers or which fields to extract.\n"
        "- For create_workbook_name: ask for the new spreadsheet title only.\n"
    )


def _compose_rules_for_gap(clarification_type: str) -> str:
    ctype = (clarification_type or "").strip()
    if ctype == "pick_create_headers":
        return (
            "- For pick_create_headers: ask for spreadsheet column headers only — "
            "do NOT ask where to extract data from.\n"
            "- Suggest 3-6 plausible column headers when you can infer them from context."
        )
    if ctype == "create_workbook_name":
        return (
            "- For create_workbook_name: ask for the spreadsheet title only — "
            "do NOT ask for extract source or migration destination.\n"
            "- Reference column headers already provided when known.\n"
            "- Suggest 2-4 short, descriptive spreadsheet titles derived from the conversation."
        )
    if ctype == "pick_tab":
        return (
            "- For pick_tab: ask which sheet tab to use within the workbook already chosen.\n"
            "- Reference the workbook name when known."
        )
    if ctype in ("pick_salesforce_org", "pick_sql_connector", "pick_google_account"):
        return (
            f"- For {ctype}: ask which connected account/org/database to use.\n"
            "- Reference the user's source/destination context when known.\n"
            "- Do NOT invent options — use the selectable list below."
        )
    if ctype == "pick_workbook_ambiguous":
        return (
            "- For pick_workbook_ambiguous: the user's phrase matched multiple spreadsheets.\n"
            "- Ask which spreadsheet they meant; reference their phrase from conversation."
        )
    if ctype == "pick_connector_by_label":
        return (
            "- For pick_connector_by_label: a business phrase matched multiple connected systems.\n"
            "- Ask which system they meant for source or destination."
        )
    return (
        "- If options already exist in the list below, do NOT invent duplicate suggestions — "
        "you may still write a contextual question referencing them."
    )


async def compose_clarification_prompt(
    clar: Clarification,
    *,
    classification: Dict[str, Any],
    user_prompt: str,
    parsed_goal: Optional[Dict[str, Any]] = None,
    user_id: str,
    conversation_id: Optional[str] = None,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
) -> Clarification:
    """
    Replace template clarification copy with a contextual question and LLM suggestions.

    Preserves clarification_type. Catalog-backed options are kept; suggestions fill gaps.
    """
    from services.planning.create_metadata import create_headers_from_extraction
    from services.planning.plan_scope import read_plan_scope

    goal = dict(parsed_goal or read_plan_scope(classification) or {})
    export_create = create_headers_from_extraction(classification, goal)
    ctype = (clar.clarification_type or "").strip()

    if export_create and ctype == "pick_create_headers":
        logger.error(
            "[clarification_compose] pick_create_headers on extract-backed create — queue bug"
        )
        return clar

    from services.intent.routing_context import build_routing_dialogue_context

    understood = _understood_context(classification, parsed_goal)
    existing_options = list(clar.options or [])
    options_block = _options_summary(existing_options)
    gap_label = _GAP_LABELS.get(clar.clarification_type, clar.clarification_type)
    dialogue = build_routing_dialogue_context(
        conversation_history,
        current_prompt=user_prompt or "",
        max_entries=8,
    )

    llm_prompt = f"""You write ONE short clarification turn for a data migration assistant.

User request:
\"\"\"{(user_prompt or '').strip()}\"\"\"

Recent conversation (oldest first):
{dialogue}

Gap to resolve: {clar.clarification_type} ({gap_label})

What we understood so far (JSON):
{json.dumps(understood, default=str)}

Existing selectable options (if any — do not duplicate these option_ids):
{options_block}

Rules:
- Help the user understand what we need and why (one short paragraph, max 3 sentences).
- Reference their source/destination names when known.
- Mention they can pick a suggestion below or reply in chat.
- Do NOT ask about destination if needs_destination is false.
- Do NOT use generic phrases like "Where should we send or create the data?" without context.
{_compose_rules_for_gap(clar.clarification_type)}
{_export_flow_policy_block(export_create)}
- Return ONLY valid JSON:
{{
  "prompt": "your question",
  "context_summary": "one-line recap of what you think they want",
  "suggestions": [
    {{"option_id": "text:Example Title", "label": "Example Title"}}
  ]
}}
- suggestions: 0-6 items; omit when existing options already cover the choice.
- For pick_create_headers use option_id like "header:First Name" or one combined fields_custom hint only in prompt.
"""

    try:
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.PLAN_CLARIFICATION_COMPOSE,
            messages=[{"role": "user", "content": llm_prompt}],
            max_tokens=380,
            temperature=0.0,
            user_id=str(user_id),
            conversation_id=str(conversation_id) if conversation_id else None,
        )
        raw = (response.choices[0].message.content or "").strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
            composed = str(data.get("prompt") or "").strip()
            context_summary = str(data.get("context_summary") or "").strip() or None
            suggestions = data.get("suggestions") if isinstance(data.get("suggestions"), list) else []
            options = _suggestions_to_options(clar, suggestions)
            if not options:
                options = existing_options
            if composed:
                return clar.model_copy(
                    update={
                        "prompt": composed,
                        "context_summary": context_summary,
                        "options": options,
                    }
                )
    except Exception as exc:
        logger.warning("[clarification_compose] LLM failed: %s", exc)

    return clar.model_copy(
        update={"prompt": _fallback_prompt(clar, classification=classification, user_prompt=user_prompt)}
    )


async def enrich_blocking_clarification_on_plan(
    plan: Any,
    *,
    classification: Dict[str, Any],
    user_prompt: str,
    parsed_goal: Optional[Dict[str, Any]] = None,
    user_id: str,
    conversation_id: Optional[str] = None,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
) -> Any:
    """Compose prompt for the single blocking clarification on a plan."""
    from services.planning.schemas import ExecutionPlan

    if not isinstance(plan, ExecutionPlan):
        return plan
    blocking = plan.blocking_clarifications()
    if not blocking:
        return plan
    if not clarification_prompt_needs_compose(blocking[0].prompt):
        return plan
    composed = await compose_clarification_prompt(
        blocking[0],
        classification=classification,
        user_prompt=user_prompt,
        parsed_goal=parsed_goal,
        user_id=user_id,
        conversation_id=conversation_id,
        conversation_history=conversation_history,
    )
    answered = [c for c in plan.clarifications if c.selected_option_id]
    plan = plan.model_copy(deep=True)
    plan.clarifications = answered + [composed]
    if composed.context_summary:
        plan.metadata = dict(plan.metadata or {})
        plan.metadata["clarification_context_summary"] = composed.context_summary
    return plan
