"""Unit tests for Snowflake connector helpers (no live Snowflake account required)."""
from __future__ import annotations

import pytest

from models.sql_connector_models import SQLConnectorCreate, normalize_sql_connector_payload
from services.sql_connector_service import (
    SQLConnectorFactory,
    SnowflakeConnector,
    _snowflake_account,
    qualify_snowflake_sql,
    split_sql_statements,
    snowflake_connect_params,
    snowflake_qualify_table,
)


def test_snowflake_factory():
    conn = SQLConnectorFactory.create_connector("snowflake", {"host": "acct", "username": "u"})
    assert isinstance(conn, SnowflakeConnector)


def test_snowflake_account_from_host():
    cfg = {"host": "xy12345.us-east-1.aws", "username": "svc"}
    assert _snowflake_account(cfg) == "xy12345.us-east-1.aws"


def test_snowflake_account_strips_url():
    cfg = {"host": "https://xy12345.us-east-1.aws.snowflakecomputing.com"}
    assert _snowflake_account(cfg) == "xy12345.us-east-1.aws"


def test_snowflake_connect_params_password():
    params = snowflake_connect_params(
        {
            "host": "acct",
            "username": "user",
            "password": "secret",
            "database_name": "DB1",
            "additional_config": {
                "role": "ARCHIAN_RO",
                "warehouse": "COMPUTE_WH",
                "schema": "ANALYTICS",
            },
        }
    )
    assert params["account"] == "acct"
    assert params["user"] == "user"
    assert params["password"] == "secret"
    assert params["database"] == "DB1"
    assert params["role"] == "ARCHIAN_RO"
    assert params["warehouse"] == "COMPUTE_WH"
    assert params["schema"] == "ANALYTICS"


def test_snowflake_connect_params_missing_account():
    with pytest.raises(ValueError, match="account identifier"):
        snowflake_connect_params({"username": "u"})


def test_snowflake_qualify_table_and_sql():
    cfg = {
        "database_name": "SNOWFLAKE_SAMPLE_DATA",
        "additional_config": {"schema": "TPCH_SF1"},
    }
    assert snowflake_qualify_table(cfg, "CUSTOMER") == "SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.CUSTOMER"
    sql = "SELECT * FROM CUSTOMER c JOIN ORDERS o ON c.C_CUSTKEY = o.O_CUSTKEY LIMIT 10"
    qualified = qualify_snowflake_sql(sql, ["CUSTOMER", "ORDERS"], cfg)
    assert "SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.CUSTOMER" in qualified
    assert "SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.ORDERS" in qualified


def test_normalize_sql_connector_payload_empty_port():
    model = SQLConnectorCreate(
        **normalize_sql_connector_payload(
            {
                "connector_type": "snowflake",
                "name": "WH",
                "host": "acct",
                "port": "",
                "database_name": "DB",
                "username": "u",
                "additional_config": {"role": "R", "warehouse": "W", "schema": "S"},
            }
        )
    )
    assert model.port is None


def test_split_sql_statements():
    sql = "CREATE TABLE t1 (id INT); CREATE VIEW v1 AS SELECT ';' AS s;  "
    out = split_sql_statements(sql)
    assert len(out) == 2
    assert out[0].startswith("CREATE TABLE t1")
    assert out[1].startswith("CREATE VIEW v1")
