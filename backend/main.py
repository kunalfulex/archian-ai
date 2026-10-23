import uuid
import boto3
import asyncio
from fastapi import FastAPI, HTTPException, File, UploadFile, Request, Form, Depends, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
import json
import logging
import traceback
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os
import tempfile
import pandas as pd
from db.connection import database, export_database
from db.extracted_schema import router as extracted_schema_router, get_flow_details
import services.architect_agent as architect
from fastapi.responses import RedirectResponse

# Import handler functions directly for clean architecture
from handlers.salesforce_handler import (
    call_salesforce_architect_agent,
    detect_salesforce_org_from_prompt,
    determine_salesforce_object_and_fields,
    generate_salesforce_soql_query,
    start_salesforce_extraction_job,
)
from handlers.sql_handler import generate_sql_query_for_tables
from handlers.google_sheets_handler import process_google_sheets_extraction_background
from handlers.classification_handler import classify_source_destination
import secrets
import base64
import hashlib
import urllib.parse
import httpx
from datetime import datetime
from services.salesforce_migration_flow import migrate_data_to_salesforce
from services.background_migration_service import background_migration_service
from services.gsheets_background_migration_service import gsheets_background_migration_service
from services.database_migration_service import DatabaseMigrationService
from services.database_background_migration_service import database_background_migration_service
from services.migration_records_service import MigrationRecordsService
from services.flow_executions_service import FlowExecutionsService
from services.entitlements import get_entitlements
from routes.auth import router as auth_router
from routes.auth import get_current_user
from routes.learning import router as learning_router
from routes.llm_admin import router as llm_admin_router
from routes.learning_admin import router as learning_admin_router
from routes.mapping_destinations import router as mapping_destinations_router
from routes.features import router as features_router
from routes.chat import router as chat_router
from routes.extraction_results import router as extraction_results_router
from routes.google_sheets import router as google_sheets_router
from routes.profile import router as profile_router
from routes.migration_stats import router as migration_stats_router
from routes.migration_records import router as migration_records_router
from routes.exports import router as exports_router
from routes.admin import router as admin_router
from routes.salesforce_connection import router as salesforce_connection_router
from routes.meta import router as meta_router
from routes.contact import router as contact_router
from routes.flows import router as flows_router
from routes.migrate import router as migrate_router
from routes.sql_connectors import router as sql_connectors_router
from routes.salesforce_oauth import router as salesforce_oauth_router
from routes.salesforce_schema import router as salesforce_schema_router
from routes.architect import router as architect_router
from routes.jobs import router as jobs_router
from routes.flows_reuse import router as flows_reuse_router
from services.data_assessment.routes import router as assessment_router
from services.connector_metadata.routes import router as connector_metadata_router
from services.labeling.routes import router as labeling_router
from services.enterprise_graph.routes import router as enterprise_graph_router
from handlers.migration_context_routes import router as migration_context_router
from handlers.conversation_actions import router as conversation_actions_router
from services.sql_background_processing_service import start_database_analysis, start_sql_extraction_task, get_task_status
from services.chat_service import ChatService
from services.extraction_results_service import ExtractionResultsService
from services.incremental_sync_service import IncrementalSyncService, compute_query_fingerprint
from utils.redis_service import REDIS_SLIM_RESULT_THRESHOLD
from utils.batch_config import compute_migration_batch_size
from models.sql_connector_models import SQLConnectorCreate, normalize_sql_connector_payload
from utils.salesforceapimigration import get_valid_salesforce_token
from utils.salesforceapimigration import fetch_and_store_salesforce_objects
from uuid import UUID
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Tuple


app = FastAPI()

# Load environment variables from .env file (must be before using os.getenv())
load_dotenv()

from utils.cors_origins import (
    DEFAULT_BACKEND_URL,
    DEFAULT_FRONTEND_URL,
    get_allowed_origins,
    get_trusted_hosts,
    is_allowed_cors_origin,
    normalize_origin,
)

# App modules use logging.getLogger(__name__).info(...). Uvicorn only configures its own loggers;
# without a root StreamHandler, Python's logging lastResort emits WARNING+ only, so INFO is invisible.
_root_logger = logging.getLogger()
if not _root_logger.handlers:
    _lvl_name = (os.getenv("LOG_LEVEL") or "INFO").upper()
    _lvl = getattr(logging, _lvl_name, logging.INFO)
    logging.basicConfig(level=_lvl, format="%(levelname)s:%(name)s:%(message)s")

# Third-party noise control (keep warnings/errors, silence periodic INFO).
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("databases").setLevel(logging.WARNING)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("boto3").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("uvicorn").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("edison.planning").setLevel(
    logging.DEBUG
    if (os.getenv("PLANNER_DEBUG") or "").strip().lower() in ("1", "true", "yes", "on")
    else logging.WARNING
)

