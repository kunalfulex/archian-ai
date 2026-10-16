from sqlalchemy import Table, Column, String, Boolean, TIMESTAMP, JSON, MetaData, Integer, Text, ForeignKey
import uuid
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID

metadata = MetaData()

extracted_schemas = Table(
    "extracted_schemas",
    metadata,
    Column("id", String, primary_key=True, default=lambda: str(uuid.uuid4())),
    Column("user_id", PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("document_type", String),
    Column("file_key", String, nullable=True),
    Column("openai_file_id", String, nullable=True),
    Column("extracted_schema", JSONB),
    Column("user_prompt", String),  # New column for storing user prompt
    Column("approved_by_user", Boolean, default=False),
    Column("created_at", TIMESTAMP),
    Column("migration_flow_id", String)  # Add migration_flow_id column
)

# New table for migration batches
migration_batches = Table(
    "migration_batches",
    metadata,
    Column("id", String, primary_key=True, default=lambda: str(uuid.uuid4())),
    Column("user_id", PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("batch_name", String),
    Column("document_type", String),
    Column("batch_type", String, default="data"),  # 'data' = record migration, 'metadata' = metadata deploy
    Column("total_records", Integer),
    Column("successful_records", Integer, default=0),
    Column("failed_records", Integer, default=0),
    Column("status", String, default="in_progress"),  # in_progress, completed, failed
    Column("mapping_config", JSONB),  # Store the field mapping configuration
    Column("created_at", TIMESTAMP),
    Column("completed_at", TIMESTAMP, nullable=True),
    Column("error_summary", Text, nullable=True),
    Column("rollback_summary", Text, nullable=True),  # User-friendly rollback message
    Column("detail_purged_at", TIMESTAMP, nullable=True),  # Row-level retention cleared; batch summary stays
)

# New table for individual migration records
migration_records = Table(
    "migration_records",
    metadata,
    Column("id", String, primary_key=True, default=lambda: str(uuid.uuid4())),
    Column("batch_id", String, ForeignKey("migration_batches.id", ondelete="CASCADE"), nullable=False),
    Column("user_id", PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("record_index", Integer),  # Position in the original data array
    Column("source_data", JSONB),  # Original record data
    Column("mapped_data", JSONB),  # Data after field mapping
    Column("salesforce_results", JSONB),  # Results from Salesforce API calls
    Column("status", String),  # success, failed, partial_success
    Column("error_message", Text, nullable=True),
    Column("created_at", TIMESTAMP),
    Column("processed_at", TIMESTAMP, nullable=True)
)

# SQL Connector tables
sql_connectors = Table(
    "sql_connectors",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("user_id", PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("connector_type", String(50), nullable=False),  # mysql, postgresql, mssql, oracle, bigquery
    Column("name", String(255), nullable=False),
    Column("host", String(255)),
    Column("port", Integer),
    Column("database_name", String(255)),
    Column("username", String(255)),
    Column("password_encrypted", Text),  # encrypted password
    Column("connection_string", Text),  # for complex connections
    Column("ssl_enabled", Boolean, default=False),
    Column("additional_config", JSONB),  # for connector-specific settings
    Column("is_active", Boolean, default=True),
    # Tracks whether full database analysis/categorization has completed for this connector
    Column("analysis_completed", Boolean, default=False),
    Column("analysis_error", Text),  # Last analysis/sync error (human-readable) for UI and email
    Column("created_at", TIMESTAMP, default="NOW()"),
    Column("updated_at", TIMESTAMP, default="NOW()")
)

sql_extracted_schemas = Table(
    "sql_extracted_schemas",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("user_id", PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("connector_id", PG_UUID(as_uuid=True), ForeignKey("sql_connectors.id", ondelete="CASCADE"), nullable=False),
    Column("table_name", String(255), nullable=False),
    Column("schema_data", JSONB, nullable=False),  # column definitions, types, constraints
    Column("ai_categorization", JSONB),  # AI-generated object_name, document_type, document_description
    Column("extracted_at", TIMESTAMP, default="NOW()"),
    Column("migration_flow_id", String(255))  # link to existing migration flow
)

# Google Sheets connections table
google_sheets_connections = Table(
    "google_sheets_connections",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("user_id", PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("connection_name", String(255), nullable=False, default="Google Sheets"),
    Column("access_token", Text, nullable=False),
    Column("refresh_token", Text, nullable=False),
    Column("expires_at", TIMESTAMP, nullable=False),
    Column("scope", Text, default="https://www.googleapis.com/auth/spreadsheets"),
    Column("google_account_email", String(255)),
    Column("google_account_name", String(255)),
    Column("is_active", Boolean, default=True),
    Column("created_at", TIMESTAMP),
    Column("updated_at", TIMESTAMP)
)

# Chat conversation tables
chat_conversations = Table(
    "chat_conversations",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("user_id", PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("title", String(255), nullable=True),
    Column("created_at", TIMESTAMP),
    Column("updated_at", TIMESTAMP),
    Column("last_message_at", TIMESTAMP, nullable=True)
)

chat_messages = Table(
    "chat_messages",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("conversation_id", PG_UUID(as_uuid=True), ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False),
    Column("user_id", PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("role", String(20), nullable=False),
    Column("content", Text, nullable=False),
    Column("metadata", JSONB, nullable=True),
    Column("created_at", TIMESTAMP)
)

# Extraction results table - stores all extraction versions for preview restoration
extraction_results = Table(
    "extraction_results",
    metadata,
    Column("id", String, primary_key=True, default=lambda: str(uuid.uuid4())),
    Column("job_id", String, unique=True, nullable=False),  # Links to extraction_jobs dictionary key
    Column("parent_job_id", String, nullable=True),  # NULL for initial extraction, links to previous extraction for re-extractions
    Column("user_id", PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("conversation_id", PG_UUID(as_uuid=True), ForeignKey("chat_conversations.id", ondelete="SET NULL"), nullable=True),
    
    # Source information
    Column("source_type", String(50)),  # file, sql, google_sheets, salesforce
    Column("source_name", String(255)),  # file name, table name, spreadsheet name, connection name
    Column("source_identifier", String(500)),  # file_key, table_name, spreadsheet_id, org_id
    
    # Extraction data
    Column("extracted_data", JSONB, nullable=False),  # Final processed data (after cleaning/transformation)
    Column("extraction_goals", Text),  # User's extraction prompt/goals
    Column("document_type", String(255)),
    Column("document_description", Text),
    
    # Metadata
    Column("record_count", Integer),
    Column("extraction_version", Integer, default=1),  # 1, 2, 3... for same source (tracked automatically)
    Column("has_cleanup_rules", Boolean, default=False),
    Column("has_transformation_rules", Boolean, default=False),
    Column("has_mapping_transform", Boolean, default=False),
    
    # Timestamps
    Column("created_at", TIMESTAMP)
)

