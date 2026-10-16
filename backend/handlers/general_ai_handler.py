"""
General AI Handler Module

This module contains general AI response functions for:
- General questions and conversations
- Non-data-source specific queries
- Fallback responses when no specific handler matches
- General assistance and guidance
"""

import asyncio
import json
import re
from datetime import datetime
from typing import Dict, Any, Optional, List


async def handle_general_ai_response_stream(
    prompt: str,
    conversation_history: list,
    source: Optional[str] = None,
    source_name: Optional[str] = None,
    destination: Optional[str] = None,
    destination_name: Optional[str] = None,
    user_id: Optional[str] = None,
):
    """
    Handle general AI questions with intelligent prompting based on classification results.

    Args:
        prompt: User's prompt
        conversation_history: Conversation history
        source: Detected source ("salesforce", "google_sheets", "sql", "file", "unknown")
        source_name: Specific source name (connection/spreadsheet/table name)
        destination: Detected destination ("salesforce", "google_sheets", "sql", "unknown")
        destination_name: Specific destination name (connection/spreadsheet/table name)
        user_id: User ID (for credit deduction)
    """
    try:
        # Architect chat is free; no credit deduction

        import os

        from services.planning.agent_runtime_enrichment import general_chat_scope_system_prompt

        # Determine what information is missing and build appropriate prompt
        source_unknown = not source or source == "unknown"
        destination_unknown = not destination or destination == "unknown"

        system_content = general_chat_scope_system_prompt(
            source_unknown=source_unknown,
            destination_unknown=destination_unknown,
            source=source,
            source_name=source_name,
            destination=destination,
            destination_name=destination_name,
        )
        # Build conversation context for more natural responses
        messages = [
            {
                "role": "system", 
                "content": system_content
            }
        ]
        
        # Add complete conversation history for context
        if conversation_history:
            # Send complete conversation without truncation
            for msg in conversation_history:
                if msg.get('type') == 'user':
                    messages.append({"role": "user", "content": msg.get('text', '')})
                elif msg.get('type') == 'system':
                    messages.append({"role": "assistant", "content": msg.get('text', '')})
        
        # Add current prompt
        messages.append({"role": "user", "content": prompt})

        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.GENERAL_CHAT,
            messages=messages,
            max_tokens=1000,
            temperature=0.8,
            stream=True,
        )

        from agent.stream_utils import iter_openai_compat_stream

        # Stream the response in real-time (same format as Salesforce)
        async def generate():
            try:
                async for chunk in iter_openai_compat_stream(response):
                    if chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        yield content
            except Exception as e:
                yield f"\n\n❌ Error during streaming: {str(e)}"

        return generate()
        
    except Exception as e:
        print(f"Error in general AI response: {e}")
        async def generate():
            yield "Hey there! 😊 I'd love to help you out! I can help you build data pipelines across Salesforce, Google Sheets, SQL databases, and files — extract, clean, map, and migrate. What would you like to work on today?"
        return generate()

