"""
Classification handler — thin entry + back-compat re-exports (P4.8).

Implementation lives in services/intent/ and services/planning/.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.intent.classify_and_enrich import classify_and_enrich
from services.intent.classify_context import (  # noqa: F401
    build_conversation_context_for_classification,
    continuation_classifier_context,
)
from services.intent.session_state import (
    create_intent_state,
    extract_intent_state_from_history,
    get_active_intent_state,
    get_active_intent_state_from_db,
    is_state_still_valid,
)
from services.planning.agent_runtime_enrichment import (  # noqa: F401
    enrich_classification_for_agent_runtime,
)
from services.planning.intent_routing import POST_EXTRACT_INTENTS  # noqa: F401
from services.planning.conversation_session import (
    inherit_session_resources_from_intent_state,
    intent_state_from_extraction_metadata,
    latest_extraction_session_for_classifier,
)
_intent_state_from_extraction_metadata = intent_state_from_extraction_metadata


async def classify_source_destination(
    prompt: str,
    user_id: str,
    conversation_history: list = None,
    conversation_id: Optional[str] = None,
    explicit_source: Optional[str] = None,
    explicit_source_name: Optional[str] = None,
    plan_auto_run: bool = False,
    gate0_decision=None,
) -> dict:
    """Classify prompt → enrich via intent pipeline → legacy classification dict."""
    return await classify_and_enrich(
        prompt,
        user_id,
        conversation_history=conversation_history,
        conversation_id=conversation_id,
        explicit_source=explicit_source,
        explicit_source_name=explicit_source_name,
        plan_auto_run=plan_auto_run,
        gate0_decision=gate0_decision,
    )
