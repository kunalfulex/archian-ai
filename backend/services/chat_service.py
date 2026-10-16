import asyncio
import json
import logging
import uuid
import os
from datetime import datetime, timezone
from typing import Any, Awaitable, Dict, List, Optional, Tuple

from db.connection import database
from openai import OpenAI

logger = logging.getLogger(__name__)

_PLANNER_INTERACTIVE_EVENTS = frozenset(
    {
        "planner_clarification_required",
        "planner_plan_ready",
        "planner_plan_execution_clarification",
    }
)


def _message_is_planner_resolved(metadata: Dict[str, Any]) -> bool:
    return bool(metadata.get("planner_resolved") or metadata.get("plannerResolved"))


def _slim_planner_pending(pending: Dict[str, Any]) -> Dict[str, Any]:
    """Drop redundant nested blobs from planner_pending for API responses."""
    slim = dict(pending)
    slim.pop("conversation_history", None)
    classification = slim.get("classification")
    if isinstance(classification, dict):
        trimmed = dict(classification)
        for heavy in (
            "_plan_requirements",
            "_binding_requirements",
            "_pipeline_tokens",
            "planning_artifact",
            "learning_context",
            "_conversation_session_facts",
        ):
            trimmed.pop(heavy, None)
        slim["classification"] = trimmed
    plan = slim.get("execution_plan")
    if isinstance(plan, dict):
        meta = plan.get("metadata")
        if isinstance(meta, dict) and isinstance(meta.get("classification"), dict):
            plan = dict(plan)
            plan_meta = dict(meta)
            plan_meta.pop("classification", None)
            plan["metadata"] = plan_meta
            slim["execution_plan"] = plan
    return slim


def _slim_message_metadata_for_api(
    metadata: Optional[Dict[str, Any]],
    *,
    keep_full_planner_pending: bool,
) -> Optional[Dict[str, Any]]:
    if not isinstance(metadata, dict) or not metadata:
        return metadata
    out = dict(metadata)
    pending = out.get("planner_pending")
    if not isinstance(pending, dict):
        return out
    if keep_full_planner_pending and not _message_is_planner_resolved(out):
        out["planner_pending"] = _slim_planner_pending(pending)
        return out
    out["planner_pending"] = {
        k: pending[k]
        for k in (
            "plan_id",
            "original_prompt",
            "clarifications",
            "job_id",
            "continuation",
            "session_snapshot",
            "intent_state",
            "pipeline_state",
            "extract_step_clarification",
        )
        if k in pending and pending[k] is not None
    }
    return out


def _pick_latest_open_planner_message_id(messages: List[Dict[str, Any]]) -> Optional[str]:
    for message in reversed(messages):
        meta = message.get("metadata")
        if not isinstance(meta, dict):
            continue
        if meta.get("event_type") not in _PLANNER_INTERACTIVE_EVENTS:
            continue
        if not meta.get("planner_pending"):
            continue
        if _message_is_planner_resolved(meta):
            continue
        status = (
            meta.get("plan_status")
            or (meta.get("planner_pending") or {}).get("execution_plan", {}).get("status")
        )
        if status not in (None, "awaiting_clarification", "awaiting_approval", "draft"):
            if not (meta.get("planner_pending") or {}).get("extract_step_clarification"):
                continue
        return str(message.get("id") or "")
    return None


def _generate_summary(text: Optional[str], max_length: int = 30) -> Optional[str]:
    """Create a short single-line summary from the provided text."""
    if not text:
        return None
    normalized = " ".join(text.split())
    if not normalized:
        return None
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 3].rstrip() + "..."


