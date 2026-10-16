"""
SQL Handler Module

This module contains all SQL database related functions for:
- SQL data extraction and querying
- SQL connection detection and management
- SQL table analysis and categorization
- Background processing for SQL operations
"""

import asyncio
import json
import re
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple

from utils.extraction_sse_payload import extraction_setup_progress_sse_line
from utils.extraction_sse_stream import stream_sql_job_events_until_terminal


def _validate_sql_columns(
    referenced_columns: List[str],
    table_schemas: Dict[str, Dict[str, str]],
    table_names: List[str],
) -> Tuple[bool, List[str], List[str]]:
    """
    Validate that columns referenced in the SQL (from LLM's referenced_columns) exist in the table schemas.
    Returns (valid, invalid_columns, available_columns).
    """
    if not table_schemas or not table_names:
        return (True, [], [])
    cols = [c for c in (referenced_columns or []) if c and isinstance(c, str)]
    if not cols:
        return (True, [], [])
    available = set()
    for t in table_names:
        schema = table_schemas.get(t, {})
        if isinstance(schema, dict):
            available.update(schema.keys())
        else:
            available.update(str(k) for k in (schema or {}))
    invalid = [c for c in cols if c and c not in available]
    available_list = sorted(available)
    return (len(invalid) == 0, invalid, available_list)


async def _build_invalid_column_clarification(
    invalid_columns: List[str],
    available_columns: List[str],
    table_names: List[str],
    connection_name: str,
    query: Optional[str] = None,
    prompt: Optional[str] = None,
) -> str:
    """Build a clarification message when invalid columns are detected."""
    table_str = ", ".join(table_names) if table_names else "the table"
    invalid_str = ", ".join(f"'{c}'" for c in invalid_columns[:10])
    if len(invalid_columns) > 10:
        invalid_str += f" and {len(invalid_columns) - 10} more"
    avail_preview = ", ".join(available_columns[:30])
    if len(available_columns) > 30:
        avail_preview += f", ... ({len(available_columns)} total)"
    msg = (
        f"The column(s) {invalid_str} are not available in {table_str} in {connection_name}.\n\n"
        f"**Available columns:** {avail_preview}\n\n"
        f"Which column would you like to use instead? Reply with the correct column name(s), and I'll rerun the extraction."
    )
    suggestion = None
    try:
        from services.llm import tasks as llm_tasks
        from services.llm.service import llm_complete_openai_compat, task_has_api_key
        if query and prompt and await task_has_api_key(llm_tasks.SQL_QUERY_GENERATION):
            async def _suggest():
                resp = await llm_complete_openai_compat(
                    llm_tasks.SQL_QUERY_GENERATION,
                    messages=[{
                        "role": "user",
                        "content": f"""The user's SQL query uses invalid column(s): {invalid_columns}.
Available columns in this table: {available_columns[:40]}

User's SQL: {query}
User's request: {prompt[:300]}

Suggest a corrected SQL using only available columns. If the invalid column was in SELECT, suggest a sensible replacement or remove it.
Reply with ONLY the corrected SQL, no explanation. If you cannot suggest one, reply with exactly: NONE""",
                    }],
                    max_tokens=200,
                    temperature=0,
                )
                text = (resp.choices[0].message.content or "").strip()
                if text and text.upper() != "NONE":
                    return text
                return None
            suggestion = await asyncio.wait_for(_suggest(), timeout=4.0)
    except Exception:
        pass
    if suggestion:
        msg += f"\n\n**Suggested query:** `{suggestion}`"
    return msg + "\n"


async def handle_sql_data_extraction_stream(prompt: str, user_id: str, conversation_history: list = None, destination: str = "salesforce", destination_name: str = None, source_name: Optional[str] = None, conversation_id: Optional[str] = None, source_entity_key: Optional[str] = None, related_entity_keys: Optional[list] = None, extract_field_scope: Optional[dict] = None, migration_intent: Optional[dict] = None, extract_contract: Optional[dict] = None):
    """
    Handle SQL data extraction requests.
    Emits job_id early so frontend can cancel before slow setup (connection, AI) completes.
    """
    if extract_contract and not source_entity_key:
        source_entity_key = extract_contract.get("primary_table") or source_entity_key
    if extract_contract and not isinstance(extract_field_scope, dict) and extract_contract.get("field_scope"):
        extract_field_scope = dict(extract_contract.get("field_scope") or {})

    import json
    import uuid
    from datetime import datetime
    try:
        # 0. Check if user has sufficient credits for extraction (credit-based limit)
        from services.architect_agent import get_user_extraction_limit
        extraction_limit = await get_user_extraction_limit(user_id)
        if extraction_limit.get("limit_reached"):
            async def limit_reached_generator():
                balance = extraction_limit.get("credit_balance") or extraction_limit.get("remaining_credits", 0)
                response_data = {
                    "status": "plan_limit_reached",
                    "message": f"Insufficient credits for extraction (balance: {balance}). Add credits or upgrade your plan to continue.",
                    "data": [],
                    "plan_info": extraction_limit,
                    "show_upgrade_option": extraction_limit.get("show_upgrade_option", True),
                }
                yield f"data: {json.dumps(response_data)}\n\n"
            return limit_reached_generator()

        # 1. Create job early so frontend can cancel before slow setup
        from utils.job_stores import sql_tasks
        job_id = str(uuid.uuid4())
        await sql_tasks.set(job_id, {
            "job_id": job_id,
            "user_id": str(user_id),
            "status": "starting",
            "created_at": datetime.now().isoformat(),
        })

        async def generate():
            nonlocal job_id
            try:
                # 2. Yield extraction_job_created first (enables cancel before setup)
                yield f"data: {json.dumps({'event_type': 'extraction_job_created', 'job_id': job_id, 'source_type': 'sql'})}\n\n"

                yield extraction_setup_progress_sse_line(
                    message="Finding your database connection…",
                    phase="resolve_connection",
                    source_type="sql",
                    job_id=job_id,
                    progress=0,
                )

                # 3. Check for active intent_state (filtered by source="sql")
                from handlers.classification_handler import get_active_intent_state
                from db.connection import database
                intent_state = await get_active_intent_state(
                    conversation_id,
                    user_id,
                    conversation_history or [],
                    source_type="sql",
                    current_prompt=prompt,
                )

                sql_connectors = await database.fetch_all(
                    """
                    SELECT name, connector_type FROM sql_connectors
                    WHERE user_id = :user_id AND is_active = true
                    ORDER BY created_at DESC
                    """,
                    {"user_id": user_id}
                )

                connection_name = None
                prompt_lower = prompt.lower()

                if source_name:
                    source_name_normalized = source_name.strip()
                    for conn in sql_connectors or []:
                        conn_name = conn.get('name') if isinstance(conn, dict) else (dict(conn).get('name') if hasattr(conn, '_mapping') or hasattr(conn, 'keys') else None)
                        if conn_name and conn_name.strip().lower() == source_name_normalized.lower():
                            connection_name = conn_name
                            break

                if not connection_name and sql_connectors:
                    for conn in sql_connectors:
                        conn_name = conn.get('name') if isinstance(conn, dict) else (dict(conn).get('name') if hasattr(conn, '_mapping') or hasattr(conn, 'keys') else None)
                        if not conn_name:
                            continue
                        if destination_name and conn_name.strip().lower() == destination_name.strip().lower():
                            continue
                        if conn_name.lower() in prompt_lower:
                            connection_name = conn_name
                            break

                if not connection_name and intent_state and intent_state.get("active_source") == "sql":
                    prompt_words = prompt.strip().split()
                    if len(prompt_words) <= 5:
                        connection_name = intent_state.get("source_name")

                if not connection_name:
                    connection_name = await detect_sql_connection_from_prompt(prompt, user_id, conversation_history, source_name=source_name, destination_name=destination_name)

                if not connection_name:
                    await sql_tasks.update(job_id, {"cancelled": True, "status": "cancelled"})
                    clarification_gen = ask_for_sql_connection_clarification(prompt, user_id, conversation_history)
                    async for chunk in clarification_gen:
                        yield chunk
                    return

                yield extraction_setup_progress_sse_line(
                    message=f"Connecting to {connection_name}…",
                    phase="connect",
                    source_type="sql",
                    job_id=job_id,
                    progress=0,
                )

                connector_data = await get_sql_connector_with_tables(connection_name, user_id)
                if not connector_data:
                    await sql_tasks.update(job_id, {"cancelled": True, "status": "cancelled"})
                    clarification_gen = ask_for_sql_connection_clarification(prompt, user_id, conversation_history)
                    async for chunk in clarification_gen:
                        yield chunk
                    return

                yield extraction_setup_progress_sse_line(
                    message=f"Connected to {connection_name} — choosing tables and SQL…",
                    phase="build_query",
                    source_type="sql",
                    job_id=job_id,
                    progress=0,
                )

                conv_hist = conversation_history or [{"type": "user", "text": prompt}]
                from services.planning.extract_field_scope import resolve_extract_field_scope

                resolved_field_scope = resolve_extract_field_scope(
                    extract_field_scope,
                    source_type="sql",
                    destination=destination,
                    source_name=source_name or connection_name,
                    destination_name=destination_name,
                )
                if migration_intent and isinstance(migration_intent, dict):
                    for key, val in migration_intent.items():
                        if val is not None and key not in resolved_field_scope:
                            resolved_field_scope[key] = val
                if related_entity_keys:
                    resolved_field_scope["parent_entity_keys"] = list(related_entity_keys)
                    resolved_field_scope["include_related_data"] = True
                from services.planning.extract_contract_gates import (
                    extract_contract_is_authoritative,
                    resolve_sql_from_extract_contract,
                )

                if extract_contract_is_authoritative(extract_contract):
                    table_and_query = await resolve_sql_from_extract_contract(
                        extract_contract,
                        connector_data,
                        prompt,
                        conv_hist,
                        str(user_id),
                        migration_intent=migration_intent,
                    )
                else:
                    table_and_query = await determine_sql_table_and_query(
                        prompt,
                        connector_data,
                        conv_hist,
                        user_id,
                        table_hint=source_entity_key,
                        extract_field_scope=resolved_field_scope,
                        migration_intent=migration_intent,
                        related_entity_keys=related_entity_keys,
                    )
                print(
                    f"📋 SQL table/query resolved for job {job_id}: "
                    f"needs_clarification={bool(table_and_query and table_and_query.get('needs_clarification'))} "
                    f"tables={table_and_query.get('table_names') if table_and_query else None}"
                )
                if not table_and_query:
                    await sql_tasks.update(job_id, {"cancelled": True, "status": "cancelled"})
                    clarification_gen = ask_for_sql_connection_clarification(prompt, user_id, conversation_history)
                    async for chunk in clarification_gen:
                        yield chunk
                    return

                table_schemas = table_and_query.get("table_schemas") or {}
                table_names = table_and_query.get("table_names") or []
                query = table_and_query.get("query")

                # Unified invalid-column path: from LLM's invalid_columns_requested or pre-flight validation of referenced_columns
                invalid_cols = table_and_query.get("invalid_columns_requested") or []
                if not invalid_cols and table_and_query.get("referenced_columns") and table_schemas and table_names:
                    valid, invalid_cols, _ = _validate_sql_columns(
                        table_and_query["referenced_columns"], table_schemas, table_names
                    )
                    if valid:
                        invalid_cols = []

                if invalid_cols and table_schemas and table_names:
                    await sql_tasks.update(job_id, {"cancelled": True, "status": "cancelled"})
                    available_cols = []
                    for t in table_names:
                        schema = table_schemas.get(t, {})
                        if isinstance(schema, dict):
                            available_cols.extend(schema.keys())
                    available_cols = sorted(set(available_cols))
                    connection_name = "your database"
                    if connector_data and connector_data.get("connector"):
                        conn = connector_data["connector"]
                        conn_dict = dict(conn) if hasattr(conn, "_mapping") else conn
                        connection_name = conn_dict.get("name", connection_name)
                    clarification = await _build_invalid_column_clarification(
                        invalid_columns=invalid_cols,
                        available_columns=available_cols,
                        table_names=table_names,
                        connection_name=connection_name,
                        query=query,
                        prompt=prompt,
                    )
                    yield f"data: {json.dumps({'status': 'clarification', 'message': clarification, 'job_id': job_id})}\n\n"
                    return

                if table_and_query.get("needs_clarification", False):
                    await sql_tasks.update(job_id, {"cancelled": True, "status": "cancelled"})
                    clarification_message = (
                        table_and_query.get("clarification_message", "")
                        or "Could you clarify what data you want to extract?"
                    )
                    print(
                        f"⚠️ SQL extraction needs clarification for job {job_id}: "
                        f"{clarification_message[:160]}"
                    )
                    yield f"data: {json.dumps({'status': 'clarification', 'message': clarification_message, 'job_id': job_id})}\n\n"
                    return

                comprehensive_prompt = table_and_query.get("comprehensive_prompt", prompt)

                # Check if cancelled before spawning
                task_data = await sql_tasks.get(job_id)
                if task_data and task_data.get("cancelled"):
                    return

                yield extraction_setup_progress_sse_line(
                    message="Running query and fetching rows…",
                    phase="execute",
                    source_type="sql",
                    job_id=job_id,
                    progress=5,
                )

                from services.sql_background_processing_service import start_sql_extraction_task
                from utils.extraction_profile import resolve_extraction_profile

                _profile = resolve_extraction_profile(
                    prompt=comprehensive_prompt,
                    source="sql",
                )
                await start_sql_extraction_task(
                    connector_data,
                    table_and_query,
                    user_id,
                    comprehensive_prompt,
                    destination=destination,
                    destination_name=destination_name,
                    conversation_id=conversation_id,
                    existing_job_id=job_id,
                    extraction_profile=_profile.value,
                    migration_intent=migration_intent,
                    extract_field_scope=resolved_field_scope,
                    related_entity_keys=related_entity_keys,
                    source_entity_key=source_entity_key,
                )
                yield f"data: {json.dumps({'status': 'processing', 'job_id': job_id, 'message': 'SQL extraction started in background', 'source_type': 'sql'})}\n\n"

                async for evt in stream_sql_job_events_until_terminal(job_id, sql_tasks):
                    yield f"data: {json.dumps(evt, default=str)}\n\n"
            except Exception as e:
                print(f"Error in SQL data extraction: {e}")
                try:
                    await sql_tasks.update(job_id, {"cancelled": True, "status": "cancelled"})
                except Exception:
                    pass
                clarification_gen = ask_for_sql_connection_clarification(prompt, user_id, conversation_history)
                async for chunk in clarification_gen:
                    yield chunk

        return generate()

    except Exception as e:
        print(f"Error in SQL data extraction: {e}")
        return await ask_for_sql_connection_clarification(prompt, user_id, conversation_history)


