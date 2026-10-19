"""Local file store (community default) — no S3 / no Archian bucket default."""
from __future__ import annotations

import asyncio

import pytest

from services import file_store


@pytest.fixture(autouse=True)
def _reset_backend_cache():
    file_store.clear_storage_backend_cache()
    yield
    file_store.clear_storage_backend_cache()


def test_auto_uses_local_without_aws(tmp_path, monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("S3_BUCKET_NAME", raising=False)
    monkeypatch.delenv("AWS_S3_BUCKET", raising=False)
    monkeypatch.setenv("FILE_STORAGE", "auto")
    monkeypatch.setenv("LOCAL_UPLOAD_DIR", str(tmp_path))
    file_store.clear_storage_backend_cache()

    assert file_store.resolve_storage_backend() == "local"

    async def _run():
        await file_store.put_bytes(file_key="job1_demo.csv", file_content=b"a,b\n1,2\n")
        return await file_store.get_bytes(file_key="job1_demo.csv")

    data = asyncio.run(_run())
    assert data == b"a,b\n1,2\n"
    assert (tmp_path / "job1_demo.csv").is_file()


def test_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_STORAGE", "local")
    monkeypatch.setenv("LOCAL_UPLOAD_DIR", str(tmp_path))
    file_store.clear_storage_backend_cache()

    async def _run():
        await file_store.put_bytes(file_key="../escape.txt", file_content=b"x")

    with pytest.raises(ValueError):
        asyncio.run(_run())


def test_s3_mode_requires_bucket(monkeypatch):
    monkeypatch.setenv("FILE_STORAGE", "s3")
    monkeypatch.delenv("S3_BUCKET_NAME", raising=False)
    monkeypatch.delenv("AWS_S3_BUCKET", raising=False)
    file_store.clear_storage_backend_cache()
    with pytest.raises(RuntimeError):
        file_store.resolve_storage_backend()


def test_no_hardcoded_edison_bucket(monkeypatch):
    monkeypatch.delenv("S3_BUCKET_NAME", raising=False)
    monkeypatch.delenv("AWS_S3_BUCKET", raising=False)
    assert file_store.s3_bucket_name() is None
