"""SF extract field validation emits clarification SSE for runtime."""
from __future__ import annotations

from agent.stream_utils import apply_sse_meta_from_line
from handlers.salesforce_handler import _clarification_stream_chunks


def test_field_clarification_chunks_set_stream_meta():
    meta: dict = {}
    chunks = list(
        _clarification_stream_chunks(
            "The field 'BadField' is not available. Which field would you like instead?"
        )
    )
    assert len(chunks) == 2
    assert chunks[0].startswith("data:")
    apply_sse_meta_from_line(chunks[0], meta)
    assert meta.get("_stream_status") == "clarification"
    assert "BadField" in (meta.get("_stream_error_message") or "")
