"""Tests for Salesforce extract debug helpers."""
from utils.salesforce_extract_debug import approx_token_count, log_sf_extract


def test_approx_token_count():
    assert approx_token_count("") == 0
    assert approx_token_count("abcd") == 1
    assert approx_token_count("a" * 400) == 100


def test_log_sf_extract_no_crash_when_disabled(monkeypatch):
    monkeypatch.delenv("SF_EXTRACT_DEBUG", raising=False)
    import utils.salesforce_extract_debug as mod

    mod._VERBOSE = None
    log_sf_extract("test_phase", foo="bar")  # should no-op quietly


def test_log_sf_extract_always_logs(monkeypatch, capsys):
    monkeypatch.delenv("SF_EXTRACT_DEBUG", raising=False)
    import utils.salesforce_extract_debug as mod

    mod._VERBOSE = None
    log_sf_extract("abort_test", always=True, reason="unit_test")
    out = capsys.readouterr().out
    assert "[SF_EXTRACT]" in out
    assert "abort_test" in out
