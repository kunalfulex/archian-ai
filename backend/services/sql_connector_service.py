import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod
from typing import AsyncIterator, List, Dict, Any, Optional

logger = logging.getLogger(__name__)
import aiomysql
import aiopg
import cx_Oracle
from google.cloud import bigquery
from google.oauth2 import service_account
import os
from cryptography.fernet import Fernet
import base64
from dataclasses import dataclass

SQL_CONNECT_TIMEOUT_SECONDS = int(os.getenv("SQL_CONNECT_TIMEOUT_SECONDS", "10"))
SQL_TEST_TIMEOUT_SECONDS = int(os.getenv("SQL_TEST_TIMEOUT_SECONDS", "15"))


@dataclass
class ConnectionTestResult:
    ok: bool
    message: str
    error_code: Optional[str] = None


def _exception_error_code(exc: Exception) -> Optional[str]:
    args = getattr(exc, "args", None)
    if args and isinstance(args[0], int):
        return str(args[0])
    return None


def _format_connection_error(
    exc: Exception,
    connector_type: str,
    config: Dict[str, Any],
) -> ConnectionTestResult:
    """User-facing message for failed connection tests."""
    code = _exception_error_code(exc)
    msg = str(exc).strip()
    host = (config.get("host") or "unknown").strip()
    port = config.get("port")
    ct = (connector_type or config.get("connector_type") or "").lower()

    if isinstance(exc, asyncio.TimeoutError):
        return ConnectionTestResult(
            ok=False,
            message=(
                f"Connection timed out after {SQL_TEST_TIMEOUT_SECONDS}s. "
                f"The server at {host} may be unreachable, firewalled, or out of connection slots."
            ),
            error_code="TIMEOUT",
        )

    if ct in ("mysql", "mariadb"):
        if code == "2003" or "2003" in msg:
            port_display = port or 3306
            return ConnectionTestResult(
                ok=False,
                message=(
                    f"Cannot reach MySQL server at {host}:{port_display}. "
                    "Check the hostname and port, that the database is online, outbound access "
                    "to port 3306 is allowed, and your IP is permitted (common on free-hosted MySQL)."
                ),
                error_code="2003",
            )
        if code == "2002" or "2002" in msg:
            return ConnectionTestResult(
                ok=False,
                message=f"Cannot resolve or reach MySQL host '{host}'. Check the hostname and DNS.",
                error_code="2002",
            )
        if code == "1045" or "1045" in msg or "access denied" in msg.lower():
            return ConnectionTestResult(
                ok=False,
                message="Access denied. Check username and password.",
                error_code="1045",
            )

    if ct in ("postgresql", "postgres"):
        if "timeout" in msg.lower() or "timed out" in msg.lower():
            return ConnectionTestResult(
                ok=False,
                message=f"Cannot reach PostgreSQL at {host}:{port or 5432} (timed out).",
                error_code=code or "TIMEOUT",
            )
        if "password authentication failed" in msg.lower():
            return ConnectionTestResult(
                ok=False,
                message="Access denied. Check username and password.",
                error_code="AUTH",
            )

    if ct == "mssql":
        if "timeout" in msg.lower() or "HYT00" in msg:
            return ConnectionTestResult(
                ok=False,
                message=f"Cannot reach SQL Server at {host}:{port or 1433} (timed out).",
                error_code="TIMEOUT",
            )

    if ct == "snowflake":
        account = (config.get("host") or config.get("account") or "unknown").strip()
        if "390100" in msg or "incorrect username" in msg.lower() or "authentication" in msg.lower():
            return ConnectionTestResult(
                ok=False,
                message="Access denied. Check username, password, or private key.",
                error_code="AUTH",
            )
        if "does not exist" in msg.lower() or "002043" in msg:
            return ConnectionTestResult(
                ok=False,
                message="Database, schema, warehouse, or role not found. Check connection settings.",
                error_code="NOT_FOUND",
            )
        if "timeout" in msg.lower() or "timed out" in msg.lower():
            return ConnectionTestResult(
                ok=False,
                message=f"Cannot reach Snowflake account '{account}' (timed out).",
                error_code="TIMEOUT",
            )

    return ConnectionTestResult(
        ok=False,
        message=f"Connection failed: {msg}" if msg else "Connection failed.",
        error_code=code,
    )


class SQLConnectorOperationError(Exception):
    """Connection or auth failure during list tables / schema operations."""

    def __init__(self, message: str, error_code: Optional[str] = None):
        self.user_message = message
        self.error_code = error_code
        super().__init__(message)


def _reraise_query_error(exc: Exception, connector_label: str) -> None:
    """Log and re-raise query failures (do not return empty rows — callers treat [] as success)."""
    print(f"Error executing {connector_label} query: {exc}")
    raise exc


def _prepare_pyformat_query(query: str, params: Optional[tuple]) -> tuple[str, tuple]:
    """
    Drivers using Python % formatting (aiopg, aiomysql) treat bare % as placeholders.
    When no bind params are supplied, escape literal % (e.g. LIKE '%@%') as %%.
    """
    bound = params or ()
    if bound or "%" not in (query or ""):
        return query, bound
    return query.replace("%", "%%"), bound


def _column_names_from_cursor_description(description) -> List[str]:
    if not description:
        return []
    names: List[str] = []
    for i, desc in enumerate(description):
        if desc and len(desc) > 0 and desc[0] is not None:
            names.append(str(desc[0]))
        else:
            names.append(f"column_{i + 1}")
    return names


def _raise_connection_error(
    exc: Exception,
    connector_type: str,
    config: Dict[str, Any],
) -> None:
    result = _format_connection_error(exc, connector_type, config)
    raise SQLConnectorOperationError(result.message, result.error_code) from exc


def _mysql_connect_kwargs(config: Dict[str, Any]) -> Dict[str, Any]:
    host = config.get("host") or ""
    if host == "localhost":
        host = "127.0.0.1"
    kwargs: Dict[str, Any] = {
        "host": host,
        "port": int(config.get("port") or 3306),
        "user": config["username"],
        "db": config["database_name"],
        "connect_timeout": SQL_CONNECT_TIMEOUT_SECONDS,
    }
    if str(config.get("password") or "").strip():
        kwargs["password"] = config["password"]
    if config.get("ssl_enabled", False):
        kwargs["ssl"] = {"check_hostname": False, "verify_cert": False}
    return kwargs