async def detect_sql_connection_from_prompt(prompt: str, user_id: str, conversation_history: list = None, source_name: Optional[str] = None, destination_name: Optional[str] = None) -> str:
    """
    Detect which SQL connection the user is referring to.
    Searches both current prompt AND conversation history for connection names.
    
    Args:
        prompt: User's prompt
        user_id: User ID
        conversation_history: Conversation history
        source_name: Source connection name from classification (highest priority)
        destination_name: Destination connection name (should be excluded from matching)
    """
    try:
        from db.connection import database
        
        # Get user's SQL connections
        sql_connectors = await database.fetch_all(
            """
            SELECT name, connector_type FROM sql_connectors 
            WHERE user_id = :user_id AND is_active = true
            ORDER BY created_at DESC
            """,
            {"user_id": user_id}
        )
        
        if not sql_connectors:
            return None
        
        # Convert Record objects to dicts and extract connection names
        connection_names = []
        for conn in sql_connectors:
            try:
                # Convert Record to dict for safe access (database returns Record objects, not dicts)
                conn_dict = dict(conn) if not isinstance(conn, dict) else conn
                conn_name = conn_dict.get('name') if isinstance(conn_dict, dict) else None
                if conn_name:
                    connection_names.append(conn_name)
            except (TypeError, ValueError):
                continue
        
        # PRIORITY 1: Use source_name if provided (highest priority)
        if source_name:
            source_name_normalized = source_name.strip()
            for conn in sql_connectors:
                conn_name = conn['name']
                if conn_name and conn_name.strip().lower() == source_name_normalized.lower():
                    print(f"✅ Using source connection '{conn_name}' from source_name parameter")
                    return conn_name
        
        # PRIORITY 2: Search conversation_history for connection names (most recent first)
        # But exclude destination_name to avoid picking destination as source
        if conversation_history:
            print(f"🔍 Searching conversation history for connection names...")
            for msg in reversed(conversation_history):
                # Validate message structure
                if not msg or not isinstance(msg, dict):
                    continue
                    
                msg_text = msg.get('text', '') or msg.get('content', '')
                if not msg_text:
                    continue
                
                # Check if any connection name appears in this message
                for conn_name in connection_names:
                    # Skip if this is the destination_name (we're looking for source, not destination)
                    if destination_name and conn_name.strip().lower() == destination_name.strip().lower():
                        continue
                    if conn_name.lower() in msg_text.lower():
                        print(f"✅ Found connection '{conn_name}' in conversation history: '{msg_text[:100]}...'")
                        return conn_name
        
        # Get table information for each connection to help with detection
        connection_info = []
        all_tables = {}  # Track which tables exist in which connections
        
        for conn in sql_connectors:
            # Convert Record to dict for safe access (database returns Record objects, not dicts)
            try:
                conn_dict = dict(conn) if not isinstance(conn, dict) else conn
            except (TypeError, ValueError):
                conn_dict = {}
            
            conn_name = conn_dict.get('name') if isinstance(conn_dict, dict) else None
            if not conn_name:
                continue
                
            # Get tables for this connection
            tables = await database.fetch_all(
                """
                SELECT table_name FROM sql_extracted_schemas 
                WHERE connector_id = (SELECT id FROM sql_connectors WHERE name = :conn_name AND user_id = :user_id)
                AND user_id = :user_id
                ORDER BY table_name
                """,
                {"conn_name": conn_name, "user_id": user_id}
            )
            # Convert table Records to dicts for safe access
            table_names = []
            for table in tables:
                try:
                    table_dict = dict(table) if not isinstance(table, dict) else table
                    table_name = table_dict.get('table_name') if isinstance(table_dict, dict) else None
                    if table_name:
                        table_names.append(table_name)
                except (TypeError, ValueError):
                    continue
            
            conn_type = conn_dict.get('connector_type', 'Unknown') if isinstance(conn_dict, dict) else 'Unknown'
            connection_info.append(f"{conn_name} ({conn_type}): {', '.join(table_names)}")
            
            # Track tables for ambiguity detection
            for table_name in table_names:
                if table_name not in all_tables:
                    all_tables[table_name] = []
                all_tables[table_name].append(conn_name)
        
        # Check if the mentioned table exists in multiple connections
        mentioned_table = extract_table_name_from_prompt(prompt)
        if mentioned_table and mentioned_table in all_tables and len(all_tables[mentioned_table]) > 1:
            print(f"⚠️ Table '{mentioned_table}' exists in multiple connections: {all_tables[mentioned_table]}")
            return None  # Force clarification
        
        print(f"🔍 Detecting SQL connection from prompt: '{prompt}'")
        print(f"📋 Available connections: {connection_names}")
        
        # Build dynamic connection list for the prompt
        connection_list_str = ", ".join([f'"{name}"' for name in connection_names])
        
        # Build conversation context for AI detection
        conversation_context = ""
        if conversation_history:
            # Include recent messages (last 5) to help AI find connection name
            recent_messages = conversation_history[-5:]
            conversation_context = "\n\n**Recent Conversation History:**\n"
            for msg in recent_messages:
                # Validate message structure
                if not msg or not isinstance(msg, dict):
                    continue
                    
                msg_type = msg.get('type', msg.get('role', 'user'))
                msg_text = msg.get('text', '') or msg.get('content', '')
                if msg_text:
                    role_label = "User" if msg_type in ['user', 'User'] else "Assistant"
                    conversation_context += f"{role_label}: {msg_text}\n"
        
        # Build source/destination context for AI
        source_destination_context = ""
        if source_name:
            source_destination_context += f"\n🚨 **CRITICAL - SOURCE CONNECTION**: The user wants to extract data FROM '{source_name}'. This is the SOURCE connection you should return.\n"
        if destination_name:
            source_destination_context += f"\n⚠️ **IMPORTANT - DESTINATION CONNECTION**: '{destination_name}' is the DESTINATION (where data goes TO). Do NOT return this as the source connection.\n"
        
        detection_prompt = f"""
        Available SQL connections: {connection_list_str}
        {source_destination_context}
        
        **Current User Message:** 
        "{prompt}"
        {conversation_context}
        
        Which connection is the user referring to as the SOURCE (where data comes FROM)? Return the exact connection name from the list above.
        
        🚨 **CRITICAL RULES:**
        1. **If source_name is provided above**, return that exact connection name (it's the SOURCE connection)
        2. **If destination_name is provided above**, do NOT return it - it's the destination, not the source
        3. Look for connection names in:
           - The current user message (words like "from", "extract from", "get data from" indicate source)
           - The conversation history (especially recent messages)
        4. If the user says "extract from X" or "get data from X", X is the SOURCE connection
        5. If the user says "migrate to Y" or "send to Y", Y is the DESTINATION (not the source)
        
        If the user directly mentions a source connection name anywhere in the conversation, return that exact name.
        Return the exact connection name if found, otherwise return "UNKNOWN".
        """
        
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks

        print(f"🔍 Full detection prompt being sent to AI:")
        print(f"--- START PROMPT ---")
        print(detection_prompt)
        print(f"--- END PROMPT ---")
        response = await async_openai_call(
            None,
            task=llm_tasks.SQL_CONNECTION_DETECT,
            messages=[{"role": "user", "content": detection_prompt}],
            max_tokens=500,
            temperature=0.1,
        )
        
        # Validate OpenAI response structure
        if not response.choices or not response.choices[0].message.content:
            print("⚠️ Empty or invalid response from OpenAI")
            return None
        
        detected_connection = response.choices[0].message.content.strip()
        
        print(f"🎯 OpenAI detected connection: '{detected_connection}'")
        
        # Clean up the detected connection (remove quotes, extra text)
        cleaned_connection = detected_connection.strip("'\"")
        
        # Get connection names list for matching (convert Records to dicts for safe access)
        connection_list = []
        for conn in sql_connectors:
            # Convert Record to dict for safe access (database returns Record objects, not dicts)
            try:
                conn_dict = dict(conn) if not isinstance(conn, dict) else conn
                conn_name = conn_dict.get('name') if isinstance(conn_dict, dict) else None
                if conn_name:
                    connection_list.append(conn_name)
            except (TypeError, ValueError):
                continue
        
        # If OpenAI returned a sentence, try to extract the connection name
        if len(cleaned_connection.split()) > 2:  # More than 2 words = likely a sentence
            # OpenAI returned sentence, extracting connection name
            # Look for connection names within the sentence
            for conn_name in connection_list:
                if conn_name.lower() in cleaned_connection.lower():
                    # Extracted connection from sentence
                    return conn_name
        
        # Try to find a match in the connection list (case-insensitive, partial match)
        for conn_name in connection_list:
            if (cleaned_connection.lower() == conn_name.lower() or 
                cleaned_connection.lower() in conn_name.lower() or
                conn_name.lower() in cleaned_connection.lower()):
                # Connection matched
                return conn_name
        
        print(f"❌ No connection match found for: '{detected_connection}'")
        return None
        
    except Exception as e:
        import traceback
        print(f"❌ Error detecting SQL connection: {e}")
        print(f"Error type: {type(e).__name__}")
        traceback.print_exc()
        return None


def extract_table_name_from_prompt(prompt: str) -> str:
    """
    Extract table name from user prompt using simple pattern matching
    """
    import re
    
    # Common patterns for table names in prompts
    patterns = [
        r'from\s+(\w+)\s+table',  # "from clientDetails table"
        r'table\s+(\w+)',         # "table clientDetails"
        r'(\w+)\s+table',         # "clientDetails table"
        r'extract.*?from\s+(\w+)', # "extract data from clientDetails"
        r'get.*?from\s+(\w+)',    # "get data from clientDetails"
        r'show.*?from\s+(\w+)',   # "show data from clientDetails"
    ]
    
    for pattern in patterns:
        match = re.search(pattern, prompt, re.IGNORECASE)
        if match:
            return match.group(1)
    
        return None


async def get_sql_connector_with_tables(connection_name: str, user_id: str):
    """
    Get SQL connector details and its categorized tables
    """
    try:
        from db.connection import database
        
        # Get connector details
        connector = await database.fetch_one(
            """
            SELECT * FROM sql_connectors 
            WHERE name = :name AND user_id = :user_id AND is_active = true
            """,
            {"name": connection_name, "user_id": user_id}
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
            {"connector_id": connector['id'], "user_id": user_id}
        )
        
        return {
            "connector": connector,
            "tables": tables
        }
        
    except Exception as e:
        print(f"Error getting SQL connector with tables: {e}")
        return None


async def list_sql_connector_tables_from_store(user_id: str, connector_id: str) -> List[str]:
    """Table names from sql_extracted_schemas (schema sync), not a live DB round-trip."""
    from db.connection import database

    rows = await database.fetch_all(
        """
        SELECT DISTINCT table_name
        FROM sql_extracted_schemas
        WHERE connector_id = :connector_id AND user_id = :user_id
          AND COALESCE(is_deleted, FALSE) = FALSE
        ORDER BY table_name
        """,
        {"connector_id": connector_id, "user_id": user_id},
    )
    out: List[str] = []
    for row in rows or []:
        name = str(dict(row).get("table_name") or "").strip()
        if name:
            out.append(name)
    return out


async def get_sql_table_fields_from_store(
    user_id: str, connector_id: str, table_name: str
) -> Optional[List[Dict[str, str]]]:
    """Column list from stored schema_data; None if the table is not in sql_extracted_schemas."""
    from db.connection import database
    from db.extracted_schema import _sql_schema_data_to_columns

    row = await database.fetch_one(
        """
        SELECT schema_data
        FROM sql_extracted_schemas
        WHERE connector_id = :connector_id AND user_id = :user_id
          AND table_name = :table_name
          AND COALESCE(is_deleted, FALSE) = FALSE
        ORDER BY extracted_at DESC
        LIMIT 1
        """,
        {
            "connector_id": connector_id,
            "user_id": user_id,
            "table_name": table_name,
        },
    )
    if not row:
        return None
    columns = _sql_schema_data_to_columns(dict(row).get("schema_data"))
    return [
        {"name": name, "type": str(meta.get("data_type") or "")}
        for name, meta in sorted(columns.items(), key=lambda x: x[0].lower())
    ]


async def build_sql_table_context_for_prompt(prompt: str, user_id: str, source_name: Optional[str] = None) -> str:
    """
    Build enhanced SQL table context for user prompts using sql_extracted_schemas.
    Resolves connector by source_name (connection name), fetches table_name, schema_data, ai_categorization,
    optionally selects relevant tables via OpenAI, and returns a formatted context string for the LLM.
    Mirrors the flow of build_salesforce_context_for_prompt for Salesforce info.
    """
    try:
        from db.connection import database

        connection_name = (source_name or "").strip()
        if not connection_name:
            # Fallback: first connector with tables for this user
            connectors = await database.fetch_all(
                """
                SELECT sc.name, sc.id FROM sql_connectors sc
                WHERE sc.user_id = :user_id AND sc.is_active = true
                AND EXISTS (
                    SELECT 1 FROM sql_extracted_schemas ses
                    WHERE ses.connector_id = sc.id AND ses.user_id = sc.user_id
                )
                ORDER BY sc.created_at DESC
                LIMIT 1
                """,
                {"user_id": user_id},
            )
            if connectors:
                conn = dict(connectors[0]) if hasattr(connectors[0], "keys") else connectors[0]
                connection_name = (conn.get("name") or "").strip()

        if not connection_name:
            return """
📊 **No SQL tables found**

Connect a database and run an extraction (or schema analysis) so Archian AI can show you table info here.

🔗 **To get table-specific guidance:**
1. **Connections** → Add your SQL database connector
2. **Extract or analyze** → So we have table names, schema, and categorization in `sql_extracted_schemas`
3. **Ask in chat** → e.g. \"What columns are in the orders table?\"

💡 **Why connect?**
- See column names and types for your tables
- Get AI categorization (object_name, document_type, description)
- Get migration guidance based on your actual schema
"""

        connector_data = await get_sql_connector_with_tables(connection_name, user_id)
        if not connector_data or not connector_data.get("tables"):
            return f"""
📊 **No tables found for connection \"{connection_name}\"**

Run an extraction or schema analysis for this connection so we have table metadata in `sql_extracted_schemas`.
"""

        connector = connector_data["connector"]
        tables = connector_data["tables"]
        conn_dict = dict(connector) if hasattr(connector, "keys") else connector
        conn_type = conn_dict.get("connector_type", "sql")
        conn_name = conn_dict.get("name", connection_name)

        from services.planning.connector_info_context import (
            build_connector_info_enrichment,
            resolve_sql_connector,
        )

        sql_connector = await resolve_sql_connector(str(user_id), conn_name)
        enrichment_block = ""
        info_meta: dict = {}
        if sql_connector:
            enrichment_block, info_meta = await build_connector_info_enrichment(
                str(user_id),
                sql_connector,
                prompt,
            )

        # Optionally select relevant tables (1–3) via OpenAI, like Salesforce object selection
        available_tables = []
        for t in tables:
            td = dict(t) if hasattr(t, "keys") else t
            cat = td.get("ai_categorization") or {}
            if isinstance(cat, str):
                try:
                    cat = json.loads(cat) if cat else {}
                except Exception:
                    cat = {}
            available_tables.append({
                "table_name": td.get("table_name"),
                "object_name": cat.get("object_name"),
                "document_type": cat.get("document_type"),
                "document_description": cat.get("document_description"),
            })

        selected_table_names = list(info_meta.get("focus_entity_keys") or [])
        if not selected_table_names:
            try:
                selection_prompt = f"""
User asked: "{prompt}"

Available tables for connection "{conn_name}" (with AI categorization when present):
{json.dumps([{"table": t["table_name"], "object_name": t.get("object_name"), "document_type": t.get("document_type"), "description": t.get("document_description")} for t in available_tables[:30]], indent=2)}

Identify the most relevant tables for the user's question (1–3 tables max). Return ONLY the table names, one per line.
"""
                from services.architect_agent import async_openai_call
                from services.llm import tasks as llm_tasks

                response = await async_openai_call(
                    None,
                    task=llm_tasks.SQL_TABLE_SELECT,
                    messages=[{"role": "user", "content": selection_prompt}],
                    max_tokens=300,
                    temperature=0.3,
                )
                selected_table_names = [n.strip() for n in response.choices[0].message.content.strip().split("\n") if n.strip()]
            except Exception as e:
                print(f"SQL table selection fallback: {e}")
                selected_table_names = [t.get("table_name") for t in available_tables[:3] if t.get("table_name")]

        prompt_lower = (prompt or "").lower()
        if not selected_table_names and any(
            term in prompt_lower
            for term in ("what tables", "list tables", "show tables", "available tables", "describe schema")
        ):
            inventory = ["\n📋 **Synced tables**\n"]
            for t in available_tables[:60]:
                inventory.append(
                    f"- **{t.get('table_name')}**"
                    + (f" ({t.get('document_type')})" if t.get("document_type") else "")
                    + "\n"
                )
            header = enrichment_block or f"\n📌 **Connection:** {conn_name} ({conn_type})\n"
            return header + "".join(inventory)

        if not selected_table_names:
            selected_table_names = [t.get("table_name") for t in available_tables[:3] if t.get("table_name")]

        matched_tables = [t for t in tables if (dict(t).get("table_name") if hasattr(t, "keys") else t.get("table_name")) in selected_table_names]
        if not matched_tables:
            matched_tables = tables[:3]

        context_parts = [enrichment_block or f"\n📌 **Connection:** {conn_name} ({conn_type})\n"]
        for t in matched_tables:
            td = dict(t) if hasattr(t, "keys") else t
            table_name = td.get("table_name", "unknown")
            schema_data = td.get("schema_data") or {}
            if isinstance(schema_data, str):
                try:
                    schema_data = json.loads(schema_data) if schema_data else {}
                except Exception:
                    schema_data = {}
            ai_cat = td.get("ai_categorization") or {}
            if isinstance(ai_cat, str):
                try:
                    ai_cat = json.loads(ai_cat) if ai_cat else {}
                except Exception:
                    ai_cat = {}

            context_parts.append(f"\n🔹 **Table: {table_name}**")
            if ai_cat.get("object_name"):
                context_parts.append(f"   📂 Salesforce object mapping: {ai_cat['object_name']}")
            if ai_cat.get("document_type"):
                context_parts.append(f"   📄 Document type: {ai_cat['document_type']}")
            if ai_cat.get("document_description"):
                context_parts.append(f"   📝 Description: {ai_cat['document_description']}")

            columns = schema_data.get("columns") if isinstance(schema_data, dict) else (schema_data if isinstance(schema_data, list) else [])
            if not columns and isinstance(schema_data, dict):
                columns = [{"name": k, "type": v} if isinstance(v, str) else v for k, v in schema_data.items() if k != "columns"]
            if columns:
                context_parts.append("   📋 Columns:")
                for col in (columns[:50] if isinstance(columns, list) else []):
                    c = col if isinstance(col, dict) else {"name": str(col), "type": ""}
                    name = c.get("name") or c.get("column_name") or str(c)
                    dtype = c.get("type") or c.get("data_type") or ""
                    context_parts.append(f"      • {name}" + (f" ({dtype})" if dtype else ""))
            else:
                context_parts.append("   📋 (No column details in schema_data)")

        return "\n".join(context_parts)
    except Exception as e:
        print(f"Error building SQL table context: {e}")
        import traceback
        traceback.print_exc()
        return "\n📊 SQL table context could not be built. Connect a database and run extraction so we have table/schema data.\n"


