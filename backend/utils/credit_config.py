"""
Credit cost constants and helpers for migration, rollback, cleanup, transformation, analysis.
Per CREDITS_BILLING_IMPLEMENTATION_PLAN: 1 record=1, relationship=1.5, file=5;
rollback at half: 0.5, 0.75, 2.
"""
import os

# Migration credits (per unit)
CREDIT_PER_RECORD = int(os.getenv("CREDIT_PER_RECORD", "1"))
CREDIT_PER_RELATIONSHIP_RECORD = float(os.getenv("CREDIT_PER_RELATIONSHIP_RECORD", "1.5"))
CREDIT_PER_FILE = int(os.getenv("CREDIT_PER_FILE", "5"))
# Rich text area images (per image uploaded during Salesforce migration)
CREDIT_PER_RICH_TEXT_IMAGE = int(os.getenv("CREDIT_PER_RICH_TEXT_IMAGE", "5"))

# Rollback credits (per unit, lower than migration)
ROLLBACK_PER_RECORD = float(os.getenv("ROLLBACK_PER_RECORD", "0.5"))
ROLLBACK_PER_RELATIONSHIP = float(os.getenv("ROLLBACK_PER_RELATIONSHIP", "0.75"))
ROLLBACK_PER_FILE = float(os.getenv("ROLLBACK_PER_FILE", "2"))

# Cleanup / transformation / analysis (from plan)
CREDIT_CLEANUP_BASE = int(os.getenv("CREDIT_CLEANUP_BASE", "5"))
CREDIT_CLEANUP_PER_500 = float(os.getenv("CREDIT_CLEANUP_PER_500", "0.5"))
CREDIT_CLEANUP_CAP = int(os.getenv("CREDIT_CLEANUP_CAP", "40"))
CREDIT_TRANSFORMATION_BASE = int(os.getenv("CREDIT_TRANSFORMATION_BASE", "8"))
CREDIT_TRANSFORMATION_PER_500 = float(os.getenv("CREDIT_TRANSFORMATION_PER_500", "2"))
CREDIT_TRANSFORMATION_CAP = int(os.getenv("CREDIT_TRANSFORMATION_CAP", "60"))
CREDIT_ANALYSIS_RUN = int(os.getenv("CREDIT_ANALYSIS_RUN", "5"))

# Connector/schema analysis: flat default kept for backward compat; size-based uses below
CREDIT_SCHEMA_ANALYSIS = int(os.getenv("CREDIT_SCHEMA_ANALYSIS", "15"))

# Size-based schema analysis (connector add + analysis): base + per entity (table/object/sheet) + per field (column/field)
CREDIT_SCHEMA_BASE = int(os.getenv("CREDIT_SCHEMA_BASE", "5"))
CREDIT_SCHEMA_PER_ENTITY = float(os.getenv("CREDIT_SCHEMA_PER_ENTITY", "0.5"))   # per table, object, or sheet
CREDIT_SCHEMA_PER_FIELD = float(os.getenv("CREDIT_SCHEMA_PER_FIELD", "0.02"))    # per column or field
CREDIT_SCHEMA_CAP = int(os.getenv("CREDIT_SCHEMA_CAP", "100"))

# Extraction (generate query + fetch records; OpenAI + API cost)
CREDIT_PER_EXTRACTION = int(os.getenv("CREDIT_PER_EXTRACTION", "5"))

# Mapping build (AI builds field mapping from schema + sample; OpenAI cost only, no migration execution)
# One gpt-4o or gpt-4o-mini call per destination (SQL/Sheets/Salesforce) + transformation analysis.
CREDIT_PER_MAPPING_BUILD = int(os.getenv("CREDIT_PER_MAPPING_BUILD", "10"))

# Metadata deploy: 5 base + 1 per field (extraction is included)
CREDIT_METADATA_DEPLOY_BASE = int(os.getenv("CREDIT_METADATA_DEPLOY_BASE", "5"))
CREDIT_PER_METADATA_FIELD = int(os.getenv("CREDIT_PER_METADATA_FIELD", "1"))

# Architect chat: info intent (schema/field analysis) and general AI response (1 credit per response)
CREDIT_PER_ARCHITECT_CHAT = int(os.getenv("CREDIT_PER_ARCHITECT_CHAT", "1"))

