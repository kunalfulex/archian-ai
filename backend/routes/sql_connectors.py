"""SQL connector CRUD, test, tables, and analysis-status endpoints."""

import asyncio
import json
import traceback
import uuid
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from db.connection import database
from models.sql_connector_models import normalize_sql_connector_payload, SQLConnectorCreate
from routes.auth import get_current_user
from services.sql_background_processing_service import start_database_analysis
from services.sql_connector_service import SQLConnectorService
from utils.task_limiter import create_limited_task
from utils.user_email import get_user_email

router = APIRouter()


# SQL Connector API Endpoints
sql_connector_service = SQLConnectorService()


@router.post("/api/sql-connectors/")
async def create_sql_connector(
    connector_data: dict,
    current_user: UUID = Depends(get_current_user)
):
    """Create a new SQL connector"""
    try:
        if not connector_data.get("connector_type"):
            return JSONResponse(
                content={"status": "error", "message": "connector_type is required"},
                status_code=400,
            )
        config = SQLConnectorCreate(**normalize_sql_connector_payload(connector_data)).model_dump()
        test_result = await sql_connector_service.test_connection(
            config["connector_type"],
            config,
        )
        
        if not test_result.ok:
            return JSONResponse(
                content={
                    "status": "error",
                    "message": test_result.message,
                    "error_code": test_result.error_code,
                },
                status_code=400
            )
        
        # Encrypt password if provided
        if config.get('password'):
            config['password_encrypted'] = sql_connector_service.encrypt_password(config['password'])
            del config['password']
        
        # Insert connector
        connector_id = str(uuid.uuid4())
        await database.execute(
            """
            INSERT INTO sql_connectors 
            (id, user_id, connector_type, name, host, port, database_name, username, 
             password_encrypted, connection_string, ssl_enabled, additional_config, is_active, created_at, updated_at)
            VALUES (:id, :user_id, :connector_type, :name, :host, :port, :database_name, :username,
                    :password_encrypted, :connection_string, :ssl_enabled, :additional_config, :is_active, NOW(), NOW())
            """,
            {
                "id": connector_id,
                "user_id": str(current_user),
                "connector_type": config['connector_type'],
                "name": config['name'],
                "host": config.get('host'),
                "port": config.get('port'),
                "database_name": config.get('database_name'),
                "username": config.get('username'),
                "password_encrypted": config.get('password_encrypted'),
                "connection_string": config.get('connection_string'),
                "ssl_enabled": config.get('ssl_enabled', False),
                "additional_config": json.dumps(config.get('additional_config', {})),
                "is_active": True
            }
        )
        
        # Start background database analysis with concurrency limiting
        user_email = await get_user_email(str(current_user))
        if user_email:
            await create_limited_task(start_database_analysis(connector_id, str(current_user), user_email))
        
        return JSONResponse(
            content={
                "status": "success",
                "message": "SQL connector created successfully. Database analysis started in background.",
                "connector_id": connector_id
            }
        )
        
    except Exception as e:
        print(f"Error creating SQL connector: {e}")
        from pydantic import ValidationError

        if isinstance(e, ValidationError):
            return JSONResponse(
                content={
                    "status": "error",
                    "message": "; ".join(err["msg"] for err in e.errors()),
                },
                status_code=400,
            )
        return JSONResponse(
            content={
                "status": "error",
                "message": f"Failed to create SQL connector: {str(e)}"
            },
            status_code=500
        )


@router.get("/api/sql-connectors/")
async def list_sql_connectors(current_user: UUID = Depends(get_current_user)):
    """List all SQL connectors for the user"""
    try:
        connectors = await database.fetch_all(
            """
            SELECT id, connector_type, name, host, port, database_name, username, 
                   ssl_enabled, additional_config, is_active, created_at, updated_at
            FROM sql_connectors 
            WHERE user_id = :user_id
            ORDER BY created_at DESC
            """,
            {"user_id": str(current_user)}
        )
        
        # Convert UUIDs and datetimes to strings for JSON serialization
        connectors_list = []
        for connector in connectors:
            connector_dict = dict(connector)
            # Convert UUID fields to strings
            if 'id' in connector_dict and connector_dict['id']:
                connector_dict['id'] = str(connector_dict['id'])
            # Convert datetime fields to strings
            if 'created_at' in connector_dict and connector_dict['created_at']:
                connector_dict['created_at'] = connector_dict['created_at'].isoformat()
            if 'updated_at' in connector_dict and connector_dict['updated_at']:
                connector_dict['updated_at'] = connector_dict['updated_at'].isoformat()
            connectors_list.append(connector_dict)
        
        return JSONResponse(
            content={
                "status": "success",
                "connectors": connectors_list
            }
        )
        
    except Exception as e:
        print(f"Error listing SQL connectors: {e}")
        return JSONResponse(
            content={
                "status": "error",
                "message": "Failed to list SQL connectors"
            },
            status_code=500
        )


