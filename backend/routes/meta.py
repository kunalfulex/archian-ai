"""Static meta endpoints (robots.txt)."""

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter()


@router.get("/robots.txt")
async def robots_txt():
    """Serve robots.txt that discourages indexing of the app surface."""
    robots_content = """User-agent: *
Disallow: /

# App surface — do not index login/dashboard routes

# Allow only essential assets
Allow: /static/
Allow: /assets/
Allow: /favicon.ico
Allow: /robots.txt
"""

    return Response(
        content=robots_content,
        media_type="text/plain",
        headers={"Cache-Control": "public, max-age=86400"},
    )
