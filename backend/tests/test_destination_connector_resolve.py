"""Fuzzy destination resolution for Salesforce and SQL connectors."""

from services.planning.resource_resolver import (
    match_salesforce_in_phrase,
    match_sql_connection_in_phrase,
)


def test_match_sql_connection_in_phrase_substring():
    names = ["Test DB", "Warehouse DB"]
    assert match_sql_connection_in_phrase("Test Database", names) == "Test DB"
    assert match_sql_connection_in_phrase("warehouse", names) == "Warehouse DB"


def test_match_salesforce_in_phrase_substring():
    names = ["Production 2 Org", "Sandbox"]
    assert match_salesforce_in_phrase("Production", names) == "Production 2 Org"
    assert match_salesforce_in_phrase("sandbox org", names) == "Sandbox"