@router.put("/api/sql-connectors/{connector_id}")
async def update_sql_connector(
    connector_id: str,
    connector_data: dict,
    current_user: UUID = Depends(get_current_user)
):
    """Update an existing SQL connector"""
    try:
        # Validate required fields
        if not connector_data.get('connector_type'):
            return JSONResponse(
                content={
                    "status": "error",
                    "message": "connector_type is required"
                },
                status_code=400
            )
        
        config = normalize_sql_connector_payload(connector_data)
        if config.get("ssl_enabled") is None:
            config["ssl_enabled"] = False
        else:
            config["ssl_enabled"] = bool(config["ssl_enabled"])

        # Test connection first
        test_result = await sql_connector_service.test_connection(
            config["connector_type"],
            config,
        )

        if not test_result.ok:
            return JSONResponse(
                content={
                    "status": "error",
                    "message": test_result.message,
                    "error_code": test_result.error_code,
                },
                status_code=400,
            )

        # Encrypt password if provided
        if config.get("password"):
            config["password_encrypted"] = sql_connector_service.encrypt_password(config["password"])
            del config["password"]

        # Clear existing schemas for this connector
        await database.execute(
            """
            DELETE FROM sql_extracted_schemas 
            WHERE connector_id = :connector_id AND user_id = :user_id
            """,
            {
                "connector_id": connector_id,
                "user_id": str(current_user)
            }
        )
        
        # Update connector
        await database.execute(
            """
            UPDATE sql_connectors 
            SET connector_type = :connector_type, name = :name, host = :host, port = :port, 
                database_name = :database_name, username = :username, 
                password_encrypted = COALESCE(:password_encrypted, password_encrypted),
                connection_string = :connection_string, ssl_enabled = :ssl_enabled, 
                additional_config = :additional_config, updated_at = NOW()
            WHERE id = :connector_id AND user_id = :user_id
            """,
            {
                "connector_id": connector_id,
                "user_id": str(current_user),
                "connector_type": config['connector_type'],
                "name": config['name'],
                "host": config.get('host'),
                "port": config.get('port'),
                "database_name": config.get('database_name'),
                "username": config.get('username'),
                "password_encrypted": config.get('password_encrypted'),
                "connection_string": config.get('connection_string'),
                "ssl_enabled": config.get('ssl_enabled', False),
                "additional_config": json.dumps(config.get('additional_config', {}))
            }
        )
        
        # Start background database analysis (same as create)
        user_email = await get_user_email(str(current_user))
        if user_email:
            asyncio.create_task(start_database_analysis(connector_id, str(current_user), user_email))
        
        return JSONResponse(
            content={
                "status": "success",
                "message": "SQL connector updated successfully. Database analysis started in background."
            }
        )
        
    except Exception as e:
        print(f"Error updating SQL connector: {e}")
        return JSONResponse(
            content={
                "status": "error",
                "message": f"Failed to update SQL connector: {str(e)}"
            },
            status_code=500
        )


@router.delete("/api/sql-connectors/{connector_id}")
async def delete_sql_connector(
    connector_id: str,
    current_user: UUID = Depends(get_current_user)
):
    """Delete a SQL connector and all associated data"""
    try:
        # First, verify the connector exists and belongs to the user
        connector_result = await database.fetch_one(
            """
            SELECT id, name FROM sql_connectors 
            WHERE id = :connector_id AND user_id = :user_id
            """,
            {"connector_id": connector_id, "user_id": str(current_user)}
        )
        
        if not connector_result:
            return JSONResponse(
                content={
                    "status": "error",
                    "message": "Connector not found or access denied"
                },
                status_code=404
            )
        
        # Delete in the correct order to respect foreign key constraints
        # 1. Delete extracted schemas first (they reference the connector)
        await database.execute(
            """
            DELETE FROM sql_extracted_schemas 
            WHERE connector_id = :connector_id AND user_id = :user_id
            """,
            {"connector_id": connector_id, "user_id": str(current_user)}
        )
        
        # 2. Delete the SQL connector
        await database.execute(
            """
            DELETE FROM sql_connectors 
            WHERE id = :connector_id AND user_id = :user_id
            """,
            {"connector_id": connector_id, "user_id": str(current_user)}
        )
        
        return JSONResponse(
            content={
                "status": "success",
                "message": f"SQL connector '{connector_result['name']}' and all associated data deleted successfully"
            }
        )
        
    except Exception as e:
        print(f"Error deleting SQL connector: {e}")
        return JSONResponse(
            content={
                "status": "error",
                "message": f"Failed to delete SQL connector: {str(e)}"
            },
            status_code=500
        )