async def handle_sql_table_info_stream(
    prompt: str,
    conversation_history: list,
    user_id: str,
    source_name: Optional[str] = None,
):
    """
    Handle SQL database table info requests in chat (streaming).
    Uses sql_extracted_schemas (table_name, schema_data, ai_categorization) to build context
    and streams a response like handle_salesforce_request_stream for Salesforce info.
    user_id scopes all data to this user's connectors and extracted schemas only.
    """
    try:
        # Architect chat is free; no credit deduction

        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks

        enhanced_context = await build_sql_table_context_for_prompt(prompt, user_id, source_name)

        messages = [
            {
                "role": "system",
                "content": f"""You are Archian AI, a friendly SQL/database and data pipeline expert.

**CRITICAL – USE THIS CONTEXT:**
{enhanced_context}

**REQUIREMENTS:**
1. Use the table names, columns, and AI categorization above to answer the user.
2. Be specific: reference actual column names and types when relevant.
3. Explain what each table is for (using document_type / document_description when present).
4. If the user asks about migrating to Salesforce, use object_name mapping from the context.
5. Keep formatting clear (bullet points, short paragraphs). Do not ask follow-up questions; give a complete answer.

**STYLE:** Conversational, technical, and helpful. Mention that Archian AI can extract, transform, and migrate this data as part of a pipeline when relevant."""
            }
        ]
        if conversation_history:
            for msg in conversation_history:
                if msg.get("type") == "user":
                    messages.append({"role": "user", "content": msg.get("text", "")})
                elif msg.get("type") == "system":
                    messages.append({"role": "assistant", "content": msg.get("text", "")})
        messages.append({"role": "user", "content": prompt})

        response = await async_openai_call(
            None,
            task=llm_tasks.GENERAL_CHAT,
            messages=messages,
            temperature=0.7,
            stream=True,
        )

        async def generate():
            try:
                for chunk in response:
                    if chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
            except Exception as e:
                yield f"\n\n❌ Error during streaming: {str(e)}"

        return generate()
    except Exception as e:
        print(f"Error in handle_sql_table_info_stream: {e}")
        import traceback
        traceback.print_exc()

        error_message = str(e)
        async def error_gen():
            yield f"\n\n❌ Could not load table info. Make sure you have a database connected and tables in sql_extracted_schemas.\n\nError: {error_message}"
        return error_gen()


async def determine_sql_table_and_query(
    prompt: str,
    connector_data: dict,
    conversation_history: list = None,
    user_id: str = None,
    *,
    table_hint: Optional[str] = None,
    extract_field_scope: Optional[dict] = None,
    migration_intent: Optional[dict] = None,
    related_entity_keys: Optional[list] = None,
):
    """
    Two-stage approach: First select relevant tables (1–4), then generate SQL with optimized schemas (JOIN-capable)
    This scales to any number of tables while staying under token limits
    
    Args:
        prompt: User's extraction request
        connector_data: SQL connector data with tables
        conversation_history: Previous conversation messages
        user_id: User ID for plan limit checking
    """
    try:
        print(f"🎯 Starting SQL extraction for prompt: '{prompt}'")
        
        hint = (table_hint or "").strip()
        from services.planning.sql_extract_contract import (
            collect_sql_fk_relationships,
            filter_tables_to_catalog,
            resolve_sql_extract_tables,
        )

        selected_tables: List[str] = []
        if hint and hint.lower() not in ("unknown",):
            plan_tables = resolve_sql_extract_tables(
                table_hint=table_hint,
                related_entity_keys=related_entity_keys,
                migration_intent=migration_intent,
            )
            selected_tables = filter_tables_to_catalog(plan_tables, connector_data)
            if selected_tables:
                print(
                    f"✅ Plan-pinned tables (no LLM table select): {selected_tables} "
                    f"(anchor={table_hint})"
                )

        if not selected_tables:
            print(f"📋 Selecting relevant tables from {len(connector_data['tables'])} available tables")
            selected_tables = await select_relevant_tables(
                prompt,
                connector_data,
                conversation_history,
                selection_role="source",
                table_hint=table_hint,
                related_entity_keys=related_entity_keys,
                migration_intent=migration_intent,
                user_id=str(user_id) if user_id else None,
            )
        
        if not selected_tables:
            return {
                "needs_clarification": True,
                "clarification_message": "No suitable table found for your request. Please check your database connection and try again."
            }
        
        print(f"✅ Tables resolved: {selected_tables}")

        from services.architect_agent import create_optimized_schema

        table_schemas_preview: Dict[str, Any] = {}
        for table in connector_data.get("tables", []):
            table_dict = table if isinstance(table, dict) else dict(table)
            tname = (table_dict.get("table_name") or "").strip()
            if tname not in selected_tables:
                continue
            schema_data = table_dict.get("schema_data") or {}
            if isinstance(schema_data, str):
                try:
                    schema_data = json.loads(schema_data)
                except (json.JSONDecodeError, TypeError):
                    schema_data = {}
            table_schemas_preview[tname] = create_optimized_schema(schema_data)

        from services.planning.sql_extract_contract import determine_sql_columns_from_scope

        fk_relationships = collect_sql_fk_relationships(
            selected_tables,
            connector_data.get("tables") or [],
        )

        column_result = determine_sql_columns_from_scope(
            selected_tables,
            table_schemas_preview,
            extract_field_scope=extract_field_scope,
            migration_intent=migration_intent,
        )
        column_result["fk_relationships"] = fk_relationships
        
        # Stage 2: Generate SQL query for selected tables with optimized schemas
        print(f"🔧 Generating SQL query for tables {selected_tables}")
        # Get user's extraction limit
        from services.architect_agent import get_user_extraction_limit
        if not user_id:
            raise ValueError("user_id is required for SQL query generation")
        extraction_limit = await get_user_extraction_limit(user_id)
        
        result = await generate_sql_query_for_tables(
            prompt, 
            selected_tables, 
            connector_data, 
            conversation_history,
            user_id=user_id,
            extraction_limit=extraction_limit,
            extract_field_scope=extract_field_scope,
            migration_intent=migration_intent,
            column_selection=column_result,
        )
        
        if result and extract_field_scope and extract_field_scope.get("cross_system"):
            from services.planning.extract_field_scope import filter_sql_referenced_columns

            refs = result.get("referenced_columns") or []
            filtered = filter_sql_referenced_columns(refs, extract_field_scope, prompt=prompt)
            if filtered != refs:
                result["referenced_columns"] = filtered
                result["extract_field_scope"] = extract_field_scope
        
        print(f"✅ SQL query generation complete for tables {selected_tables}")
        
        return result
        
    except Exception as e:
        print(f"Error in two-stage SQL extraction: {e}")
        return {
            "needs_clarification": True,
            "clarification_message": "I encountered an error processing your request. Please try again."
        }


async def select_relevant_tables(
    prompt: str,
    connector_data: dict,
    conversation_history: list = None,
    *,
    selection_role: str = "source",
    table_hint: Optional[str] = None,
    related_entity_keys: Optional[list] = None,
    migration_intent: Optional[dict] = None,
    user_id: Optional[str] = None,
) -> list:
    """
    Select relevant tables (1–4). When table_hint is set, resolve from plan contract (no LLM).
    Falls back to LLM selection for ad-hoc extracts without a plan anchor.
    """
    try:
        hint = (table_hint or "").strip()
        from services.planning.entity_catalog_policy import is_metadata_entity, is_vague_user_data_migration_prompt

        if hint and is_metadata_entity(hint, connector_type="sql") and is_vague_user_data_migration_prompt(prompt):
            print(f"⚠️ Refusing metadata table_hint={hint} for vague user-data prompt")
            hint = ""
        if hint and hint.lower() not in ("unknown",):
            from services.planning.sql_extract_contract import (
                filter_tables_to_catalog,
                resolve_sql_extract_tables,
            )

            plan_tables = resolve_sql_extract_tables(
                table_hint=hint,
                related_entity_keys=related_entity_keys,
                migration_intent=migration_intent,
            )
            pinned = filter_tables_to_catalog(plan_tables, connector_data)
            if pinned:
                print(
                    f"✅ Plan-pinned table(s) {pinned} from extract contract (table_hint={table_hint})"
                )
                return pinned
        from services.labeling.catalog_helpers import fetch_semantics_map, primary_from_semantics

        semantics_map: Dict[str, Any] = {}
        connector_row = connector_data.get("connector")
        if connector_row:
            conn_dict = dict(connector_row) if hasattr(connector_row, "keys") else connector_row
            cid = str(conn_dict.get("id") or "").strip()
            uid = str(user_id or conn_dict.get("user_id") or "").strip()
            if cid and uid:
                semantics_map = await fetch_semantics_map(
                    user_id=uid,
                    connector_type="sql",
                    connector_instance_id=cid,
                )

        # Prepare lightweight table information (names + descriptions only)
        table_summaries = []
        for table in connector_data.get('tables', []):
            # Safe dict conversion with type checking
            if isinstance(table, dict):
                table_dict = table
            elif hasattr(table, 'keys') and callable(getattr(table, 'keys', None)):
                try:
                    table_dict = dict(table)
                except (TypeError, ValueError):
                    continue  # Skip invalid tables
            else:
                continue  # Skip non-dict tables
            
            # Use .get() for safe access
            table_name = table_dict.get('table_name')
            if not table_name:  # Skip tables without required field
                continue
            
            sem = semantics_map.get(table_name) if semantics_map else None
            primary = primary_from_semantics(sem) if sem else {}
            doc_type = primary.get("category") or table_name
            doc_desc = primary.get("description") or f"Table {table_name}"
            table_summaries.append({
                "table_name": table_name,
                "document_type": doc_type,
                "document_description": doc_desc,
                "confidence_score": primary.get("confidence") or 50,
            })
        
        # Build conversation context if available (last 4 messages)
        conversation_context = ""
        if conversation_history:
            # Get last 4 messages (slicing works even if length is <= 4)
            recent_messages = conversation_history[-4:]
            # Format conversation history
            context_lines = []
            for msg in recent_messages:
                # Validate message structure
                if not msg or not isinstance(msg, dict):
                    continue
                    
                msg_type = msg.get('type', msg.get('role', 'user'))
                msg_text = msg.get('text', msg.get('content', ''))
                if msg_text:
                    role_label = "User" if msg_type in ['user', 'User'] else "Assistant"
                    context_lines.append(f"{role_label}: {msg_text}")
            
            if context_lines:
                conversation_context = f"""
        
        **Conversation Context (for reference):**
        {chr(10).join(context_lines)}
        
        ⚠️ NOTE: The current user message may be a short clarification (like a connection name). Use the conversation context to understand the full user intent, especially what data/columns they want to extract.
        """
        
        is_destination = (selection_role or "source").strip().lower() == "destination"

        if is_destination:
            role_instructions = """
        You are selecting the SQL **destination table** for field mapping and migration.
        Choose the table the user wants data written **TO**, not the table they extract FROM.

        - Focus on migrate/map "to table …" / "into …" wording.
        - Do NOT select tables mentioned only as the extraction source.
        - Return a JSON array with ONE table name when possible, e.g. ["service_appointments"].
        """
        else:
            role_instructions = """
        You are Archian AI, an intelligent database assistant. Based on the user's current request, select the most relevant tables for data extraction. ONLY select multiple tables if the user's request absolutely requires data from multiple tables that cannot be fulfilled by a single table.
        Only focus on what user want to extract from the database. Don't included tables based on user destination to salesforce migration object i.e if user prompt says extarct customer data from data and migrate them to salesforce contact object then you focus on extarct customer data from database.

        Your task is to analyze the user's intent and select 1 table based on user prompt, if the user prompt specifically looking for the data which needs multiple tables only then you select the mukltile table but not more than 3.

        Guidelines:
        - PREFER SINGLE TABLE: If one table can fulfill the user's request, select only that table
        - MULTIPLE TABLES ONLY WHEN NECESSARY: Only select multiple tables if the user explicitly needs data from different tables (e.g., "customers with their orders", "users and their roles")
        - Look at table names, document types, and descriptions to understand what each table contains
        - For simple data extraction (like "get customer emails"), one table is usually sufficient
        - Return a JSON array of table names ordered by relevance (most relevant first). No extra text.
        - Focus on user data extraction request and not the destination to salesforce migration object.
        - **IMPORTANT**: If conversation context shows the user already specified what data/columns they want, use that information even if the current message is short.

        Example responses:
        ["users"]  # For "get customer emails" - single table sufficient
        ["customers", "orders"]  # For "customers with their orders" - needs JOIN
        ["orders", "order_items", "products"]  # For "order details with product info" - needs multiple JOINs
        """

        selection_prompt = f"""
        {role_instructions.strip()}

        Available Tables: {[table.get('table_name') for table in table_summaries if table.get('table_name')]}
        Table Details: {json.dumps(table_summaries, indent=2)}
        
        **Current User Message:**
        "{prompt}"
{conversation_context}
        """
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.SQL_TABLE_SELECT,
            messages=[{"role": "user", "content": selection_prompt}],
            max_tokens=500,
            temperature=0.1,
        )
        
        # Validate OpenAI response structure
        if not response.choices or not response.choices[0].message.content:
            print("⚠️ Empty or invalid response from OpenAI")
            # Fallback to first table
            available_tables = [table.get('table_name') for table in table_summaries if table.get('table_name')]
            return [available_tables[0]] if available_tables else []
        
        ai_resp = response.choices[0].message.content.strip()
        
        # Try to extract JSON array
        if ai_resp.startswith('```json'):
            ai_resp = ai_resp[7:]
        if ai_resp.endswith('```'):
            ai_resp = ai_resp[:-3]
        if '[' in ai_resp and ']' in ai_resp:
            start = ai_resp.find('[')
            end = ai_resp.rfind(']') + 1
            ai_resp = ai_resp[start:end]
        else:
            # No JSON array found - use fallback
            print(f"⚠️ No JSON array found in OpenAI response: {ai_resp[:100]}")
            available_tables = [table.get('table_name') for table in table_summaries if table.get('table_name')]
            return [available_tables[0]] if available_tables else []
        
        # Use centralized JSON parsing function (handles markdown code blocks, JSON fixing, etc.)
        from utils.openai_helpers import parse_openai_json_response
        try:
            suggested = parse_openai_json_response(ai_resp)
            # Ensure it's a list (parse_openai_json_response can return dict or list)
            if not isinstance(suggested, list):
                suggested = [str(suggested)]
        except (ValueError, json.JSONDecodeError) as e:
            print(f"⚠️ Failed to parse JSON response: {e}")
            print(f"Response was: {ai_resp[:200]}")
            # Fallback to first table
            available_tables = [table.get('table_name') for table in table_summaries if table.get('table_name')]
            return [available_tables[0]] if available_tables else []
        
        # Validate that the selected tables exist and limit to max 4
        available_tables = [table.get('table_name') for table in table_summaries if table.get('table_name')]
        filtered = [t for t in suggested if t in available_tables][:4]
        
        # Fallback if empty
        if not filtered and available_tables:
            filtered = [available_tables[0]]
        
        return filtered
            
    except Exception as e:
        print(f"Error selecting table: {e}")
        import traceback
        traceback.print_exc()
        # Fallback to first table
        # Initialize table_summaries in case exception happened before it was populated
        table_summaries = table_summaries if 'table_summaries' in locals() else []
        available_tables = [table.get('table_name') for table in table_summaries if table.get('table_name')]
        return [available_tables[0]] if available_tables else []


