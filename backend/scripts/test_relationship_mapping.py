#!/usr/bin/env python3
"""
Map-only relationship regression (no extract/migrate execution).

Validates lookup_of on Account fields, no direct AccountId mapping, and that
post-map transforms do not corrupt lookup rows.

  cd edison-ai/backend && ./venv/bin/python scripts/test_relationship_mapping.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List

import httpx
import jwt
from dotenv import load_dotenv

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

load_dotenv(os.path.join(_BACKEND, ".env"))
BASE = os.getenv("SMOKE_API_BASE", "https://localhost:8000").rstrip("/")
JWT_SECRET = os.getenv("JWT_SECRET")


CASES = [
    {
        "name": "contact_with_account_lookup",
        "flat": {
            "first_name": "Rose",
            "last_name": "Gonzalez",
            "email": "rose@edge.com",
            "phone": "5127576000",
            "company": "Edge Communications",
            "account_phone": "5127576001",
        },
        "mapping_context": (
            "Map to Contact in SF 2. Company is the related Account name; "
            "account_phone is Account phone. Use lookup relationship, not AccountId."
        ),
        "expect_lookup_keys": ["company", "account_phone"],
        "expect_lookup_of": "Contact.AccountId",
        "expect_base_fields": {"first_name": "FirstName", "last_name": "LastName"},
        "forbid_direct_fields": ["AccountId"],
    },
    {
        "name": "snake_case_account_name",
        "flat": {
            "FirstName": "Ann",
            "LastName": "Lee",
            "Email": "ann@example.com",
            "AccountName": "Acme Corp",
            "AccountPhone": "5550100",
        },
        "mapping_context": (
            "Migrate Contact to SF 2. AccountName and AccountPhone are on related Account."
        ),
        "expect_lookup_keys": ["AccountName", "AccountPhone"],
        "expect_lookup_of": "Contact.AccountId",
        "expect_base_fields": {},
        "forbid_direct_fields": ["AccountId"],
    },
]


def _entry_lookup_of(entry: Any) -> str:
    if isinstance(entry, dict):
        return (entry.get("lookup_of") or "").strip()
    if isinstance(entry, list) and entry and isinstance(entry[0], dict):
        return (entry[0].get("lookup_of") or "").strip()
    return ""


def _entry_object_field(entry: Any) -> tuple[str, str]:
    if isinstance(entry, dict):
        return (entry.get("object") or "", entry.get("field") or "")
    if isinstance(entry, list) and entry and isinstance(entry[0], dict):
        return (entry[0].get("object") or "", entry[0].get("field") or "")
    return "", ""


def _invalid_accountid_split(entry: Any) -> bool:
    if isinstance(entry, list):
        for item in entry:
            if isinstance(item, dict) and (item.get("field") or "").lower() == "accountid":
                return True
    if isinstance(entry, dict) and (entry.get("field") or "").lower() == "accountid":
        return True
    return False


async def _mint_token() -> str:
    from db.connection import database

    await database.connect()
    row = await database.fetch_one(
        """
        SELECT id, email FROM users
        WHERE is_deleted = FALSE AND is_active = TRUE
        ORDER BY created_at ASC LIMIT 1
        """
    )
    if not row or not JWT_SECRET:
        raise RuntimeError("No user or JWT_SECRET")
    token = jwt.encode(
        {
            "email": row["email"],
            "user_id": str(row["id"]),
            "exp": datetime.utcnow() + timedelta(hours=2),
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    return token


async def run_case(client: httpx.AsyncClient, headers: dict, case: Dict[str, Any]) -> List[str]:
    failures: List[str] = []
    body = {
        "extracted_schema": case["flat"],
        "document_type": "Contact",
        "document_description": "Relationship mapping regression",
        "destination": "salesforce",
        "destination_name": "SF 2",
        "extracted_schema_type": "file",
        "mapping_context_summary": case["mapping_context"],
    }
    r = await client.post(f"{BASE}/map-fields", headers=headers, json=body, timeout=180.0)
    if r.status_code != 200:
        return [f"HTTP {r.status_code}: {r.text[:400]}"]

    payload = r.json()
    mapping = payload.get("mapping") or {}
    audit = payload.get("mapping_audit") or {}
    enhanced = payload.get("enhanced_mapping") or {}

    print(f"  mapping keys: {sorted(mapping.keys())}")
    if audit:
        print(f"  audit status={audit.get('status')} score={audit.get('mapping_score')}")
        codes = [i.get("code") for i in (audit.get("issues") or []) if isinstance(i, dict)]
        print(f"  audit codes: {codes[:8]}")

    for forbidden in case.get("forbid_direct_fields") or []:
        if forbidden in mapping:
            failures.append(f"direct mapping to {forbidden} must not exist")

    for key in case.get("expect_lookup_keys") or []:
        if key not in mapping:
            failures.append(f"missing mapping key {key!r}")
            continue
        entry = mapping[key]
        if _invalid_accountid_split(entry):
            failures.append(f"{key} incorrectly split/mapped to AccountId")
            continue
        obj, field = _entry_object_field(entry)
        if obj != "Account":
            failures.append(f"{key} should map to Account, got {obj}.{field}")
        lookup = _entry_lookup_of(entry)
        expected_lo = case["expect_lookup_of"]
        if lookup.lower() != expected_lo.lower():
            failures.append(f"{key} lookup_of={lookup!r}, expected {expected_lo!r}")

    for src, dest_field in (case.get("expect_base_fields") or {}).items():
        if src not in mapping:
            failures.append(f"missing base field mapping {src}")
            continue
        entry = mapping[src]
        if isinstance(entry, list):
            fields = {e.get("field") for e in entry if isinstance(e, dict)}
            if dest_field not in fields:
                failures.append(f"{src} split fields {fields}, expected {dest_field}")
        elif isinstance(entry, dict):
            if entry.get("lookup_of"):
                failures.append(f"{src} should be base Contact field, not lookup")
            if (entry.get("field") or "") != dest_field:
                failures.append(f"{src} -> {entry.get('field')}, expected {dest_field}")

    for key, entry in enhanced.items():
        if _invalid_accountid_split(entry):
            failures.append(f"enhanced_mapping corrupts {key} to AccountId split")

    rules = (payload.get("transformation_rules") or {}).get("fields") or []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if rule.get("action", "").startswith("split"):
            src = rule.get("source_field") or ""
            if src.lower() in ("accountname", "company", "account_phone", "accountphone"):
                failures.append(f"transform must not split lookup source {src}")

    return failures


async def main() -> int:
    token = await _mint_token()
    headers = {"Authorization": f"Bearer {token}"}
    print(f"API: {BASE}\n")

    all_failures: List[str] = []
    try:
        async with httpx.AsyncClient(verify=False, timeout=200.0) as client:
            for case in CASES:
                print(f"→ {case['name']}")
                fails = await run_case(client, headers, case)
                if fails:
                    all_failures.extend(f"{case['name']}: {f}" for f in fails)
                    print(f"  ✗ {'; '.join(fails)}")
                else:
                    print("  ✓ lookup relationship OK")
    finally:
        from db.connection import database

        await database.disconnect()

    print(f"\n{'='*50}")
    if all_failures:
        for f in all_failures:
            print(f"  ✗ {f}")
        return 1
    print(f"Passed {len(CASES)}/{len(CASES)} relationship map-only cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
