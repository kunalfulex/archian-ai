"""Google Sheets OAuth connections — multi-account per user."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from db.connection import database
from services.google_sheets_oauth_service import GoogleSheetsOAuthService

logger = logging.getLogger(__name__)

_CONNECTION_COLUMNS = """
    id, user_id, connection_name, google_account_email, google_account_name,
    access_token, refresh_token, expires_at, is_active, created_at, updated_at
"""


def _row_dict(row: Any) -> Dict[str, Any]:
    return dict(row) if row else {}


def _public_connection(row: Dict[str, Any], *, access_token: Optional[str] = None) -> Dict[str, Any]:
    out = {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "connection_name": row.get("connection_name") or "Google Sheets",
        "google_account_email": row.get("google_account_email"),
        "google_account_name": row.get("google_account_name"),
        "is_active": bool(row.get("is_active", True)),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "workbook_count": int(row.get("workbook_count") or 0),
    }
    if access_token is not None:
        out["access_token"] = access_token
    return out


async def _refresh_token_if_expired(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return row with a valid access_token (refresh in DB when expired)."""
    access_token = row.get("access_token")
    expires_at = row.get("expires_at")
    if not expires_at:
        return row

    now = datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at > now:
        return row

    refresh_token = row.get("refresh_token")
    if not refresh_token:
        return row

    try:
        oauth = GoogleSheetsOAuthService()
        token_response = await oauth.refresh_access_token(refresh_token)
        new_access_token = token_response.get("access_token")
        new_expires_at = oauth.calculate_expires_at(token_response.get("expires_in", 3600))
        await database.execute(
            """
            UPDATE google_sheets_connections
            SET access_token = :access_token, expires_at = :expires_at, updated_at = NOW()
            WHERE id = :connection_id
            """,
            {
                "access_token": new_access_token,
                "expires_at": new_expires_at,
                "connection_id": row["id"],
            },
        )
        row = {**row, "access_token": new_access_token, "expires_at": new_expires_at}
        logger.info("Refreshed Google Sheets token for connection %s", row["id"])
    except Exception as exc:
        logger.warning("Failed to refresh Google Sheets token for %s: %s", row.get("id"), exc)

    return row


async def list_connections(user_id: str) -> List[Dict[str, Any]]:
    rows = await database.fetch_all(
        f"""
        SELECT gsc.*,
               (
                   SELECT COUNT(DISTINCT ugs.spreadsheet_id)
                   FROM user_google_spreadsheets ugs
                   WHERE ugs.google_sheets_connection_id = gsc.id
                     AND ugs.user_id = gsc.user_id
               ) AS workbook_count
        FROM google_sheets_connections gsc
        WHERE gsc.user_id = :user_id AND gsc.is_active = true
        ORDER BY gsc.created_at DESC
        """,
        {"user_id": str(user_id)},
    )
    return [_public_connection(_row_dict(r)) for r in rows or []]


async def get_connection(
    user_id: str,
    connection_id: str,
    *,
    refresh_token: bool = False,
) -> Optional[Dict[str, Any]]:
    row = await database.fetch_one(
        f"""
        SELECT {_CONNECTION_COLUMNS}
        FROM google_sheets_connections
        WHERE user_id = :user_id AND id = :connection_id AND is_active = true
        """,
        {"user_id": str(user_id), "connection_id": str(connection_id)},
    )
    if not row:
        return None
    data = _row_dict(row)
    if refresh_token:
        data = await _refresh_token_if_expired(data)
    return _public_connection(data, access_token=data.get("access_token"))


def parse_google_sheets_oauth_state(state: str) -> Tuple[str, Optional[str]]:
    """
    Parse OAuth state: ``{nonce}:{user_id}`` or ``{nonce}:{user_id}:{connection_id}``.
    Returns (user_id, connection_id_or_none).
    """
    if not state or ":" not in state:
        raise ValueError("Invalid state parameter")
    parts = state.split(":")
    if len(parts) < 2 or not parts[1].strip():
        raise ValueError("Invalid state format")
    user_id = parts[1].strip()
    connection_id = parts[2].strip() if len(parts) >= 3 and parts[2].strip() else None
    return user_id, connection_id


