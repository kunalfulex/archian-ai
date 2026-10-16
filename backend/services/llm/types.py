from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TaskConfig:
    task_key: str
    display_name: str
    provider_id: str
    model_id: str
    module_type: str
    env_key_name: str
    temperature: float = 0.0
    max_tokens: Optional[int] = None
    max_concurrent: int = 5
    batch_size: int = 1
    fallback_provider_id: Optional[str] = None
    fallback_model_id: Optional[str] = None
    fallback_module_type: Optional[str] = None
    is_active: bool = True


@dataclass
class LLMRequest:
    task_key: str
    messages: List[Dict[str, str]]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: bool = False
    user_id: Optional[str] = None
    job_id: Optional[str] = None
    conversation_id: Optional[str] = None
    response_format: Optional[Dict[str, Any]] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    content: str
    provider_id: str
    model_id: str
    latency_ms: int
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    raw: Any = None
