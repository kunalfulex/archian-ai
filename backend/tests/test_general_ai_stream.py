import asyncio

from agent.stream_utils import iter_openai_compat_stream


class _FakeDelta:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.delta = _FakeDelta(content)


class _FakeChunk:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _SyncStream:
    def __init__(self, parts):
        self._parts = list(parts)

    def __iter__(self):
        return iter(self._parts)


def test_iter_openai_compat_stream_sync():
    async def _run():
        parts = []
        async for chunk in iter_openai_compat_stream(_SyncStream([_FakeChunk("Hi"), _FakeChunk(" there")])):
            parts.append(chunk.choices[0].delta.content)
        assert parts == ["Hi", " there"]

    asyncio.run(_run())


def test_general_handler_iterates_sync_stream(monkeypatch):
    async def _run():
        from handlers import general_ai_handler

        class _Resp:
            def __iter__(self):
                yield _FakeChunk("Hello")

        async def fake_call(*_a, **_k):
            return _Resp()

        monkeypatch.setattr(
            "services.architect_agent.async_openai_call",
            fake_call,
        )

        gen = await general_ai_handler.handle_general_ai_response_stream("hi", [])
        text = []
        async for part in gen:
            text.append(part)
        assert "".join(text) == "Hello"
        assert "Error during streaming" not in "".join(text)

    asyncio.run(_run())