async def generate_sql_query_for_tables(prompt: str, table_names: list, connector_data: dict, conversation_history: list = None, user_id: Optional[str] = None, extraction_limit: Optional[Dict[str, Any]] = None, *, extract_field_scope: Optional[dict] = None, migration_intent: Optional[dict] = None, column_selection: Optional[dict] = None) -> dict:
    """
    Stage 2: Generate SQL query for the selected table(s) with optimized schemas (JOIN-capable)
    """
    table_schemas = {}
    try:

        # Build optimized schemas for requested tables
        from services.architect_agent import create_optimized_schema
        
        table_schemas = {}
        table_descriptions = {}
        for table in connector_data.get('tables', []):
            # Safe dict conversion with type checking
            if isinstance(table, dict):
                table_dict = table
            elif hasattr(table, 'keys') and callable(getattr(table, 'keys', None)):
                try:
                    table_dict = dict(table)
                except (TypeError, ValueError):
                    continue  # Skip invalid tables
            else:
                continue  # Skip non-dict tables
            
            # Use .get() for safe access
            table_name = table_dict.get('table_name')
            if not table_name or table_name not in table_names:
                continue
            
            # Parse schema
            schema_data = {}
            try:
                if table_dict.get('schema_data'):
                    if isinstance(table_dict['schema_data'], str):
                        schema_data = json.loads(table_dict['schema_data'])
                    else:
                        schema_data = table_dict['schema_data']
            except (json.JSONDecodeError, TypeError):
                schema_data = {}
            
            # Assign schema after parsing (outside try-except)
            table_schemas[table_name] = create_optimized_schema(schema_data)
            table_descriptions[table_name] = f"Table {table_name}"
        
        if not table_schemas:
            return {"needs_clarification": True, "clarification_message": "Selected tables not found"}

        connection_name = "your database"
        connector_type = ""
        connector = None
        if connector_data and connector_data.get('connector'):
            connector = connector_data['connector']
            # Handle both dict and Record objects
            if hasattr(connector, '_mapping'):
                connector_dict = dict(connector)
                connection_name = connector_dict.get('name', 'your database')
                connector_type = (connector_dict.get('connector_type') or "").strip().lower()
            else:
                connection_name = connector.get('name', 'your database')
                connector_type = (connector.get('connector_type') or "").strip().lower()
        
        # Dialect-specific SQL instructions (MSSQL uses TOP, not LIMIT)
        dialect_instruction = ""
        if connector_type in ("mssql", "sqlserver"):
            dialect_instruction = """
**DATABASE: Microsoft SQL Server**
- Do NOT use LIMIT. Use SELECT TOP N instead.
- TOP goes immediately after SELECT. Example: SELECT TOP 1000 * FROM table_name
- If plan limit is provided, use: SELECT TOP {limit} ... (e.g. SELECT TOP 1000 col1, col2 FROM Customers)
"""
        elif connector_type == "snowflake":
            snowflake_table_map = {}
            connector_cfg = {}
            if connector:
                if hasattr(connector, "_mapping"):
                    connector = dict(connector)
                elif not isinstance(connector, dict):
                    connector = dict(connector)
                from services.sql_connector_service import (
                    SQLConnectorService,
                    snowflake_qualify_table,
                )

                connector_cfg = SQLConnectorService().prepare_connector_config(connector)
                for tn in table_names:
                    snowflake_table_map[tn] = snowflake_qualify_table(connector_cfg, tn)
            dialect_instruction = f"""
**DATABASE: Snowflake**
- Use LIMIT N for row caps (e.g. SELECT ... LIMIT 1000), not TOP.
- **Always use fully qualified table names** in FROM/JOIN exactly as in this map (do not use bare table names):
{json.dumps(snowflake_table_map, indent=2)}
- Example: SELECT c.*, o.* FROM {snowflake_table_map.get(table_names[0], "DB.SCHEMA.TABLE1")} c JOIN {snowflake_table_map.get(table_names[-1], "DB.SCHEMA.TABLE2")} o ON ...
"""
        
        # Build conversation context if available (last 4 messages)
        conversation_context = ""
        if conversation_history and len(conversation_history) > 0:
            # Get last 4 messages
            recent_messages = conversation_history[-4:] if len(conversation_history) > 4 else conversation_history
            # Format conversation history
            context_lines = []
            for msg in recent_messages:
                msg_type = msg.get('type', msg.get('role', 'user'))
                msg_text = msg.get('text', msg.get('content', ''))
                if msg_text:
                    role_label = "User" if msg_type in ['user', 'User'] else "Assistant"
                    context_lines.append(f"{role_label}: {msg_text}")
            
            if context_lines:
                conversation_context = f"""
        
        **Conversation Context (for reference):**
        {chr(10).join(context_lines)}
        
        ⚠️ NOTE: The current user message may be a short clarification (like a connection name). Use the conversation context to understand the full user intent, especially what data/columns they want to extract.
        """
        
        # Build plan limit instruction
        plan_limit_instruction = ""
        if extraction_limit:
            can_extract = extraction_limit.get("can_extract", 0)
            limit_reached = extraction_limit.get("limit_reached", False)
            extraction_cap = extraction_limit.get("extraction_cap", None)
            
            if limit_reached or can_extract == 0:
                plan_limit_instruction = """
**CREDIT LIMIT REACHED:**
- User has insufficient credits for extraction
- They can extract 0 more records until they add credits or upgrade
- Return needs_clarification: true with a message about credit limit
"""
            elif can_extract > 0:
                if connector_type in ("mssql", "sqlserver"):
                    plan_limit_instruction = f"""
**CREDIT LIMIT APPLIED:**
- User can extract maximum {can_extract} records (based on current credit balance)
- **MUST use SELECT TOP {can_extract}** (e.g. SELECT TOP {can_extract} * FROM table_name)
"""
            elif connector_type == "snowflake":
                plan_limit_instruction = f"""
**CREDIT LIMIT APPLIED:**
- User can extract maximum {can_extract} records (based on current credit balance)
- **MUST add LIMIT {can_extract}** to the Snowflake query (unless user explicitly requests fewer)
- Example: SELECT ... FROM database.schema.table LIMIT {can_extract}
        """
            else:
                plan_limit_instruction = f"""
**CREDIT LIMIT APPLIED:**
- User can extract maximum {can_extract} records (based on current credit balance)
- **MUST add LIMIT {can_extract} to the SQL query** (unless user explicitly requests fewer)
- Example: SELECT * FROM table_name LIMIT {can_extract}
        """
        
        from services.planning.extract_field_scope import sql_cross_system_prompt_block

        field_scope = extract_field_scope if isinstance(extract_field_scope, dict) else {}
        cross_system_block = sql_cross_system_prompt_block(field_scope)

        from services.planning.sql_extract_contract import (
            build_sql_extract_contract,
            enforce_sql_select_contract,
            sql_authority_prompt_block,
            user_context_prompt_block,
            determine_sql_columns_from_scope,
        )

        col_result = column_selection or determine_sql_columns_from_scope(
            table_names,
            table_schemas,
            extract_field_scope=extract_field_scope,
            migration_intent=migration_intent,
        )
        contract = build_sql_extract_contract(
            col_result,
            extract_field_scope=extract_field_scope,
            migration_intent=migration_intent,
        )
        authority_block = sql_authority_prompt_block(contract)
        user_block = user_context_prompt_block(prompt, conversation_context)

        query_prompt = f"""
        You are Archian AI, an intelligent SQL query generator. Build a valid SQL query from the EXTRACT CONTRACT below.
        Use the contract for SELECT columns and allowed tables; use FK relationships for JOIN ON clauses.
{dialect_instruction}
{authority_block}
        Selected Tables (ordered by relevance): {json.dumps(table_names)}
        Table Descriptions: {json.dumps(table_descriptions, indent=2)}
        Available Columns Per Table: {json.dumps(table_schemas, indent=2)}
{user_block}

{plan_limit_instruction}
{cross_system_block}

        Your task is to generate the most appropriate SQL query based on the EXTRACT CONTRACT and user context.

        Guidelines for SQL generation:
        - **SELECT clause:** Use ONLY columns listed in the EXTRACT CONTRACT (exact names). Never add columns because the user mentioned them.
        - Use the provided columns to write accurate SQL queries
        - If multiple tables are in the contract, generate JOINs using the FK RELATIONSHIPS in the contract (e.g. user_plans.user_id = users.id)
        - Prefer explicit JOIN syntax; alias tables meaningfully; anchor FROM on the Primary table in the contract
        - Don't include WHERE clauses unless the user specifically requests filtering
        - Follow proper SQL syntax and best practices
        - Choose columns that are relevant to the user's request
        - Avoid selecting too many columns - focus on what the user needs
        - Avoid Id columns in the SELECT clause as this migth be sensitive data, try avoid sensitive data in the SELECT clause
        - **IMPORTANT**: If the user asks to "ignore", "exclude", "remove", or "not include" specific columns, DO NOT include those columns in the SELECT statement
        - Pay special attention to exclusion requests like "ignore the company column", "exclude timezone", "don't include phone numbers", etc.
        - **CRITICAL**: If conversation context shows the user already specified what columns/data they want (e.g., "first name, last name, email"), use that information even if the current message is short. Do NOT ask for clarification if the information is already in the conversation context.
        - **CRITICAL - Missing columns:** If the user requests a column that does not exist in Available Columns Per Table (e.g. "professor_name" or "lead_extention"), include it in invalid_columns_requested. Prefer generating the query with referenced_columns and invalid_columns_requested; we will show available columns and a suggested corrected query. Only use needs_clarification for truly ambiguous requests (e.g. which tables, what filters, unclear intent); when doing so for column issues, include invalid_columns_requested.
        - **CRITICAL - Credit-based extraction limit (IMPORTANT):**
          - For Microsoft SQL Server: use SELECT TOP N (e.g. SELECT TOP 1000 * FROM table_name). Do NOT use LIMIT.
          - For MySQL/PostgreSQL: if a credit-based extraction limit is provided, add LIMIT N to the query (e.g. LIMIT 1000)
          - The limit should match the extraction limit from the user's credits (unless user explicitly requests fewer records)
          - If no extraction limit is provided, you may omit limit (or use a reasonable default like TOP 1000 / LIMIT 1000 for large tables)
        - **CRITICAL - Deduplication, validation, and cleanup (do NOT encode in SQL):**
          - If the user asks to remove duplicates, dedupe, validate emails, filter invalid values, or clean data, generate a plain SELECT of the relevant rows/columns only.
          - Do NOT use GROUP BY, DISTINCT ON, window functions, or subqueries solely to deduplicate or validate — a later pipeline step handles that.
          - Only use GROUP BY when the user explicitly asks for aggregated/summary data (counts, sums, averages by group).
          - For email validity or duplicate removal requests, you may add WHERE filters only when the user explicitly asks to exclude rows in SQL (e.g. "only rows where email is not null"); otherwise SELECT all candidate rows.

        Response format - Return ONE of these JSON structures:

        If you can generate a query:
        {{
            "needs_clarification": false,
            "table_names": {json.dumps(table_names)},
            "query": "SELECT ...",
            "referenced_columns": ["col1", "col2", "col3"],
            "invalid_columns_requested": ["col_x"],
            "comprehensive_prompt": "Write a natural, conversational 2-sentence summary from the user's perspective explaining what they requested to extract from the {connection_name} connection and the selected tables."
        }}

        **referenced_columns (REQUIRED when generating a query):** List EVERY column name used in the SQL: SELECT clause, WHERE clause, ORDER BY, JOIN conditions, and GROUP BY. Use unqualified names (e.g. first_name, email) as they appear in the table schema. This list will be validated against the table schema before execution.

        **invalid_columns_requested (include when applicable):** If the user explicitly requested columns that do NOT exist in Available Columns Per Table above, list them here (e.g. ["professor_name", "lead_extention"]). Use empty array [] when all requested columns exist. We use this to show the user available columns and a suggested corrected query.

        If you need clarification (for truly ambiguous requests, not for missing columns):
        {{
            "needs_clarification": true,
            "clarification_message": "Helpful message asking for specific information about what they want to extract",
            "invalid_columns_requested": ["col_x"]
        }}

        When clarification is about missing/invalid columns, include invalid_columns_requested so we can show available columns and a suggested query.

        Be intelligent, context-aware, and make reasonable inferences. Trust your AI capabilities to understand user intent and generate appropriate JOIN-capable SQL queries.
        
        Examples of exclusion handling:
        - User says "ignore the company column" → SELECT first_name, last_name, email, phone FROM users (NOT including company)
        - User says "exclude timezone and phone" → SELECT first_name, last_name, email FROM users (NOT including timezone, phone)
        - User says "don't include sensitive data" → SELECT name, email FROM users (NOT including id, password, etc.)
        """
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.SQL_QUERY_GENERATION,
            messages=[{"role": "user", "content": query_prompt}],
            max_tokens=500,
            temperature=0.1,
        )
        
        # Validate OpenAI response structure
        if not response.choices or not response.choices[0].message.content:
            print("⚠️ Empty or invalid response from OpenAI")
            return {
                "needs_clarification": True,
                "clarification_message": "I encountered an error processing your request. Please try again.",
                "table_names": table_names,
                "table_schemas": table_schemas,
            }
        
        ai_response = response.choices[0].message.content.strip()
        
        # Use centralized JSON parsing function (handles markdown code blocks, JSON fixing, etc.)
        from utils.openai_helpers import parse_openai_json_response
        try:
            result = parse_openai_json_response(ai_response)
        except (ValueError, json.JSONDecodeError) as e:
            print(f"⚠️ Failed to parse JSON response: {e}")
            print(f"Response was: {ai_response[:200]}")
            return {
                "needs_clarification": True,
                "clarification_message": "I encountered an error processing your request. Please try again.",
                "table_names": table_names,
                "table_schemas": table_schemas,
            }
        
        # Validate required fields and attach schema for pre-flight validation
        if 'table_names' not in result:
            result['table_names'] = table_names
        result['table_schemas'] = table_schemas

        # Normalize invalid_columns_requested (strip table prefix: table.col -> col)
        def _normalize_columns(cols):
            out = []
            for c in cols or []:
                if c and isinstance(c, str):
                    parts = c.split(".")
                    col = parts[-1].strip() if parts else c.strip()
                    if col:
                        out.append(col)
            return list(dict.fromkeys(out))

        result["invalid_columns_requested"] = _normalize_columns(result.get("invalid_columns_requested"))

        if not result.get('needs_clarification', False):
            if 'query' not in result:
                result['needs_clarification'] = True
                result['clarification_message'] = "Unable to generate SQL query"
            else:
                result["referenced_columns"] = _normalize_columns(result.get("referenced_columns"))
                if connector_type == "snowflake" and result.get("query"):
                    from services.sql_connector_service import (
                        SQLConnectorService,
                        qualify_snowflake_sql,
                    )

                    conn_row = connector
                    if hasattr(conn_row, "_mapping"):
                        conn_row = dict(conn_row)
                    elif conn_row and not isinstance(conn_row, dict):
                        conn_row = dict(conn_row)
                    if conn_row:
                        sf_cfg = SQLConnectorService().prepare_connector_config(conn_row)
                        result["query"] = qualify_snowflake_sql(
                            result["query"],
                            table_names,
                            sf_cfg,
                        )
                allowed_cols = contract.get("allowed_select_columns") or []
                if result.get("query") and allowed_cols:
                    enforced, kept, removed = enforce_sql_select_contract(
                        result["query"],
                        allowed_cols,
                    )
                    if enforced:
                        result["query"] = enforced
                    if removed:
                        result["contract_enforced_removed"] = removed
                result["selected_columns"] = col_result.get("selected_columns") or []
                result["extraction_metadata"] = {
                    "extract_contract": contract,
                    "selected_columns": result.get("selected_columns"),
                    "table_names": table_names,
                }

        return result
        
    except json.JSONDecodeError as e:
        print(f"Error parsing AI response: {e}")
        return {
            "needs_clarification": True,
            "clarification_message": "I encountered an error processing your request. Please try again.",
            "table_names": table_names,
            "table_schemas": table_schemas,
        }
    except Exception as e:
        print(f"Error generating SQL query: {e}")
        return {
            "needs_clarification": True,
            "clarification_message": "I encountered an error processing your request. Please try again.",
            "table_names": table_names,
            "table_schemas": table_schemas,
        }


