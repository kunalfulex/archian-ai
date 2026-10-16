"""Load per-user connector resources from the database for planning."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ConnectorProfileSummary:
    user_description: Optional[str] = None
    synopsis: Optional[str] = None
    business_domains: List[str] = field(default_factory=list)
    system_roles: List[str] = field(default_factory=list)
    synonyms: List[str] = field(default_factory=list)
    profile_status: Optional[str] = None
    entity_labeled_count: int = 0
    entity_total_count: int = 0


@dataclass
class SqlConnectionRef:
    name: str
    connector_type: str = "sql"
    connector_id: Optional[str] = None
    profile: Optional[ConnectorProfileSummary] = None


@dataclass
class SalesforceConnectionRef:
    name: str
    environment_type: Optional[str] = None
    is_default: bool = False
    token_id: Optional[str] = None
    org_id: Optional[str] = None
    profile: Optional[ConnectorProfileSummary] = None


@dataclass
class GoogleSheetsConnectionRef:
    connection_id: str
    name: str
    profile: Optional[ConnectorProfileSummary] = None


@dataclass
class GoogleSheetRef:
    spreadsheet_name: str
    sheet_name: Optional[str] = None
    spreadsheet_id: Optional[str] = None
    google_sheets_connection_id: Optional[str] = None
    account_name: Optional[str] = None


@dataclass
class ResourceCatalog:
    """Snapshot of resources the user can use in the current turn."""

    sql_connections: List[SqlConnectionRef] = field(default_factory=list)
    salesforce_connections: List[SalesforceConnectionRef] = field(default_factory=list)
    google_sheets_connections: List[GoogleSheetsConnectionRef] = field(default_factory=list)
    google_sheet_rows: List[GoogleSheetRef] = field(default_factory=list)
    has_google_sheets: bool = False

    @property
    def sql_names(self) -> List[str]:
        return [c.name for c in self.sql_connections if c.name]

    @property
    def salesforce_names(self) -> List[str]:
        return [c.name for c in self.salesforce_connections if c.name]

    @property
    def spreadsheet_names(self) -> List[str]:
        seen: set = set()
        out: List[str] = []
        for row in self.google_sheet_rows:
            sn = (row.spreadsheet_name or "").strip()
            key = sn.lower()
            if not sn or key in seen:
                continue
            seen.add(key)
            out.append(sn)
        return out

    def salesforce_connections_json(self) -> Dict[str, Any]:
        return {
            "connections": [
                {
                    "name": c.name,
                    "environment": c.environment_type,
                    "status": "Connected",
                }
                for c in self.salesforce_connections
            ],
        }


def _as_row_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


async def _profile_summary_for_connector(
    user_id: str,
    connector_type: str,
    connector_instance_id: str,
) -> ConnectorProfileSummary:
    from services.labeling.store import LabelingStore

    profile_row = await LabelingStore.get_business_profile(
        user_id=user_id,
        connector_type=connector_type,
        connector_instance_id=connector_instance_id,
    )
    stats = await LabelingStore.get_entity_labeling_stats(
        user_id=user_id,
        connector_type=connector_type,
        connector_instance_id=connector_instance_id,
    )
    ai = (profile_row or {}).get("ai_profile") if profile_row else None
    if isinstance(ai, str):
        import json

        try:
            ai = json.loads(ai)
        except json.JSONDecodeError:
            ai = {}
    ai = ai if isinstance(ai, dict) else {}
    return ConnectorProfileSummary(
        user_description=(profile_row or {}).get("user_description"),
        synopsis=ai.get("synopsis"),
        business_domains=list(ai.get("business_domains") or []),
        system_roles=list(ai.get("system_roles") or []),
        synonyms=list(ai.get("synonyms") or []),
        profile_status=(profile_row or {}).get("profile_status"),
        entity_labeled_count=int(stats.get("ready") or 0),
        entity_total_count=int(stats.get("total") or 0),
    )


async def _enrich_catalog_profiles(user_id: str, catalog: ResourceCatalog) -> None:
    for conn in catalog.sql_connections:
        if conn.connector_id:
            conn.profile = await _profile_summary_for_connector(user_id, "sql", conn.connector_id)
    for conn in catalog.salesforce_connections:
        if conn.token_id:
            conn.profile = await _profile_summary_for_connector(
                user_id, "salesforce", conn.token_id
            )
    for conn in catalog.google_sheets_connections:
        conn.profile = await _profile_summary_for_connector(
            user_id, "google_sheets", conn.connection_id
        )


def coerce_planning_catalog(
    catalog: Any = None,
    classification: Optional[Dict[str, Any]] = None,
) -> Optional[ResourceCatalog]:
    """Return a ResourceCatalog instance; ignore stale/non-object _planning_catalog pins."""
    if isinstance(catalog, ResourceCatalog):
        return catalog
    if classification:
        raw = classification.get("_planning_catalog")
        if isinstance(raw, ResourceCatalog):
            return raw
    return None


async def load_resource_catalog(user_id: str) -> ResourceCatalog:
    from db.connection import database

    catalog = ResourceCatalog()

    try:
        sql_rows = await database.fetch_all(
            """
            SELECT id, name, connector_type FROM sql_connectors
            WHERE user_id = :user_id AND is_active = true
            ORDER BY created_at DESC
            """,
            {"user_id": user_id},
        )
        for raw in sql_rows or []:
            row = _as_row_dict(raw)
            name = row.get("name")
            if not name:
                continue
            catalog.sql_connections.append(
                SqlConnectionRef(
                    name=str(name).strip(),
                    connector_type=str(row.get("connector_type") or "sql"),
                    connector_id=str(row["id"]) if row.get("id") else None,
                )
            )
    except Exception as exc:
        logger.warning("[planning] Could not fetch SQL connections: %s", exc)

    try:
        sf_rows = await database.fetch_all(
            """
            SELECT
                id AS token_id,
                org_id,
                org_display_name AS connection_name,
                environment_type
            FROM salesforce_tokens
            WHERE user_id = :user_id
            ORDER BY issued_at DESC
            """,
            {"user_id": user_id},
        )
        for raw in sf_rows or []:
            row = _as_row_dict(raw)
            label = (row.get("connection_name") or "Salesforce").strip()
            catalog.salesforce_connections.append(
                SalesforceConnectionRef(
                    name=label,
                    environment_type=row.get("environment_type"),
                    token_id=str(row["token_id"]) if row.get("token_id") else None,
                    org_id=str(row["org_id"]) if row.get("org_id") else None,
                )
            )
    except Exception as exc:
        logger.warning("[planning] Could not fetch Salesforce connections: %s", exc)

    try:
        gs_count_row = await database.fetch_one(
            """
            SELECT COUNT(*) as count FROM google_sheets_connections
            WHERE user_id = :user_id AND is_active = true
            """,
            {"user_id": user_id},
        )
        count = 0
        if gs_count_row is not None:
            try:
                count = int(gs_count_row["count"])
            except (TypeError, ValueError, KeyError):
                count = int(_as_row_dict(gs_count_row).get("count", 0))
        catalog.has_google_sheets = count > 0

        gs_conn_rows = await database.fetch_all(
            """
            SELECT id, COALESCE(connection_name, google_account_email, 'Google Sheets') AS name
            FROM google_sheets_connections
            WHERE user_id = :user_id AND is_active = true
            ORDER BY created_at DESC
            """,
            {"user_id": user_id},
        )
        for raw in gs_conn_rows or []:
            row = _as_row_dict(raw)
            cid = row.get("id")
            if not cid:
                continue
            catalog.google_sheets_connections.append(
                GoogleSheetsConnectionRef(
                    connection_id=str(cid),
                    name=str(row.get("name") or "Google Sheets").strip(),
                )
            )

        if catalog.has_google_sheets:
            conn_name_by_id = {
                str(c.connection_id): (c.name or "").strip()
                for c in catalog.google_sheets_connections
                if c.connection_id
            }
            schema_rows = await database.fetch_all(
                """
                SELECT DISTINCT
                    ges.spreadsheet_name,
                    ges.sheet_name,
                    ges.spreadsheet_id,
                    gsc.id AS google_sheets_connection_id,
                    COALESCE(gsc.connection_name, gsc.google_account_email, 'Google Sheets') AS account_name
                FROM gsheets_extracted_schemas ges
                JOIN google_sheets_connections gsc
                  ON ges.google_sheets_connection_id = gsc.id
                WHERE ges.user_id = :user_id
                  AND ges.spreadsheet_name IS NOT NULL
                  AND TRIM(ges.spreadsheet_name) != ''
                  AND COALESCE(ges.is_deleted, FALSE) = FALSE
                ORDER BY ges.spreadsheet_name, ges.sheet_name
                """,
                {"user_id": user_id},
            )
            seen: set = set()
            for raw in schema_rows or []:
                row = _as_row_dict(raw)
                sn = (row.get("spreadsheet_name") or "").strip()
                tab = (row.get("sheet_name") or "").strip() or "Sheet1"
                conn_id = str(row.get("google_sheets_connection_id") or "").strip() or None
                key = (sn.lower(), tab.lower(), (conn_id or "").lower())
                if not sn or key in seen:
                    continue
                seen.add(key)
                account_name = (row.get("account_name") or conn_name_by_id.get(conn_id or "", "")).strip() or None
                catalog.google_sheet_rows.append(
                    GoogleSheetRef(
                        spreadsheet_name=sn,
                        sheet_name=tab,
                        spreadsheet_id=row.get("spreadsheet_id"),
                        google_sheets_connection_id=conn_id,
                        account_name=account_name,
                    )
                )
            try:
                user_sheets = await database.fetch_all(
                    """
                    SELECT
                        ugs.spreadsheet_name,
                        ugs.sheet_name,
                        ugs.spreadsheet_id,
                        gsc.id AS google_sheets_connection_id,
                        COALESCE(gsc.connection_name, gsc.google_account_email, 'Google Sheets') AS account_name
                    FROM user_google_spreadsheets ugs
                    JOIN google_sheets_connections gsc ON ugs.google_sheets_connection_id = gsc.id
                    WHERE gsc.user_id = :user_id
                    ORDER BY ugs.is_favorite DESC, ugs.last_accessed_at DESC
                    """,
                    {"user_id": user_id},
                )
                for raw in user_sheets or []:
                    row = _as_row_dict(raw)
                    sn = (row.get("spreadsheet_name") or "").strip()
                    tab = (row.get("sheet_name") or "").strip() or "Sheet1"
                    conn_id = str(row.get("google_sheets_connection_id") or "").strip() or None
                    key = (sn.lower(), tab.lower(), (conn_id or "").lower())
                    if not sn or key in seen:
                        continue
                    seen.add(key)
                    account_name = (row.get("account_name") or conn_name_by_id.get(conn_id or "", "")).strip() or None
                    catalog.google_sheet_rows.append(
                        GoogleSheetRef(
                            spreadsheet_name=sn,
                            sheet_name=tab,
                            spreadsheet_id=row.get("spreadsheet_id"),
                            google_sheets_connection_id=conn_id,
                            account_name=account_name,
                        )
                    )
            except Exception as ugs_exc:
                logger.warning("[planning] user_google_spreadsheets: %s", ugs_exc)
    except Exception as exc:
        logger.warning("[planning] Could not fetch Google Sheets catalog: %s", exc)

    try:
        await _enrich_catalog_profiles(user_id, catalog)
    except Exception as exc:
        logger.warning("[planning] Could not enrich connector profiles: %s", exc)

    return catalog