# Free plan: one-time signup grant (no monthly refill; once exhausted user must upgrade)
FREE_SIGNUP_CREDITS = int(os.getenv("FREE_SIGNUP_CREDITS", "500"))

# Show "upgrade" when balance is below this (so user is nudged to add credits or upgrade plan)
LOW_CREDIT_UPGRADE_THRESHOLD = int(os.getenv("LOW_CREDIT_UPGRADE_THRESHOLD", "100"))


def mapping_build_credits() -> int:
    """Credits required to build a single field mapping (OpenAI mapping + transformation analysis)."""
    return CREDIT_PER_MAPPING_BUILD


def compute_connector_schema_credits(entity_count: int, field_count: int) -> int:
    """
    Credits for connector schema analysis (add connector + analyze).
    entity_count = tables (SQL), objects (Salesforce), or sheet tabs (Google Sheets).
    field_count = columns (SQL/Sheets) or fields (Salesforce).
    """
    if entity_count < 0:
        entity_count = 0
    if field_count < 0:
        field_count = 0
    raw = CREDIT_SCHEMA_BASE + entity_count * CREDIT_SCHEMA_PER_ENTITY + field_count * CREDIT_SCHEMA_PER_FIELD
    return min(CREDIT_SCHEMA_CAP, max(CREDIT_SCHEMA_BASE, int(raw)))


def affordable_extraction_record_count(balance: int, cap: int = None) -> int:
    """How many records the user can afford to extract and migrate.
    Extraction is included; only migration uses credits (1 per record).
    Returns the number of records affordable given the credit balance. Pass cap to enforce an explicit upper bound."""
    if balance is None or balance <= 0:
        return 0
    # 1 record = 1 credit for migration; extraction is included
    affordable = balance // CREDIT_PER_RECORD
    if cap is not None:
        return min(max(0, affordable), cap)
    return max(0, affordable)


def compute_rich_text_image_credits(image_count: int) -> int:
    """Credits for rich text area images uploaded during Salesforce migration (5 per image)."""
    return max(0, image_count) * CREDIT_PER_RICH_TEXT_IMAGE


def compute_migration_credits(
    record_count: int,
    relationship_record_count: int = 0,
    file_count: int = 0,
) -> int:
    """Credits for migration: 1 per record, 1.5 per relationship record, 5 per file."""
    normal_records = max(0, record_count - relationship_record_count)
    return int(
        normal_records * CREDIT_PER_RECORD
        + relationship_record_count * CREDIT_PER_RELATIONSHIP_RECORD
        + file_count * CREDIT_PER_FILE
    )


def compute_rollback_credits(
    record_count: int,
    relationship_record_count: int = 0,
    file_count: int = 0,
) -> int:
    """Credits for rollback (per-unit lower rate): 0.5, 0.75, 2."""
    normal_records = max(0, record_count - relationship_record_count)
    return int(
        normal_records * ROLLBACK_PER_RECORD
        + relationship_record_count * ROLLBACK_PER_RELATIONSHIP
        + file_count * ROLLBACK_PER_FILE
    )


def compute_cleanup_credits(record_count: int) -> int:
    """5 base + 0.5 per 500 records, cap 40."""
    add = min(CREDIT_CLEANUP_CAP, int(record_count / 500 * CREDIT_CLEANUP_PER_500))
    return CREDIT_CLEANUP_BASE + add


def compute_transformation_credits(record_count: int) -> int:
    """8 base + 2 per 500 records, cap 60."""
    add = min(CREDIT_TRANSFORMATION_CAP, int(record_count / 500 * CREDIT_TRANSFORMATION_PER_500))
    return CREDIT_TRANSFORMATION_BASE + add


def compute_metadata_deploy_credits(
    field_count: int,
    permission_field_count: int = 0,
) -> int:
    """
    Credits for Salesforce metadata deploy (per plan: 5 base + 1 per field).
    Extraction is included; only deploy is charged.
    """
    return CREDIT_METADATA_DEPLOY_BASE + (field_count + permission_field_count) * CREDIT_PER_METADATA_FIELD
