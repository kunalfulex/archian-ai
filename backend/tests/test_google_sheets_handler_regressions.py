"""Regressions for Google Sheets handler error paths and imports."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch


def test_update_confirmation_outer_error_streams_message():
    from handlers.google_sheets import pending

    async def _run():
        with patch.object(
            pending,
            "check_for_pending_update_confirmation",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            stream = await pending.handle_update_confirmation_response("u1", "update")
        return [chunk async for chunk in stream]

    assert asyncio.run(_run()) == ["**Error**: boom\n\n"]


def test_metadata_stream_resolves_update_helper():
    from handlers.google_sheets import background, streams_create

    assert streams_create.update_google_spreadsheet_metadata is background.update_google_spreadsheet_metadata
