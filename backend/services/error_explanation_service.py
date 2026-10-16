"""
Error Explanation Service - Uses OpenAI to convert raw technical errors
into human-readable explanations and suggested solutions.
Works across Salesforce, SQL, Google Sheets, and other sources without hardcoding.
"""
import asyncio
import json
import re
from typing import Optional, Dict, Any


# Truncate long errors to avoid token limits and reduce cost
MAX_ERROR_TEXT_LENGTH = 1500

# Sanitize patterns - remove or redact sensitive data before sending to OpenAI
SENSITIVE_PATTERNS = [
    (re.compile(r'https?://[^\s]+', re.I), '[URL redacted]'),
    (re.compile(r'Bearer\s+[A-Za-z0-9_\-\.]+', re.I), '[token redacted]'),
    (re.compile(r'api[_-]?key["\']?\s*[:=]\s*["\']?[^\s"\']+', re.I), 'api_key=[redacted]'),
    (re.compile(r'password["\']?\s*[:=]\s*["\']?[^\s"\']+', re.I), 'password=[redacted]'),
]


def _sanitize_error_text(text: str) -> str:
    """Remove or redact potentially sensitive data from error text."""
    if not text or not isinstance(text, str):
        return ""
    result = text
    for pattern, replacement in SENSITIVE_PATTERNS:
        result = pattern.sub(replacement, result)
    return result[:MAX_ERROR_TEXT_LENGTH]


async def explain_error(
    raw_error: str,
    source: str,
    context: Optional[Dict[str, Any]] = None,
    timeout_seconds: float = 5.0,
) -> Dict[str, Optional[str]]:
    """
    Call OpenAI to produce a user-friendly explanation and solution for a technical error.
    Non-blocking: uses AsyncOpenAI and asyncio.wait_for to avoid blocking the event loop.

    Args:
        raw_error: The raw error text (exception message, API response, etc.)
        source: One of "salesforce", "sql", "google_sheets", "file", or "unknown"
        context: Optional dict with keys like object_name, operation, user_query, error_type (exception class name)
        timeout_seconds: Max time to wait for OpenAI (default 5s)

    Returns:
        {"explanation": str|None, "solution": str|None}
        Returns None for both if the call fails or times out.
    """
    if not raw_error or not str(raw_error).strip():
        return {"explanation": None, "solution": None}

    sanitized = _sanitize_error_text(str(raw_error))
    from services.llm import tasks as llm_tasks
    from services.llm.service import task_has_api_key

    if not await task_has_api_key(llm_tasks.ERROR_EXPLANATION):
        print("⚠️ [ERROR_EXPLANATION] No LLM API key configured, skipping AI explanation")
        return {"explanation": None, "solution": None}

    ctx_str = ""
    if context:
        parts = []
        if context.get("object_name"):
            parts.append(f"Object/table: {context['object_name']}")
        if context.get("operation"):
            parts.append(f"Operation: {context['operation']}")
        if context.get("user_query"):
            uq = str(context["user_query"])[:200]
            parts.append(f"User request: {uq}")
        if context.get("available_fields"):
            af = context["available_fields"]
            if isinstance(af, (list, tuple)):
                af = ", ".join(str(f) for f in af[:40])
            parts.append(f"Available fields on object: {af}")
        if context.get("error_type"):
            parts.append(f"Exception type: {context['error_type']}")
        if parts:
            ctx_str = "\nContext: " + "; ".join(parts)

    prompt = f"""You are a helpful assistant that explains technical errors in plain language for non-technical users.

Given this error from {source.upper()}{ctx_str}:

---
{sanitized}
---

Provide a brief, friendly response in this exact JSON format (no markdown, no extra text):
{{"explanation": "1-2 sentence user-friendly explanation of what went wrong", "solution": "Concrete steps the user can take to fix it (1-3 bullets or short paragraph)"}}

Rules:
- Infer the real issue from the message itself. Errors may be about: missing or wrong fields/columns/tables, permissions, authentication or session expiry, API limits, network/timeout, invalid syntax, wrong org or connector, type mismatches, row-level security, or anything else—the response must match what the text actually says, not a fixed template.
- Do not assume a single error type; avoid boilerplate. If the error is vague, say what is known and what the user should check next.
- If context lists available fields, you may reference them in the solution; otherwise suggest where to verify (e.g. Setup, schema browser, Connections page) without inventing field names.
- Avoid repeating raw stack traces; translate them into meaning. Keep it concise and actionable."""

    try:
        from services.llm import tasks as llm_tasks
        from services.llm.service import llm_complete_openai_compat

        async def _call():
            response = await llm_complete_openai_compat(
                llm_tasks.ERROR_EXPLANATION,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
                temperature=0.2,
            )
            content = (response.choices[0].message.content or "").strip()
            if not content:
                return {"explanation": None, "solution": None}
            # Parse JSON from response (handle possible markdown code block)
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```\s*$", "", content)
            data = json.loads(content)
            return {
                "explanation": data.get("explanation") or None,
                "solution": data.get("solution") or None,
            }

        result = await asyncio.wait_for(_call(), timeout=timeout_seconds)
        return result

    except asyncio.TimeoutError:
        print("⚠️ [ERROR_EXPLANATION] OpenAI call timed out, using raw error")
        return {"explanation": None, "solution": None}
    except Exception as e:
        print(f"⚠️ [ERROR_EXPLANATION] Failed to get AI explanation: {e}")
        return {"explanation": None, "solution": None}
