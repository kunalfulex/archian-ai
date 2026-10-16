"""SQL connector query execution must not swallow errors as empty result sets."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.sql_connector_service import (
    PostgreSQLConnector,
    _reraise_query_error,
)


def test_reraise_query_error_reraises():
    with pytest.raises(ValueError, match="bad sql"):
        try:
            raise ValueError("bad sql")
        except Exception as e:
            _reraise_query_error(e, "PostgreSQL")


def test_prepare_pyformat_query_escapes_like_when_no_params():
    from services.sql_connector_service import _prepare_pyformat_query

    q = "SELECT * FROM users WHERE email LIKE '%@%.%'"
    prepared, params = _prepare_pyformat_query(q, None)
    assert params == ()
    assert prepared == "SELECT * FROM users WHERE email LIKE '%%@%%.%%'"


def test_prepare_pyformat_query_keeps_placeholders_when_params_provided():
    from services.sql_connector_service import _prepare_pyformat_query

    q = "SELECT * FROM users WHERE id = %s"
    prepared, params = _prepare_pyformat_query(q, (1,))
    assert prepared == q
    assert params == (1,)


def test_postgresql_execute_query_raises_instead_of_empty_list():
    connector = PostgreSQLConnector(
        {
            "host": "localhost",
            "port": 5432,
            "username": "u",
            "password": "p",
            "database_name": "db",
        }
    )
    mock_cursor = AsyncMock()
    mock_cursor.execute.side_effect = Exception("GROUP BY error")
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_cm.__aexit__ = AsyncMock(return_value=None)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cm
    connector.connection = mock_conn

    with pytest.raises(Exception, match="GROUP BY error"):
        asyncio.run(connector.execute_query("SELECT bad FROM users"))


def test_postgresql_execute_query_iter_batches_escapes_like_literals():
    connector = PostgreSQLConnector(
        {
            "host": "localhost",
            "port": 5432,
            "username": "u",
            "password": "p",
            "database_name": "db",
        }
    )
    mock_cursor = AsyncMock()
    mock_cursor.description = (("id",), ("email",))
    mock_cursor.fetchmany = AsyncMock(side_effect=[[(1, "a@b.c")], []])
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_cm.__aexit__ = AsyncMock(return_value=None)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cm
    connector.connection = mock_conn

    async def _collect():
        batches = []
        async for batch in connector.execute_query_iter_batches(
            "SELECT id, email FROM users WHERE email LIKE '%@%.%'"
        ):
            batches.append(batch)
        return batches

    result = asyncio.run(_collect())
    assert result == [[{"id": 1, "email": "a@b.c"}]]
    executed_query = mock_cursor.execute.call_args[0][0]
    assert "LIKE '%%@%%.%%'" in executed_query


def test_postgresql_execute_query_iter_batches_raises_on_error():
    connector = PostgreSQLConnector(
        {
            "host": "localhost",
            "port": 5432,
            "username": "u",
            "password": "p",
            "database_name": "db",
        }
    )
    mock_cursor = AsyncMock()
    mock_cursor.execute.side_effect = Exception("syntax error")
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_cm.__aexit__ = AsyncMock(return_value=None)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cm
    connector.connection = mock_conn

    async def _collect():
        batches = []
        async for batch in connector.execute_query_iter_batches("SELECT 1"):
            batches.append(batch)
        return batches

    with pytest.raises(Exception, match="syntax error"):
        asyncio.run(_collect())


def test_runtime_await_extraction_persisted_accepts_empty_data():
    from agent.runtime import AgentRuntime

    runtime = AgentRuntime(registry=MagicMock())
    empty_row = {"job_id": "job-empty", "extracted_data": []}

    async def _run():
        with patch(
            "services.extraction_results_service.ExtractionResultsService.get_extraction_result",
            new_callable=AsyncMock,
            return_value=empty_row,
        ):
            return await runtime._await_extraction_persisted("user-1", "job-empty")

    assert asyncio.run(_run()) is True
