from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol

from services.llm.types import LLMResponse


class ChatProvider(Protocol):
    async def complete(
        self,
        *,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: Optional[int],
        api_key: str,
        stream: bool = False,
    ) -> LLMResponse:
        ...