class BaseSQLConnector(ABC):
    """Base class for all SQL connectors"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.connection = None
    
    @abstractmethod
    async def test_connection(self) -> ConnectionTestResult:
        """Test database connection"""
        pass
    
    @abstractmethod
    async def get_tables(self) -> List[str]:
        """Get list of tables in the database"""
        pass
    
    @abstractmethod
    async def get_table_schema(self, table_name: str) -> Dict[str, Any]:
        """Get table schema with column details"""
        pass
    
    @abstractmethod
    async def execute_query(self, query: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """Execute SQL query and return results"""
        pass
    
    @abstractmethod
    async def execute_non_query(self, query: str, params: Optional[tuple] = None) -> int:
        """Execute SQL command that does not return result set (INSERT/UPDATE/DELETE)"""
        pass
    
    async def close_connection(self):
        """Close database connection (handles sync and async driver close methods)."""
        if not self.connection:
            return
        conn = self.connection
        self.connection = None
        try:
            if hasattr(conn, "ensure_closed"):
                await conn.ensure_closed()
                return
        except Exception:
            pass
        close_fn = getattr(conn, "close", None)
        if callable(close_fn):
            result = close_fn()
            if asyncio.iscoroutine(result):
                await result

    async def execute_query_iter_batches(
        self,
        query: str,
        params: Optional[tuple] = None,
        batch_size: int = 5000,
    ) -> AsyncIterator[List[Dict[str, Any]]]:
        """
        Execute query and yield rows in batches (streaming).
        Default implementation fetches all then yields chunks; override for true streaming.
        """
        all_rows = await self.execute_query(query, params)
        for i in range(0, len(all_rows), batch_size):
            yield all_rows[i : i + batch_size]


class MySQLConnector(BaseSQLConnector):
    """MySQL database connector"""
    
    async def test_connection(self) -> ConnectionTestResult:
        """Test MySQL connection"""
        try:
            connection = await aiomysql.connect(**_mysql_connect_kwargs(self.config))
            await connection.ensure_closed()
            return ConnectionTestResult(ok=True, message="Connection successful")
        except Exception as e:
            print(f"MySQL connection test failed: {e}")
            return _format_connection_error(e, "mysql", self.config)
    
    async def get_tables(self) -> List[str]:
        """Get list of MySQL tables"""
        try:
            if not self.connection:
                self.connection = await aiomysql.connect(**_mysql_connect_kwargs(self.config))
            
            async with self.connection.cursor() as cursor:
                await cursor.execute("SHOW TABLES")
                tables = await cursor.fetchall()
                return [table[0] for table in tables]
        except Exception as e:
            print(f"Error getting MySQL tables: {e}")
            _raise_connection_error(e, "mysql", self.config)
    
    async def get_table_schema(self, table_name: str) -> Dict[str, Any]:
        """Get MySQL table schema with column details"""
        try:
            if not self.connection:
                self.connection = await aiomysql.connect(**_mysql_connect_kwargs(self.config))
            
            async with self.connection.cursor() as cursor:
                # Get column information
                await cursor.execute(f"""
                    SELECT 
                        COLUMN_NAME,
                        DATA_TYPE,
                        IS_NULLABLE,
                        COLUMN_DEFAULT,
                        COLUMN_COMMENT,
                        COLUMN_KEY,
                        EXTRA
                    FROM INFORMATION_SCHEMA.COLUMNS 
                    WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                    ORDER BY ORDINAL_POSITION
                """, (self.config['database_name'], table_name))
                
                columns = await cursor.fetchall()
                
                schema_data = {
                    'table_name': table_name,
                    'columns': [],
                    'foreign_keys': []
                }
                
                for col in columns:
                    schema_data['columns'].append({
                        'column_name': col[0],
                        'data_type': col[1],
                        'is_nullable': col[2] == 'YES',
                        'column_default': col[3],
                        'column_comment': col[4],
                        'is_primary_key': col[5] == 'PRI',
                        'extra': col[6]
                    })

                # Get foreign key relationships for this table
                await cursor.execute("""
                    SELECT
                        kcu.CONSTRAINT_NAME,
                        kcu.COLUMN_NAME,
                        kcu.REFERENCED_TABLE_NAME,
                        kcu.REFERENCED_COLUMN_NAME,
                        rc.UPDATE_RULE,
                        rc.DELETE_RULE,
                        kcu.ORDINAL_POSITION
                    FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
                    LEFT JOIN INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc
                      ON rc.CONSTRAINT_SCHEMA = kcu.CONSTRAINT_SCHEMA
                     AND rc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
                    WHERE kcu.TABLE_SCHEMA = %s
                      AND kcu.TABLE_NAME = %s
                      AND kcu.REFERENCED_TABLE_NAME IS NOT NULL
                    ORDER BY kcu.CONSTRAINT_NAME, kcu.ORDINAL_POSITION
                """, (self.config['database_name'], table_name))
                fk_rows = await cursor.fetchall()
                for fk in fk_rows or []:
                    schema_data['foreign_keys'].append({
                        'constraint_name': fk[0],
                        'column_name': fk[1],
                        'referenced_table': fk[2],
                        'referenced_column': fk[3],
                        'on_update': fk[4],
                        'on_delete': fk[5],
                        'position': fk[6],
                    })
                
                return schema_data
        except Exception as e:
            print(f"Error getting MySQL table schema: {e}")
            return {}
    
    async def execute_query(self, query: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """Execute MySQL query and return results"""
        try:
            if not self.connection:
                self.connection = await aiomysql.connect(**_mysql_connect_kwargs(self.config))
            
            async with self.connection.cursor(aiomysql.DictCursor) as cursor:
                q, p = _prepare_pyformat_query(query, params)
                await cursor.execute(q, p)
                results = await cursor.fetchall()
                return results
        except Exception as e:
            _reraise_query_error(e, "MySQL")

    async def execute_query_iter_batches(
        self,
        query: str,
        params: Optional[tuple] = None,
        batch_size: int = 5000,
    ) -> AsyncIterator[List[Dict[str, Any]]]:
        """Execute MySQL query and yield rows in batches using fetchmany."""
        if not self.connection:
            self.connection = await aiomysql.connect(**_mysql_connect_kwargs(self.config))
        try:
            async with self.connection.cursor(aiomysql.DictCursor) as cursor:
                q, p = _prepare_pyformat_query(query, params)
                await cursor.execute(q, p)
                while True:
                    batch = await cursor.fetchmany(batch_size)
                    if not batch:
                        break
                    yield batch
        except Exception as e:
            await self.close_connection()
            _reraise_query_error(e, "MySQL (streaming)")
    
    async def execute_non_query(self, query: str, params: Optional[tuple] = None) -> int:
        """Execute INSERT/UPDATE/DELETE for MySQL"""
        try:
            if not self.connection:
                self.connection = await aiomysql.connect(**_mysql_connect_kwargs(self.config))

            async with self.connection.cursor() as cursor:
                q, p = _prepare_pyformat_query(query, params)
                await cursor.execute(q, p)
                await self.connection.commit()
                affected = cursor.rowcount or 0
                return affected
        except Exception as e:
            print(f"Error executing MySQL non-query: {e}")
            raise


class PostgreSQLConnector(BaseSQLConnector):
    """PostgreSQL database connector"""
    
    async def test_connection(self) -> ConnectionTestResult:
        """Test PostgreSQL connection"""
        try:
            connection = await aiopg.connect(
                host=self.config['host'],
                port=self.config.get('port', 5432),
                user=self.config['username'],
                password=self.config['password'],
                dbname=self.config['database_name'],
                sslmode='require' if self.config.get('ssl_enabled', False) else 'disable',
                timeout=SQL_CONNECT_TIMEOUT_SECONDS,
            )
            connection.close()
            return ConnectionTestResult(ok=True, message="Connection successful")
        except Exception as e:
            print(f"PostgreSQL connection test failed: {e}")
            return _format_connection_error(e, "postgresql", self.config)
    
    async def get_tables(self) -> List[str]:
        """Get list of PostgreSQL tables"""
        try:
            if not self.connection:
                print(
                    f"🧪 PostgreSQL connect -> host: {self.config['host']}, "
                    f"port: {self.config.get('port', 5432)}, "
                    f"database: {self.config['database_name']}, "
                    f"user: {self.config['username']}"
                )
                self.connection = await aiopg.connect(
                    host=self.config['host'],
                    port=self.config.get('port', 5432),
                    user=self.config['username'],
                    password=self.config['password'],
                    dbname=self.config['database_name'],
                    sslmode='require' if self.config.get('ssl_enabled', False) else 'disable'
                )
            
            async with self.connection.cursor() as cursor:
                await cursor.execute("""
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                """)
                tables = await cursor.fetchall()
                return [table[0] for table in tables]
        except Exception as e:
            print(f"Error getting PostgreSQL tables: {e}")
            _raise_connection_error(e, "postgresql", self.config)
    
    async def get_table_schema(self, table_name: str) -> Dict[str, Any]:
        """Get PostgreSQL table schema with column details"""
        try:
            if not self.connection:
                self.connection = await aiopg.connect(
                    host=self.config['host'],
                    port=self.config.get('port', 5432),
                    user=self.config['username'],
                    password=self.config['password'],
                    dbname=self.config['database_name'],
                    sslmode='require' if self.config.get('ssl_enabled', False) else 'disable'
                )
            
            async with self.connection.cursor() as cursor:
                # Get column information using universal information_schema
                await cursor.execute("""
                    SELECT 
                        c.column_name,
                        c.data_type,
                        c.is_nullable,
                        c.column_default,
                        NULL as column_comment,
                        CASE WHEN pk.column_name IS NOT NULL THEN true ELSE false END as is_primary_key
                    FROM information_schema.columns c
                    LEFT JOIN (
                        SELECT ku.column_name
                        FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage ku
                        ON tc.constraint_name = ku.constraint_name
                        WHERE tc.constraint_type = 'PRIMARY KEY'
                        AND tc.table_name = %s
                    ) pk ON c.column_name = pk.column_name
                    WHERE c.table_name = %s
                    ORDER BY c.ordinal_position
                """, (table_name, table_name))
                
                columns = await cursor.fetchall()
                
                schema_data = {
                    'table_name': table_name,
                    'columns': [],
                    'foreign_keys': []
                }
                
                for col in columns:
                    schema_data['columns'].append({
                        'column_name': col[0],
                        'data_type': col[1],
                        'is_nullable': col[2] == 'YES',
                        'column_default': col[3],
                        'column_comment': col[4],
                        'is_primary_key': col[5]
                    })

                # Get foreign key relationships for this table
                await cursor.execute("""
                    SELECT
                        tc.constraint_name,
                        kcu.column_name,
                        ccu.table_name AS referenced_table,
                        ccu.column_name AS referenced_column,
                        rc.update_rule,
                        rc.delete_rule,
                        kcu.ordinal_position
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema = kcu.table_schema
                    JOIN information_schema.constraint_column_usage ccu
                      ON ccu.constraint_name = tc.constraint_name
                     AND ccu.table_schema = tc.table_schema
                    LEFT JOIN information_schema.referential_constraints rc
                      ON rc.constraint_name = tc.constraint_name
                     AND rc.constraint_schema = tc.table_schema
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.table_name = %s
                      AND tc.table_schema = current_schema()
                    ORDER BY tc.constraint_name, kcu.ordinal_position
                """, (table_name,))
                fk_rows = await cursor.fetchall()
                for fk in fk_rows or []:
                    schema_data['foreign_keys'].append({
                        'constraint_name': fk[0],
                        'column_name': fk[1],
                        'referenced_table': fk[2],
                        'referenced_column': fk[3],
                        'on_update': fk[4],
                        'on_delete': fk[5],
                        'position': fk[6],
                    })
                
                return schema_data
        except Exception as e:
            print(f"Error getting PostgreSQL table schema: {e}")
            _raise_connection_error(e, "postgresql", self.config)
    
    async def execute_query(self, query: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """Execute PostgreSQL query and return results"""
        try:
            if not self.connection:
                self.connection = await aiopg.connect(
                    host=self.config['host'],
                    port=self.config.get('port', 5432),
                    user=self.config['username'],
                    password=self.config['password'],
                    dbname=self.config['database_name'],
                    sslmode='require' if self.config.get('ssl_enabled', False) else 'disable'
                )
            
            async with self.connection.cursor() as cursor:
                q, p = _prepare_pyformat_query(query, params)
                await cursor.execute(q, p)
                columns = _column_names_from_cursor_description(cursor.description)
                results = await cursor.fetchall()
                formatted = [dict(zip(columns, row)) for row in results]
                return formatted
        except Exception as e:
            _reraise_query_error(e, "PostgreSQL")

    async def execute_query_iter_batches(
        self,
        query: str,
        params: Optional[tuple] = None,
        batch_size: int = 5000,
    ) -> AsyncIterator[List[Dict[str, Any]]]:
        """Execute PostgreSQL query and yield rows in batches using fetchmany."""
        try:
            if not self.connection:
                self.connection = await aiopg.connect(
                    host=self.config['host'],
                    port=self.config.get('port', 5432),
                    user=self.config['username'],
                    password=self.config['password'],
                    dbname=self.config['database_name'],
                    sslmode='require' if self.config.get('ssl_enabled', False) else 'disable'
                )
            async with self.connection.cursor() as cursor:
                q, p = _prepare_pyformat_query(query, params)
                await cursor.execute(q, p)
                columns = _column_names_from_cursor_description(cursor.description)
                while True:
                    rows = await cursor.fetchmany(batch_size)
                    if not rows:
                        break
                    yield [dict(zip(columns, row)) for row in rows]
        except Exception as e:
            _reraise_query_error(e, "PostgreSQL (streaming)")

    async def execute_non_query(self, query: str, params: Optional[tuple] = None) -> int:
        """Execute INSERT/UPDATE/DELETE for PostgreSQL"""
        try:
            if not self.connection:
                self.connection = await aiopg.connect(
                    host=self.config['host'],
                    port=self.config.get('port', 5432),
                    user=self.config['username'],
                    password=self.config['password'],
                    dbname=self.config['database_name'],
                    sslmode='require' if self.config.get('ssl_enabled', False) else 'disable'
                )
            
            # Execute query
            async with self.connection.cursor() as cursor:
                q, p = _prepare_pyformat_query(query, params)
                await cursor.execute(q, p)
                rowcount = cursor.rowcount or 0
            
            # Try to commit, but handle the case where commit is not allowed
            # (some aiopg versions/environments auto-commit or handle transactions differently)
            try:
                await self.connection.commit()
            except Exception as commit_error:
                # If commit fails with "cannot be used in asynchronous mode" error,
                # it might be because the transaction is already handled automatically
                # This can happen in different aiopg versions or connection configurations
                error_msg = str(commit_error).lower()
                if "commit cannot be used" in error_msg or "asynchronous mode" in error_msg:
                    # Transaction might be auto-committed, continue without explicit commit
                    logger.debug(
                        "Commit not needed (auto-commit or transaction already handled): %s",
                        commit_error,
                    )
                else:
                    # Re-raise if it's a different error
                    raise
            
            return rowcount
        except Exception as e:
            logger.debug("Error executing PostgreSQL non-query: %s", e)
            raise


class MSSQLConnector(BaseSQLConnector):
    """Microsoft SQL Server database connector (uses pyodbc; sync driver run in executor)"""

    def _get_additional_config(self) -> Dict[str, Any]:
        """Return additional_config as a dict (API/DB may send it as string or dict). Never return a non-dict."""
        raw = self.config.get('additional_config') if isinstance(self.config, dict) else None
        if raw is None:
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                if not raw.strip():
                    return {}
                parsed = json.loads(raw)
                # Handle double-encoded JSON (e.g. '"{}"' or stored as JSON string)
                if isinstance(parsed, dict):
                    return parsed
                if isinstance(parsed, str):
                    inner = json.loads(parsed) if parsed.strip() else {}
                    return inner if isinstance(inner, dict) else {}
            except (json.JSONDecodeError, AttributeError, TypeError):
                return {}
        return {}

    def _get_mssql_driver(self) -> str:
        """Return best available MSSQL ODBC driver (18 preferred, 17 fallback for local/dev)."""
        import pyodbc
        preferred = ['ODBC Driver 18 for SQL Server', 'ODBC Driver 17 for SQL Server']
        available = pyodbc.drivers()
        for name in preferred:
            if name in available:
                return name
        return preferred[0]  # default to 18 if none found (caller will get clear error)

    def _build_connection_string(self) -> str:
        """Build pyodbc connection string from config."""
        host = self.config.get('host', 'localhost')
        if host == 'localhost':
            host = '127.0.0.1'
        port = self.config.get('port', 1433)
        database = self.config.get('database_name', '')
        user = self.config.get('username', '')
        password = self.config.get('password', '') or ''
        addl = self._get_additional_config()
        driver = (addl.get('driver') if isinstance(addl, dict) else None) or self._get_mssql_driver()
        parts = [
            f"DRIVER={{{driver}}}",
            f"SERVER={host},{port}",
            f"DATABASE={database}",
            f"UID={user}",
            f"PWD={password}",
        ]
        if self.config.get('ssl_enabled', False):
            parts.append("Encrypt=yes")
            parts.append("TrustServerCertificate=yes")
        parts.append(f"Connection Timeout={SQL_CONNECT_TIMEOUT_SECONDS}")
        return ";".join(parts)

    def _connect_sync(self):
        """Sync connect (run from executor)."""
        import pyodbc
        conn_str = self._build_connection_string()
        return pyodbc.connect(conn_str)

    async def test_connection(self) -> ConnectionTestResult:
        """Test MSSQL connection."""
        try:
            conn = await asyncio.wait_for(
                asyncio.to_thread(self._connect_sync),
                timeout=SQL_TEST_TIMEOUT_SECONDS,
            )
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()
                cursor.close()
            finally:
                conn.close()
            return ConnectionTestResult(ok=True, message="Connection successful")
        except Exception as e:
            print(f"MSSQL connection test failed: {e}")
            return _format_connection_error(e, "mssql", self.config)

    async def get_tables(self) -> List[str]:
        """Get list of MSSQL tables (current database, all schemas)."""
        query = """
            SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_CATALOG = DB_NAME()
            ORDER BY TABLE_NAME
        """
        try:
            if not self.connection:
                self.connection = await asyncio.to_thread(self._connect_sync)
            db_name = self.config.get('database_name', '')
            print(f"[MSSQL get_tables] config database_name={db_name!r}")

            def _get_tables():
                cur = self.connection.cursor()
                cur.execute("SELECT DB_NAME()")
                row0 = cur.fetchone()
                actual_db = (row0[0] if row0 else None)
                print(f"[MSSQL get_tables] SELECT DB_NAME() => {actual_db!r}")
                cur.execute(query)
                rows = cur.fetchall()
                cur.close()
                return [row[0] for row in rows]
            tables = await asyncio.to_thread(_get_tables)
            print(f"[MSSQL get_tables] returned {len(tables)} tables: {tables[:15]}{'...' if len(tables) > 15 else ''}")
            return tables
        except Exception as e:
            print(f"[MSSQL get_tables] Error: {e}")
            _raise_connection_error(e, "mssql", self.config)

    async def get_table_schema(self, table_name: str) -> Dict[str, Any]:
        """Get MSSQL table schema with column details."""
        try:
            if not self.connection:
                self.connection = await asyncio.to_thread(self._connect_sync)

            def _get_schema():
                cur = self.connection.cursor()
                cur.execute("""
                    SELECT
                        c.COLUMN_NAME,
                        c.DATA_TYPE,
                        c.IS_NULLABLE,
                        c.COLUMN_DEFAULT,
                        CASE WHEN pk.COLUMN_NAME IS NOT NULL THEN 1 ELSE 0 END AS is_pk
                    FROM INFORMATION_SCHEMA.COLUMNS c
                    LEFT JOIN (
                        SELECT ku.TABLE_CATALOG, ku.TABLE_SCHEMA, ku.TABLE_NAME, ku.COLUMN_NAME
                        FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
                        JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE ku
                            ON tc.CONSTRAINT_NAME = ku.CONSTRAINT_NAME
                            AND tc.TABLE_SCHEMA = ku.TABLE_SCHEMA
                            AND tc.TABLE_NAME = ku.TABLE_NAME
                        WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
                    ) pk ON c.TABLE_CATALOG = pk.TABLE_CATALOG AND c.TABLE_SCHEMA = pk.TABLE_SCHEMA
                        AND c.TABLE_NAME = pk.TABLE_NAME AND c.COLUMN_NAME = pk.COLUMN_NAME
                    WHERE c.TABLE_NAME = ?
                    ORDER BY c.ORDINAL_POSITION
                """, (table_name,))
                rows = cur.fetchall()
                cur.close()
                schema_data = {'table_name': table_name, 'columns': []}
                for row in rows:
                    schema_data['columns'].append({
                        'column_name': row[0],
                        'data_type': row[1],
                        'is_nullable': row[2] == 'YES',
                        'column_default': row[3],
                        'column_comment': None,
                        'is_primary_key': bool(row[4]),
                    })

                # Get foreign key relationships for this table
                cur.execute("""
                    SELECT
                        fk.name AS constraint_name,
                        c.name AS column_name,
                        rt.name AS referenced_table,
                        rc.name AS referenced_column,
                        fk.update_referential_action_desc AS on_update,
                        fk.delete_referential_action_desc AS on_delete,
                        fkc.constraint_column_id AS position
                    FROM sys.foreign_keys fk
                    JOIN sys.foreign_key_columns fkc
                      ON fk.object_id = fkc.constraint_object_id
                    JOIN sys.tables pt
                      ON fkc.parent_object_id = pt.object_id
                    JOIN sys.columns c
                      ON c.object_id = pt.object_id
                     AND c.column_id = fkc.parent_column_id
                    JOIN sys.tables rt
                      ON fkc.referenced_object_id = rt.object_id
                    JOIN sys.columns rc
                      ON rc.object_id = rt.object_id
                     AND rc.column_id = fkc.referenced_column_id
                    WHERE pt.name = ?
                    ORDER BY fk.name, fkc.constraint_column_id
                """, (table_name,))
                fk_rows = cur.fetchall()
                schema_data['foreign_keys'] = []
                for fk in fk_rows or []:
                    schema_data['foreign_keys'].append({
                        'constraint_name': fk[0],
                        'column_name': fk[1],
                        'referenced_table': fk[2],
                        'referenced_column': fk[3],
                        'on_update': fk[4],
                        'on_delete': fk[5],
                        'position': fk[6],
                    })
                return schema_data

            return await asyncio.to_thread(_get_schema)
        except Exception as e:
            print(f"Error getting MSSQL table schema: {e}")
            _raise_connection_error(e, "mssql", self.config)

    def _query_params_to_pyodbc(self, query: str, params: Optional[tuple]) -> tuple:
        """Convert %s placeholders to ? for pyodbc; return (query, params)."""
        if params is None:
            params = ()
        # Migration service and callers use %s; pyodbc uses ?
        q = query.replace('%s', '?')
        return (q, params)

    @staticmethod
    def _limit_to_top(query: str) -> str:
        """Rewrite MySQL/PostgreSQL-style LIMIT n to MSSQL SELECT TOP n (safety net if AI emits LIMIT)."""
        q = query.strip()
        match = re.search(r'\s+LIMIT\s+(\d+)\s*;?\s*$', q, re.IGNORECASE)
        if not match:
            return query
        n = match.group(1)
        q = re.sub(r'\s+LIMIT\s+\d+\s*;?\s*$', '', q, flags=re.IGNORECASE)
        # Insert TOP n after SELECT or SELECT DISTINCT
        q = re.sub(r'^(\s*SELECT\s+)(DISTINCT\s+)?', rf'\1TOP {n} \2', q, count=1, flags=re.IGNORECASE)
        return q

    async def execute_query(self, query: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """Execute MSSQL query and return results as list of dicts."""
        try:
            query = self._limit_to_top(query)
            if not self.connection:
                self.connection = await asyncio.to_thread(self._connect_sync)
            q, p = self._query_params_to_pyodbc(query, params)

            def _execute():
                cur = self.connection.cursor()
                cur.execute(q, p)
                columns = [d[0] for d in cur.description] if cur.description else []
                rows = cur.fetchall()
                cur.close()
                return [dict(zip(columns, row)) for row in rows]

            return await asyncio.to_thread(_execute)
        except Exception as e:
            _reraise_query_error(e, "MSSQL")

    async def execute_non_query(self, query: str, params: Optional[tuple] = None) -> int:
        """Execute INSERT/UPDATE/DELETE for MSSQL."""
        try:
            if not self.connection:
                self.connection = await asyncio.to_thread(self._connect_sync)
            q, p = self._query_params_to_pyodbc(query, params)

            def _execute():
                cur = self.connection.cursor()
                cur.execute(q, p)
                self.connection.commit()
                count = cur.rowcount or 0
                cur.close()
                return count

            return await asyncio.to_thread(_execute)
        except Exception as e:
            print(f"Error executing MSSQL non-query: {e}")
            raise

    async def close_connection(self):
        """Close MSSQL connection (sync close run in executor)."""
        if self.connection:
            await asyncio.to_thread(self.connection.close)
            self.connection = None


class OracleConnector(BaseSQLConnector):
    """Oracle database connector"""
    
    def _connect_sync_oracle(self):
        dsn = cx_Oracle.makedsn(
            self.config['host'],
            self.config.get('port', 1521),
            service_name=self.config['database_name'],
        )
        connection = cx_Oracle.connect(
            self.config['username'],
            self.config['password'],
            dsn,
        )
        connection.close()

    async def test_connection(self) -> ConnectionTestResult:
        """Test Oracle connection"""
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._connect_sync_oracle),
                timeout=SQL_TEST_TIMEOUT_SECONDS,
            )
            return ConnectionTestResult(ok=True, message="Connection successful")
        except Exception as e:
            print(f"Oracle connection test failed: {e}")
            return _format_connection_error(e, "oracle", self.config)
    
    async def get_tables(self) -> List[str]:
        """Get list of Oracle tables"""
        try:
            dsn = cx_Oracle.makedsn(
                self.config['host'],
                self.config.get('port', 1521),
                service_name=self.config['database_name']
            )
            self.connection = cx_Oracle.connect(
                self.config['username'],
                self.config['password'],
                dsn
            )
            
            cursor = self.connection.cursor()
            cursor.execute("""
                SELECT table_name 
                FROM user_tables 
                ORDER BY table_name
            """)
            tables = cursor.fetchall()
            return [table[0] for table in tables]
        except Exception as e:
            print(f"Error getting Oracle tables: {e}")
            _raise_connection_error(e, "oracle", self.config)
    
    async def get_table_schema(self, table_name: str) -> Dict[str, Any]:
        """Get Oracle table schema with column details"""
        try:
            if not self.connection:
                dsn = cx_Oracle.makedsn(
                    self.config['host'],
                    self.config.get('port', 1521),
                    service_name=self.config['database_name']
                )
                self.connection = cx_Oracle.connect(
                    self.config['username'],
                    self.config['password'],
                    dsn
                )
            
            cursor = self.connection.cursor()
            cursor.execute("""
                SELECT 
                    column_name,
                    data_type,
                    nullable,
                    data_default,
                    comments
                FROM user_tab_columns c
                LEFT JOIN user_col_comments cc ON c.table_name = cc.table_name 
                    AND c.column_name = cc.column_name
                WHERE c.table_name = :table_name
                ORDER BY column_id
            """, table_name=table_name)
            
            columns = cursor.fetchall()
            
            schema_data = {
                'table_name': table_name,
                'columns': []
            }
            
            for col in columns:
                schema_data['columns'].append({
                    'column_name': col[0],
                    'data_type': col[1],
                    'is_nullable': col[2] == 'Y',
                    'column_default': col[3],
                    'column_comment': col[4]
                })
            
            return schema_data
        except Exception as e:
            print(f"Error getting Oracle table schema: {e}")
            return {}
    
    async def execute_query(self, query: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """Execute Oracle query and return results"""
        try:
            if not self.connection:
                dsn = cx_Oracle.makedsn(
                    self.config['host'],
                    self.config.get('port', 1521),
                    service_name=self.config['database_name']
                )
                self.connection = cx_Oracle.connect(
                    self.config['username'],
                    self.config['password'],
                    dsn
                )
            
            cursor = self.connection.cursor()
            cursor.execute(query, params or ())
            columns = [desc[0] for desc in cursor.description]
            results = cursor.fetchall()
            formatted = [dict(zip(columns, row)) for row in results]
            return formatted
        except Exception as e:
            _reraise_query_error(e, "Oracle")

    async def execute_non_query(self, query: str, params: Optional[tuple] = None) -> int:
        raise NotImplementedError("Non-query execution not implemented for Oracle connectors yet.")


class BigQueryConnector(BaseSQLConnector):
    """Google BigQuery connector"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.client = None
    
    def _test_connection_sync_bq(self) -> None:
        if self.config.get('service_account_key'):
            credentials = service_account.Credentials.from_service_account_info(
                json.loads(self.config['service_account_key'])
            )
            client = bigquery.Client(credentials=credentials)
        else:
            client = bigquery.Client()
        client.query("SELECT 1 as test").result()

    async def test_connection(self) -> ConnectionTestResult:
        """Test BigQuery connection"""
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._test_connection_sync_bq),
                timeout=SQL_TEST_TIMEOUT_SECONDS,
            )
            return ConnectionTestResult(ok=True, message="Connection successful")
        except Exception as e:
            print(f"BigQuery connection test failed: {e}")
            return _format_connection_error(e, "bigquery", self.config)
    
    async def get_tables(self) -> List[str]:
        """Get list of BigQuery tables"""
        try:
            if not self.client:
                if self.config.get('service_account_key'):
                    credentials = service_account.Credentials.from_service_account_info(
                        json.loads(self.config['service_account_key'])
                    )
                    self.client = bigquery.Client(credentials=credentials)
                else:
                    self.client = bigquery.Client()
            
            dataset_id = self.config['database_name']  # BigQuery uses dataset_id
            dataset_ref = self.client.dataset(dataset_id)
            tables = list(self.client.list_tables(dataset_ref))
            return [table.table_id for table in tables]
        except Exception as e:
            print(f"Error getting BigQuery tables: {e}")
            _raise_connection_error(e, "bigquery", self.config)
    
    async def get_table_schema(self, table_name: str) -> Dict[str, Any]:
        """Get BigQuery table schema with column details"""
        try:
            if not self.client:
                if self.config.get('service_account_key'):
                    credentials = service_account.Credentials.from_service_account_info(
                        json.loads(self.config['service_account_key'])
                    )
                    self.client = bigquery.Client(credentials=credentials)
                else:
                    self.client = bigquery.Client()
            
            dataset_id = self.config['database_name']
            table_ref = self.client.dataset(dataset_id).table(table_name)
            table = self.client.get_table(table_ref)
            
            schema_data = {
                'table_name': table_name,
                'columns': []
            }
            
            for field in table.schema:
                schema_data['columns'].append({
                    'column_name': field.name,
                    'data_type': field.field_type,
                    'is_nullable': field.mode != 'REQUIRED',
                    'column_default': None,
                    'column_comment': field.description,
                    'is_primary_key': False  # BigQuery doesn't have primary keys
                })
            
            return schema_data
        except Exception as e:
            print(f"Error getting BigQuery table schema: {e}")
            return {}
    
    async def execute_query(self, query: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """Execute BigQuery query and return results"""
        try:
            if not self.client:
                if self.config.get('service_account_key'):
                    credentials = service_account.Credentials.from_service_account_info(
                        json.loads(self.config['service_account_key'])
                    )
                    self.client = bigquery.Client(credentials=credentials)
                else:
                    self.client = bigquery.Client()
            
            query_job = self.client.query(query)
            results = query_job.result()
            return [dict(row) for row in results]
        except Exception as e:
            _reraise_query_error(e, "BigQuery")

    async def execute_non_query(self, query: str, params: Optional[tuple] = None) -> int:
        raise NotImplementedError("Non-query execution not implemented for BigQuery connectors yet.")


def parse_connector_additional_config(raw: Any) -> Dict[str, Any]:
    """Normalize additional_config from DB (dict or JSON string)."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def snowflake_qualify_table(config: Dict[str, Any], table_name: str) -> str:
    """Fully qualified Snowflake table: DATABASE.SCHEMA.TABLE."""
    table = (table_name or "").strip()
    if not table or table.count(".") >= 2:
        return table
    db = (config.get("database_name") or "").strip()
    schema = _snowflake_schema_name(config)
    if table.count(".") == 1:
        if db and not table.upper().startswith(db.upper() + "."):
            return f"{db}.{table}"
        return table
    if db and schema:
        return f"{db}.{schema}.{table}"
    if schema:
        return f"{schema}.{table}"
    return table


def qualify_snowflake_sql(sql: str, table_names: List[str], config: Dict[str, Any]) -> str:
    """Replace unqualified table names with database.schema.table in generated SQL."""
    if not sql or not table_names:
        return sql
    out = sql
    for table in sorted(set(table_names), key=len, reverse=True):
        fqn = snowflake_qualify_table(config, table)
        if not fqn or fqn == table:
            continue
        for kw in ("FROM", "JOIN", "INTO"):
            pattern = (
                rf"(\b{kw}\s+){re.escape(table)}"
                rf"(?=\s+AS\b|\s+[A-Za-z_][\w]*\s+(?:ON|WHERE|JOIN|,)|\s*,|\s+WHERE\b|\s+ON\b|\s+LIMIT\b|\s+ORDER\b|\s+GROUP\b|\s*$)"
            )
            out = re.sub(pattern, rf"\1{fqn}", out, flags=re.IGNORECASE)
    return out


def split_sql_statements(sql_text: str) -> List[str]:
    """Split SQL text into executable statements (simple quote-aware parser)."""
    if not sql_text:
        return []
    statements: List[str] = []
    buf: List[str] = []
    in_single = False
    in_double = False
    prev = ""
    for ch in sql_text:
        if ch == "'" and not in_double and prev != "\\":
            in_single = not in_single
        elif ch == '"' and not in_single and prev != "\\":
            in_double = not in_double
        if ch == ";" and not in_single and not in_double:
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
        else:
            buf.append(ch)
        prev = ch
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _snowflake_additional_config(config: Dict[str, Any]) -> Dict[str, Any]:
    raw = config.get("additional_config") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    return raw if isinstance(raw, dict) else {}


def _snowflake_account(config: Dict[str, Any]) -> str:
    extra = _snowflake_additional_config(config)
    account = (config.get("host") or extra.get("account") or "").strip()
    if account.startswith("https://"):
        account = account.replace("https://", "").split(".snowflakecomputing.com")[0]
    return account


def _snowflake_schema_name(config: Dict[str, Any]) -> str:
    extra = _snowflake_additional_config(config)
    return (extra.get("schema") or config.get("schema") or "PUBLIC").strip()


def _snowflake_private_key_bytes(config: Dict[str, Any]) -> Optional[bytes]:
    extra = _snowflake_additional_config(config)
    pem = (extra.get("private_key_pem") or config.get("private_key_pem") or "").strip()
    if not pem:
        return None
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization

    passphrase = (extra.get("private_key_passphrase") or config.get("private_key_passphrase") or "").strip()
    pwd = passphrase.encode() if passphrase else None
    p_key = serialization.load_pem_private_key(pem.encode(), password=pwd, backend=default_backend())
    return p_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def snowflake_connect_params(config: Dict[str, Any]) -> Dict[str, Any]:
    """Build kwargs for snowflake.connector.connect (also used in tests)."""
    extra = _snowflake_additional_config(config)
    account = _snowflake_account(config)
    if not account:
        raise ValueError("Snowflake account identifier is required (host / account field).")

    params: Dict[str, Any] = {
        "account": account,
        "user": (config.get("username") or "").strip(),
    }
    database = (config.get("database_name") or extra.get("database") or "").strip()
    if database:
        params["database"] = database
    schema = _snowflake_schema_name(config)
    if schema:
        params["schema"] = schema
    role = (extra.get("role") or config.get("role") or "").strip()
    if role:
        params["role"] = role
    warehouse = (extra.get("warehouse") or config.get("warehouse") or "").strip()
    if warehouse:
        params["warehouse"] = warehouse

    private_key = _snowflake_private_key_bytes(config)
    password = str(config.get("password") or "").strip()
    if private_key:
        params["private_key"] = private_key
    elif password:
        params["password"] = password

    return params


class SnowflakeConnector(BaseSQLConnector):
    """Snowflake data warehouse connector."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._conn = None

    def _connect_sync(self):
        import snowflake.connector

        if self._conn is not None:
            return self._conn
        self._conn = snowflake.connector.connect(**snowflake_connect_params(self.config))
        return self._conn

    def _close_sync(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        self.connection = None

    async def close_connection(self):
        await asyncio.to_thread(self._close_sync)

    def _test_connection_sync(self) -> None:
        conn = self._connect_sync()
        cur = conn.cursor()
        try:
            cur.execute("SELECT 1")
            cur.fetchone()
        finally:
            cur.close()

    async def test_connection(self) -> ConnectionTestResult:
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._test_connection_sync),
                timeout=SQL_TEST_TIMEOUT_SECONDS,
            )
            return ConnectionTestResult(ok=True, message="Connection successful")
        except Exception as e:
            print(f"Snowflake connection test failed: {e}")
            return _format_connection_error(e, "snowflake", self.config)
        finally:
            await self.close_connection()

    def _get_tables_sync(self) -> List[str]:
        from snowflake.connector import DictCursor

        conn = self._connect_sync()
        schema = _snowflake_schema_name(self.config).upper()
        database = (self.config.get("database_name") or "").strip().upper()
        cur = conn.cursor(DictCursor)
        try:
            if database:
                cur.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_catalog = %s
                      AND table_schema = %s
                      AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                    """,
                    (database, schema),
                )
            else:
                cur.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = %s
                      AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                    """,
                    (schema,),
                )
            rows = cur.fetchall()
            return [str(r["TABLE_NAME"] if "TABLE_NAME" in r else r["table_name"]) for r in rows]
        finally:
            cur.close()

    async def get_tables(self) -> List[str]:
        try:
            tables = await asyncio.to_thread(self._get_tables_sync)
            self.connection = self._conn
            return tables
        except Exception as e:
            print(f"Error getting Snowflake tables: {e}")
            _raise_connection_error(e, "snowflake", self.config)

    def _get_table_schema_sync(self, table_name: str) -> Dict[str, Any]:
        from snowflake.connector import DictCursor

        conn = self._connect_sync()
        schema = _snowflake_schema_name(self.config).upper()
        database = (self.config.get("database_name") or "").strip().upper()
        cur = conn.cursor(DictCursor)
        try:
            if database:
                cur.execute(
                    """
                    SELECT column_name, data_type, is_nullable, column_default, comment
                    FROM information_schema.columns
                    WHERE table_catalog = %s
                      AND table_schema = %s
                      AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    (database, schema, table_name.upper()),
                )
            else:
                cur.execute(
                    """
                    SELECT column_name, data_type, is_nullable, column_default, comment
                    FROM information_schema.columns
                    WHERE table_schema = %s
                      AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    (schema, table_name.upper()),
                )
            rows = cur.fetchall()

            def _col(row: Dict[str, Any], *keys: str) -> Any:
                for key in keys:
                    if key in row:
                        return row[key]
                    upper = key.upper()
                    if upper in row:
                        return row[upper]
                return None

            schema_data: Dict[str, Any] = {"table_name": table_name, "columns": []}
            for row in rows:
                nullable = str(_col(row, "is_nullable", "IS_NULLABLE") or "").upper()
                schema_data["columns"].append(
                    {
                        "column_name": _col(row, "column_name", "COLUMN_NAME"),
                        "data_type": _col(row, "data_type", "DATA_TYPE"),
                        "is_nullable": nullable in ("YES", "Y", "TRUE"),
                        "column_default": _col(row, "column_default", "COLUMN_DEFAULT"),
                        "column_comment": _col(row, "comment", "COMMENT"),
                        "is_primary_key": False,
                    }
                )
            return schema_data
        finally:
            cur.close()

    async def get_table_schema(self, table_name: str) -> Dict[str, Any]:
        try:
            return await asyncio.to_thread(self._get_table_schema_sync, table_name)
        except Exception as e:
            print(f"Error getting Snowflake table schema: {e}")
            return {}

    def _apply_session_context_sync(self, cur, *, ensure_schema: bool = False) -> None:
        db = (self.config.get("database_name") or "").strip()
        schema = _snowflake_schema_name(self.config)
        if db:
            cur.execute(f"USE DATABASE {db}")
        if schema:
            if ensure_schema:
                # DDL execution should work even when target schema is not created yet.
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            cur.execute(f"USE SCHEMA {schema}")

    def _execute_query_sync(self, query: str, params: Optional[tuple]) -> List[Dict[str, Any]]:
        from snowflake.connector import DictCursor

        conn = self._connect_sync()
        cur = conn.cursor(DictCursor)
        try:
            self._apply_session_context_sync(cur, ensure_schema=False)
            cur.execute(query, params or ())
            rows = cur.fetchall()
            return [dict(row) for row in rows]
        finally:
            cur.close()

    def _execute_non_query_sync(self, query: str, params: Optional[tuple] = None) -> int:
        conn = self._connect_sync()
        cur = conn.cursor()
        try:
            self._apply_session_context_sync(cur, ensure_schema=True)
            affected_total = 0
            statements = split_sql_statements(query)
            if not statements:
                return 0
            if params and len(statements) == 1:
                cur.execute(statements[0], params)
                return int(cur.rowcount or 0)
            for stmt in statements:
                cur.execute(stmt)
                affected_total += int(cur.rowcount or 0)
            return affected_total
        finally:
            cur.close()

    async def execute_query(self, query: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
        try:
            return await asyncio.to_thread(self._execute_query_sync, query, params)
        except Exception as e:
            print(f"Error executing Snowflake query: {e}")
            result = _format_connection_error(e, "snowflake", self.config)
            raise SQLConnectorOperationError(result.message, result.error_code) from e

    async def execute_non_query(self, query: str, params: Optional[tuple] = None) -> int:
        try:
            return await asyncio.to_thread(self._execute_non_query_sync, query, params)
        except Exception as e:
            print(f"Error executing Snowflake non-query: {e}")
            result = _format_connection_error(e, "snowflake", self.config)
            raise SQLConnectorOperationError(result.message, result.error_code) from e


class SQLConnectorFactory:
    """Factory class to create appropriate SQL connector"""
    
    @staticmethod
    def create_connector(connector_type: str, config: Dict[str, Any]) -> BaseSQLConnector:
        """Create appropriate SQL connector based on type"""
        t = (connector_type or "").strip().lower()
        if t == 'mysql':
            return MySQLConnector(config)
        elif t == 'postgresql':
            return PostgreSQLConnector(config)
        elif t in ('mssql', 'sqlserver'):
            return MSSQLConnector(config)
        elif t == 'oracle':
            return OracleConnector(config)
        elif t == 'bigquery':
            return BigQueryConnector(config)
        elif t == 'snowflake':
            return SnowflakeConnector(config)
        else:
            raise ValueError(f"Unsupported connector type: {connector_type}")


class SQLConnectorService:
    """Service class for managing SQL connectors"""
    
    def __init__(self):
        # Use consistent encryption key from environment variable
        self.encryption_key = os.getenv('SQL_CONNECTOR_ENCRYPTION_KEY')
        if isinstance(self.encryption_key, str):
            self.encryption_key = self.encryption_key.encode()
        self.cipher = Fernet(self.encryption_key)
    
    def encrypt_password(self, password: str) -> str:
        """Encrypt password for storage"""
        return self.cipher.encrypt(password.encode()).decode()
    
    def decrypt_password(self, encrypted_password: str) -> str:
        """Decrypt password for use"""
        return self.cipher.decrypt(encrypted_password.encode()).decode()
    
    async def get_connector_by_id(self, connector_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Get connector data by ID"""
        try:
            from db.connection import database
            
            # Get connector details
            connector = await database.fetch_one(
                """
                SELECT * FROM sql_connectors 
                WHERE id = :connector_id AND user_id = :user_id AND is_active = true
                """,
                {"connector_id": connector_id, "user_id": user_id}
            )
            
            if not connector:
                return None
            
            # Get categorized tables
            tables = await database.fetch_all(
                """
                SELECT table_name, schema_data, ai_categorization FROM sql_extracted_schemas 
                WHERE connector_id = :connector_id AND user_id = :user_id
                ORDER BY extracted_at DESC
                """,
                {"connector_id": connector_id, "user_id": user_id}
            )
            
            return {
                "connector": connector,
                "tables": tables
            }
            
        except Exception as e:
            print(f"Error getting connector by ID: {e}")
            return None
    
    async def test_connection(
        self, connector_type: str, config: Dict[str, Any]
    ) -> ConnectionTestResult:
        """Test SQL connection with connect and overall timeouts."""
        connector = None
        try:
            connector = SQLConnectorFactory.create_connector(connector_type, config)

            async def _run() -> ConnectionTestResult:
                return await connector.test_connection()

            result = await asyncio.wait_for(_run(), timeout=SQL_TEST_TIMEOUT_SECONDS)
            return result
        except asyncio.TimeoutError:
            host = (config.get("host") or "unknown").strip()
            return ConnectionTestResult(
                ok=False,
                message=(
                    f"Connection timed out after {SQL_TEST_TIMEOUT_SECONDS}s while testing "
                    f"{connector_type} at {host}."
                ),
                error_code="TIMEOUT",
            )
        except Exception as e:
            print(f"Connection test failed: {e}")
            return _format_connection_error(e, connector_type, config)
        finally:
            if connector:
                try:
                    await connector.close_connection()
                except Exception:
                    pass
    
    def prepare_connector_config(self, connector: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare connector configuration from database record"""
        config = {
            'connector_type': connector['connector_type'],
            'host': connector.get('host'),
            'port': connector.get('port'),
            'database_name': connector.get('database_name'),
            'username': connector.get('username'),
            'ssl_enabled': connector.get('ssl_enabled', False),
            'additional_config': parse_connector_additional_config(connector.get('additional_config')),
        }
        
        # Decrypt password if available
        if connector.get('password_encrypted'):
            try:
                config['password'] = self.decrypt_password(connector['password_encrypted'])
            except Exception as e:
                print(f"Warning: Could not decrypt password, using as-is: {e}")
                config['password'] = connector['password_encrypted']
        
        return config
    
    def create_connector_instance(self, connector_type: str, config: Dict[str, Any]):
        """Create a connector instance"""
        return SQLConnectorFactory.create_connector(connector_type, config)
