#!/usr/bin/env python3
"""Read-only diagnostics: Salesforce catalog vs entity labels after schema sync."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.connection import database


async def diagnose(*, org_index: int = 0, org_id: str | None = None) -> None:
    await database.connect()
    try:
        orgs = await database.fetch_all(
            """
            SELECT st.user_id::text, st.org_id, st.id::text AS connector_id,
                   st.instance_url, st.issued_at
            FROM salesforce_tokens st
            ORDER BY st.issued_at DESC
            LIMIT 20
            """
        )
        if not orgs:
            print("No Salesforce tokens found.")
            return

        print("=== Salesforce org connections (newest first) ===")
        for i, row in enumerate(orgs):
            r = dict(row)
            print(
                f"  [{i}] org_id={r['org_id']} "
                f"connector={r['connector_id'][:8]}… "
                f"url={r.get('instance_url', '')}"
            )

        if org_id:
            match = next((dict(o) for o in orgs if dict(o)["org_id"] == org_id), None)
            if not match:
                print(f"\norg_id {org_id!r} not found among recent tokens.")
                return
            org = match
        else:
            if org_index < 0 or org_index >= len(orgs):
                print(f"\nInvalid --index {org_index}; use 0..{len(orgs) - 1}")
                return
            org = dict(orgs[org_index])

        uid, oid, cid = org["user_id"], org["org_id"], org["connector_id"]
        print(f"\n=== Diagnostics for org_id={oid} ===\n")

        summary = await database.fetch_one(
            """
            SELECT
              (SELECT COUNT(*)::int FROM salesforce_objects
               WHERE user_id = :uid AND org_id = :org_id) AS objects_total,
              (SELECT COUNT(*)::int FROM salesforce_objects so
               WHERE so.user_id = :uid AND so.org_id = :org_id
                 AND EXISTS (
                   SELECT 1 FROM salesforce_object_fields f
                   WHERE f.user_id = so.user_id AND f.org_id = so.org_id
                     AND f.object_api_name = so.object_api_name)) AS objects_with_fields,
              (SELECT COUNT(*)::int FROM salesforce_objects so
               WHERE so.user_id = :uid AND so.org_id = :org_id
                 AND NOT EXISTS (
                   SELECT 1 FROM salesforce_object_fields f
                   WHERE f.user_id = so.user_id AND f.org_id = so.org_id
                     AND f.object_api_name = so.object_api_name)) AS objects_without_fields,
              (SELECT COUNT(*)::int FROM connector_entity_semantics
               WHERE user_id = :uid AND connector_type = 'salesforce'
                 AND connector_instance_id = :cid) AS semantics_total,
              (SELECT COUNT(*)::int FROM connector_entity_semantics
               WHERE user_id = :uid AND connector_type = 'salesforce'
                 AND connector_instance_id = :cid AND labeling_status = 'ready') AS semantics_ready,
              (SELECT COUNT(*)::int FROM connector_entity_semantics
               WHERE user_id = :uid AND connector_type = 'salesforce'
                 AND connector_instance_id = :cid AND labeling_status = 'pending') AS semantics_pending,
              (SELECT COUNT(*)::int FROM salesforce_objects
               WHERE user_id = :uid AND org_id = :org_id AND category IS NOT NULL) AS legacy_labeled
            """,
            {"uid": uid, "org_id": oid, "cid": cid},
        )
        for k, v in dict(summary).items():
            print(f"  {k}: {v}")

        orphan_count = await database.fetch_val(
            """
            SELECT COUNT(*)::int
            FROM connector_entity_semantics ces
            WHERE ces.user_id = :uid AND ces.connector_type = 'salesforce'
              AND ces.connector_instance_id = :cid
              AND NOT EXISTS (
                SELECT 1 FROM salesforce_objects so
                WHERE so.user_id = :uid AND so.org_id = :org_id
                  AND so.object_api_name = ces.entity_key
              )
            """,
            {"uid": uid, "org_id": oid, "cid": cid},
        )
        print(f"\n  orphan_semantics: {orphan_count}")

        cleaninfo = await database.fetch_val(
            """
            SELECT COUNT(*)::int FROM (
              SELECT object_api_name FROM salesforce_objects
              WHERE user_id = :uid AND org_id = :org_id AND object_api_name ILIKE '%CleanInfo%'
              UNION ALL
              SELECT entity_key FROM connector_entity_semantics
              WHERE user_id = :uid AND connector_type = 'salesforce'
                AND connector_instance_id = :cid AND entity_key ILIKE '%CleanInfo%'
            ) t
            """,
            {"uid": uid, "org_id": oid, "cid": cid},
        )
        print(f"  cleaninfo_rows: {cleaninfo}")

        evidence = await database.fetch_val(
            """
            SELECT COUNT(*)::int FROM label_evidence le
            WHERE le.user_id = :uid AND le.connector_type = 'salesforce'
              AND le.connector_instance_id = :cid AND le.entity_key IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM salesforce_objects so
                WHERE so.user_id = :uid AND so.org_id = :org_id
                  AND so.object_api_name = le.entity_key
              )
            """,
            {"uid": uid, "org_id": oid, "cid": cid},
        )
        print(f"  orphan_label_evidence: {evidence}")

    finally:
        await database.disconnect()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--index", type=int, default=0, help="Org index from newest token (default 0)")
    p.add_argument("--org-id", help="Specific Salesforce org_id")
    args = p.parse_args()
    asyncio.run(diagnose(org_index=args.index, org_id=args.org_id))


if __name__ == "__main__":
    main()
