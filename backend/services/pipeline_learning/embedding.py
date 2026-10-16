from __future__ import annotations

import os
from typing import List, Optional

from openai import AsyncOpenAI

from services.llm.keys import resolve_api_key

_client: Optional[AsyncOpenAI] = None


def _embedding_model() -> str:
    return (os.getenv("OPENAI_EMBEDDING_MODEL") or "text-embedding-3-small").strip()


def _client_instance(api_key: str) -> AsyncOpenAI:
    global _client
    if _client is None or _client.api_key != api_key:
        _client = AsyncOpenAI(api_key=api_key)
    return _client


async def embed_texts(texts: List[str]) -> List[List[float]]:
    """Batch embed texts; returns empty list if API key missing."""
    if not texts:
        return []
    api_key = resolve_api_key("openai")
    if not api_key:
        return []
    cleaned = [(t or "")[:8000] for t in texts]
    resp = await _client_instance(api_key).embeddings.create(
        model=_embedding_model(),
        input=cleaned,
    )
    return [list(item.embedding) for item in resp.data]


async def embed_text(text: str) -> Optional[List[float]]:
    vecs = await embed_texts([text])
    return vecs[0] if vecs else None


def vector_to_pg_literal(vec: List[float]) -> str:
    """Format float list for Postgres vector literal."""
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"