async def execute_sql_query_and_stream(connector_data: dict, table_and_query: dict, user_id: str, user_prompt: str, destination: str = "salesforce", destination_name: str = None):
    """
    Execute the SQL query and stream the results
    
    Args:
        connector_data: SQL connector configuration
        table_and_query: Table names and SQL query
        user_id: User ID
        user_prompt: User's extraction request
        destination: Target destination (salesforce, google_sheets, sql, etc.)
        destination_name: Specific destination name (org name, spreadsheet name, connector name)
    """
    try:
        from services.sql_connector_service import SQLConnectorService
        
        # Executing SQL query with data
        
        # Create connector instance
        connector_service = SQLConnectorService()
        
        # Convert database record to dictionary
        connector_dict = dict(connector_data['connector'])
        # Connector dict prepared
        
        connector_config = connector_service.prepare_connector_config(connector_dict)
        connector_instance = connector_service.create_connector_instance(
            connector_dict['connector_type'], 
            connector_config
        )
        
        # About to execute query
        
        # Execute query
        results = await connector_instance.execute_query(table_and_query['query'])
        
        # Format results for streaming
        if results:
            # Convert dates to strings for JSON serialization
            def convert_datetime_for_json(obj):
                if hasattr(obj, 'isoformat'):
                    return obj.isoformat()
                elif hasattr(obj, 'strftime'):
                    return obj.strftime('%Y-%m-%d %H:%M:%S')
                return obj
            
            # Convert all datetime/date objects in results
            serializable_results = []
            for row in results:
                if isinstance(row, dict):
                    serializable_row = {k: convert_datetime_for_json(v) for k, v in row.items()}
                    serializable_results.append(serializable_row)
                else:
                    serializable_results.append(convert_datetime_for_json(row))
            
            # Convert to the format expected by DataTablePage
            # Use the existing extract_structured_data_from_file logic for SQL data
            # Support multi-table output from AI (backward-compatible with single table)
            selected_table_names = []
            if isinstance(table_and_query, dict):
                if 'table_names' in table_and_query and isinstance(table_and_query['table_names'], list):
                    selected_table_names = table_and_query['table_names']
                elif 'table_name' in table_and_query and table_and_query['table_name']:
                    selected_table_names = [table_and_query['table_name']]
            display_table_name = selected_table_names[0] if selected_table_names else None

            extraction_result = await extract_structured_data_from_sql(
                serializable_results,
                user_id,
                user_prompt,
                display_table_name or 'unknown',
                destination=destination,
                destination_name=destination_name
            )
            
            if extraction_result and len(extraction_result) >= 10:
                extracted_data, document_type, document_description, plan_limit_info, original_file_records, show_upgrade_option, object_name, destination_name, extraction_rules, mapping_context_summary = extraction_result
                
                formatted_data = {
                    "status": "success",
                    "data": extracted_data,
                    "total_records": len(extracted_data),
                    "table_name": display_table_name,
                    "table_names": selected_table_names,
                    "query": table_and_query['query'],
                    "user_prompt": user_prompt,
                    "document_type": document_type,
                    "document_description": document_description,
                    "extraction_rules": extraction_rules if extraction_rules else None,
                    "mapping_context_summary": mapping_context_summary,
                    "plan_limit_info": plan_limit_info
                }
            else:
                # Fallback if extraction fails
                formatted_data = {
                    "status": "success",
                    "data": serializable_results,
                    "total_records": len(serializable_results),
                    "table_name": display_table_name,
                    "table_names": selected_table_names,
                    "query": table_and_query['query'],
                    "user_prompt": user_prompt,
                    "document_type": "SQL Data",
                    "document_description": f"Data from {display_table_name or 'selected'} table(s)"
                }
            
            # Stream the response
            async def generate():
                yield f"data: {json.dumps(formatted_data)}\n\n"
            
            return generate()
        else:
            # No data found
            async def generate():
                yield f"data: {json.dumps({'status': 'success', 'data': [], 'message': 'No data found matching your criteria'})}\n\n"
            
            return generate()
            
    except Exception as e:
        error_message = str(e)
        print(f"Error executing SQL query: {error_message}")
        async def generate():
            yield f"Error executing query: {error_message}"
        return generate()


async def extract_structured_data_from_sql(
    sql_data: list,
    user_id: str,
    extraction_goals: str,
    table_name: str,
    destination: str = "salesforce",
    destination_name: str = None,
    saved_extraction_rules: Optional[Dict[str, Any]] = None,
    extraction_profile: Optional[str] = None,
    intent: Optional[str] = None,
    template: Optional[str] = None,
):
    """
    Extract structured data from SQL results using the EXACT same logic as file uploads
    Reuses extract_structured_data_from_file logic but works with JSON data instead of files
    
    Args:
        sql_data: List of SQL records
        user_id: User ID
        extraction_goals: User's extraction goals
        table_name: Source table name
        destination: Target destination (salesforce, google_sheets, sql, etc.)
        destination_name: Specific destination name (org name, spreadsheet name, connector name)
    """
    try:
        import pandas as pd
        
        # Lazy imports to avoid circular dependency
        from services.architect_agent import (
            get_user_extraction_limit,
            convert_datetime_for_json,
            create_sample_records_from_schema,
            safe_json_serialize,
            async_openai_call
        )
        from handlers.file_handler import apply_cleanup_and_validation_rules
        from handlers.prompt_handler import PromptBuilder
        from utils.extraction_profile import (
            profile_allows_pre_openai_rule_apply,
            resolve_extraction_profile,
            sanitize_extraction_rules_for_profile,
        )

        profile = resolve_extraction_profile(
            saved_extraction_rules=saved_extraction_rules,
            extraction_profile=extraction_profile,
            prompt=extraction_goals,
            template=template,
            intent=intent,
            source="sql",
        )

        # Get user's extraction limit (same as file uploads)
        extraction_limit = await get_user_extraction_limit(user_id)
        
        # Convert SQL data to DataFrame for consistent processing
        df = pd.DataFrame(sql_data)
        
        # Track original record count for credit-limit detection
        original_file_records = len(df)
        
        # Convert datetime objects to strings for JSON serialization (same as file uploads)
        real_data = df.fillna("").map(convert_datetime_for_json).to_dict(orient="records")
        
        # ⭐ FILTERING LOGIC - Apply user-specified filter criteria BEFORE plan limits
        # Use saved extraction rules if provided, otherwise extract from OpenAI response
        filter_rules = None
        deduplication_rules = None
        transformation_rules = None
        
        # Defensive check: ensure saved_extraction_rules is a dict (not a list or other type)
        if saved_extraction_rules and isinstance(saved_extraction_rules, dict):
            # Use saved extraction rules (from flow rerun)
            filter_rules = saved_extraction_rules.get("filter_rules")
            deduplication_rules = saved_extraction_rules.get("deduplication_rules")
            transformation_rules = saved_extraction_rules.get("transformation_rules")
        elif saved_extraction_rules is not None:
            # saved_extraction_rules exists but is not a dict (could be a list or other type)
            print(f"⚠️ [SQL HANDLER] saved_extraction_rules found but is not a dict (type: {type(saved_extraction_rules)}), ignoring")
            saved_extraction_rules = None  # Reset to None to prevent further issues
        else:
            # Will extract from OpenAI response after calling it
            pass
        
        if profile_allows_pre_openai_rule_apply(profile):
            if saved_extraction_rules and transformation_rules:
                from handlers.file_handler import apply_transformations
                real_data = apply_transformations(real_data, transformation_rules)
            if filter_rules and filter_rules.get("conditions"):
                from handlers.file_handler import apply_filter_rules
                real_data = apply_filter_rules(real_data, filter_rules)
            if deduplication_rules and deduplication_rules.get("enabled"):
                from handlers.file_handler import apply_deduplication_rules
                real_data = apply_deduplication_rules(real_data, deduplication_rules)
        
        # ⭐ NOW apply plan limits to the filtered and deduplicated data
        if extraction_limit["can_extract"] > 0:
                real_data = real_data[:extraction_limit["can_extract"]]
        else:
            print(f"❌ Credit limit reached: Cannot extract any more records (insufficient credits)")
            return [], "Credit Limit Reached", None, "Insufficient credits for extraction. Add credits or upgrade your plan.", extraction_limit, 0, extraction_limit.get("show_upgrade_option", True), None, destination_name, saved_extraction_rules or {}, None
        
        # Use real data for OpenAI extraction analysis (limit to 5 records for token management)
        # Data is only sent for analysis, not stored, and data sharing with OpenAI is disabled
        sample_data = real_data[:5] if len(real_data) > 5 else real_data
        
        # Use the EXACT same prompt and logic as file uploads
        
        # Build plan limit instruction using PromptBuilder
        plan_limit_instruction = PromptBuilder.build_plan_limit_instruction(extraction_limit)
        
        # Build extraction prompt using PromptBuilder
        instruction = await PromptBuilder.build_extraction_prompt(
            destination=destination,
            destination_name=destination_name,
            extraction_goals=f"Extract from {table_name} table: {extraction_goals}",
            sample_data=safe_json_serialize(sample_data),
            plan_limit_instruction=plan_limit_instruction,
            user_id=str(user_id),
            source="sql",
            source_name=table_name,
            extraction_profile=profile.value,
        )
        
        # Use the same async OpenAI call as file uploads
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.SQL_EXTRACTION,
            messages=[{"role": "user", "content": instruction}],
            max_tokens=4000,
            temperature=0.1,
        )
        
        # Initialize default values for error paths
        object_name = 'SQL_Data'
        document_type = 'SQL Data'
        document_description = f'Data from {table_name} table'
        extraction_rules = {}
        mapping_context_summary = None
        
        # Parse the response using centralized JSON parsing function
        try:
            # Extract JSON from the response
            response_text = response.choices[0].message.content.strip()
            
            # Use centralized JSON parsing function (handles markdown code blocks, JSON fixing, etc.)
            from utils.openai_helpers import parse_openai_json_response
            try:
                extracted_data = parse_openai_json_response(response_text)
                # Ensure it's a list (parse_openai_json_response can return dict or list)
                if isinstance(extracted_data, dict):
                    extracted_data = [extracted_data]
                elif not isinstance(extracted_data, list):
                    extracted_data = []
            except (ValueError, json.JSONDecodeError) as json_error:
                print(f"⚠️ Failed to parse JSON response using parse_openai_json_response: {json_error}")
                print(f"Response was: {response_text[:500]}")
                raise ValueError(f"Failed to parse OpenAI JSON response: {json_error}")
            
            # Extract metadata from first record
            if extracted_data and len(extracted_data) > 0:
                first_record = extracted_data[0]
                object_name = first_record.get('object_name', 'SQL_Data')
                document_type = first_record.get('document_type', 'SQL Data')
                document_description = first_record.get('document_description', f'Data from {table_name} table')
                mapping_context_summary = first_record.get("mapping_context_summary")
                
                # Use saved extraction rules if provided, otherwise extract from OpenAI response
                if saved_extraction_rules and isinstance(saved_extraction_rules, dict):
                    # Use saved extraction rules (from flow rerun)
                    extraction_rules = saved_extraction_rules.copy()
                elif saved_extraction_rules is not None:
                    # saved_extraction_rules exists but is not a dict, reset to empty dict
                    print(f"⚠️ [SQL HANDLER] saved_extraction_rules found but is not a dict (type: {type(saved_extraction_rules)}), using empty dict")
                    extraction_rules = {}
                else:
                    # Extract from OpenAI response
                    filter_rules = first_record.get("filter_rules")
                    deduplication_rules = first_record.get("deduplication_rules")
                    transformation_rules = first_record.get("transformation_rules")
                    
                    if filter_rules:
                        extraction_rules["filter_rules"] = filter_rules
                    if deduplication_rules:
                        extraction_rules["deduplication_rules"] = deduplication_rules
                    if transformation_rules:
                        extraction_rules["transformation_rules"] = transformation_rules

                # Extract destination_name from classification if it was None or "unknown"
                # Only extract from object_name when destination is google_sheets
                if (destination_name == None or destination_name == "unknown") and destination == "google_sheets":
                    destination_name = first_record.pop("object_name", None)
                    print(f"Destination name: {destination_name}")
                
                source_schema = sorted(
                    {k for r in real_data[:50] if isinstance(r, dict) for k in r.keys()}
                )
                extraction_rules = sanitize_extraction_rules_for_profile(
                    extraction_rules, profile, source_schema=source_schema
                )
                if source_schema:
                    extraction_rules.setdefault("source_schema", source_schema)

                for record in extracted_data:
                    record.pop('object_name', None)
                    record.pop('document_type', None)
                    record.pop('document_description', None)
                
                from utils.extraction_utils import apply_extraction_rules_to_structured_data
                # If saved_extraction_rules provided, create a mock extracted_data structure with saved rules
                if saved_extraction_rules and isinstance(saved_extraction_rules, dict):
                    # Create mock extracted_data with saved rules for the utility function
                    mock_extracted_data = [saved_extraction_rules.copy()]
                    # Remove transformation_rules if they were already applied early (to prevent double transformation)
                    if transformation_rules and "transformation_rules" in mock_extracted_data[0]:
                        del mock_extracted_data[0]["transformation_rules"]
                    # Also include mapping_context_summary if available
                    if mapping_context_summary:
                        mock_extracted_data[0]["mapping_context_summary"] = mapping_context_summary
                    processed_real_data = apply_extraction_rules_to_structured_data(
                        real_data,
                        mock_extracted_data,
                        extraction_profile=profile,
                        saved_extraction_rules=saved_extraction_rules,
                        prompt=extraction_goals,
                        template=template,
                        intent=intent,
                        source="sql",
                    )
                else:
                    processed_real_data = apply_extraction_rules_to_structured_data(
                        real_data,
                        extracted_data,
                        extraction_profile=profile,
                        prompt=extraction_goals,
                        template=template,
                        intent=intent,
                        source="sql",
                    )
                
                _cap = extraction_limit.get("extraction_cap")
                show_upgrade_option = extraction_limit.get(
                    "show_upgrade_option",
                    (_cap is not None and extraction_limit.get("can_extract", 0) < _cap)
                    or extraction_limit.get("limit_reached", False),
                )
                return processed_real_data, document_type, document_description, extraction_limit, original_file_records, show_upgrade_option, object_name, destination_name, extraction_rules, mapping_context_summary
            else:
                show_upgrade_option = extraction_limit.get(
                    "show_upgrade_option",
                    extraction_limit.get("limit_reached", False),
                )
                return [], document_type, document_description, extraction_limit, original_file_records, show_upgrade_option, object_name, destination_name, extraction_rules, mapping_context_summary
                
        except Exception as parse_error:
            print(f"❌ Error parsing OpenAI response: {parse_error}")
            import traceback
            traceback.print_exc()
            show_upgrade_option = extraction_limit.get("show_upgrade_option", extraction_limit.get("limit_reached", False)) if extraction_limit else False
            return [], document_type, document_description, extraction_limit, original_file_records, show_upgrade_option, object_name, destination_name, extraction_rules, mapping_context_summary
        
    except Exception as e:
        print(f"❌ Error extracting structured data from SQL: {e}")
        import traceback
        traceback.print_exc()
        # Initialize defaults for outer exception handler
        default_extraction_limit = {"can_extract": 0, "extraction_cap": None, "plan_type": "free"}
        return [], "SQL Data", f"Data from {table_name} table", default_extraction_limit, 0, False, "SQL_Data", destination_name, {}, None