@router.post("/api/sql-connectors/test-connection")
async def test_sql_connection(
    connector_data: dict,
    current_user: UUID = Depends(get_current_user)
):
    """Test SQL connection before saving"""
    try:
        # Validate required fields
        if not connector_data.get('connector_type'):
            return JSONResponse(
                content={
                    "status": "error",
                    "message": "connector_type is required"
                },
                status_code=400
            )
        
        # Clean up the data - convert None to empty strings and ensure proper types
        cleaned_data = {}
        for key, value in connector_data.items():
            if value is None:
                cleaned_data[key] = ""
            elif key == 'port' and value:
                try:
                    cleaned_data[key] = int(value)
                except (ValueError, TypeError):
                    cleaned_data[key] = value
            elif key == 'ssl_enabled':
                cleaned_data[key] = bool(value)
            else:
                cleaned_data[key] = value
        
        # Test connection
        test_result = await sql_connector_service.test_connection(
            cleaned_data['connector_type'], 
            cleaned_data
        )
        
        return JSONResponse(
            content={
                "status": "success" if test_result.ok else "error",
                "message": test_result.message,
                "is_valid": test_result.ok,
                "error_code": test_result.error_code,
            }
        )
        
    except Exception as e:
        print(f"Error testing SQL connection: {e}")
        return JSONResponse(
            content={
                "status": "error",
                "message": f"Connection test failed: {str(e)}"
            },
            status_code=500
        )


@router.post("/api/sql-connectors/{connector_id}/test")
async def test_sql_connector(
    connector_id: str,
    current_user: UUID = Depends(get_current_user)
):
    """Test SQL connector connection"""
    try:
        # Get connector details
        connector = await database.fetch_one(
            """
            SELECT * FROM sql_connectors 
            WHERE id = :connector_id AND user_id = :user_id
            """,
            {"connector_id": connector_id, "user_id": str(current_user)}
        )
        
        if not connector:
            return JSONResponse(
                content={
                    "status": "error",
                    "message": "Connector not found"
                },
                status_code=404
            )
        
        # Prepare config
        config = {
            'host': connector['host'],
            'port': connector['port'],
            'database_name': connector['database_name'],
            'username': connector['username'],
            'ssl_enabled': connector['ssl_enabled'],
            'additional_config': json.loads(connector['additional_config']) if connector['additional_config'] else {}
        }
        
        # Decrypt password
        if connector['password_encrypted']:
            config['password'] = sql_connector_service.decrypt_password(connector['password_encrypted'])
        
        # Test connection
        test_result = await sql_connector_service.test_connection(connector['connector_type'], config)
        
        return JSONResponse(
            content={
                "status": "success" if test_result.ok else "error",
                "message": test_result.message,
                "is_valid": test_result.ok,
                "error_code": test_result.error_code,
            }
        )
        
    except Exception as e:
        print(f"Error testing SQL connector: {e}")
        return JSONResponse(
            content={
                "status": "error",
                "message": f"Connection test failed: {str(e)}"
            },
            status_code=500
        )


@router.get("/api/sql-connectors/{connector_id}/tables")
async def get_sql_tables(
    connector_id: str,
    current_user: UUID = Depends(get_current_user)
):
    """List tables from sql_extracted_schemas (populated by schema sync / analysis)."""
    from handlers.sql_handler import list_sql_connector_tables_from_store

    try:
        connector = await database.fetch_one(
            """
            SELECT id FROM sql_connectors 
            WHERE id = :connector_id AND user_id = :user_id
            """,
            {"connector_id": connector_id, "user_id": str(current_user)}
        )
        
        if not connector:
            return JSONResponse(
                content={
                    "status": "error",
                    "message": "Connector not found"
                },
                status_code=404
            )
        
        tables = await list_sql_connector_tables_from_store(str(current_user), connector_id)
        
        return JSONResponse(
            content={
                "status": "success",
                "tables": tables,
                "source": "sql_extracted_schemas",
            }
        )
        
    except Exception as e:
        print(f"Error getting SQL tables: {e}")
        return JSONResponse(
            content={
                "status": "error",
                "message": f"Failed to get tables: {str(e)}"
            },
            status_code=500
        )


