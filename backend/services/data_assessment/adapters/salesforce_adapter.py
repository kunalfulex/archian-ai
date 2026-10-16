from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from db.connection import database
from services.connectors.salesforce_ref import normalize_salesforce_connector_ref
from services.data_assessment.config import (
    ASSESSMENT_CHUNK_SIZE,
    DEFAULT_SAMPLE_ROW_CAP,
    DEFAULT_SF_BULK_CONCURRENCY,
    SF_QUICK_OBJECTS,
)
from services.data_assessment.models import EntityRef, GapFinding, ObjectProfile
from services.data_assessment.streaming_profiler import profile_rows_streaming_async
from utils.salesforce_bulk_api import (
    create_bulk_query_job,
    stream_bulk_query_results,
    wait_for_bulk_query_job_completion,
)
from utils.migration_cancel import MigrationCancelledError
from utils.salesforceapimigration import get_valid_salesforce_token

logger = logging.getLogger(__name__)

_sf_semaphores: Dict[str, asyncio.Semaphore] = {}


def _semaphore_for_org(org_id: str) -> asyncio.Semaphore:
    if org_id not in _sf_semaphores:
        _sf_semaphores[org_id] = asyncio.Semaphore(DEFAULT_SF_BULK_CONCURRENCY)
    return _sf_semaphores[org_id]


def _is_sf_session_error(err: str) -> bool:
    lowered = err.lower()
    return (
        "invalid_session_id" in lowered
        or "session expired" in lowered
        or "session invalid" in lowered
        or "401" in err
        or "unauthorized" in lowered
    )


# Bulk API 2.0 CSV cannot return binary / compound field types.
_BULK_UNSUPPORTED_FIELD_TYPES = frozenset(
    {
        "base64",
        "location",
        "address",
        "complexvalue",
        "encryptedstring",
    }
)

# Known binary / large payload columns even when type metadata differs.
_BULK_UNSUPPORTED_FIELD_NAMES = frozenset(
    {
        "Body",
        "VersionData",
        "Binary",
        "Photo",
        "Picture",
        "File",
    }
)

# Objects that cannot be queried without a parent Id filter (full-org LIMIT queries fail).
_BULK_RESTRICTED_OBJECTS = frozenset(
    {
        "ContentDocumentLink",
        "ContentFolderMember",
        "ContentFolder",
        "AttachedContentDocument",
        "ContentBody",
        "FeedComment",
        "FeedItem",
        "Vote",
        "QuestionSubscription",
        "EntitySubscription",
    }
)


def _bulk_skip_reason_for_object(object_api_name: str) -> Optional[str]:
    if object_api_name in _BULK_RESTRICTED_OBJECTS:
        return (
            "This object requires a scoped parent Id filter and is skipped in org-wide sampling."
        )
    return None


def _field_ok_for_bulk_query(field_name: str, data_type: Optional[str]) -> bool:
    if not field_name or field_name == "Id":
        return False
    if field_name in _BULK_UNSUPPORTED_FIELD_NAMES:
        return False
    dt = (data_type or "").lower()
    if dt in _BULK_UNSUPPORTED_FIELD_TYPES:
        return False
    return True


def _is_expected_bulk_profiling_error(err: str) -> bool:
    lowered = err.lower()
    return (
        "blob field not supported" in lowered
        or "implementation restriction" in lowered
        or "requires a filter" in lowered
        or "not supported in bulk" in lowered
    )


def _id_only_profiling_soql(object_api_name: str, limit: int) -> str:
    return f"SELECT Id FROM {object_api_name} LIMIT {limit}"