# Rate limiting configuration
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

# Initialize rate limiter
# Use environment variable or default to 100 requests per minute
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "100"))
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"


def _rate_limit_key(request):
    """Use user token when authenticated, else IP. Avoids shared limit for users behind same NAT."""
    auth = request.headers.get("Authorization") if hasattr(request, "headers") else None
    if auth and isinstance(auth, str) and auth.startswith("Bearer ") and len(auth) > 10:
        return f"user:{hash(auth) & 0x7FFFFFFF}"
    return get_remote_address(request)


if RATE_LIMIT_ENABLED:
    limiter = Limiter(key_func=_rate_limit_key, default_limits=[f"{RATE_LIMIT_PER_MINUTE}/minute"])
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    # Note: Print statement will appear at startup
else:
    # Note: Print statement will appear at startup
    limiter = None

# Redis-backed job tracking system for background extraction processing
# Falls back to in-memory if Redis is unavailable
from utils.job_stores import assessment_jobs, extraction_jobs, migration_jobs, sql_tasks
from utils.async_job_store import create_sync_progress_callback
from utils.user_email import get_user_email


app.include_router(extracted_schema_router)
app.include_router(auth_router)
app.include_router(learning_router)
app.include_router(mapping_destinations_router)
app.include_router(assessment_router)
app.include_router(connector_metadata_router)
app.include_router(labeling_router)
app.include_router(enterprise_graph_router)
app.include_router(llm_admin_router)
app.include_router(learning_admin_router)
app.include_router(migration_context_router)
app.include_router(conversation_actions_router)
app.include_router(features_router)
if get_entitlements().billing_enabled:
    from archian_cloud.billing_routes import router as billing_router

    app.include_router(billing_router)

# Add security headers middleware AFTER routes are included
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    # Add debugging for CORS and request issues
    origin = request.headers.get("origin")
    method = request.method
    path = request.url.path
    
    
    try:
        response = await call_next(request)
        
        # Add CORS headers explicitly
        if origin and is_allowed_cors_origin(origin):
            response.headers["Access-Control-Allow-Origin"] = normalize_origin(origin)
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = (
                "Authorization, Content-Type, X-CSRF-Token, X-Requested-With, Accept, Origin"
            )
        
        # Add security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        # Add Content Security Policy
        response.headers["Content-Security-Policy"] = CSP_HEADER
        
        return response
        
    except Exception as e:
        print(f"❌ Middleware Error: {e}")
        # Return a proper error response instead of letting it fail
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error"},
            headers={
                "Access-Control-Allow-Origin": normalize_origin(origin) if (origin and is_allowed_cors_origin(origin)) else "*",
                "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": (
                    "Authorization, Content-Type, X-CSRF-Token, X-Requested-With, Accept, Origin"
                ),
            }
        )

AUTH_URL = "https://login.salesforce.com/services/oauth2/authorize"
TOKEN_URL = "https://login.salesforce.com/services/oauth2/token"
CLIENT_ID = os.getenv("SALESFORCE_CLIENT_ID")


ALLOWED_ORIGINS = get_allowed_origins()

# CSP connect-src: self + configured app URLs + known third-party APIs (no product hosts baked in).
_CSP_VENDOR_CONNECT = (
    "https://api.openai.com https://api.postmarkapp.com https://api.stripe.com"
)


def get_csp_directives():
    """Build CSP from FRONTEND_URL / BACKEND_URL (local defaults when unset)."""
    frontend_url = normalize_origin(os.getenv("FRONTEND_URL", DEFAULT_FRONTEND_URL))
    backend_url = normalize_origin(os.getenv("BACKEND_URL", DEFAULT_BACKEND_URL))
    connect_parts = ["'self'", _CSP_VENDOR_CONNECT]
    if frontend_url:
        connect_parts.append(frontend_url)
    if backend_url:
        connect_parts.append(backend_url)
    if "localhost" in frontend_url or "127.0.0.1" in frontend_url:
        connect_parts.extend(["http://localhost:*", "https://localhost:*"])
    return {
        "default-src": "'self'",
        "script-src": "'self' 'unsafe-inline' 'unsafe-eval'",
        "style-src": "'self' 'unsafe-inline'",
        "img-src": "'self' data: https:",
        "font-src": "'self' data:",
        "connect-src": " ".join(connect_parts),
        "frame-ancestors": "'none'",
    }


CSP_DIRECTIVES = get_csp_directives()
CSP_HEADER = "; ".join([f"{key} {value}" for key, value in CSP_DIRECTIVES.items()])

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-CSRF-Token",
        "X-Requested-With",
        "Accept",
        "Origin",
        "Access-Control-Request-Method",
        "Access-Control-Request-Headers",
    ],
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=get_trusted_hosts(),
)

