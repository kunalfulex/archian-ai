"""Google Sheets reconnect OAuth helpers."""

from urllib.parse import parse_qs, urlparse

from services.google_sheets_connection_service import parse_google_sheets_oauth_state
from services.google_sheets_oauth_service import GoogleSheetsOAuthService


def test_parse_oauth_state_new_connect():
    user_id, conn_id = parse_google_sheets_oauth_state("abc123:user-uuid")
    assert user_id == "user-uuid"
    assert conn_id is None


def test_parse_oauth_state_reconnect():
    user_id, conn_id = parse_google_sheets_oauth_state("abc123:user-uuid:conn-uuid")
    assert user_id == "user-uuid"
    assert conn_id == "conn-uuid"


def test_authorization_url_includes_login_hint(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://app.example/oauth/callback")

    svc = GoogleSheetsOAuthService()
    url = svc.get_authorization_url(
        "nonce:user-1:conn-1",
        login_hint="user@example.com",
    )
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert params["login_hint"] == ["user@example.com"]
    assert params["state"] == ["nonce:user-1:conn-1"]
    assert params["prompt"] == ["select_account consent"]
