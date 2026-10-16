"""Test helpers for PLAN_SCOPE-required plan building."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.planning.plan_scope import read_plan_scope, write_plan_scope


def with_plan_scope(
    classification: Dict[str, Any],
    *,
    operations: Optional[List[str]] = None,
    data_operations: Optional[List[str]] = None,
    **goal_extra: Any,
) -> Dict[str, Any]:
    """Attach minimal _plan_scope when tests call build_plan_steps directly."""
    cls = dict(classification)
    if read_plan_scope(cls):
        return cls

    ops = list(operations or cls.get("operations") or [])
    intent = (cls.get("intent") or "").lower()
    mode = (cls.get("mode") or "").lower()
    if not ops:
        if mode == "map" or intent == "map_data":
            ops = ["map"]
        elif mode in ("migrate",) or intent in ("migrate_data", "migrate"):
            ops = ["migrate"]
        elif mode == "data_ops":
            ops = []
        else:
            ops = ["extract"]

    goal: Dict[str, Any] = {"operations": ops, **goal_extra}
    data_ops = data_operations if data_operations is not None else cls.get("data_operations")
    if data_ops:
        goal["data_operations"] = list(data_ops)
    return write_plan_scope(cls, goal)


def assert_pipeline_steps(plan, expected: List[str]) -> None:
    """Prefer pipeline_steps over legacy template metadata in tests."""
    tokens = (plan.metadata or {}).get("pipeline_steps")
    assert tokens == expected, f"expected pipeline_steps {expected}, got {tokens}"


def assert_plan_has_tools(plan, *tool_ids: str) -> None:
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    for tid in tool_ids:
        assert tid in tools, f"expected tool {tid} in {tools}"