@router.get("/api/sql-connectors/{connector_id}/tables/{table_name}/columns")
async def get_sql_table_columns(
    connector_id: str,
    table_name: str,
    current_user: UUID = Depends(get_current_user),
):
    """Column names and types from sql_extracted_schemas.schema_data (schema sync)."""
    from handlers.sql_handler import get_sql_table_fields_from_store

    try:
        connector = await database.fetch_one(
            """
            SELECT id FROM sql_connectors
            WHERE id = :connector_id AND user_id = :user_id
            """,
            {"connector_id": connector_id, "user_id": str(current_user)},
        )
        if not connector:
            return JSONResponse(
                content={"status": "error", "message": "Connector not found"},
                status_code=404,
            )

        fields = await get_sql_table_fields_from_store(
            str(current_user), connector_id, table_name
        )
        if fields is None:
            return JSONResponse(
                content={
                    "status": "error",
                    "message": (
                        f"No stored schema for table '{table_name}'. "
                        "Run schema sync on this connection to refresh."
                    ),
                },
                status_code=404,
            )

        return JSONResponse(
            content={
                "status": "success",
                "table_name": table_name,
                "fields": fields,
                "source": "sql_extracted_schemas",
            }
        )
    except Exception as e:
        print(f"Error getting SQL table columns: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse(
            content={
                "status": "error",
                "message": f"Failed to get columns: {str(e)}",
            },
            status_code=500,
        )


@router.get("/api/sql-connectors/{connector_id}/analysis-status")
async def get_analysis_status(
    connector_id: str,
    current_user: UUID = Depends(get_current_user)
):
    """Get database analysis status"""
    try:
        connector_row = await database.fetch_one(
            """
            SELECT analysis_completed, analysis_error
            FROM sql_connectors
            WHERE id = :connector_id AND user_id = :user_id
            """,
            {"connector_id": connector_id, "user_id": str(current_user)},
        )
        row = dict(connector_row) if connector_row else {}
        analysis_error = (row.get("analysis_error") or "").strip()

        from services.labeling.readiness_service import get_readiness_state

        readiness = await get_readiness_state(
            user_id=str(current_user),
            connector_type="sql",
            connector_instance_id=str(connector_id),
            connector_ref={"connector_id": str(connector_id)},
        )

        # Labeled entities from connector_entity_semantics (semantics-first)
        labeled_result = await database.fetch_one(
            """
            SELECT COUNT(*) FILTER (WHERE labeling_status = 'ready') AS categorized_tables
            FROM connector_entity_semantics
            WHERE connector_type = 'sql'
              AND connector_instance_id = :connector_id
              AND user_id = :user_id
            """,
            {"connector_id": connector_id, "user_id": str(current_user)},
        )
        total_tables = readiness["entity_labeling_stats"].get("total_entities") or 0
        if not total_tables:
            schema_count = await database.fetch_one(
                """
                SELECT COUNT(*) AS total_tables
                FROM sql_extracted_schemas
                WHERE connector_id = :connector_id AND user_id = :user_id
                """,
                {"connector_id": connector_id, "user_id": str(current_user)},
            )
            total_tables = schema_count["total_tables"] if schema_count else 0
        categorized_tables = labeled_result["categorized_tables"] if labeled_result else 0
        
        # Percentage is for UI only; completion is driven by explicit flag
        categorization_percentage = (categorized_tables / total_tables * 100) if total_tables > 0 else 0
        
        # Agent ready when metadata + labeling + profiling complete
        sync_complete = readiness["is_ready"]
        
        payload = {
            "status": "success",
            "sync_complete": sync_complete,
            "phase": readiness["phase"],
            "display_name": readiness["display_name"],
            "entity_labeling_stats": readiness["entity_labeling_stats"],
            "total_tables": total_tables,
            "categorized_tables": categorized_tables,
            "categorization_percentage": round(categorization_percentage, 2),
        }
        if analysis_error:
            payload["analysis_error"] = analysis_error
        return JSONResponse(content=payload)
        
    except Exception as e:
        print(f"Error getting analysis status: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse(
            content={
                "status": "error",
                "message": "Failed to get analysis status"
            },
            status_code=500
        )