async def ask_for_sql_connection_clarification(prompt: str, user_id: str, conversation_history: list = None):
    """
    Ask user to clarify which SQL connection to use
    """
    try:
        from db.connection import database
        
        # Get available connections
        sql_connectors = await database.fetch_all(
            """
            SELECT name, connector_type FROM sql_connectors 
            WHERE user_id = :user_id AND is_active = true
            ORDER BY created_at DESC
            """,
            {"user_id": user_id}
        )
        
        if not sql_connectors:
            message = "Hey! I don't see any SQL database connections set up yet. No worries though! Just head over to the Connections page and create one, then we can get your data migrated! 🚀"
        else:
            # Extract table name from prompt if mentioned
            table_name = extract_table_name_from_prompt(prompt)
            table_context = f" for the **{table_name}** table" if table_name else ""
            
            # Add conversation context for better clarification
            conversation_context = ""
            if conversation_history:
                recent_messages = conversation_history[-3:] if len(conversation_history) > 3 else conversation_history
                conversation_context = "\n\nBased on our conversation, I can see you're working with data extraction. "
                if any("connection" in msg.get('text', '').lower() for msg in recent_messages):
                    conversation_context += "I noticed you mentioned connections before, so let me help you choose the right one. "
            
            connection_list = [
                f"- **{conn.get('name', 'Unknown')}** ({conn.get('connector_type', 'Unknown')})" 
                for conn in sql_connectors 
                if isinstance(conn, dict) and conn.get('name')
            ]
            message = f"I'd be happy to help you extract data{table_context}! 🎉{conversation_context}\n\nI can see you have multiple database connections, so I need to know which one you'd like me to use:\n\n" + "\n".join(connection_list) + f"\n\nPlease specify which connection you'd like me to use{table_context} and I'll get that data for you right away! 😊"
        
        async def generate():
            yield message
        
        return generate()
        
    except Exception as e:
        print(f"Error asking for clarification: {e}")
        async def generate():
            yield "I'm having trouble processing your request. Please try again."
        return generate()


