"""Salesforce org rules sync — validation rule Tooling API fetch."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.salesforce_org_rules_service import fetch_validation_rules_json


def test_fetch_validation_rules_json_uses_two_step_for_multiple_rules():
    list_q_marker = "EntityDefinition.QualifiedApiName"
    detail_calls: list[str] = []

    async def _fake_query_pages(_url, _token, soql, *, tooling=False):
        assert tooling is True
        if list_q_marker in soql and "Metadata" not in soql:
            return [
                {"Id": "rule-1", "ValidationName": "Rule One", "Active": True},
                {"Id": "rule-2", "ValidationName": "Rule Two", "Active": False},
            ]
        if "WHERE Id = 'rule-1'" in soql:
            detail_calls.append("rule-1")
            return [
                {
                    "Id": "rule-1",
                    "FullName": "Obj.Rule_One",
                    "ValidationName": "Rule One",
                    "Active": True,
                    "Metadata": {"errorMessage": "Bad value"},
                }
            ]
        if "WHERE Id = 'rule-2'" in soql:
            detail_calls.append("rule-2")
            return [
                {
                    "Id": "rule-2",
                    "FullName": "Obj.Rule_Two",
                    "ValidationName": "Rule Two",
                    "Active": False,
                    "Metadata": {"errorMessage": "Required"},
                }
            ]
        raise AssertionError(f"Unexpected SOQL: {soql}")

    async def _run():
        with patch(
            "services.salesforce_org_rules_service._sf_query_pages",
            new=AsyncMock(side_effect=_fake_query_pages),
        ):
            return await fetch_validation_rules_json(
                "https://example.my.salesforce.com",
                "token",
                "Survey_Request__c",
            )

    result = asyncio.run(_run())
    assert result["object_api_name"] == "Survey_Request__c"
    assert len(result["rules"]) == 2
    assert {r["Id"] for r in result["rules"]} == {"rule-1", "rule-2"}
    assert result["rules"][0]["Metadata"]["errorMessage"] == "Bad value"
    assert detail_calls == ["rule-1", "rule-2"]


def test_fetch_validation_rules_json_skips_detail_when_no_rules():
    query_count = {"n": 0}

    async def _fake_query_pages(_url, _token, soql, *, tooling=False):
        query_count["n"] += 1
        assert "Metadata" not in soql
        return []

    async def _run():
        with patch(
            "services.salesforce_org_rules_service._sf_query_pages",
            new=AsyncMock(side_effect=_fake_query_pages),
        ):
            return await fetch_validation_rules_json(
                "https://example.my.salesforce.com",
                "token",
                "Account",
            )

    result = asyncio.run(_run())
    assert result["rules"] == []
    assert query_count["n"] == 1


def test_fetch_validation_rules_json_single_rule_still_fetches_detail():
    calls: list[str] = []

    async def _fake_query_pages(_url, _token, soql, *, tooling=False):
        calls.append(soql)
        if "EntityDefinition.QualifiedApiName" in soql:
            return [{"Id": "only-rule", "ValidationName": "Only", "Active": True}]
        if "WHERE Id = 'only-rule'" in soql:
            return [
                {
                    "Id": "only-rule",
                    "FullName": "Obj.Only",
                    "ValidationName": "Only",
                    "Active": True,
                    "Metadata": {},
                }
            ]
        raise AssertionError(soql)

    async def _run():
        with patch(
            "services.salesforce_org_rules_service._sf_query_pages",
            new=AsyncMock(side_effect=_fake_query_pages),
        ):
            return await fetch_validation_rules_json(
                "https://example.my.salesforce.com",
                "token",
                "Contact",
            )

    result = asyncio.run(_run())
    assert len(result["rules"]) == 1
    assert result["rules"][0]["FullName"] == "Obj.Only"
    assert len(calls) == 2
    assert "Metadata" not in calls[0]
    assert "Metadata" in calls[1]
