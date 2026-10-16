"""Planning activity milestones during planner resume flows."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

from services.architect_agent import handle_text_prompt_for_architect_agent_stream_real
from services.planning.planning_activity import (
    drain_planning_activity_sse_lines,
    emit_planning_activity,
    install_planning_activity_bus,
    reset_planning_activity_bus,
)
from services.planning.resource_catalog import ResourceCatalog


def _parse_sse_chunks(chunks: List[str]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if not line.startswith("data: "):
                continue
            events.append(json.loads(line[6:]))
    return events


def test_planner_resume_stream_yields_planning_activity():
    async def _mock_resume(*_args, **_kwargs) -> Dict[str, Any]:
        emit_planning_activity(
            "merge_clarifications",
            "Merging your answers",
            "done",
            segment="scope",
        )
        emit_planning_activity(
            "assemble_plan",
            "Assembling execution plan",
            "running",
            segment="plan_build",
        )
        return {
            "needs_clarification": True,
            "source": "salesforce",
            "destination": "google_sheets",
            "clarifications": [
                {"id": "c1", "clarification_type": "pick_workbook", "options": []},
            ],
        }

    pending = {
        "original_prompt": "export contacts to google sheet",
        "classification": {"source": "salesforce", "destination": "google_sheets"},
        "execution_plan": {"steps": [], "status": "draft"},
        "clarifications": [{"id": "c1", "clarification_type": "pick_workbook", "options": []}],
    }

    async def _run() -> List[str]:
        chunks: List[str] = []
        with patch(
            "services.planning.orchestrator.resume_classification_after_clarifications",
            new=_mock_resume,
        ), patch(
            "services.file_extraction.pending_upload.resolve_pending_file_upload",
            new_callable=AsyncMock,
            return_value=None,
        ):
            async for line in handle_text_prompt_for_architect_agent_stream_real(
                "contact list",
                [],
                "user-resume-test",
                conversation_id="conv-resume-test",
                planner_resume={
                    "planner_pending": pending,
                    "selections": [{"clarification_id": "c1", "option_id": "o1"}],
                },
            ):
                chunks.append(line)
        return chunks

    events = _parse_sse_chunks(asyncio.run(_run()))
    planning = [e for e in events if e.get("event_type") == "planning_activity"]
    activity_ids = [e.get("activity_id") for e in planning]

    assert "apply_selection" in activity_ids
    assert "merge_clarifications" in activity_ids
    assert "assemble_plan" in activity_ids
    assert any(e.get("event_type") == "planner_clarification_required" for e in events)


def test_resume_classification_emits_merge_and_plan_build_milestones():
    async def _run() -> str:
        token = install_planning_activity_bus()
        try:
            pending = {
                "original_prompt": "migrate contacts",
                "classification": {
                    "source": "salesforce",
                    "destination": "google_sheets",
                    "source_name": "SF 1",
                    "_plan_scope": {
                        "operations": ["extract", "migrate"],
                        "needs_source": False,
                        "needs_destination": False,
                    },
                },
                "execution_plan": {"steps": [], "status": "draft", "clarifications": []},
                "clarifications": [],
            }
            cat = ResourceCatalog(has_google_sheets=True)

            async def _fake_kernel(session, **kwargs):
                return session

            async def _fake_finalize(session, **kwargs):
                from services.planning.schemas import PlanStep, StepType

                if not session.plan.steps:
                    session.plan.steps = [
                        PlanStep(
                            step_type=StepType.EXTRACT,
                            label="Extract Contact",
                            parameters={"tool_id": "extract_salesforce"},
                        )
                    ]
                session.plan.status = "awaiting_approval"
                return session

            with patch(
                "services.planning.planning_kernel.run_planning_kernel",
                new=_fake_kernel,
            ), patch(
                "services.planning.planner_session_loop.finalize_planner_session",
                new=_fake_finalize,
            ), patch(
                "services.intent.apply.apply_intent_pipeline_to_classification",
                new_callable=AsyncMock,
                side_effect=lambda classification, **kwargs: classification,
            ), patch(
                "services.intent.resume.resume_intent_after_clarification",
                new_callable=AsyncMock,
                side_effect=lambda classification, **kwargs: classification,
            ), patch(
                "services.planning.clarification_queue.merge_plan_clarification_queue_async",
                new_callable=AsyncMock,
                side_effect=lambda plan, *args, **kwargs: plan,
            ):
                from services.planning.orchestrator import resume_classification_after_clarifications

                await resume_classification_after_clarifications(
                    pending,
                    [],
                    user_id="user-resume-test",
                    catalog=cat,
                )

            return " ".join(drain_planning_activity_sse_lines())
        finally:
            reset_planning_activity_bus(token)

    payload = asyncio.run(_run())
    assert "merge_clarifications" in payload
    assert "bind_connectors" in payload
    assert "assemble_plan" in payload
    assert "plan_ready" in payload