def _metadata_to_json_str(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
    """Serialize chat message metadata for JSONB storage (UUID-safe)."""
    if metadata is None:
        return None
    from services.planning.schemas import json_safe_value

    return json.dumps(json_safe_value(metadata))


async def _generate_conversation_title(user_prompt: str) -> str:
    """
    Generate a 3-word title for a conversation based on the user's first message.
    Uses OpenAI to create a concise, descriptive title.
    """
    try:
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks

        title_prompt = f"""
Generate a concise 3-word title for this conversation based on the user's message.

User's message: "{user_prompt}"

Rules:
- Return EXACTLY 3 words
- Make it descriptive and specific to what the user is asking
- Use title case (Capitalize Each Word)
- Focus on the main action or topic
- Examples:
  - "Extract customer data from database" → "Extract Customer Data"
  - "Migrate contacts to Salesforce" → "Migrate Contacts Salesforce"
  - "How do I import accounts?" → "Import Accounts Guide"
  - "Create spreadsheet for leads" → "Create Leads Spreadsheet"

Return ONLY the 3-word title, nothing else. No quotes, no explanation, just the title.
"""
        response = await async_openai_call(
            None,
            task=llm_tasks.GENERAL,
            messages=[
                {"role": "system", "content": "You are a helpful assistant that generates concise conversation titles."},
                {"role": "user", "content": title_prompt},
            ],
            max_tokens=20,
            temperature=0.3,
        )
        
        title = response.choices[0].message.content.strip()
        # Remove quotes if present
        title = title.strip('"').strip("'").strip()
        
        # Validate it's approximately 3 words (allow some flexibility)
        words = title.split()
        if len(words) > 5:
            # Fallback: use first 3 words of user prompt
            prompt_words = user_prompt.strip().split()[:3]
            title = " ".join(prompt_words).title() if prompt_words else "New Conversation"
        elif len(words) < 2:
            # Fallback: use first 2-3 words of user prompt
            prompt_words = user_prompt.strip().split()[:3]
            title = " ".join(prompt_words).title() if prompt_words else "New Conversation"
        
        return title[:60]  # Ensure it fits in database column
        
    except Exception as e:
        print(f"⚠️ Error generating conversation title with OpenAI: {e}")
        # Fallback: use first 3 words of user prompt
        prompt_words = user_prompt.strip().split()[:3]
        return " ".join(prompt_words).title() if prompt_words else "New Conversation"


class ChatService:
    """Service layer for chat conversations and messages."""

    _tables_initialized: bool = False
    _init_lock: asyncio.Lock = asyncio.Lock()

    @classmethod
    async def _ensure_tables_exist(cls) -> None:
        if cls._tables_initialized:
            return

        async with cls._init_lock:
            if cls._tables_initialized:
                return

            # Create conversations table
            await database.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_conversations (
                    id UUID PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    title VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_message_at TIMESTAMPTZ
                )
                """
            )

            await database.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id UUID PRIMARY KEY,
                    conversation_id UUID NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    role VARCHAR(20) NOT NULL,
                    content TEXT NOT NULL,
                    metadata JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            await database.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_conversations_user 
                ON chat_conversations(user_id)
                """
            )

            await database.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_conversations_last_message 
                ON chat_conversations(user_id, last_message_at DESC NULLS LAST)
                """
            )

            await database.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation 
                ON chat_messages(conversation_id, created_at)
                """
            )

            cls._tables_initialized = True

    @classmethod
    async def ensure_schema(cls) -> None:
        """Initialize chat tables once at application startup."""
        await cls._ensure_tables_exist()

    @staticmethod
    def schedule_message_persist(coro: Awaitable[Any]) -> None:
        """Fire-and-forget message persist with logging (does not block SSE)."""

        async def _run() -> None:
            try:
                await coro
            except Exception as exc:
                logger.warning("Background chat message persist failed: %s", exc)

        asyncio.create_task(_run())

    @staticmethod
    def _serialize_conversation(record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if record is None:
            return None

        conversation = dict(record)
        for key in ("created_at", "updated_at", "last_message_at"):
            value = conversation.get(key)
            if value:
                conversation[key] = value.isoformat()
        conversation["summary"] = _generate_summary(conversation.get("title"))
        return conversation

    @staticmethod
    def _serialize_message(record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if record is None:
            return None

        message = dict(record)
        value = message.get("created_at")
        if value:
            message["created_at"] = value.isoformat()
        
        # Parse metadata if it's a string (JSONB might be returned as string by some drivers)
        metadata = message.get("metadata")
        if metadata and isinstance(metadata, str):
            try:
                import json
                message["metadata"] = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                # If parsing fails, keep as is
                pass
        
        return message

    @classmethod
    async def create_conversation(cls, user_id: str, title: Optional[str] = None, conversation_id: Optional[str] = None) -> Dict[str, Any]:
        await cls._ensure_tables_exist()
        # Use provided conversation_id if valid, otherwise generate a new one
        if not conversation_id:
            conversation_id = str(uuid.uuid4())
        else:
            # Validate it's a valid UUID format
            try:
                uuid.UUID(conversation_id)
            except ValueError:
                # If invalid, generate a new one
                conversation_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        await database.execute(
            """
            INSERT INTO chat_conversations (id, user_id, title, created_at, updated_at, last_message_at)
            VALUES (:id, :user_id, :title, :created_at, :updated_at, :last_message_at)
            """,
            {
                "id": conversation_id,
                "user_id": user_id,
                "title": (title or "").strip() or None,
                "created_at": now,
                "updated_at": now,
                "last_message_at": now,
            },
        )

        conversation = await cls.get_conversation(conversation_id, user_id)
        if conversation is None:
            raise RuntimeError("Conversation creation failed")
        return conversation

    @classmethod
    async def list_conversations(cls, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        await cls._ensure_tables_exist()
        records = await database.fetch_all(
            """
            SELECT id, user_id, title, created_at, updated_at, last_message_at
            FROM chat_conversations
            WHERE user_id = :user_id
            ORDER BY COALESCE(last_message_at, updated_at) DESC, created_at DESC
            LIMIT :limit
            """,
            {"user_id": user_id, "limit": limit},
        )
        return [cls._serialize_conversation(record) for record in records]

    @classmethod
    async def get_conversation(cls, conversation_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        await cls._ensure_tables_exist()

        async def _fetch():
            return await database.fetch_one(
                """
                SELECT id, user_id, title, created_at, updated_at, last_message_at
                FROM chat_conversations
                WHERE id = :id AND user_id = :user_id
                """,
                {"id": conversation_id, "user_id": user_id},
            )

        from db.retry import with_db_retry

        record = await with_db_retry(_fetch)
        return cls._serialize_conversation(record)

    @classmethod
    async def _fetch_message_records(
        cls,
        conversation_id: str,
        user_id: str,
        limit: int,
    ) -> List[Any]:
        async def _fetch():
            return await database.fetch_all(
                """
                SELECT id, conversation_id, user_id, role, content, metadata, created_at
                FROM chat_messages
                WHERE conversation_id = :conversation_id AND user_id = :user_id
                ORDER BY created_at DESC
                LIMIT :limit
                """,
                {"conversation_id": conversation_id, "user_id": user_id, "limit": limit},
            )

        from db.retry import with_db_retry

        return await with_db_retry(_fetch)

    @classmethod
    def _apply_slim_metadata_to_messages(cls, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        open_id = _pick_latest_open_planner_message_id(messages)
        for msg in messages:
            meta = msg.get("metadata")
            if not isinstance(meta, dict):
                continue
            mid = str(msg.get("id") or "")
            msg["metadata"] = _slim_message_metadata_for_api(
                meta,
                keep_full_planner_pending=(open_id is not None and mid == open_id),
            )
        return messages

    @classmethod
    async def get_messages(
        cls,
        conversation_id: str,
        user_id: str,
        limit: int = 50,
        *,
        conversation_verified: bool = False,
        slim: bool = True,
    ) -> List[Dict[str, Any]]:
        await cls._ensure_tables_exist()
        if not conversation_verified:
            conversation = await cls.get_conversation(conversation_id, user_id)
            if not conversation:
                return []
        records = await cls._fetch_message_records(conversation_id, user_id, limit)
        messages = [cls._serialize_message(record) for record in records]
        messages = [m for m in messages if m is not None]
        if slim and messages:
            messages = cls._apply_slim_metadata_to_messages(messages)
        return messages

    @classmethod
    async def add_message(
        cls,
        conversation_id: str,
        user_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        await cls._ensure_tables_exist()
        conversation = await cls.get_conversation(conversation_id, user_id)
        
        # If conversation doesn't exist and this is a user message, create it
        # Use the provided conversation_id (which might be a UUID generated by frontend)
        if not conversation and role == "user":
            conversation = await cls.create_conversation(user_id, title=None, conversation_id=conversation_id)
            conversation_id = conversation["id"]

        message_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        title_to_set: Optional[str] = None
        existing_title = (conversation.get("title") or "").strip()
        if role == "user" and not existing_title:
            title_to_set = await _generate_conversation_title(content)

        # Ensure metadata is properly serialized for JSONB column
        metadata_json = _metadata_to_json_str(metadata)

        async def _insert_and_touch():
            await database.execute(
                """
                INSERT INTO chat_messages (id, conversation_id, user_id, role, content, metadata, created_at)
                VALUES (:id, :conversation_id, :user_id, :role, :content, :metadata, :created_at)
                """,
                {
                    "id": message_id,
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "role": role,
                    "content": content,
                    "metadata": metadata_json,
                    "created_at": now,
                },
            )
            if title_to_set:
                await database.execute(
                    """
                    UPDATE chat_conversations
                    SET 
                        updated_at = :updated_at,
                        last_message_at = :updated_at,
                        title = :new_title
                    WHERE id = :conversation_id
                    """,
                    {
                        "updated_at": now,
                        "conversation_id": conversation_id,
                        "new_title": title_to_set,
                    },
                )
            else:
                await database.execute(
                    """
                    UPDATE chat_conversations
                    SET 
                        updated_at = :updated_at,
                        last_message_at = :updated_at
                    WHERE id = :conversation_id
                    """,
                    {"updated_at": now, "conversation_id": conversation_id},
                )

        from db.retry import with_db_retry

        await with_db_retry(_insert_and_touch)

        updated_conversation = await cls.get_conversation(conversation_id, user_id)

        return (
            cls._serialize_message(
                {
                    "id": message_id,
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "role": role,
                    "content": content,
                    "metadata": metadata,
                    "created_at": now,
                }
            ),
            updated_conversation,
        )

    @classmethod
    async def get_messages_for_context(
        cls, conversation_id: str, user_id: str, limit: int = 25
    ) -> List[Dict[str, str]]:
        """Legacy text-only context (metadata stripped). Prefer get_messages_with_metadata_for_context."""
        messages = await cls.get_messages_with_metadata_for_context(
            conversation_id, user_id, limit=limit
        )
        return [
            {"type": msg.get("type", "system"), "text": msg.get("text", "")}
            for msg in messages
        ]

    @classmethod
    async def get_messages_with_metadata_for_context(
        cls,
        conversation_id: str,
        user_id: str,
        limit: int = 25,
        *,
        conversation_verified: bool = False,
        slim: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Last N messages in chronological order for architect / session helpers.

        Shape matches frontend conversation_history: type, text, metadata, id, role.
        """
        await cls._ensure_tables_exist()
        if not conversation_verified:
            conversation = await cls.get_conversation(conversation_id, user_id)
            if not conversation:
                return []

        records = await cls._fetch_message_records(conversation_id, user_id, limit)
        chronological = list(reversed(records))
        formatted: List[Dict[str, Any]] = []
        for record in chronological:
            message = cls._serialize_message(record) or {}
            role = (message.get("role") or "assistant").lower()
            msg_type = "user" if role == "user" else "system"
            if role == "data_event":
                msg_type = "system"
            entry: Dict[str, Any] = {
                "id": message.get("id"),
                "type": msg_type,
                "role": role,
                "text": message.get("content") or "",
            }
            metadata = message.get("metadata")
            if isinstance(metadata, dict) and metadata:
                entry["metadata"] = dict(metadata)
            created = message.get("created_at")
            if created:
                entry["created_at"] = created
            formatted.append(entry)
        if slim and formatted:
            open_id = _pick_latest_open_planner_message_id(formatted)
            for entry in formatted:
                meta = entry.get("metadata")
                if not isinstance(meta, dict):
                    continue
                mid = str(entry.get("id") or "")
                entry["metadata"] = _slim_message_metadata_for_api(
                    meta,
                    keep_full_planner_pending=(open_id is not None and mid == open_id),
                )
        return formatted

    @classmethod
    async def delete_conversation(cls, conversation_id: str, user_id: str) -> bool:
        """
        Delete a conversation and all of its messages.
        Returns True if a conversation was removed.
        """
        await cls._ensure_tables_exist()
        conversation = await cls.get_conversation(conversation_id, user_id)
        if not conversation:
            return False

        await database.execute(
            """
            DELETE FROM chat_conversations
            WHERE id = :conversation_id AND user_id = :user_id
            """,
            {"conversation_id": conversation_id, "user_id": user_id},
        )
        return True

    @classmethod
    async def log_data_event(
        cls,
        conversation_id: str,
        user_id: str,
        event_type: str,
        event_data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Log a structured data event (extraction, mapping, migration) as a message.
        
        Args:
            conversation_id: The conversation ID
            user_id: The user ID
            event_type: Type of event (extraction_started, extraction_completed, 
                       mapping_started, mapping_completed, migration_started, migration_completed)
            event_data: Event-specific data (job_id, record_count, status, etc.)
        
        Returns:
            The created message or None if conversation doesn't exist
        """
        await cls._ensure_tables_exist()
        conversation = await cls.get_conversation(conversation_id, user_id)
        if not conversation:
            return None

        # Generate human-readable content based on event type
        content = cls._generate_event_content(event_type, event_data)
        
        # Build metadata with event information
        metadata = {
            "event_type": event_type,
            **event_data
        }

        message_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        # Ensure metadata is properly serialized for JSONB column
        metadata_json = _metadata_to_json_str(metadata)

        await database.execute(
            """
            INSERT INTO chat_messages (id, conversation_id, user_id, role, content, metadata, created_at)
            VALUES (:id, :conversation_id, :user_id, :role, :content, :metadata, :created_at)
            """,
            {
                "id": message_id,
                "conversation_id": conversation_id,
                "user_id": user_id,
                "role": "data_event",
                "content": content,
                "metadata": metadata_json,
                "created_at": now,
            },
        )

        # Update conversation timestamps
        await database.execute(
            """
            UPDATE chat_conversations
            SET updated_at = :updated_at, last_message_at = :updated_at
            WHERE id = :conversation_id
            """,
            {
                "updated_at": now,
                "conversation_id": conversation_id,
            },
        )

        return cls._serialize_message(
            {
                "id": message_id,
                "conversation_id": conversation_id,
                "user_id": user_id,
                "role": "data_event",
                "content": content,
                "metadata": metadata,
                "created_at": now,
            }
        )

    @classmethod
    async def find_persisted_data_event(
        cls,
        conversation_id: str,
        user_id: str,
        event_type: str,
        *,
        job_id: Optional[str] = None,
        batch_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Find a persisted chat data event by type and migration identifiers."""
        await cls._ensure_tables_exist()
        if not conversation_id or not user_id or not event_type:
            return None

        params: Dict[str, Any] = {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "event_type": event_type,
        }
        job_clause = ""
        if job_id:
            params["job_id"] = str(job_id)
            job_clause = "AND metadata->>'job_id' = :job_id"
        batch_clause = ""
        if batch_id:
            params["batch_id"] = str(batch_id)
            batch_clause = (
                "AND (metadata->>'batch_id' = :batch_id "
                "OR metadata->'planner_pending'->>'recovery_batch_id' = :batch_id "
                "OR metadata->>'recovery_batch_id' = :batch_id)"
            )

        async def _fetch():
            return await database.fetch_one(
                f"""
                SELECT id, conversation_id, user_id, role, content, metadata, created_at
                FROM chat_messages
                WHERE conversation_id = :conversation_id
                  AND user_id = :user_id
                  AND metadata->>'event_type' = :event_type
                  {job_clause}
                  {batch_clause}
                ORDER BY created_at DESC
                LIMIT 1
                """,
                params,
            )

        from db.retry import with_db_retry

        record = await with_db_retry(_fetch)
        return cls._serialize_message(record)

    @staticmethod
    def _generate_event_content(event_type: str, event_data: Dict[str, Any]) -> str:
        """Generate human-readable content for a data event."""
        job_id = event_data.get("job_id") or event_data.get("task_id", "N/A")
        
        if event_type in (
            "extraction_started",
            "plan_execution_started",
            "plan_step_checkpoint",
            "spreadsheet_creation_started",
        ):
            # Internal pipeline checkpoints — UI uses operation preview / panels instead
            return ""

        elif event_type == "spreadsheet_creation_completed":
            name = event_data.get("spreadsheet_name") or "Spreadsheet"
            url = event_data.get("spreadsheet_url")
            spreadsheet_id = event_data.get("spreadsheet_id")
            if not url and spreadsheet_id:
                url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
            if url:
                return (
                    f"**Spreadsheet created successfully!**\n\n"
                    f"**Name**: {name}\n\n"
                    f"**View Spreadsheet**: [Open in Google Sheets]({url})\n"
                )
            return f"**Spreadsheet created successfully!**\n\n**Name**: {name}\n"

        elif event_type == "extraction_completed":
            record_count = event_data.get("record_count", 0)
            status = event_data.get("status", "completed")
            source_type = event_data.get("source_type", "unknown")
            if status == "success":
                # Build base message
                base_message = f"✅ **Extraction Completed**\n\nSource: {source_type}\nRecords extracted: **{record_count}**"
                
                # Add data quality analysis if available
                data_quality_report = event_data.get("data_quality_report")
                if data_quality_report and data_quality_report.get("summary_text"):
                    quality_summary = data_quality_report.get("summary_text", "")
                    if quality_summary.strip():
                        base_message += f"\n\n{quality_summary}"

                pipeline_narrative = event_data.get("pipeline_narrative")
                if isinstance(pipeline_narrative, str) and pipeline_narrative.strip():
                    base_message += f"\n\n**Operations summary**\n{pipeline_narrative.strip()}"
                
                return base_message
            else:
                return f"❌ **Extraction Failed**\n\nSource: {source_type}\nJob ID: `{job_id}`\nError: {event_data.get('error', 'Unknown error')}"
        
        elif event_type == "mapping_started":
            destination = event_data.get("destination", "unknown")
            destination_name = event_data.get("destination_name")
            dest_label = f"{destination}" + (f" ({destination_name})" if destination_name else "")
            return f"🗺️ **Mapping Started**\n\nDestination: {dest_label}\nJob ID: `{job_id}`"
        
        elif event_type == "mapping_completed":
            destination = event_data.get("destination", "unknown")
            field_count = event_data.get("field_mappings_count", 0)
            return f"✅ **Mapping Completed**\n\nDestination: {destination}\nFields mapped: **{field_count}**\nJob ID: `{job_id}`"
        
        elif event_type == "migration_started":
            destination = event_data.get("destination", "unknown")
            record_count = event_data.get("record_count", 0)
            return f"🚀 **Migration Started**\n\nDestination: {destination}\nRecords to migrate: **{record_count}**\nJob ID: `{job_id}`"
        
        elif event_type == "migration_completed":
            destination = event_data.get("destination", "unknown")
            success_count = event_data.get("success_count", 0)
            error_count = event_data.get("error_count", 0)
            status = event_data.get("status", "completed")
            if status == "success":
                return f"✅ **Migration Completed**\n\nDestination: {destination}\n✅ Successful: **{success_count}**\n❌ Failed: **{error_count}**\nJob ID: `{job_id}`"
            elif status == "partial_success":
                dest_label = destination
                dest_name = event_data.get("destination_name")
                if dest_name:
                    dest_label = f"{destination} • {dest_name}"
                return (
                    f"⚠️ **Migration Completed with Errors**\n\n"
                    f"Destination: {dest_label}\n"
                    f"✅ Successful: **{success_count}**\n"
                    f"❌ Failed: **{error_count}**\n"
                    f"Job ID: `{job_id}`"
                )
            elif status == "cancelled":
                return f"🛑 **Migration Cancelled**\n\nDestination: {destination}\nPartial progress: **{success_count}** successful, **{error_count}** failed.\nJob ID: `{job_id}`"
            else:
                return f"❌ **Migration Failed**\n\nDestination: {destination}\nJob ID: `{job_id}`\nError: {event_data.get('error', 'Unknown error')}"
        
        elif event_type == "flow_created":
            flow_name = event_data.get("flow_name", "Unknown Flow")
            source_type = event_data.get("source_type", "unknown")
            source_name = event_data.get("source_name", "")
            destination_type = event_data.get("destination_type", "unknown")
            destination_name = event_data.get("destination_name", "")
            
            source_label = f"{source_type}" + (f" • {source_name}" if source_name else "")
            dest_label = f"{destination_type}" + (f" • {destination_name}" if destination_name else "")
            
            return f"✅ **Flow Created: {flow_name}**\n\nSource: {source_label}\nDestination: {dest_label}\nFlow ID: `{event_data.get('flow_id', 'N/A')}`"
        
        elif event_type == "flow_scheduled":
            flow_name = event_data.get("flow_name", "Unknown Flow")
            schedule_action = event_data.get("schedule_action", "updated")
            schedule_frequency = event_data.get("schedule_frequency", "")
            
            # Capitalize frequency for display
            frequency_label = schedule_frequency.capitalize() if schedule_frequency else ""
            
            flow_id = event_data.get("flow_id", "N/A")
            
            if schedule_action == "created":
                return f"✅ **Flow Scheduled: {flow_name}**\n\nSchedule: {frequency_label}\nFlow ID: `{flow_id}`"
            elif schedule_action == "updated":
                return f"✅ **Flow Schedule Updated: {flow_name}**\n\nNew Schedule: {frequency_label}\nFlow ID: `{flow_id}`"
            elif schedule_action == "disabled":
                return f"⚠️ **Flow Schedule Disabled: {flow_name}**\n\nFlow ID: `{flow_id}`"
            else:
                # Default to created format
                return f"✅ **Flow Scheduled: {flow_name}**\n\nSchedule: {frequency_label}\nFlow ID: `{flow_id}`"

        elif event_type == "plan_dismissed":
            return "Plan dismissed. Send a new message when you are ready to continue."

        else:
            if job_id and job_id != "N/A":
                return f"📊 **Data Event: {event_type}**\n\nJob ID: `{job_id}`"
            else:
                return f"📊 **Data Event: {event_type}**"

    @classmethod
    async def update_message_metadata(
        cls,
        message_id: str,
        conversation_id: str,
        user_id: str,
        metadata: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Update the metadata of an existing chat message.
        
        Args:
            message_id: The message ID to update
            conversation_id: The conversation ID (for validation)
            user_id: The user ID (for validation)
            metadata: The new metadata to set (will replace existing metadata)
        
        Returns:
            The updated message or None if message doesn't exist or user doesn't have access
        """
        await cls._ensure_tables_exist()
        
        # Verify the message exists and belongs to the user
        existing_message = await database.fetch_one(
            """
            SELECT id, conversation_id, user_id, metadata
            FROM chat_messages
            WHERE id = :message_id AND conversation_id = :conversation_id AND user_id = :user_id
            """,
            {
                "message_id": message_id,
                "conversation_id": conversation_id,
                "user_id": user_id,
            },
        )
        
        if not existing_message:
            return None
        
        # Merge with existing metadata if needed, or replace entirely
        # For mapping updates, we want to replace the metadata entirely
        # Access record fields using bracket notation (database records are not dicts)
        existing_metadata = existing_message["metadata"]
        if existing_metadata and isinstance(existing_metadata, str):
            try:
                existing_metadata = json.loads(existing_metadata)
            except (json.JSONDecodeError, TypeError):
                existing_metadata = {}
        
        # Merge existing metadata with new metadata (new values override old ones)
        updated_metadata = {**(existing_metadata or {}), **metadata}
        
        # Ensure metadata is properly serialized for JSONB column
        metadata_json = _metadata_to_json_str(updated_metadata)
        
        # Update the message metadata
        await database.execute(
            """
            UPDATE chat_messages
            SET metadata = :metadata
            WHERE id = :message_id AND conversation_id = :conversation_id AND user_id = :user_id
            """,
            {
                "message_id": message_id,
                "conversation_id": conversation_id,
                "user_id": user_id,
                "metadata": metadata_json,
            },
        )
        
        # Fetch and return the updated message
        updated_message = await database.fetch_one(
            """
            SELECT id, conversation_id, user_id, role, content, metadata, created_at
            FROM chat_messages
            WHERE id = :message_id
            """,
            {"message_id": message_id},
        )
        
        return cls._serialize_message(updated_message)

