from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from db.connection import database


class ReplayGuardService:
    """Internal replay guard for bidirectional loop suppression."""

    DEFAULT_WINDOW_MINUTES = 60
    EXCLUDED_HASH_FIELDS = {
        "Id",
        "LastModifiedDate",
        "SystemModstamp",
        "CreatedDate",
        "attributes",
    }
    LOOKUP_CHUNK_SIZE = 1000
    INSERT_CHUNK_SIZE = 1000

    @staticmethod
    async def resolve_flow_pair_context(flow_id: str, user_id: str) -> Optional[Dict[str, str]]:
        row = await database.fetch_one(
            """
            SELECT id::text AS id, parent_flow_id::text AS parent_flow_id
            FROM migration_flows
            WHERE id = CAST(:flow_id AS uuid) AND user_id = CAST(:user_id AS uuid)
            LIMIT 1
            """,
            {"flow_id": flow_id, "user_id": user_id},
        )
        if not row:
            return None
        d = dict(row)
        parent = (d.get("parent_flow_id") or "").strip() or None
        if parent:
            return {"flow_pair_id": parent, "direction": "B_TO_A"}
        return {"flow_pair_id": str(d.get("id")), "direction": "A_TO_B"}

    @staticmethod
    def _normalize_for_hash(value: Any) -> Any:
        if isinstance(value, dict):
            out = {}
            for k in sorted(value.keys(), key=lambda x: str(x).lower()):
                if str(k) in ReplayGuardService.EXCLUDED_HASH_FIELDS:
                    continue
                out[str(k)] = ReplayGuardService._normalize_for_hash(value[k])
            return out
        if isinstance(value, list):
            return [ReplayGuardService._normalize_for_hash(v) for v in value]
        if isinstance(value, datetime):
            dt = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        return value

    @staticmethod
    def payload_hash(record: Dict[str, Any]) -> str:
        normalized = ReplayGuardService._normalize_for_hash(record or {})
        body = json.dumps(normalized, separators=(",", ":"), sort_keys=True, default=str)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    @staticmethod
    def _build_business_payload(record: Dict[str, Any], mapping: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """
        Build canonical outbound business payload keyed by destination object/field.
        This avoids org-specific source artifacts (e.g. Salesforce Id) from hash matching.
        """
        payload: Dict[str, Dict[str, Any]] = {}
        rec = record or {}
        for source_field, mapping_info in (mapping or {}).items():
            items = mapping_info if isinstance(mapping_info, list) else [mapping_info]
            value = rec.get(source_field)
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            for mi in items:
                if not isinstance(mi, dict):
                    continue
                obj = str(mi.get("object") or "").strip()
                fld = str(mi.get("field") or "").strip()
                if not obj or not fld:
                    continue
                # Exclude non-business identifiers/system fields from replay hash.
                if fld in ReplayGuardService.EXCLUDED_HASH_FIELDS:
                    continue
                payload.setdefault(obj, {})
                payload[obj][fld] = value
        return payload

    @staticmethod
    def payload_hash_from_mapping(record: Dict[str, Any], mapping: Dict[str, Any]) -> str:
        business_payload = ReplayGuardService._build_business_payload(record, mapping)
        if business_payload:
            normalized = ReplayGuardService._normalize_for_hash(business_payload)
            body = json.dumps(normalized, separators=(",", ":"), sort_keys=True, default=str)
            return hashlib.sha256(body.encode("utf-8")).hexdigest()
        # Fallback: if payload extraction fails (no mapped fields), hash cleaned raw record.
        return ReplayGuardService.payload_hash(record)

    @staticmethod
    def extract_record_key(record: Dict[str, Any], mapping: Dict[str, Any]) -> Optional[str]:
        keys: List[str] = []
        for source_field, mapping_info in (mapping or {}).items():
            if isinstance(mapping_info, list):
                items = mapping_info
            else:
                items = [mapping_info]
            for mi in items:
                if not isinstance(mi, dict) or not mi.get("is_duplicate_check"):
                    continue
                rv = (record or {}).get(source_field)
                if rv is None or (isinstance(rv, str) and not rv.strip()):
                    continue
                keys.append(f"{source_field}:{str(rv).strip()}")
        if keys:
            keys.sort()
            return "|".join(keys)
        # Fallback only when no business key fields are available.
        rid = (record or {}).get("Id")
        if rid:
            return f"Id:{str(rid).strip()}"
        return None

    @staticmethod
    async def filter_bouncebacks(
        records: List[Dict[str, Any]],
        mapping: Dict[str, Any],
        flow_pair_id: str,
        direction: str,
        window_minutes: int = DEFAULT_WINDOW_MINUTES,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]], int]:
        if not records:
            return [], [], 0
        opposite = "B_TO_A" if direction == "A_TO_B" else "A_TO_B"
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(1, int(window_minutes)))

        candidates: List[Tuple[Dict[str, Any], str, str]] = []
        passthrough: List[Dict[str, Any]] = []
        for rec in records:
            rk = ReplayGuardService.extract_record_key(rec, mapping)
            if not rk:
                passthrough.append(rec)
                continue
            candidates.append((rec, rk, ReplayGuardService.payload_hash_from_mapping(rec, mapping)))

        existing_pairs: set[Tuple[str, str]] = set()
        if candidates:
            # Batched tuple lookup against opposite direction.
            for i in range(0, len(candidates), ReplayGuardService.LOOKUP_CHUNK_SIZE):
                chunk = candidates[i : i + ReplayGuardService.LOOKUP_CHUNK_SIZE]
                keys = [c[1] for c in chunk]
                hashes = [c[2] for c in chunk]
                rows = await database.fetch_all(
                    """
                    SELECT s.record_key, s.payload_hash
                    FROM sync_replay_guard s
                    INNER JOIN (
                        SELECT * FROM unnest(
                            CAST(:record_keys AS text[]),
                            CAST(:payload_hashes AS text[])
                        ) AS t(record_key, payload_hash)
                    ) c
                      ON s.record_key = c.record_key
                     AND s.payload_hash = c.payload_hash
                    WHERE s.flow_pair_id = CAST(:flow_pair_id AS uuid)
                      AND s.direction = :direction
                      AND s.processed_at >= :cutoff
                    """,
                    {
                        "record_keys": keys,
                        "payload_hashes": hashes,
                        "flow_pair_id": flow_pair_id,
                        "direction": opposite,
                        "cutoff": cutoff,
                    },
                )
                for r in rows or []:
                    d = dict(r)
                    existing_pairs.add((str(d.get("record_key") or ""), str(d.get("payload_hash") or "")))

        kept: List[Dict[str, Any]] = list(passthrough)
        kept_meta: List[Dict[str, str]] = [{"record_key": "", "payload_hash": ""} for _ in passthrough]
        skipped = 0
        for rec, rk, ph in candidates:
            if (rk, ph) in existing_pairs:
                skipped += 1
                continue
            kept.append(rec)
            kept_meta.append({"record_key": rk, "payload_hash": ph})
        return kept, kept_meta, skipped

    @staticmethod
    async def mark_successful_writes(
        flow_pair_id: str,
        direction: str,
        successful_meta: List[Dict[str, str]],
        source_flow_id: Optional[str] = None,
    ) -> None:
        if not successful_meta:
            return
        rows = []
        for m in successful_meta:
            rk = (m.get("record_key") or "").strip()
            ph = (m.get("payload_hash") or "").strip()
            if not rk or not ph:
                continue
            rows.append((str(uuid4()), rk, ph))
        if not rows:
            return

        now = datetime.now(timezone.utc)
        for i in range(0, len(rows), ReplayGuardService.INSERT_CHUNK_SIZE):
            chunk = rows[i : i + ReplayGuardService.INSERT_CHUNK_SIZE]
            ids = [r[0] for r in chunk]
            keys = [r[1] for r in chunk]
            hashes = [r[2] for r in chunk]
            await database.execute(
                """
                INSERT INTO sync_replay_guard (
                    id, flow_pair_id, direction, record_key, payload_hash, processed_at, source_flow_id
                )
                SELECT
                    CAST(t.id AS uuid),
                    CAST(:flow_pair_id AS uuid),
                    :direction,
                    t.record_key,
                    t.payload_hash,
                    :processed_at,
                    CAST(:source_flow_id AS uuid)
                FROM unnest(
                    CAST(:ids AS text[]),
                    CAST(:record_keys AS text[]),
                    CAST(:payload_hashes AS text[])
                ) AS t(id, record_key, payload_hash)
                """,
                {
                    "flow_pair_id": flow_pair_id,
                    "direction": direction,
                    "processed_at": now,
                    "source_flow_id": source_flow_id,
                    "ids": ids,
                    "record_keys": keys,
                    "payload_hashes": hashes,
                },
            )
