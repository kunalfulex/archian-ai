"""Global CSRF validation for cookie-authenticated browser sessions."""
from __future__ import annotations

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from utils.auth_cookies import validate_csrf
from utils.cors_origins import cors_headers_for_origin


class CsrfMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        try:
            validate_csrf(request)
        except HTTPException as exc:
            origin = request.headers.get("origin")
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=cors_headers_for_origin(origin),
            )
        return await call_next(request)
