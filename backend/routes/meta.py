"""Static meta endpoints (robots.txt)."""

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter()


# Robots.txt endpoint - Block all crawling on app.archian.ai
@router.get("/robots.txt")
async def robots_txt():
    """
    Serve robots.txt to block all crawling on app.archian.ai
    This is the login/dashboard subdomain and should not be indexed
    """
    robots_content = """User-agent: *
Disallow: /

# Block all crawling on app.archian.ai
# This is the login/dashboard subdomain and should not be indexed

# Allow only essential assets
Allow: /static/
Allow: /assets/
Allow: /favicon.ico
Allow: /robots.txt

# Sitemap for main website (not this subdomain)
# Sitemap: https://www.archian.ai/sitemap.xml"""
    
    return Response(
        content=robots_content,
        media_type="text/plain",
        headers={"Cache-Control": "public, max-age=86400"}  # Cache for 24 hours
    )