async def reconnect_from_oauth(
    *,
    user_id: str,
    connection_id: str,
    access_token: str,
    refresh_token: Optional[str],
    expires_at: datetime,
    email: Optional[str],
    account_name: str,
) -> str:
    """Refresh OAuth tokens for an existing connection row (reconnect flow)."""
    if not email:
        raise ValueError("Google account email is required")

    row = await database.fetch_one(
        f"""
        SELECT {_CONNECTION_COLUMNS}
        FROM google_sheets_connections
        WHERE user_id = :user_id AND id = :connection_id AND is_active = true
        """,
        {"user_id": str(user_id), "connection_id": str(connection_id)},
    )
    if not row:
        raise ValueError("Google Sheets connection not found")

    data = _row_dict(row)
    stored_email = (data.get("google_account_email") or "").strip().lower()
    email_norm = email.strip().lower()
    if stored_email and email_norm != stored_email:
        raise ValueError(
            f"You signed in as {email.strip()} but this connection is for "
            f"{data.get('google_account_email')}. Please reconnect using the same Google account."
        )

    effective_refresh = refresh_token or data.get("refresh_token")
    if not effective_refresh:
        raise ValueError("No refresh token received from Google; reconnect with consent.")

    await database.execute(
        """
        UPDATE google_sheets_connections
        SET access_token = :access_token,
            refresh_token = :refresh_token,
            expires_at = :expires_at,
            google_account_email = :email,
            google_account_name = :name,
            updated_at = NOW()
        WHERE id = :connection_id AND user_id = :user_id
        """,
        {
            "connection_id": str(connection_id),
            "user_id": str(user_id),
            "access_token": access_token,
            "refresh_token": effective_refresh,
            "expires_at": expires_at,
            "email": email.strip(),
            "name": account_name,
        },
    )
    return str(connection_id)


async def upsert_from_oauth(
    *,
    user_id: str,
    access_token: str,
    refresh_token: Optional[str],
    expires_at: datetime,
    email: Optional[str],
    account_name: str,
) -> str:
    """Insert or update connection keyed by google_account_email. Returns connection id."""
    if not email:
        raise ValueError("Google account email is required")

    email_norm = email.strip().lower()
    existing = await database.fetch_one(
        """
        SELECT id, refresh_token FROM google_sheets_connections
        WHERE user_id = :user_id
          AND LOWER(TRIM(google_account_email)) = :email
          AND is_active = true
        LIMIT 1
        """,
        {"user_id": str(user_id), "email": email_norm},
    )

    effective_refresh = refresh_token or (dict(existing).get("refresh_token") if existing else None)
    if not effective_refresh:
        raise ValueError("No refresh token received from Google; reconnect with consent.")

    if existing:
        conn_id = existing["id"]
        await database.execute(
            """
            UPDATE google_sheets_connections
            SET access_token = :access_token,
                refresh_token = :refresh_token,
                expires_at = :expires_at,
                google_account_email = :email,
                google_account_name = :name,
                updated_at = NOW()
            WHERE id = :connection_id
            """,
            {
                "connection_id": conn_id,
                "access_token": access_token,
                "refresh_token": effective_refresh,
                "expires_at": expires_at,
                "email": email.strip(),
                "name": account_name,
            },
        )
        return str(conn_id)

    row = await database.fetch_one(
        """
        INSERT INTO google_sheets_connections (
            user_id, access_token, refresh_token, expires_at,
            google_account_email, google_account_name, connection_name,
            created_at, updated_at
        ) VALUES (
            :user_id, :access_token, :refresh_token, :expires_at,
            :email, :name, :connection_name, NOW(), NOW()
        )
        RETURNING id
        """,
        {
            "user_id": str(user_id),
            "access_token": access_token,
            "refresh_token": effective_refresh,
            "expires_at": expires_at,
            "email": email.strip(),
            "name": account_name,
            "connection_name": email.strip(),
        },
    )
    return str(dict(row)["id"])


async def update_connection_name(
    user_id: str,
    connection_id: str,
    connection_name: Optional[str],
) -> Optional[Dict[str, Any]]:
    name = (connection_name or "").strip()
    await database.execute(
        """
        UPDATE google_sheets_connections
        SET connection_name = :connection_name, updated_at = NOW()
        WHERE user_id = :user_id AND id = :connection_id AND is_active = true
        """,
        {
            "user_id": str(user_id),
            "connection_id": str(connection_id),
            "connection_name": name or None,
        },
    )
    return await get_connection(user_id, connection_id)


