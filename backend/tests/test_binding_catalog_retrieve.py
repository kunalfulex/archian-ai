"""Tests for full-catalog scoring in plan binding retrieve."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.planning.entity_scoring import score_catalog_entity


CONTACT_SEMANTICS = {
    "primary": {
        "category": "Customer Contact Information",
        "description": "Individual contacts with name, email, and phone.",
    },
    "synonyms": ["client contact", "customer contact details"],
}

DIGITAL_WALLET_SEMANTICS = {
    "primary": {
        "category": "Digital Wallet",
        "description": "Payment wallet configuration for digital commerce.",
    },
    "synonyms": ["wallet", "digital payment"],
}


def test_contact_scores_higher_than_digital_wallet_for_contact_sync_prompt():
    prompt = "pipeline between SF 1 and SF 2 for contact record sync"
    catalog = [
        {"label": "Contact", "object_api_name": "Contact"},
        {"label": "Digital Wallet", "object_api_name": "DigitalWallet"},
        {"label": "Account", "object_api_name": "Account"},
    ]
    score_contact = score_catalog_entity(
        entity_key="Contact",
        label="Contact",
        prompt=prompt,
        hints=["Contact", "contact record sync"],
        semantics=CONTACT_SEMANTICS,
        catalog=catalog,
        connector_type="salesforce",
    )
    score_wallet = score_catalog_entity(
        entity_key="DigitalWallet",
        label="Digital Wallet",
        prompt=prompt,
        hints=["Contact", "contact record sync"],
        semantics=DIGITAL_WALLET_SEMANTICS,
        catalog=catalog,
        connector_type="salesforce",
    )
    assert score_contact.score > score_wallet.score


def test_load_side_catalog_uses_structural_salesforce_catalog():
    from services.planning.catalog_grounding import load_side_catalog_summary

    structural = [
        {"api_name": "Contact", "label": "Contact"},
        {"api_name": "DigitalWallet", "label": "Digital Wallet"},
        {"api_name": "Account", "label": "Account"},
    ]

    async def _run():
        with patch(
            "utils.destination_connector_resolve.resolve_salesforce_token_record",
            new_callable=AsyncMock,
            return_value={"org_id": "org-1", "token_id": "tok-1"},
        ), patch(
            "services.labeling.catalog_helpers.resolve_connector_instance_id",
            new_callable=AsyncMock,
            return_value="inst-1",
        ), patch(
            "services.planning.catalog_grounding._structural_catalog_entries_salesforce",
            new_callable=AsyncMock,
            return_value=[
                {"entity_key": r["api_name"], "api_name": r["api_name"], "label": r["label"]}
                for r in structural
            ],
        ) as mock_structural, patch(
            "services.planning.catalog_grounding._semantics_catalog_entries",
            new_callable=AsyncMock,
            return_value=[
                {"entity_key": "DigitalWallet", "api_name": "DigitalWallet", "label": "Digital Wallet"}
            ],
        ) as mock_semantics:
            entries, ct, ctx = await load_side_catalog_summary(
                "user-1",
                {"source": "salesforce", "source_name": "SF 1"},
                role="source",
            )
        return entries, ct, ctx, mock_structural, mock_semantics

    entries, ct, ctx, mock_structural, mock_semantics = asyncio.run(_run())

    mock_structural.assert_awaited_once()
    mock_semantics.assert_not_called()
    assert ct == "salesforce"
    assert ctx.get("org_id") == "org-1"
    keys = {e["entity_key"] for e in entries}
    assert "Contact" in keys
    assert "DigitalWallet" in keys
