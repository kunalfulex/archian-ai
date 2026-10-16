"""Per-turn LLM pick and tool usage quotas (Agent Runtime v4.2)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict

from agent.recovery_policy import MAX_DYNAMIC_STEPS, MAX_UNIVERSAL_STEPS


def max_llm_picks_per_turn() -> int:
    try:
        return max(1, int(os.getenv("AGENT_MAX_LLM_PICKS_PER_TURN", "8")))
    except ValueError:
        return 8


def max_replay_picks_per_turn() -> int:
    try:
        return max(1, int(os.getenv("AGENT_MAX_REPLAY_PICKS_PER_TURN", str(MAX_UNIVERSAL_STEPS))))
    except ValueError:
        return MAX_UNIVERSAL_STEPS


def max_tool_invokes_per_turn() -> int:
    try:
        cap = int(os.getenv("AGENT_MAX_TOOL_INVOKES_PER_TURN", str(MAX_UNIVERSAL_STEPS)))
        return max(1, cap)
    except ValueError:
        return MAX_UNIVERSAL_STEPS


def max_dynamic_llm_picks() -> int:
    try:
        return max(1, min(int(os.getenv("AGENT_MAX_LLM_PICKS_PER_TURN", "5")), MAX_DYNAMIC_STEPS))
    except ValueError:
        return min(5, MAX_DYNAMIC_STEPS)


@dataclass
class TurnUsage:
    llm_picks: int = 0
    replay_picks: int = 0
    tool_invokes: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            "llm_picks": self.llm_picks,
            "replay_picks": self.replay_picks,
            "tool_invokes": self.tool_invokes,
        }


def get_turn_usage(session) -> TurnUsage:
    snap = getattr(session, "classification_snapshot", None) or {}
    raw = snap.get("_turn_usage") or {}
    return TurnUsage(
        llm_picks=int(raw.get("llm_picks") or 0),
        replay_picks=int(raw.get("replay_picks") or 0),
        tool_invokes=int(raw.get("tool_invokes") or 0),
    )


def save_turn_usage(session, usage: TurnUsage) -> None:
    snap = dict(getattr(session, "classification_snapshot", None) or {})
    snap["_turn_usage"] = usage.to_dict()
    session.classification_snapshot = snap


def record_llm_pick(session) -> TurnUsage:
    usage = get_turn_usage(session)
    usage.llm_picks += 1
    save_turn_usage(session, usage)
    return usage


def record_replay_pick(session) -> TurnUsage:
    usage = get_turn_usage(session)
    usage.replay_picks += 1
    save_turn_usage(session, usage)
    return usage


def record_tool_invoke(session) -> TurnUsage:
    usage = get_turn_usage(session)
    usage.tool_invokes += 1
    save_turn_usage(session, usage)
    return usage


def llm_pick_allowed(session) -> tuple[bool, str]:
    usage = get_turn_usage(session)
    cap = max_llm_picks_per_turn()
    if usage.llm_picks >= cap:
        return False, f"LLM pick quota reached ({cap} per turn)."
    return True, ""


def replay_pick_allowed(session) -> tuple[bool, str]:
    usage = get_turn_usage(session)
    cap = max_replay_picks_per_turn()
    if usage.replay_picks >= cap:
        return False, f"Trace replay quota reached ({cap} per turn)."
    return True, ""


def tool_invoke_allowed(session) -> tuple[bool, str]:
    usage = get_turn_usage(session)
    cap = max_tool_invokes_per_turn()
    if usage.tool_invokes >= cap:
        return False, f"Tool invoke quota reached ({cap} per turn)."
    return True, ""
