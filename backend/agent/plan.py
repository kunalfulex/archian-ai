from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PlanStep:
    tool_id: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[int] = field(default_factory=list)
    label: Optional[str] = None


@dataclass
class ExecutionPlan:
    mode: str
    steps: List[PlanStep]
    reasoning: Optional[str] = None