async def delete_connection(user_id: str, connection_id: str) -> bool:
    row = await database.fetch_one(
        """
        SELECT id FROM google_sheets_connections
        WHERE user_id = :user_id AND id = :connection_id AND is_active = true
        """,
        {"user_id": str(user_id), "connection_id": str(connection_id)},
    )
    if not row:
        return False

    cid = str(connection_id)
    uid = str(user_id)

    await database.execute(
        """
        DELETE FROM connector_labeling_jobs
        WHERE user_id = :user_id
          AND connector_type = 'google_sheets'
          AND connector_instance_id = :connection_id
        """,
        {"user_id": uid, "connection_id": cid},
    )
    await database.execute(
        """
        DELETE FROM connector_entity_semantics
        WHERE user_id = :user_id
          AND connector_type = 'google_sheets'
          AND connector_instance_id = :connection_id
        """,
        {"user_id": uid, "connection_id": cid},
    )
    await database.execute(
        """
        DELETE FROM connector_business_profiles
        WHERE user_id = :user_id
          AND connector_type = 'google_sheets'
          AND connector_instance_id = :connection_id
        """,
        {"user_id": uid, "connection_id": cid},
    )
    await database.execute(
        """
        DELETE FROM gsheets_extracted_schemas
        WHERE user_id = :user_id AND google_sheets_connection_id = :connection_id
        """,
        {"user_id": uid, "connection_id": cid},
    )
    await database.execute(
        """
        DELETE FROM user_google_spreadsheets
        WHERE user_id = :user_id AND google_sheets_connection_id = :connection_id
        """,
        {"user_id": uid, "connection_id": cid},
    )
    await database.execute(
        """
        DELETE FROM google_sheets_connections
        WHERE user_id = :user_id AND id = :connection_id
        """,
        {"user_id": uid, "connection_id": cid},
    )
    return True


async def user_has_active_connection(user_id: str) -> bool:
    row = await database.fetch_one(
        """
        SELECT 1 FROM google_sheets_connections
        WHERE user_id = :user_id AND is_active = true
        LIMIT 1
        """,
        {"user_id": str(user_id)},
    )
    return row is not None


async def connection_id_for_spreadsheet(user_id: str, spreadsheet_id: str) -> Optional[str]:
    uid = str(user_id)
    sid = str(spreadsheet_id)
    for query in (
        """
        SELECT google_sheets_connection_id::text AS cid
        FROM user_google_spreadsheets
        WHERE user_id = :user_id AND spreadsheet_id = :spreadsheet_id
        ORDER BY last_accessed_at DESC NULLS LAST
        LIMIT 1
        """,
        """
        SELECT google_sheets_connection_id::text AS cid
        FROM gsheets_extracted_schemas
        WHERE user_id = :user_id AND spreadsheet_id = :spreadsheet_id
        ORDER BY extracted_at DESC NULLS LAST
        LIMIT 1
        """,
    ):
        row = await database.fetch_one(query, {"user_id": uid, "spreadsheet_id": sid})
        if row and dict(row).get("cid"):
            return str(dict(row)["cid"])
    return None


def _connection_label(row: Dict[str, Any]) -> str:
    name = (row.get("connection_name") or "").strip()
    email = (row.get("google_account_email") or "").strip()
    if name and name.lower() != "google sheets":
        return name
    return email or "Google Sheets"


def _workbook_match_row(row: Dict[str, Any]) -> Dict[str, Any]:
    r = dict(row)
    return {
        "spreadsheet_id": r.get("spreadsheet_id"),
        "spreadsheet_name": r.get("spreadsheet_name"),
        "sheet_name": r.get("sheet_name"),
        "google_sheets_connection_id": str(r.get("google_sheets_connection_id") or ""),
        "google_account_email": r.get("google_account_email"),
        "connection_label": _connection_label(r),
    }


