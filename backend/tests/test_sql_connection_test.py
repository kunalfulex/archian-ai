"""Tests for SQL connection test timeouts and error messages."""
from services.sql_connector_service import (
    ConnectionTestResult,
    _format_connection_error,
    _mysql_connect_kwargs,
    SQL_CONNECT_TIMEOUT_SECONDS,
)


def test_mysql_connect_kwargs_includes_timeout():
    kwargs = _mysql_connect_kwargs(
        {
            "host": "db.example.com",
            "port": 3306,
            "username": "u",
            "password": "p",
            "database_name": "mydb",
        }
    )
    assert kwargs["connect_timeout"] == SQL_CONNECT_TIMEOUT_SECONDS
    assert kwargs["host"] == "db.example.com"


def test_format_mysql_2003_unreachable():
    exc = Exception("(2003, \"Can't connect to MySQL server on 'sql3.freesqldatabase.com'\")")
    result = _format_connection_error(exc, "mysql", {"host": "sql3.freesqldatabase.com", "port": 3306})
    assert result.ok is False
    assert result.error_code == "2003"
    assert "Cannot reach MySQL" in result.message
    assert "sql3.freesqldatabase.com" in result.message


def test_mysql_close_connection_does_not_await_sync_close():
    """aiomysql Connection.close() is sync; ensure_closed must be used."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from services.sql_connector_service import MySQLConnector

    connector = MySQLConnector(
        {
            "host": "localhost",
            "port": 3306,
            "username": "u",
            "password": "p",
            "database_name": "db",
        }
    )
    mock_conn = MagicMock()
    mock_conn.close.return_value = None
    mock_conn.ensure_closed = AsyncMock()
    connector.connection = mock_conn

    asyncio.run(connector.close_connection())

    mock_conn.ensure_closed.assert_awaited_once()
    mock_conn.close.assert_not_called()


def test_format_timeout():
    import asyncio

    result = _format_connection_error(
        asyncio.TimeoutError(),
        "mysql",
        {"host": "slow.example.com"},
    )
    assert result.error_code == "TIMEOUT"
    assert "timed out" in result.message.lower()