def _normalize_doc_label(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _schema_column_names_from_row(table_row: Any) -> set:
    """Lowercase column names from sql_extracted_schemas.schema_data."""
    names: set = set()
    try:
        sd_raw = table_row.get("schema_data") if isinstance(table_row, dict) else table_row["schema_data"]
        schema_data_raw = json.loads(sd_raw) if isinstance(sd_raw, str) else sd_raw
        if isinstance(schema_data_raw, dict) and "columns" in schema_data_raw:
            for col in schema_data_raw.get("columns") or []:
                if isinstance(col, dict):
                    cn = (col.get("column_name") or "").strip()
                    if cn:
                        names.add(cn.lower())
        elif isinstance(schema_data_raw, list):
            for col in schema_data_raw:
                if isinstance(col, dict):
                    cn = (col.get("column_name") or "").strip()
                    if cn:
                        names.add(cn.lower())
    except Exception:
        pass
    return names


def _select_tables_for_mapping(
    tables: list,
    flat_data: dict,
    document_type: str,
    document_description: str,
) -> list:
    """
    Pick destination table(s) in sql_extracted_schemas for map-fields.

    1) Exact ai_categorization match (type + description)
    2) document_type only
    3) document_type substring / token overlap
    4) Best column-name overlap with extracted row keys
    """
    dt = _normalize_doc_label(document_type)
    dd = _normalize_doc_label(document_description)
    exact: list = []
    type_only: list = []
    fuzzy_type: list = []

    for table in tables:
        try:
            ai_cat = json.loads(table["ai_categorization"]) if table["ai_categorization"] else {}
        except (json.JSONDecodeError, TypeError):
            ai_cat = {}
        category = _normalize_doc_label(ai_cat.get("document_type"))
        category_desc = _normalize_doc_label(ai_cat.get("document_description"))
        if category == dt and category_desc == dd:
            exact.append(table)
        elif category == dt:
            type_only.append(table)
        elif dt and category and (dt in category or category in dt):
            fuzzy_type.append(table)

    if exact:
        print(f"[MAP_SQL] table match: exact categorization ({len(exact)} tables)")
        return exact
    if type_only:
        print(f"[MAP_SQL] table match: document_type only ({len(type_only)} tables)")
        return type_only
    if fuzzy_type:
        print(f"[MAP_SQL] table match: fuzzy document_type ({len(fuzzy_type)} tables)")
        return fuzzy_type

    flat_keys = {str(k).lower() for k in (flat_data or {}).keys() if k and not str(k).startswith("_")}
    if not flat_keys:
        return []

    best_row = None
    best_score = 0
    for table in tables:
        cols = _schema_column_names_from_row(table)
        if not cols:
            continue
        score = len(flat_keys & cols)
        if score > best_score:
            best_score = score
            best_row = table
    if best_row and best_score >= 1:
        tbl = dict(best_row).get("table_name")
        print(
            f"[MAP_SQL] table match: column overlap score={best_score} table={tbl} "
            f"flat_keys={sorted(flat_keys)[:12]}"
        )
        return [best_row]

    available = [dict(t).get("table_name") for t in tables if dict(t).get("table_name")]
    print(
        f"[MAP_SQL] no table match for document_type={document_type!r} "
        f"connector_tables={available[:20]}"
    )
    return []


async def map_to_database(
    flat_data: dict,
    document_type: str,
    document_description: str,
    destination_name: str,
    user_id,
    mapping_context_summary: Optional[str] = None,
    destination_connector_id: Optional[str] = None,
    explicit_target_table: Optional[str] = None,
    *,
    plan_pinned_entity: bool = False,
):
    """
    Map extracted data fields to SQL database table columns.
    
    Args:
        flat_data: First record from extracted data (dict with field names and values)
        document_type: Category/document type
        document_description: Description of the document
        destination_name: Database connection name
        user_id: User ID (UUID)
        
    Returns:
        dict: Mapping result with structure:
            {
                "destination": "sql",
                "destination_name": str,
                "connector_id": str,
                "mapping": dict,  # Field to column mappings
                "available_tables": dict  # Available tables and columns
            }
    """
    try:
        from db.connection import database
        import json
        from utils.document_category_match import reconcile_document_category, apply_reconcile_to_tuple

        pinned_table = (explicit_target_table or "").strip()
        if not pinned_table and not plan_pinned_entity:
            reconcile = await reconcile_document_category(
                user_id,
                "sql",
                destination_name,
                document_type,
                document_description,
                flat_data,
            )
            document_type, document_description = apply_reconcile_to_tuple(
                document_type, document_description, reconcile
            )

        # Step 1: Resolve destination SQL connector (UUID), not Salesforce source token
        from utils.destination_connector_resolve import resolve_sql_connector_record

        connector = None
        if destination_connector_id or (
            destination_name and destination_name != "unknown"
        ):
            resolved = await resolve_sql_connector_record(
                user_id,
                destination_name=destination_name,
                connector_id=destination_connector_id,
            )
            if not resolved:
                if destination_connector_id:
                    raise ValueError(
                        f"Database connector id '{destination_connector_id}' not found"
                    )
                raise ValueError(f"Database connector '{destination_name}' not found")
            connector = resolved

        connector_id = str(connector["id"]) if connector else None
        
        # Step 2: Find tables matching document_type and document_description in ai_categorization
        if connector_id:
            tables = await database.fetch_all("""
                SELECT 
                    table_name,
                    schema_data,
                    ai_categorization,
                    connector_id
                FROM sql_extracted_schemas
                WHERE connector_id = :connector_id AND user_id = :user_id
            """, values={"connector_id": str(connector_id), "user_id": str(user_id)})
        else:
            # Get all tables for user and filter by categorization
            tables = await database.fetch_all("""
                SELECT 
                    table_name,
                    schema_data,
                    ai_categorization,
                    connector_id
                FROM sql_extracted_schemas
                WHERE user_id = :user_id
            """, values={"user_id": str(user_id)})
        
        if not tables:
            hint = (
                f"No analyzed tables found for database '{destination_name}'. "
                "Open Connections → your database → analyze table schemas, then try mapping again."
            )
            raise ValueError(hint)

        if pinned_table:
            pt_norm = pinned_table.lower()
            matching_tables = [
                t
                for t in tables
                if (dict(t).get("table_name") or "").strip().lower() == pt_norm
            ]
            if not matching_tables:
                raise ValueError(
                    f"Pinned table '{pinned_table}' was not found in analyzed schemas "
                    f"for database '{destination_name}'."
                )
            print(f"[MAP_SQL] table match: plan-pinned {pinned_table!r}")
        else:
            matching_tables = _select_tables_for_mapping(
                tables, flat_data, document_type, document_description
            )

        if not matching_tables:
            sample_cats = []
            for t in tables[:8]:
                try:
                    ai_cat = json.loads(t["ai_categorization"]) if t["ai_categorization"] else {}
                    sample_cats.append(
                        f"{t['table_name']}: {ai_cat.get('document_type', '?')}"
                    )
                except Exception:
                    sample_cats.append(str(t.get("table_name")))
            raise ValueError(
                "Could not match your extracted data to a table in this database. "
                f"Expected categorization like document_type '{document_type}'. "
                f"Tables analyzed: {', '.join(sample_cats) or 'none'}. "
                "Re-run schema analysis on the destination database or adjust document type."
            )
        
        # Provisional connector (matching tables for one DB should share connector_id)
        table_name = matching_tables[0]["table_name"]
        connector_id = matching_tables[0]["connector_id"]

        # Get connector details
        connector = await database.fetch_one("""
            SELECT id, name, connector_type
            FROM sql_connectors
            WHERE id = :connector_id AND user_id = :user_id
        """, values={"connector_id": str(connector_id), "user_id": str(user_id)})
        
        if not connector:
            raise ValueError(f"Database connector not found for table: {table_name}")
        
        def _normalize_fk_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            if not isinstance(item, dict):
                return None
            col = (item.get("column_name") or item.get("column") or "").strip()
            ref_tbl = (item.get("referenced_table") or item.get("foreign_table_name") or "").strip()
            ref_col = (item.get("referenced_column") or item.get("foreign_column_name") or "").strip()
            if not col or not ref_tbl or not ref_col:
                return None
            return {
                "constraint_name": (item.get("constraint_name") or "").strip() or None,
                "column_name": col,
                "referenced_table": ref_tbl,
                "referenced_column": ref_col,
                "on_update": (item.get("on_update") or "").strip() or None,
                "on_delete": (item.get("on_delete") or "").strip() or None,
                "position": item.get("position"),
            }

        async def _resolve_foreign_keys_for_table(
            schema_payload: Any,
            tbl_name: str,
            connector_id_local: str,
            connector_type_local: str,
        ) -> List[Dict[str, Any]]:
            """
            Prefer cached schema_data.foreign_keys, fallback to runtime discovery when missing.
            """
            cached: List[Dict[str, Any]] = []
            if isinstance(schema_payload, dict):
                raw_fks = schema_payload.get("foreign_keys")
                if isinstance(raw_fks, list):
                    for r in raw_fks:
                        n = _normalize_fk_item(r)
                        if n:
                            cached.append(n)
            if cached:
                return cached
            discovered = await discover_foreign_keys(
                connector_id=str(connector_id_local),
                table_name=str(tbl_name),
                database_type=str(connector_type_local),
                user_id=str(user_id),
            )
            out: List[Dict[str, Any]] = []
            for r in discovered or []:
                n = _normalize_fk_item(r)
                if n:
                    out.append(n)
            return out

        tables_structure: Dict[str, Any] = {}

        async def _ingest_one_table_into_structure(row_in: Any) -> Optional[str]:
            """
            Parse sql_extracted_schemas row into tables_structure[tbl].
            Returns canonical table_name if added, None if skipped or failed.
            """
            row = dict(row_in) if not isinstance(row_in, dict) else row_in
            tbl_name = (row.get("table_name") or "").strip()
            if not tbl_name:
                return None
            if any(k.lower() == tbl_name.lower() for k in tables_structure):
                return None
            try:
                sd_raw = row.get("schema_data")
                schema_data_raw = json.loads(sd_raw) if isinstance(sd_raw, str) else sd_raw
                columns: Dict[str, Any] = {}
                if isinstance(schema_data_raw, dict) and "columns" in schema_data_raw:
                    for col in schema_data_raw["columns"]:
                        if isinstance(col, dict):
                            col_name = col.get("column_name", "")
                            if col_name:
                                is_nullable = col.get("is_nullable", True)
                                column_default = col.get("column_default", "")
                                is_primary_key = col.get("is_primary_key", False)
                                extra = col.get("extra", "") or ""
                                has_default = column_default not in (None, "", "NULL")
                                is_auto_increment = isinstance(extra, str) and "auto" in extra.lower()
                                columns[col_name] = {
                                    "data_type": col.get("data_type", "VARCHAR"),
                                    "is_nullable": is_nullable,
                                    "is_primary_key": is_primary_key,
                                    "column_comment": col.get("column_comment", ""),
                                    "column_default": column_default,
                                    "extra": extra,
                                    "is_required": (
                                        not is_nullable
                                        and not has_default
                                        and not is_primary_key
                                        and not is_auto_increment
                                    ),
                                }
                elif isinstance(schema_data_raw, list):
                    for col in schema_data_raw:
                        if isinstance(col, dict):
                            col_name = col.get("column_name", "")
                            if col_name:
                                is_nullable = col.get("is_nullable", True)
                                column_default = col.get("column_default", "")
                                is_primary_key = col.get("is_primary_key", False)
                                extra = col.get("extra", "") or ""
                                has_default = column_default not in (None, "", "NULL")
                                is_auto_increment = isinstance(extra, str) and "auto" in extra.lower()
                                columns[col_name] = {
                                    "data_type": col.get("data_type", "VARCHAR"),
                                    "is_nullable": is_nullable,
                                    "is_primary_key": is_primary_key,
                                    "column_comment": col.get("column_comment", col.get("description", "")),
                                    "column_default": column_default,
                                    "extra": extra,
                                    "is_required": (
                                        not is_nullable
                                        and not has_default
                                        and not is_primary_key
                                        and not is_auto_increment
                                    ),
                                }
                if not columns:
                    return None
                cid_use = str(row.get("connector_id") or connector_id)
                foreign_keys = await _resolve_foreign_keys_for_table(
                    schema_data_raw,
                    tbl_name,
                    cid_use,
                    str(connector["connector_type"]),
                )
                tables_structure[tbl_name] = {"columns": columns, "foreign_keys": foreign_keys}
                return tbl_name
            except (json.JSONDecodeError, AttributeError, TypeError) as e:
                print(
                    f"⚠️ Warning: Could not ingest schema for table {tbl_name}: {type(e).__name__}: {e}"
                )
                return None

        def _apply_sql_lookup_of_from_foreign_keys(
            mapping_obj: Dict[str, Any],
            primary_tbl: str,
            tables_meta: Dict[str, Any],
        ) -> None:
            """
            Add SQL relationship hints using existing lookup_of field (same contract as mapping UI):
            - Outgoing: when primary has FK column(s) toward target table T → lookup_of = "<primary>.<fk_col>".
            - Incoming: when another table U (U != primary) has an FK toward primary and mapping targets U
              (e.g. primary users, target user_plans with user_plans.user_id → users) → lookup_of = "<U>.<fk_col>".
            """
            primary_tbl = (primary_tbl or "").strip()
            if not primary_tbl or not isinstance(tables_meta, dict):
                return

            primary_meta = tables_meta.get(primary_tbl) or {}
            p_fks = primary_meta.get("foreign_keys") if isinstance(primary_meta.get("foreign_keys"), list) else []

            fk_by_ref_table: Dict[str, str] = {}
            for fk in p_fks:
                if not isinstance(fk, dict):
                    continue
                rtbl = (fk.get("referenced_table") or "").strip()
                c = (fk.get("column_name") or "").strip()
                if rtbl and c and rtbl.lower() not in fk_by_ref_table:
                    fk_by_ref_table[rtbl.lower()] = c

            # Child / bridge tables in scope that reference primary (for primary=parent, target=child)
            fk_on_table_toward_primary: Dict[str, tuple[str, str]] = {}
            for u_name, meta in tables_meta.items():
                u_clean = (u_name or "").strip()
                if not u_clean or u_clean.lower() == primary_tbl.lower():
                    continue
                for fk in (meta.get("foreign_keys") or []):
                    if not isinstance(fk, dict):
                        continue
                    ref = (fk.get("referenced_table") or "").strip().lower()
                    if ref != primary_tbl.lower():
                        continue
                    col = (fk.get("column_name") or "").strip()
                    if col and u_clean.lower() not in fk_on_table_toward_primary:
                        fk_on_table_toward_primary[u_clean.lower()] = (u_clean, col)

            for _, v in (mapping_obj or {}).items():
                items = v if isinstance(v, list) else [v]
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    if (it.get("lookup_of") or "").strip():
                        continue
                    tgt_table = (it.get("table") or it.get("object") or it.get("sheet") or "").strip()
                    if not tgt_table:
                        continue
                    if tgt_table.lower() == primary_tbl.lower():
                        continue
                    fk_col = fk_by_ref_table.get(tgt_table.lower())
                    if fk_col:
                        it["lookup_of"] = f"{primary_tbl}.{fk_col}"
                        continue
                    inc = fk_on_table_toward_primary.get(tgt_table.lower())
                    if inc:
                        owner, col = inc
                        it["lookup_of"] = f"{owner}.{col}"

        # Step 4: Build table structure from category-matched tables
        for table in matching_tables:
            await _ingest_one_table_into_structure(table)

        if not tables_structure:
            raise ValueError(f"No valid table schemas found")

        # Step 4b: Enrich available_tables like Salesforce (base + lookup-related tables on same connector)
        structure_lower = {k.lower() for k in tables_structure}

        async def _fetch_schema_row_by_name(name: str) -> Optional[Dict[str, Any]]:
            nm = (name or "").strip()
            if not nm:
                return None
            row = await database.fetch_one(
                """
                SELECT table_name, schema_data, connector_id
                FROM sql_extracted_schemas
                WHERE user_id = CAST(:uid AS uuid)
                  AND connector_id = CAST(:cid AS uuid)
                  AND LOWER(TRIM(table_name::text)) = LOWER(TRIM(:tbl))
                ORDER BY extracted_at DESC NULLS LAST
                LIMIT 1
                """,
                {"uid": str(user_id).strip(), "cid": str(connector_id).strip(), "tbl": nm},
            )
            return dict(row) if row else None

        # 4b-i: Follow outgoing FK referenced_table targets (e.g. user_plans → users)
        for _ in range(16):
            added = 0
            snap = list(tables_structure.items())
            for _tn, meta in snap:
                for fk in (meta.get("foreign_keys") or []):
                    if not isinstance(fk, dict):
                        continue
                    ref = (fk.get("referenced_table") or "").strip()
                    if not ref or ref.lower() in structure_lower:
                        continue
                    srow = await _fetch_schema_row_by_name(ref)
                    if srow and await _ingest_one_table_into_structure(srow):
                        structure_lower.add(ref.lower())
                        added += 1
            if not added:
                break

        # 4b-ii: Pull tables on this connector that reference any table already in structure (incoming FKs)
        if connector_id and tables:
            present = {k.lower() for k in tables_structure}
            cid_scope = str(connector_id).strip()
            for table in tables:
                tr = dict(table)
                if str(tr.get("connector_id") or "").strip() != cid_scope:
                    continue
                tnm = (tr.get("table_name") or "").strip()
                if not tnm or tnm.lower() in present:
                    continue
                try:
                    sd_raw = tr.get("schema_data")
                    raw = json.loads(sd_raw) if isinstance(sd_raw, str) else sd_raw
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
                fks_try = await _resolve_foreign_keys_for_table(
                    raw,
                    tnm,
                    str(tr.get("connector_id") or connector_id),
                    str(connector["connector_type"]),
                )
                touches = False
                for fk in fks_try or []:
                    if not isinstance(fk, dict):
                        continue
                    rlow = (fk.get("referenced_table") or "").strip().lower()
                    if rlow and rlow in present:
                        touches = True
                        break
                if touches and await _ingest_one_table_into_structure(tr):
                    present.add(tnm.lower())
                    structure_lower.add(tnm.lower())

        # 4b-iii: Another outgoing pass (new tables may reference additional parents)
        structure_lower = {k.lower() for k in tables_structure}
        for _ in range(8):
            added = 0
            snap = list(tables_structure.items())
            for _tn, meta in snap:
                for fk in (meta.get("foreign_keys") or []):
                    if not isinstance(fk, dict):
                        continue
                    ref = (fk.get("referenced_table") or "").strip()
                    if not ref or ref.lower() in structure_lower:
                        continue
                    srow = await _fetch_schema_row_by_name(ref)
                    if srow and await _ingest_one_table_into_structure(srow):
                        structure_lower.add(ref.lower())
                        added += 1
            if not added:
                break

        # Prefer primary among category-matched tables: favor the one with most FKs into any enriched table
        fk_scope_lower = {k.lower() for k in tables_structure}
        best_row = matching_tables[0]
        best_score = -1
        for t in matching_tables:
            tnm = t["table_name"]
            meta = tables_structure.get(tnm) or {}
            fks = meta.get("foreign_keys") or []
            score = 0
            for fk in fks:
                if not isinstance(fk, dict):
                    continue
                rt = (fk.get("referenced_table") or "").strip().lower()
                if rt and rt in fk_scope_lower:
                    score += 1
            if score > best_score:
                best_score = score
                best_row = t
        table_name = best_row["table_name"]
        connector_id = best_row["connector_id"]

        connector = await database.fetch_one(
            """
            SELECT id, name, connector_type
            FROM sql_connectors
            WHERE id = :connector_id AND user_id = :user_id
            """,
            values={"connector_id": str(connector_id), "user_id": str(user_id)},
        )
        if not connector:
            raise ValueError(f"Database connector not found for table: {table_name}")

        # Removed verbose debug log: total tables structure summary
        
        # Mapping build is free; no credit deduction

        # Step 6: Call AI to create intelligent mapping
        mapping = await call_database_mapping_agent(
            flat_data=flat_data,
            document_type=document_type,
            database_name=connector["name"],
            database_type=connector["connector_type"],
            primary_table=table_name,
            tables_structure=tables_structure,
            mapping_context_summary=mapping_context_summary
        )

        from utils.mapping_destination_validate import (
            filter_sql_mapping_to_destination_schema,
        )

        mapping = filter_sql_mapping_to_destination_schema(
            mapping, tables_structure, primary_table=table_name
        )
        
        # Step 7: Use real data for transformation analysis (no masking)
        # Data is only sent for analysis, not stored, and data sharing with OpenAI is disabled
        sample_data = [flat_data]
        
        # Step 8: Analyze if transformations are needed (e.g., field splitting)
        transformation_analysis = await analyze_database_mapping_transformations(
            mapping=mapping,
            flat_data=flat_data,
            tables_structure=tables_structure,
            sample_data=sample_data,
            primary_table=table_name,
        )
        
        transformation_rules = transformation_analysis.get("transformation_rules", {"fields": []})
        enhanced_mapping_entries = transformation_analysis.get("enhanced_mapping", {})
        
        # Step 9: Update mapping with enhanced entries
        # Handle both splitting (arrays) and combining (updated mapping entries with flags)
        if enhanced_mapping_entries:
            # Removed verbose debug log: updating mapping entries count
            for key, value in enhanced_mapping_entries.items():
                # Check if this is a split field (array) or combined field (dict with flags)
                if isinstance(value, list):
                    # Split field: Replace single object with array
                    # Ensure each item in array has is_split_field flag
                    for item in value:
                        if isinstance(item, dict):
                            item["is_split_field"] = True
                    # Replace the original mapping entry with the array
                    mapping[key] = value
                elif isinstance(value, dict):
                    # Combined field: Update mapping entry with combine flags
                    # The value should have is_combined_field, combine_group, combine_order
                    if value.get("is_combined_field"):
                        mapping[key] = value
                    else:
                        # Regular mapping update (shouldn't happen, but handle gracefully)
                        mapping[key] = value
                else:
                    print(f"   ⚠️ Warning: enhanced_mapping entry '{key}' has unexpected type {type(value)}, skipping")

        mapping = filter_sql_mapping_to_destination_schema(
            mapping, tables_structure, primary_table=table_name
        )

        # Step 9.1: Preserve a Salesforce-like relationship hint contract for SQL mappings.
        # This does not change SQL execution yet, but keeps architect/mapping semantics consistent.
        _apply_sql_lookup_of_from_foreign_keys(mapping, table_name, tables_structure)

        from utils.sql_mapping_sanitize import sanitize_sql_mapping_for_migration

        mapping = sanitize_sql_mapping_for_migration(
            mapping,
            table_name,
            tables_structure=tables_structure,
        )

        # Step 10: Return mapping result (mapping_transformation = panel splits/combines)
        from utils.migration_rules_utils import dual_publish_mapping_transform_api

        from utils.mapping_transform_context import source_fields_from_flat_data

        result = {
            "destination": "sql",
            "destination_name": connector["name"],
            "connector_id": str(connector_id),
            "primary_table": table_name,
            "document_type": document_type,
            "document_description": document_description,
            "mapping": mapping,
            "available_tables": tables_structure,
            "source_fields": source_fields_from_flat_data(flat_data if isinstance(flat_data, dict) else {}),
        }
        dual_publish_mapping_transform_api(result, transformation_rules)
        
        # Removed verbose debug logs: SQL database mapping result summary
        
        return result
        
    except Exception as e:
        print(f"❌ Error in map_to_database: {e}")
        import traceback
        traceback.print_exc()
        raise


async def discover_foreign_keys(connector_id: str, table_name: str, database_type: str, user_id):
    """
    Discover foreign key relationships for a table by executing SQL queries.
    
    Args:
        connector_id: Database connector ID
        table_name: Name of the table
        connector_type: Type of connector (mysql, postgresql, etc.)
        user_id: User ID
        
    Returns:
        list: List of foreign key relationships
    """
    try:
        from db.connection import database
        from services.sql_connector_service import SQLConnectorService
        
        # Get connector details
        connector_dict = await database.fetch_one("""
            SELECT 
                id, name, host, port, database_name, username, password_encrypted, 
                connector_type, ssl_enabled, additional_config
            FROM sql_connectors
            WHERE id = :connector_id AND user_id = :user_id
        """, values={"connector_id": connector_id, "user_id": str(user_id)})
        
        if not connector_dict:
            print(f"⚠️ Connector not found for foreign key discovery")
            return []
        
        # Create connector instance
        sql_service = SQLConnectorService()
        connector_dict = dict(connector_dict)
        config = sql_service.prepare_connector_config(connector_dict)
        connector_type = connector_dict.get('connector_type') or database_type
        sql_connector = sql_service.create_connector_instance(connector_type, config)
        
        # Build query based on database type (use connector_type from database, fallback to parameter)
        db_type = connector_type.lower() if connector_type else database_type.lower()
        if db_type in ['postgresql', 'postgres']:
            fk_query = f"""
                SELECT
                    kcu.column_name,
                    ccu.table_name AS foreign_table_name,
                    ccu.column_name AS foreign_column_name
                FROM information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage AS ccu
                    ON ccu.constraint_name = tc.constraint_name
                    AND ccu.table_schema = tc.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                    AND tc.table_name = '{table_name}';
            """
        elif db_type == 'mysql':
            fk_query = f"""
                SELECT
                    COLUMN_NAME as column_name,
                    REFERENCED_TABLE_NAME as foreign_table_name,
                    REFERENCED_COLUMN_NAME as foreign_column_name
                FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
                WHERE TABLE_NAME = '{table_name}'
                    AND REFERENCED_TABLE_NAME IS NOT NULL
                    AND TABLE_SCHEMA = DATABASE();
            """
        elif db_type in ['mssql', 'sqlserver']:
            fk_query = """
                SELECT
                    c.name AS column_name,
                    OBJECT_NAME(fk.referenced_object_id) AS foreign_table_name,
                    rc.name AS foreign_column_name
                FROM sys.foreign_key_columns fk
                INNER JOIN sys.columns c ON fk.parent_object_id = c.object_id AND fk.parent_column_id = c.column_id
                INNER JOIN sys.columns rc ON fk.referenced_object_id = rc.object_id AND fk.referenced_column_id = rc.column_id
                WHERE OBJECT_NAME(fk.parent_object_id) = %s
            """
            # Execute with param for table_name (connector converts %s to ? for pyodbc)
            try:
                results = await sql_connector.execute_query(fk_query, (table_name,))
            except Exception as fk_err:
                print(f"⚠️ Warning: Could not discover foreign keys: {fk_err}")
                results = []
            foreign_keys = [{"column": row.get("column_name"), "referenced_table": row.get("foreign_table_name"), "referenced_column": row.get("foreign_column_name")} for row in results]
            print(f"✅ Discovered {len(foreign_keys)} foreign keys for table: {table_name}")
            await sql_connector.close_connection()
            return foreign_keys
        else:
            print(f"⚠️ Foreign key discovery not supported for database type: {database_type}")
            return []
        
        # Execute query
        try:
            results = await sql_connector.execute_query(fk_query)
            
            foreign_keys = []
            for row in results:
                foreign_keys.append({
                    "column": row.get("column_name"),
                    "referenced_table": row.get("foreign_table_name"),
                    "referenced_column": row.get("foreign_column_name")
                })
            
            print(f"✅ Discovered {len(foreign_keys)} foreign keys for table: {table_name}")
            return foreign_keys
            
        except Exception as e:
            print(f"⚠️ Warning: Could not discover foreign keys: {e}")
            return []
        finally:
            await sql_connector.close_connection()
            
    except Exception as e:
        print(f"⚠️ Warning: Error in discover_foreign_keys: {e}")
        import traceback
        traceback.print_exc()
        return []


async def analyze_database_mapping_transformations(
    mapping: dict,
    flat_data: dict,
    tables_structure: dict,
    sample_data: list,
    *,
    primary_table: Optional[str] = None,
) -> dict:
    """
    Analyze the mapping to determine if field transformations are needed (e.g., name splitting).
    Returns transformation_rules and enhanced_mapping with new split field entries for SQL Database.
    """
    try:
        from services.architect_agent import async_openai_call
        from utils.openai_helpers import parse_openai_json_response
        import json

        from utils.mapping_audit import resolve_post_map_transforms
        from utils.mapping_transform_context import (
            build_transform_llm_context_section,
            classify_sql_destination_name_shape,
        )

        dest_name_shape = classify_sql_destination_name_shape(tables_structure or {})
        resolved = resolve_post_map_transforms(
            flat_data=flat_data or {},
            mapping=mapping or {},
            destination_name_shape=dest_name_shape,
            destination_type="sql",
            base_container=primary_table,
            base_objects=[primary_table] if primary_table else None,
            sample_data=sample_data,
        )
        if resolved.get("skip_transform_llm") and resolved.get("enhanced_mapping"):
            fields = (resolved.get("transformation_rules") or {}).get("fields") or []
            action = (fields[0].get("action") if fields else "") or ""
            if str(action).startswith("split"):
                print("🔧 [MAP_SQL_TRANSFORM] Using programmatic compound name → first/last split (no transform LLM)")
            else:
                print("🔧 [MAP_SQL_TRANSFORM] Using programmatic first+last → Name combine (no transform LLM)")
            return {
                "transformation_rules": resolved.get("transformation_rules") or {"fields": []},
                "enhanced_mapping": resolved.get("enhanced_mapping") or {},
                "mapping_audit": resolved.get("mapping_audit"),
            }
        if resolved.get("skip_transform_llm"):
            print("🔧 [MAP_SQL_TRANSFORM] Skipping transform LLM — destination fields already aligned")
            return {
                "transformation_rules": {"fields": []},
                "enhanced_mapping": {},
                "mapping_audit": resolved.get("mapping_audit"),
            }

        mapping_json = json.dumps(mapping, indent=2)
        sample_data_json = json.dumps(sample_data, indent=2)
        tables_structure_json = json.dumps(tables_structure, indent=2)
        transform_context_section = build_transform_llm_context_section(
            flat_data=flat_data or {},
            mapping=mapping or {},
            destination_name_shape=dest_name_shape,
            destination_type="sql",
        )
        
        prompt = f"""You are Archian AI, analyzing a field mapping to determine if transformations are needed for SQL Database.

{transform_context_section}

📋 **Current Mapping:**
{mapping_json}

📦 **Sample Source Data:**
{sample_data_json}

📚 **Destination Database Tables Structure:**
{tables_structure_json}

🎯 **Your Task:**
Analyze the mapping and determine if any transformations are needed:
1. **SPLITTING**: One source field → Multiple destination columns
2. **COMBINING**: Multiple source fields → One destination column

**🚨 CRITICAL - DO NOT SPLIT IF ALREADY SPLIT (combining may still be required):**
Before suggesting any split, check if the source data is ALREADY split:

1. **Name Fields - Check Source Field Names:**
   - If source already has separate fields like: "first_name", "last_name", "firstName", "lastName", "First Name", "Last Name", "fname", "lname"
   - AND they map to "first_name" and "last_name" destination columns respectively
   - Then DO NOT suggest splitting - the data is already correctly split
   - **But if destination only has a generic name/full_name column (see context above), DO combine those source fields** — even if omitted from Current Mapping

2. **Address Fields - Check Source Field Names:**
   - If source already has separate fields like: "street", "city", "state", "postal_code", "address_line_1", "address_line_2"
   - AND they map to street, city, state, postal_code destination columns respectively
   - Then DO NOT suggest splitting - the data is already correctly split

**Key Analysis Points (ONLY when source is NOT already split):**

1. **Name Field Splitting - ONLY when source contains COMPOUND names**: 
   - If a source field (e.g., "Contact Name", "Full Name", "Name", "Employee Name") is mapped to a SINGLE column (e.g., `"last_name"`)
   - **Also split when** fields like **CustomerName**, **ContactName**, or **FullName** map to **only** `first_name` **or** only `last_name` while the sample value has **2+ words** and the table has **both** `first_name` and `last_name` (the first mapper often picks one column by mistake).
   - AND the source field name suggests it contains FULL/COMPOUND names (not already split)
   - AND the sample data value contains multiple words separated by spaces (e.g., "John Doe", "Jane Smith", "Mary Ann Johnson")
   - AND the destination table has "first_name", "last_name", "middle_name" columns available
   - THEN it SHOULD be split into multiple columns (first_name + last_name)
   - Example: "Contact Name" with value "John Doe" → split into "first_name"="John", "last_name"="Doe"

2. **Address Field Splitting - ONLY when source contains COMPOUND addresses**:
   - If a source field (e.g., "Address", "Full Address", "Mailing Address") contains full addresses (e.g., "123 Main St, City, State, ZIP")
   - AND the source field name suggests it contains FULL/COMPOUND addresses (not already split)
   - AND the sample data value contains multiple parts separated by commas or spaces
   - AND destination table has street, city, state, postal_code, country columns
   - THEN it SHOULD be split

**3. Field Combining - When source has SEPARATE fields but destination expects COMBINED column**:
   - If source has separate fields like: "first_name", "last_name", "firstName", "lastName", "First Name", "Last Name"
   - AND they both map to the SAME destination column (e.g., `"full_name"` or `"name"`)
   - OR destination has a single "full_name", "name", "display_name" column but source has separate first/last
   - AND the destination table does NOT have separate "first_name" and "last_name" columns (check tables_structure)
   - THEN they SHOULD be combined into a single column
   - Example: "first_name" + "last_name" → "full_name" = "John Doe"
   
   **Combining Detection Rules:**
   - ✅ DO combine: Source has "first_name" and "last_name" both mapping to same "full_name" destination column
   - ✅ DO combine: Source has separate fields but destination only has "name" or "full_name" column
   - ✅ DO combine: Source has "street", "city", "state", "postal_code" but destination has single "address" column
   - ❌ DO NOT combine: If destination has separate "first_name" and "last_name" columns (use splitting instead)
   - ❌ DO NOT combine: If source already has a combined "full_name" field
   - ❌ DO NOT combine: If source fields map to different destination columns (they should stay separate)

**Summary Rules:**
- ✅ DO split: Source field names like "Contact Name", "Full Name", "Address", "Mailing Address" with compound values
- ❌ DO NOT split: Source field names like "first_name", "last_name", "street", "city" - they're already split!
- ❌ DO NOT split: If source already has separate fields mapping to different destination columns (e.g., "first_name" → "first_name", "last_name" → "last_name")
- ❌ DO NOT split: If the sample data value is a single word (e.g., "Doe" only, not "John Doe")
- ✅ DO combine: Multiple source fields (e.g., "first_name", "last_name") mapping to same destination column (e.g., "full_name")
- ❌ DO NOT combine: If destination has separate columns available (e.g., "first_name", "last_name") - use splitting instead

**Output Format:**
Return a JSON object with two keys:
1. `transformation_rules`: Transformation rules following the format below
2. `enhanced_mapping`: Mapping entries with the SAME key as original, but value is an ARRAY of mapping objects when splitting is needed

**IMPORTANT - Enhanced Mapping Structure:**
- Use the EXACT same source field key from the original mapping
- If a field needs to be split, replace its single mapping object with an ARRAY of mapping objects
- Each object in the array should have `"is_split_field": true` flag
- If no splitting is needed, return empty object `{{}}`

**Transformation Rules Format:**
```json
{{
  "transformation_rules": {{
    "fields": [
      {{
        "source_field": "Contact Name",
        "action": "split_2",
        "split_char": " ",
        "output_fields": ["first_name", "last_name"]
      }},
      {{
        "source_fields": ["first_name", "last_name"],
        "action": "combine",
        "separator": " ",
        "output_field": "full_name"
      }}
    ]
  }},
  "enhanced_mapping": {{
    "Contact Name": [
      {{
        "column": "first_name",
        "table": "users",
        "is_duplicate_check": false,
        "is_split_field": true
      }},
      {{
        "column": "last_name",
        "table": "users",
        "is_duplicate_check": true,
        "is_split_field": true
      }}
    ],
    "first_name": {{
      "column": "full_name",
      "table": "users",
      "is_duplicate_check": false,
      "is_combined_field": true,
      "combine_group": "name",
      "combine_order": 0
    }},
    "last_name": {{
      "column": "full_name",
      "table": "users",
      "is_duplicate_check": false,
      "is_combined_field": true,
      "combine_group": "name",
      "combine_order": 1
    }}
  }}
}}
```

**Key Rules for Enhanced Mapping:**
- **For Splitting**: Use the EXACT source field key from current mapping (e.g., "Contact Name", not "Contact Name (first_name)")
  - Value must be an ARRAY when splitting (even if splitting into 2 columns, it's still an array)
  - Include `"is_split_field": true` in EACH mapping object within the array
  - Copy all properties from original mapping (like `table`, `is_duplicate_check`, etc.) to each array item
  
- **For Combining**: Update the mapping entries for EACH source field that should be combined
  - Use the EXACT source field keys from current mapping (e.g., "first_name", "last_name")
  - Each source field mapping should point to the SAME destination column
  - Add `"is_combined_field": true` to each mapping entry
  - Add `"combine_group": "name"` (or similar identifier) to group fields that should be combined together
  - Add `"combine_order": 0, 1, 2...` to specify the order in which fields should be combined
  - The destination column should be the same for all fields in the combine_group
  - Use `column` (not `field`) for database column names
  - Use `table` for table name
  
- **If no transformations are needed, return empty enhanced_mapping object `{{}}`**
- **Only include fields in enhanced_mapping that NEED transformation (splitting or combining)**

**Actions Available:**
- `split_2`: Split into 2 parts (e.g., first_name, last_name)
- `split_3`: Split into 3 parts (e.g., first_name, middle_name, last_name)
- `split_address`: Split address into components (street, city, state, postal_code, country)
- `combine`: Combine multiple source fields into one destination column (e.g., first_name + last_name → full_name)

**Important Rules:**
- Only suggest splitting if the destination table has the component columns available (check tables_structure)
- Use exact column names from tables_structure
- If no transformations are needed, return empty arrays: `{{"transformation_rules": {{"fields": []}}, "enhanced_mapping": {{}}}}`
- Use exact field names from the mapping keys as `source_field` in transformation_rules

Return ONLY valid JSON, no markdown or explanations.
"""
        
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.FIELD_MAPPING,
            messages=[
                {
                    "role": "system",
                    "content": "You are Archian AI, specializing in data transformation analysis for SQL Database field mappings."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.0,
        )
        
        reply = response.choices[0].message.content
        result = parse_openai_json_response(reply)
        
        # Ensure we have the expected structure
        transformation_rules = result.get("transformation_rules", {"fields": []})
        enhanced_mapping = result.get("enhanced_mapping", {})

        from utils.mapping_structure import resolve_base_container
        from utils.mapping_transform_context import apply_post_llm_transform_reconciliation

        sql_base = resolve_base_container(
            mapping=mapping or {},
            destination_type="sql",
        )
        reconciled = apply_post_llm_transform_reconciliation(
            mapping=mapping or {},
            flat_data=flat_data or {},
            destination_name_shape=dest_name_shape,
            destination_type="sql",
            transformation_rules=transformation_rules,
            enhanced_mapping=enhanced_mapping,
            sample_data=sample_data,
            base_container=sql_base,
        )

        return {
            "transformation_rules": reconciled["transformation_rules"],
            "enhanced_mapping": reconciled.get("enhanced_mapping") or {},
        }
        
    except Exception as e:
        print(f"⚠️ Error analyzing Database transformations: {e}")
        import traceback
        traceback.print_exc()
        # Return empty on error - mapping will work without transformations
        return {
            "transformation_rules": {"fields": []},
            "enhanced_mapping": {}
        }


async def call_database_mapping_agent(flat_data: dict, document_type: str, database_name: str, 
                                      database_type: str, primary_table: str, tables_structure: dict, mapping_context_summary: Optional[str] = None):
    """
    Use AI to intelligently map extracted fields to SQL database columns.
    
    Args:
        flat_data: First record from extracted data
        document_type: Category/document type
        database_name: Name of the database connection
        database_type: Type of database (mysql, postgresql, etc.)
        primary_table: Primary table name
        tables_structure: Dictionary of tables and their columns
        
    Returns:
        dict: Mapping of extracted fields to SQL columns
    """
    try:
        import json
        import asyncio
        
        # Use real data directly for mapping analysis (no masking)
        # Data is only sent for analysis, not stored, and data sharing with OpenAI is disabled
        
        # Build example mapping (include lookup_of pattern for related tables — mirrors Salesforce map prompt)
        field_names = list(flat_data.keys())
        first_field = field_names[0] if len(field_names) > 0 else "email"
        second_field = field_names[1] if len(field_names) > 1 else "name"
        third_field = field_names[2] if len(field_names) > 2 else "related_profile_email"

        example_obj: Dict[str, Any] = {
            first_field: {
                "table": primary_table,
                "column": "plan_type",
                "data_type": "VARCHAR(50)",
                "value": flat_data.get(first_field, ""),
                "is_duplicate_check": False,
            },
            second_field: {
                "table": primary_table,
                "column": "stripe_customer_id",
                "data_type": "VARCHAR(100)",
                "value": flat_data.get(second_field, ""),
                "is_duplicate_check": True,
            },
        }
        # Illustrative related-table row: when primary holds the FK toward users, set lookup_of on users.* mappings
        example_obj[third_field] = {
            "table": "users",
            "column": "email",
            "data_type": "VARCHAR(255)",
            "value": flat_data.get(third_field, "") if third_field in flat_data else "",
            "is_duplicate_check": False,
            "lookup_of": f"{primary_table}.user_id",
        }

        example_mapping = json.dumps(example_obj, indent=2)
        example_mapping_safe = example_mapping.replace("{", "{{").replace("}", "}}")

        rel_lines: List[str] = []
        for tnm, meta in sorted((tables_structure or {}).items()):
            for fk in (meta.get("foreign_keys") or []):
                if not isinstance(fk, dict):
                    continue
                c = (fk.get("column_name") or "").strip()
                r = (fk.get("referenced_table") or "").strip()
                rc = (fk.get("referenced_column") or "").strip()
                if c and r:
                    rel_lines.append(f"  - {tnm}.{c} → {r}" + (f".{rc}" if rc else ""))
        relationship_cheat_sheet = (
            "\n".join(rel_lines[:50])
            if rel_lines
            else "  (No foreign_keys listed in tables_structure — still infer sensible tables from names/types.)"
        )

        mapping_context_section = ""
        if mapping_context_summary:
            mapping_context_section = f"""
📝 **User's Extraction Request Context:**
{mapping_context_summary}

Use this context to:
- Prioritize tables/columns mentioned by the user
- Understand the user's intent and requirements
- Make more accurate mapping decisions
"""
        prompt = f"""
You are a SQL database data mapping expert. Map extracted data fields to the most appropriate database table columns.

✅ **DATA USAGE**: The data below contains real field names and values for mapping analysis.

📄 Document Type: {document_type}
🗄️ Database: {database_name} ({database_type})
📋 **Primary Table (extract / driving row)**: {primary_table}
{mapping_context_section}

🚨 **CRITICAL FIELD NAME REQUIREMENTS**:
- Each key in the final JSON must be the EXACT FIELD NAME from the extracted data
- Use field names exactly as they appear in the sample data
- Do NOT rename or transform field names

📊 Available Tables and Columns:
{json.dumps(tables_structure, indent=2)}

📎 **Known foreign keys (use for relationship decisions)**:
{relationship_cheat_sheet}

🔍 **Duplicate Check Tagging** (Important for Updates):
For every field in the final mapping:
1. Determine if the field should be used to find existing rows before updating:
   - If yes → add `"is_duplicate_check": true`
   - If no → add `"is_duplicate_check": false`

2. Best practices for duplicate checking:
   - Prefer unique identifiers: Primary keys, Email, Phone, ID numbers, Employee ID
   - Avoid non-unique fields: Notes, Description, Address, Created Date
   - For person data: Email > Phone > Unique ID > Full Name
   - For company data: Company Name, Tax ID, Account Number

3. Your output JSON **must include `is_duplicate_check` for every field**.

🔗 **Relationship field `lookup_of` (REQUIRED when `"table"` ≠ Primary Table)** — same idea as Salesforce `lookup_of`:
- When a source field maps to columns on a **different** table than **Primary Table** `{primary_table}`, you MUST add a string field **`lookup_of`** on that mapping object.
- **`lookup_of` format**: `SomeTable.fk_column` where `fk_column` is a real foreign-key column listed under `SomeTable` in `foreign_keys` whose `referenced_table` matches the **Primary Table** OR the **target** table, following these rules:
  1. **Primary owns FK to target** (e.g. `user_plans.user_id` → `users`): if `"table"` is `users` and Primary is `user_plans`, set `"lookup_of": "{primary_table}.user_id"` using the actual column name from `foreign_keys` on `{primary_table}` that references `users`.
  2. **Target owns FK to primary** (child row points at parent): if `"table"` is the child (e.g. `user_plans`) and Primary is `users`, set `"lookup_of": "user_plans.user_id"` (child table + column from `foreign_keys` where `referenced_table` is the Primary table).
- **Do NOT** invent column names: copy from the `foreign_keys` arrays in **Available Tables** above.
- **Do NOT** add `lookup_of` when `"table"` equals Primary Table (direct columns on the driving row).
- **Do NOT** map the same logical person field only to `user_plans` if it clearly belongs on `users` — map First/Last/Email to `users` and use `lookup_of` as above.
- Omit `is_foreign_key` unless you are sure; downstream uses `lookup_of` for relationship semantics.

✅ Example Output Format (note `lookup_of` on the related-table mapping):
{example_mapping_safe}

📦 Extracted Data for Mapping:
{json.dumps(flat_data, indent=2)}

🎯 Mapping Rules:
1. Map each extracted field to the most semantically similar column on the correct table.
2. Prefer the **Primary Table** for facts that belong on that row; map profile / identity fields to parent tables (e.g. `users`) when the join model implies it.
3. Match data types appropriately (VARCHAR, INT, DATE, BOOLEAN, etc.).
4. Include the `is_duplicate_check` flag for each field.
5. When mapping to a non-primary table, **always** include `lookup_of` per the rules above.
6. Return ONLY valid JSON, no markdown, no explanations

Return the mapping as a single flat JSON object.
"""

        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.FIELD_MAPPING,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a SQL database data mapping expert. Return only valid JSON. "
                        "When a field maps to a table other than the Primary Table, include lookup_of "
                        "using the foreign_keys metadata (SomeTable.fk_column)."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )

        # Parse response using centralized JSON parsing function
        response_text = response.choices[0].message.content.strip()

        from utils.openai_helpers import parse_openai_json_response

        try:
            mapping = parse_openai_json_response(response_text)
        except (ValueError, json.JSONDecodeError) as e:
            print(f"❌ Error parsing SQL mapping JSON response: {e}")
            print(f"⚠️ Response (first 200 chars): {response_text[:200]}")
            raise

        return mapping
        
    except Exception as e:
        print(f"❌ Error in call_database_mapping_agent: {e}")
        import traceback
        traceback.print_exc()
        raise