class SalesforceAssessmentAdapter:
    connector_type = "salesforce"

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self._token_cache: Dict[str, Dict[str, str]] = {}

    async def _org_id_from_ref(self, connector_ref: Dict[str, Any]) -> str:
        ref = await normalize_salesforce_connector_ref(self.user_id, connector_ref)
        return str(ref["org_id"])

    async def list_assessable_entities(
        self,
        scope: str,
        connector_ref: Dict[str, Any],
        entity_list: Optional[List[str]] = None,
    ) -> List[EntityRef]:
        org_id = await self._org_id_from_ref(connector_ref)
        if entity_list:
            names = entity_list
        elif scope == "quick":
            names = list(SF_QUICK_OBJECTS)
        else:
            rows = await database.fetch_all(
                """
                SELECT object_api_name, label FROM salesforce_objects
                WHERE user_id = :user_id AND org_id = :org_id
                ORDER BY label
                """,
                {"user_id": self.user_id, "org_id": org_id},
            )
            names = [
                r["object_api_name"]
                for r in (rows or [])
                if r["object_api_name"] not in _BULK_RESTRICTED_OBJECTS
            ]
        out = []
        for name in names:
            label_row = await database.fetch_one(
                """
                SELECT label FROM salesforce_objects
                WHERE user_id = :user_id AND org_id = :org_id AND object_api_name = :obj
                LIMIT 1
                """,
                {"user_id": self.user_id, "org_id": org_id, "obj": name},
            )
            out.append(
                EntityRef(
                    entity_id=name,
                    label=(label_row["label"] if label_row else name) or name,
                )
            )
        return out

    async def profile_entity(
        self,
        entity: EntityRef,
        connector_ref: Dict[str, Any],
        options: Dict[str, Any],
        cancel_check: Callable[[], Awaitable[bool]],
    ) -> ObjectProfile:
        org_id = await self._org_id_from_ref(connector_ref)
        row_cap = int(options.get("sample_row_cap") or DEFAULT_SAMPLE_ROW_CAP)
        creds = await self._credentials(org_id)
        if not creds:
            return ObjectProfile(
                entity_id=entity.entity_id,
                label=entity.label,
                records_sampled=0,
                quality_report={},
                dimension_scores={},
                error="Salesforce credentials not found",
            )

        skip_reason = _bulk_skip_reason_for_object(entity.entity_id)
        if skip_reason:
            logger.info("SF assessment skip object=%s: %s", entity.entity_id, skip_reason)
            return ObjectProfile(
                entity_id=entity.entity_id,
                label=entity.label,
                records_sampled=0,
                quality_report={},
                dimension_scores={},
                error=skip_reason,
            )

        soql = await self._profiling_soql(org_id, entity.entity_id, row_cap)
        if not soql:
            return ObjectProfile(
                entity_id=entity.entity_id,
                label=entity.label,
                records_sampled=0,
                quality_report={},
                dimension_scores={},
                error="No fields available for profiling",
            )

        bulk_job_id: Optional[str] = None
        access_token = ""
        instance_url = ""

        async def _run_bulk(
            access_token: str, instance_url: str, query_soql: str
        ) -> ObjectProfile:
            nonlocal bulk_job_id
            async with _semaphore_for_org(org_id):
                if await cancel_check():
                    raise asyncio.CancelledError()
                bulk_job_id = await create_bulk_query_job(
                    entity.entity_id,
                    query_soql,
                    access_token,
                    instance_url,
                )
                await wait_for_bulk_query_job_completion(
                    bulk_job_id,
                    access_token,
                    instance_url,
                    poll_interval=3,
                    max_wait_time=3600,
                    cancel_check=cancel_check,
                )
                chunks: List[List[Dict[str, Any]]] = []
                total = 0
                async for chunk in stream_bulk_query_results(
                    bulk_job_id,
                    access_token,
                    instance_url,
                    chunk_size=ASSESSMENT_CHUNK_SIZE,
                ):
                    if await cancel_check():
                        raise asyncio.CancelledError()
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= row_cap:
                        break
                return await profile_rows_streaming_async(entity.entity_id, entity.label, chunks)

        try:
            for attempt in range(2):
                force_refresh = attempt > 0
                fresh = await self._credentials(org_id, force_refresh=force_refresh)
                if not fresh:
                    return ObjectProfile(
                        entity_id=entity.entity_id,
                        label=entity.label,
                        records_sampled=0,
                        quality_report={},
                        dimension_scores={},
                        error="Salesforce credentials not found or refresh failed",
                    )
                access_token = fresh["access_token"]
                instance_url = fresh["instance_url"]
                try:
                    try:
                        return await _run_bulk(access_token, instance_url, soql)
                    except Exception as bulk_exc:
                        err = str(bulk_exc)
                        if (
                            "blob field not supported" in err.lower()
                            or "not supported in bulk" in err.lower()
                        ):
                            id_only = _id_only_profiling_soql(entity.entity_id, row_cap)
                            if id_only != soql:
                                logger.info(
                                    "SF assessment Id-only retry object=%s",
                                    entity.entity_id,
                                )
                                return await _run_bulk(
                                    access_token, instance_url, id_only
                                )
                        raise
                except (asyncio.CancelledError, MigrationCancelledError):
                    raise asyncio.CancelledError() from None
                except Exception as exc:
                    err = str(exc)
                    if attempt == 0 and _is_sf_session_error(err):
                        logger.warning(
                            "SF assessment session expired for object=%s, refreshing token",
                            entity.entity_id,
                        )
                        self._token_cache.pop(org_id, None)
                        continue
                    raise
        except (asyncio.CancelledError, MigrationCancelledError):
            raise asyncio.CancelledError() from None
        except Exception as exc:
            err = str(exc)
            if "429" in err or "rate limit" in err.lower():
                raise RateLimitPause(err) from exc
            if _is_expected_bulk_profiling_error(err):
                logger.warning(
                    "SF assessment skipped object=%s: %s",
                    entity.entity_id,
                    err,
                )
            else:
                logger.exception("SF assessment profile failed object=%s", entity.entity_id)
            return ObjectProfile(
                entity_id=entity.entity_id,
                label=entity.label,
                records_sampled=0,
                quality_report={},
                dimension_scores={},
                error=err,
            )

    async def metadata_gaps(
        self,
        connector_ref: Dict[str, Any],
        entities: List[EntityRef],
        entity_profiles: Optional[List[Dict[str, Any]]] = None,
    ) -> List[GapFinding]:
        from services.data_assessment.metadata_gap_analyzer import analyze_salesforce_gaps

        org_id = await self._org_id_from_ref(connector_ref)
        return await analyze_salesforce_gaps(self.user_id, org_id, entities)

    async def _credentials(
        self, org_id: str, *, force_refresh: bool = False
    ) -> Optional[Dict[str, str]]:
        if not force_refresh and org_id in self._token_cache:
            return self._token_cache[org_id]
        try:
            token = await get_valid_salesforce_token(
                self.user_id,
                org_id=org_id,
                force_refresh=force_refresh,
            )
        except Exception as exc:
            logger.warning("SF assessment token lookup failed org=%s: %s", org_id, exc)
            return None
        if not token:
            return None
        creds = {
            "access_token": token["access_token"],
            "instance_url": token["instance_url"],
        }
        self._token_cache[org_id] = creds
        return creds

    async def _profiling_soql(self, org_id: str, object_api_name: str, limit: int) -> Optional[str]:
        fields = await database.fetch_all(
            """
            SELECT field_api_name, data_type FROM salesforce_object_fields
            WHERE user_id = :user_id AND org_id = :org_id AND object_api_name = :obj
            ORDER BY is_required DESC, field_api_name
            LIMIT 120
            """,
            {"user_id": self.user_id, "org_id": org_id, "obj": object_api_name},
        )
        if not fields:
            return _id_only_profiling_soql(object_api_name, limit)

        selected = ["Id"]
        for row in fields or []:
            field = dict(row)
            name = field.get("field_api_name")
            data_type = field.get("data_type")
            if not name or name in selected:
                continue
            if not _field_ok_for_bulk_query(name, data_type):
                continue
            selected.append(name)
            if len(selected) >= 25:
                break

        if len(selected) <= 1:
            return _id_only_profiling_soql(object_api_name, limit)

        cols = ", ".join(selected)
        return f"SELECT {cols} FROM {object_api_name} LIMIT {limit}"


class RateLimitPause(Exception):
    """Signal job worker to pause until rate limit clears."""

    pass
