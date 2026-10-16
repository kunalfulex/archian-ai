"""Salesforce OAuth URL helpers for connect vs org-scoped reconnect."""
from routes.salesforce_oauth import _salesforce_global_oauth_urls, _salesforce_instance_oauth_urls


def test_global_production_oauth_urls():
    urls = _salesforce_global_oauth_urls("production")
    assert urls["authorize"] == "https://login.salesforce.com/services/oauth2/authorize"
    assert urls["token"] == "https://login.salesforce.com/services/oauth2/token"


def test_global_sandbox_oauth_urls():
    urls = _salesforce_global_oauth_urls("sandbox")
    assert urls["authorize"] == "https://test.salesforce.com/services/oauth2/authorize"


def test_instance_oauth_urls_strips_trailing_slash():
    urls = _salesforce_instance_oauth_urls("https://orgfarm-dev-ed.develop.my.salesforce.com/")
    assert urls["authorize"] == (
        "https://orgfarm-dev-ed.develop.my.salesforce.com/services/oauth2/authorize"
    )
    assert urls["token"] == (
        "https://orgfarm-dev-ed.develop.my.salesforce.com/services/oauth2/token"
    )
