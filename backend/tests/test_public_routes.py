"""Every route must require a signed-in user unless it is explicitly listed as public here.

Adding a route without Depends(get_current_user) / Depends(get_super_admin_user) fails this
test; if the route really must be public (login, OAuth callbacks, signed one-time links,
signature-verified webhooks), add it to PUBLIC_ROUTES with the reason.
"""
from __future__ import annotations

from fastapi.routing import APIRoute

from routes.auth import get_current_user, get_super_admin_user

AUTH_DEPENDENCIES = {get_current_user, get_super_admin_user}

PUBLIC_ROUTES = {
    # Sign-up, sign-in and account recovery happen before a session exists.
    ("POST", "/api/signup"),
    ("POST", "/api/send-code"),
    ("POST", "/api/verify-code"),
    ("POST", "/api/login"),
    ("POST", "/api/logout"),
    ("GET", "/api/verify-token"),
    ("POST", "/api/forgot-password/send-code"),
    ("POST", "/api/forgot-password/verify-code"),
    ("POST", "/api/forgot-password/update-password"),
    ("POST", "/api/reactivate-account"),
    # OAuth redirects and callbacks.
    ("GET", "/api/auth/google/url"),
    ("GET", "/api/auth/google/callback"),
    ("POST", "/api/auth/google/reactivate"),
    ("POST", "/oauth/callback"),
    # One-time download links sent by email; the token is the credential.
    ("GET", "/export-download"),
    ("GET", "/migration-export-download"),
    # Static/public content.
    ("GET", "/profile/timezones"),
    ("GET", "/robots.txt"),
    ("POST", "/contact-form"),
}


def _dependency_calls(dependant):
    for sub in dependant.dependencies:
        yield sub.call
        yield from _dependency_calls(sub)


def _open_routes(app):
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        # Cloud billing routes are covered by archian-cloud's own allowlist test.
        if route.endpoint.__module__.startswith("archian_cloud"):
            continue
        if AUTH_DEPENDENCIES.isdisjoint(_dependency_calls(route.dependant)):
            for method in route.methods:
                yield (method, route.path)


def test_only_allowlisted_routes_are_public():
    import main

    open_routes = set(_open_routes(main.app)) - {("HEAD", path) for _, path in PUBLIC_ROUTES}
    unexpected = sorted(open_routes - PUBLIC_ROUTES)
    assert not unexpected, f"Routes without an auth dependency: {unexpected}"
