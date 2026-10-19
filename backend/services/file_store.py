"""Object storage for uploaded files (community local disk or optional S3).

Community / self-host default: local filesystem under ``LOCAL_UPLOAD_DIR``.
S3 is used only when explicitly configured (``FILE_STORAGE=s3`` or ``auto`` with
bucket + AWS credentials). There is no default Archian cloud bucket.
"""
from __future__ import annotations

import asyncio
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO, Literal, Optional

logger = logging.getLogger(__name__)

StorageBackend = Literal["local", "s3"]


def _env_truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def local_upload_dir() -> Path:
    raw = (os.getenv("LOCAL_UPLOAD_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    # Prefer Docker volume path when present
    docker_default = Path("/data/uploads")
    if docker_default.is_dir() or _env_truthy("RUNNING_IN_DOCKER"):
        return docker_default
    return (Path.cwd() / "data" / "uploads").resolve()


def s3_bucket_name() -> Optional[str]:
    return (os.getenv("S3_BUCKET_NAME") or os.getenv("AWS_S3_BUCKET") or "").strip() or None


def _has_aws_credentials() -> bool:
    if (os.getenv("AWS_ACCESS_KEY_ID") or "").strip() and (os.getenv("AWS_SECRET_ACCESS_KEY") or "").strip():
        return True
    # Allow default credential chain (instance role) only when explicitly opted in
    return _env_truthy("AWS_USE_DEFAULT_CREDENTIALS")


def resolve_storage_backend() -> StorageBackend:
    """Pick local vs S3. Never falls back to a hardcoded Archian bucket."""
    mode = (os.getenv("FILE_STORAGE") or "auto").strip().lower()
    if mode in ("local", "filesystem", "fs", "disk"):
        return "local"
    if mode == "s3":
        if not s3_bucket_name():
            raise RuntimeError(
                "FILE_STORAGE=s3 requires S3_BUCKET_NAME (or AWS_S3_BUCKET) to be set"
            )
        return "s3"
    # auto
    if s3_bucket_name() and _has_aws_credentials():
        return "s3"
    return "local"


@lru_cache(maxsize=1)
def get_storage_backend() -> StorageBackend:
    backend = resolve_storage_backend()
    logger.info("File storage backend: %s", backend)
    return backend


def clear_storage_backend_cache() -> None:
    get_storage_backend.cache_clear()


def _safe_local_path(file_key: str) -> Path:
    """Map object key to a path under the upload root (no path traversal)."""
    root = local_upload_dir()
    # Normalize key: strip leading slashes, reject ..
    parts = [p for p in file_key.replace("\\", "/").split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise ValueError(f"Invalid file key: {file_key!r}")
    path = root.joinpath(*parts)
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as e:
        raise ValueError(f"Invalid file key: {file_key!r}") from e
    return path


async def put_bytes(*, file_key: str, file_content: bytes) -> None:
    backend = get_storage_backend()
    if backend == "local":
        path = _safe_local_path(file_key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(file_content)

        await asyncio.to_thread(_write)
        return

    import boto3

    bucket = s3_bucket_name()
    assert bucket
    client = boto3.client("s3")
    await asyncio.to_thread(
        client.put_object,
        Bucket=bucket,
        Key=file_key,
        Body=file_content,
    )


async def get_bytes(*, file_key: str) -> bytes:
    backend = get_storage_backend()
    if backend == "local":
        path = _safe_local_path(file_key)

        def _read() -> bytes:
            if not path.is_file():
                raise FileNotFoundError(f"Upload not found: {file_key}")
            return path.read_bytes()

        return await asyncio.to_thread(_read)

    import boto3

    bucket = s3_bucket_name()
    assert bucket
    client = boto3.client("s3")

    def _get() -> bytes:
        obj = client.get_object(Bucket=bucket, Key=file_key)
        return obj["Body"].read()

    return await asyncio.to_thread(_get)


async def download_to_fileobj(*, file_key: str, fileobj: BinaryIO) -> None:
    """Write object contents into an open binary file object."""
    backend = get_storage_backend()
    if backend == "local":
        path = _safe_local_path(file_key)

        def _copy() -> None:
            if not path.is_file():
                raise FileNotFoundError(f"Upload not found: {file_key}")
            with path.open("rb") as src:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    fileobj.write(chunk)

        await asyncio.to_thread(_copy)
        return

    import boto3

    bucket = s3_bucket_name()
    assert bucket
    client = boto3.client("s3")
    await asyncio.to_thread(client.download_fileobj, bucket, file_key, fileobj)


async def delete_key(*, file_key: str) -> None:
    backend = get_storage_backend()
    if backend == "local":
        path = _safe_local_path(file_key)

        def _unlink() -> None:
            try:
                path.unlink(missing_ok=True)
            except Exception as e:
                logger.warning("local delete failed key=%s: %s", file_key, e)

        await asyncio.to_thread(_unlink)
        return

    import boto3

    bucket = s3_bucket_name()
    if not bucket:
        return
    client = boto3.client("s3")

    def _delete() -> None:
        try:
            client.delete_object(Bucket=bucket, Key=file_key)
        except Exception as e:
            logger.warning("S3 delete_object failed key=%s: %s", file_key, e)

    await asyncio.to_thread(_delete)