from middleware.csrf_middleware import CsrfMiddleware

app.add_middleware(CsrfMiddleware)


app.include_router(contact_router)


app.include_router(meta_router)


@app.on_event("startup")
async def startup():
    # Uvicorn may override logger levels after importing this module; re-apply noise control here.
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("databases").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    await database.connect()
    await export_database.connect()
    
    # Initialize Redis connection (will create connection on first use)
    from utils.redis_service import get_redis_client
    try:
        client = await get_redis_client()
        _ = client
    except Exception as e:
        _ = e
    
    # Display rate limiting status
    _ = RATE_LIMIT_ENABLED
    
    # Display task limiting status
    from utils.task_limiter import MAX_CONCURRENT_BACKGROUND_TASKS
    _ = MAX_CONCURRENT_BACKGROUND_TASKS
    
    # Start flow scheduler service
    from services.flow_scheduler_service import flow_scheduler_service
    flow_scheduler_service.start()

    from services.data_retention_scheduler_service import get_data_retention_scheduler
    get_data_retention_scheduler().start()

    from services.connector_metadata.scheduler_service import connector_metadata_scheduler_service
    connector_metadata_scheduler_service.start()

    from services.data_assessment.job_worker import recover_orphaned_assessment_jobs
    await recover_orphaned_assessment_jobs()

    from services.planning.rollout import log_planner_rollout_status
    log_planner_rollout_status()

    from agent.rollout import log_agent_runtime_rollout_status
    log_agent_runtime_rollout_status()

    from services.chat_service import ChatService

    await ChatService.ensure_schema()
    try:
        from services.extraction_results_service import ExtractionResultsService

        await ExtractionResultsService.ensure_schema()
    except AttributeError:
        pass

@app.on_event("shutdown")
async def shutdown():
    from utils.db_shutdown import mark_db_shutting_down

    mark_db_shutting_down()

    from services.enterprise_graph.job_worker import cancel_graph_background_tasks

    await cancel_graph_background_tasks()

    from services.data_assessment.job_worker import cancel_assessment_background_tasks
    await cancel_assessment_background_tasks()

    # Cancel in-flight export tasks so server can exit (avoids hang on Ctrl+C)
    from routes.exports import cancel_in_flight_export_tasks
    await cancel_in_flight_export_tasks()
    # Stop flow scheduler service
    from services.flow_scheduler_service import flow_scheduler_service
    flow_scheduler_service.stop()
    from services.data_retention_scheduler_service import get_data_retention_scheduler
    get_data_retention_scheduler().stop()

    from services.connector_metadata.scheduler_service import connector_metadata_scheduler_service
    connector_metadata_scheduler_service.stop()
    # Close Redis connection
    from utils.redis_service import close_redis_client
    await close_redis_client()
    await export_database.disconnect()
    await database.disconnect()


app.include_router(jobs_router)


app.include_router(migrate_router)


app.include_router(salesforce_oauth_router)


app.include_router(exports_router)


app.include_router(migration_records_router)


app.include_router(migration_stats_router)


app.include_router(chat_router)


app.include_router(extraction_results_router)


app.include_router(architect_router)


app.include_router(salesforce_connection_router)


app.include_router(admin_router)


# ============================================================================
# STRIPE PAYMENT ENDPOINTS
# ============================================================================


# ============================================================================
# PAYMENT VERIFICATION ENDPOINTS
# ============================================================================


# ============================================================================
# PROFILE MANAGEMENT ENDPOINTS
# ============================================================================


app.include_router(profile_router)


app.include_router(salesforce_schema_router)


app.include_router(sql_connectors_router)


# SQL Re-extraction endpoint
class SQLReExtractRequest(BaseModel):
    task_id: str
    new_prompt: str
    conversation_id: Optional[str] = None

# Google Sheets Re-extraction endpoint
class GoogleSheetsReExtractRequest(BaseModel):
    task_id: str
    new_prompt: str
    conversation_id: Optional[str] = None

class SQLRerunFlowRequest(BaseModel):
    flow_id: str
    user_prompt: str

class GoogleSheetsRerunFlowRequest(BaseModel):
    flow_id: str
    user_prompt: str


# ============================================================================
# GOOGLE SHEETS DESTINATION ENDPOINTS
# ============================================================================


app.include_router(google_sheets_router)


class GoogleSheetsRerunFlowRequest(BaseModel):
    flow_id: str
    user_prompt: str


app.include_router(flows_reuse_router)


app.include_router(flows_router)


# ============================================================================
# FLOW SCHEDULING ENDPOINTS
# ============================================================================


