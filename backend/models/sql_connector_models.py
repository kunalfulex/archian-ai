from pydantic import BaseModel, Field, field_validator
from typing import Any, Dict, Optional


def normalize_sql_connector_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce frontend form values before validation (e.g. port '' for Snowflake)."""
    out = dict(data or {})
    port = out.get("port")
    if port in ("", None):
        out["port"] = None
    else:
        try:
            out["port"] = int(port)
        except (TypeError, ValueError):
            pass
    for key in ("host", "database_name", "username", "connection_string", "name"):
        if out.get(key) == "":
            out[key] = None
    if out.get("additional_config") in ("", None):
        out["additional_config"] = {}
    return out


class SQLConnectorCreate(BaseModel):
    """Model for creating SQL connectors"""
    connector_type: str = Field(..., description="Type of SQL connector (mysql, postgresql, mssql, oracle, bigquery, snowflake)")
    name: str = Field(..., description="Display name for the connector")
    host: Optional[str] = Field(None, description="Database host")
    port: Optional[int] = Field(None, description="Database port")
    database_name: Optional[str] = Field(None, description="Database name")
    username: Optional[str] = Field(None, description="Database username")
    password: Optional[str] = Field(None, description="Database password")
    connection_string: Optional[str] = Field(None, description="Full connection string")
    ssl_enabled: bool = Field(False, description="Enable SSL connection")
    additional_config: Optional[Dict[str, Any]] = Field(None, description="Additional connector-specific configuration")

    @field_validator("port", mode="before")
    @classmethod
    def coerce_port(cls, value: Any) -> Optional[int]:
        if value in ("", None):
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return int(stripped) if stripped else None
        return int(value)