async def list_workbook_matches(
    user_id: str,
    spreadsheet_name: str,
    sheet_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """All workbook rows matching name (may span multiple Google accounts)."""
    uid = str(user_id)
    name = (spreadsheet_name or "").strip()
    if not name:
        return []

    params: Dict[str, Any] = {"user_id": uid, "spreadsheet_name": name}
    sheet_clause = ""
    if sheet_name and str(sheet_name).strip():
        sheet_clause = (
            " AND LOWER(TRIM(COALESCE(ugs.sheet_name, ''))) = LOWER(TRIM(:sheet_name))"
        )
        params["sheet_name"] = str(sheet_name).strip()

    ugs_rows = await database.fetch_all(
        f"""
        SELECT ugs.spreadsheet_id, ugs.spreadsheet_name, ugs.sheet_name,
               ugs.google_sheets_connection_id,
               gsc.google_account_email, gsc.connection_name
        FROM user_google_spreadsheets ugs
        JOIN google_sheets_connections gsc
          ON ugs.google_sheets_connection_id = gsc.id AND gsc.is_active = true
        WHERE ugs.user_id = :user_id
          AND LOWER(TRIM(ugs.spreadsheet_name)) = LOWER(TRIM(:spreadsheet_name))
          {sheet_clause}
        ORDER BY ugs.last_accessed_at DESC NULLS LAST
        """,
        params,
    )

    schema_rows = await database.fetch_all(
        f"""
        SELECT ges.spreadsheet_id, ges.spreadsheet_name, ges.sheet_name,
               ges.google_sheets_connection_id,
               gsc.google_account_email, gsc.connection_name
        FROM gsheets_extracted_schemas ges
        JOIN google_sheets_connections gsc
          ON ges.google_sheets_connection_id = gsc.id AND gsc.is_active = true
        WHERE ges.user_id = :user_id
          AND COALESCE(ges.is_deleted, FALSE) = FALSE
          AND LOWER(TRIM(ges.spreadsheet_name)) = LOWER(TRIM(:spreadsheet_name))
          {sheet_clause.replace('ugs.', 'ges.')}
        ORDER BY ges.extracted_at DESC NULLS LAST
        """,
        params,
    )

    seen: set = set()
    out: List[Dict[str, Any]] = []
    for raw in (ugs_rows or []) + (schema_rows or []):
        m = _workbook_match_row(dict(raw))
        key = (
            m.get("spreadsheet_id"),
            m.get("google_sheets_connection_id"),
            (m.get("sheet_name") or "").lower(),
        )
        if not m.get("spreadsheet_id") or key in seen:
            continue
        seen.add(key)
        out.append(m)
    return out


async def list_workbooks_by_spreadsheet_id(
    user_id: str,
    spreadsheet_id: str,
) -> List[Dict[str, Any]]:
    uid = str(user_id)
    sid = str(spreadsheet_id)
    rows = await database.fetch_all(
        """
        SELECT ugs.spreadsheet_id, ugs.spreadsheet_name, ugs.sheet_name,
               ugs.google_sheets_connection_id,
               gsc.google_account_email, gsc.connection_name
        FROM user_google_spreadsheets ugs
        JOIN google_sheets_connections gsc
          ON ugs.google_sheets_connection_id = gsc.id AND gsc.is_active = true
        WHERE ugs.user_id = :user_id AND ugs.spreadsheet_id = :spreadsheet_id
        ORDER BY ugs.last_accessed_at DESC NULLS LAST
        """,
        {"user_id": uid, "spreadsheet_id": sid},
    )
    if rows:
        return [_workbook_match_row(dict(r)) for r in rows]

    schema_rows = await database.fetch_all(
        """
        SELECT ges.spreadsheet_id, ges.spreadsheet_name, ges.sheet_name,
               ges.google_sheets_connection_id,
               gsc.google_account_email, gsc.connection_name
        FROM gsheets_extracted_schemas ges
        JOIN google_sheets_connections gsc
          ON ges.google_sheets_connection_id = gsc.id AND gsc.is_active = true
        WHERE ges.user_id = :user_id
          AND ges.spreadsheet_id = :spreadsheet_id
          AND COALESCE(ges.is_deleted, FALSE) = FALSE
        ORDER BY ges.extracted_at DESC NULLS LAST
        LIMIT 5
        """,
        {"user_id": uid, "spreadsheet_id": sid},
    )
    return [_workbook_match_row(dict(r)) for r in schema_rows or []]


def _dedupe_workbook_candidates(matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for m in matches:
        key = (m.get("spreadsheet_id"), m.get("google_sheets_connection_id"))
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    return out


def ambiguous_workbook_response(spreadsheet_name: str, matches: List[Dict[str, Any]]) -> Dict[str, Any]:
    candidates = []
    for m in _dedupe_workbook_candidates(matches):
        email = m.get("google_account_email") or "unknown account"
        label = m.get("connection_label") or email
        candidates.append(
            {
                "spreadsheet_id": m.get("spreadsheet_id"),
                "spreadsheet_name": m.get("spreadsheet_name"),
                "sheet_name": m.get("sheet_name"),
                "google_sheets_connection_id": m.get("google_sheets_connection_id"),
                "google_account_email": m.get("google_account_email"),
                "connection_label": label,
            }
        )
    example = candidates[0]["google_account_email"] if candidates else "account@example.com"
    message = (
        f"Multiple workbooks named **{spreadsheet_name}** are connected under different Google accounts. "
        f"Please specify which one — for example: \"{spreadsheet_name} in {example}\"."
    )
    return {
        "found": False,
        "error": "ambiguous_spreadsheet_name",
        "spreadsheet_name": spreadsheet_name,
        "message": message,
        "candidates": candidates,
    }


def found_workbook_response(
    match: Dict[str, Any],
    *,
    source: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    out = {
        "found": True,
        "spreadsheet_id": match.get("spreadsheet_id"),
        "spreadsheet_name": match.get("spreadsheet_name"),
        "sheet_name": match.get("sheet_name"),
        "range_name": None,
        "source": source,
        "google_sheets_connection_id": match.get("google_sheets_connection_id"),
        "google_account_email": match.get("google_account_email"),
    }
    if extra:
        out.update(extra)
    return out


async def resolve_workbook_by_name(
    user_id: str,
    spreadsheet_name: str,
    sheet_name: Optional[str] = None,
    *,
    source: str = "name_lookup",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    import re

    raw_name = (spreadsheet_name or "").strip()
    email_hint: Optional[str] = None
    paren_match = re.match(r"^(.+?)\s+\(([^)]+)\)\s*$", raw_name)
    if paren_match and "@" in paren_match.group(2):
        raw_name = paren_match.group(1).strip()
        email_hint = paren_match.group(2).strip().lower()
    in_match = re.match(r"^(.+?)\s+in\s+(.+@.+)\s*$", raw_name, re.I)
    if in_match:
        raw_name = in_match.group(1).strip()
        email_hint = in_match.group(2).strip().lower()

    matches = await list_workbook_matches(user_id, raw_name, sheet_name)
    if email_hint:
        matches = [
            m
            for m in matches
            if (m.get("google_account_email") or "").strip().lower() == email_hint
        ]
    unique = _dedupe_workbook_candidates(matches)
    if len(unique) == 1:
        return found_workbook_response(unique[0], source=source, extra=extra)
    if len(unique) > 1:
        return ambiguous_workbook_response(raw_name, unique)
    return {"found": False}


async def resolve_workbook_by_spreadsheet_id(
    user_id: str,
    spreadsheet_id: str,
    *,
    source: str = "spreadsheet_id_lookup",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    matches = await list_workbooks_by_spreadsheet_id(user_id, spreadsheet_id)
    unique = _dedupe_workbook_candidates(matches)
    if len(unique) == 1:
        return found_workbook_response(unique[0], source=source, extra=extra)
    if len(unique) > 1:
        name = unique[0].get("spreadsheet_name") or spreadsheet_id
        return ambiguous_workbook_response(str(name), unique)
    return {"found": False}


async def get_newest_connection(user_id: str, *, refresh_token: bool = True) -> Optional[Dict[str, Any]]:
    row = await database.fetch_one(
        f"""
        SELECT {_CONNECTION_COLUMNS}
        FROM google_sheets_connections
        WHERE user_id = :user_id AND is_active = true
        ORDER BY created_at DESC
        LIMIT 1
        """,
        {"user_id": str(user_id)},
    )
    if not row:
        return None
    data = _row_dict(row)
    if refresh_token:
        data = await _refresh_token_if_expired(data)
    return _public_connection(data, access_token=data.get("access_token"))


async def resolve_connection_for_spreadsheet(
    user_id: str,
    spreadsheet_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Pick the Google Sheets connection that can access this spreadsheet.
    Prefers a prior user_google_spreadsheets link, then tries each active account.
    """
    from utils.google_sheets_auth import get_valid_google_sheets_token
    from services.google_sheets_service import GoogleSheetsService

    uid = str(user_id)
    sid = str(spreadsheet_id)

    linked = await database.fetch_one(
        """
        SELECT gsc.id::text AS connection_id
        FROM user_google_spreadsheets ugs
        JOIN google_sheets_connections gsc ON ugs.google_sheets_connection_id = gsc.id
        WHERE ugs.user_id = :user_id
          AND ugs.spreadsheet_id = :spreadsheet_id
          AND gsc.is_active = true
        ORDER BY ugs.last_accessed_at DESC NULLS LAST
        LIMIT 1
        """,
        {"user_id": uid, "spreadsheet_id": sid},
    )
    if linked:
        conn = await get_valid_google_sheets_token(uid, linked["connection_id"])
        if conn:
            return conn

    rows = await database.fetch_all(
        """
        SELECT id::text AS connection_id
        FROM google_sheets_connections
        WHERE user_id = :user_id AND is_active = true
        ORDER BY created_at DESC
        """,
        {"user_id": uid},
    )
    for row in rows or []:
        cid = dict(row).get("connection_id")
        if not cid:
            continue
        conn = await get_valid_google_sheets_token(uid, cid)
        if not conn:
            continue
        try:
            sheets_service = GoogleSheetsService(
                access_token=conn["access_token"],
                refresh_token=conn.get("refresh_token"),
                expires_at=conn.get("expires_at"),
            )
            await sheets_service.get_spreadsheet_metadata(sid)
            return conn
        except Exception:
            continue

    return await get_valid_google_sheets_token(uid)
