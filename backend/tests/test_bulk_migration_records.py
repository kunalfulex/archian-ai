import asyncio
from unittest.mock import AsyncMock

from services.salesforce_bulk_migration_flow import create_migration_records_from_bulk_results


def test_migration_records_include_primary_object_without_duplicate_check(monkeypatch):
    """Primary object fields without is_duplicate_check must still appear in salesforce_results."""
    captured_results = []

    async def fake_create(**kwargs):
        return "rec-1"

    async def fake_update(**kwargs):
        captured_results.append(kwargs.get("salesforce_results"))

    monkeypatch.setattr(
        "services.salesforce_bulk_migration_flow.MigrationRecordsService.create_migration_record",
        AsyncMock(side_effect=fake_create),
    )
    monkeypatch.setattr(
        "services.salesforce_bulk_migration_flow.MigrationRecordsService.update_migration_record_result",
        AsyncMock(side_effect=fake_update),
    )

    mapping = {
        "ACTION_VC": {
            "object": "ServiceAppointment",
            "field": "Status",
            "is_duplicate_check": False,
        },
        "APPOINTDATE_D": {
            "object": "ServiceAppointment",
            "field": "DueDate",
            "is_duplicate_check": False,
        },
        "Customer Account": {
            "object": "Account",
            "field": "Name",
            "lookup_of": "ServiceAppointment.ParentRecordId",
            "is_duplicate_check": True,
        },
    }

    original_data = [
        {
            "ACTION_VC": "NewAppointment",
            "APPOINTDATE_D": "2018-11-21",
            "Customer Account": "Acme Corp",
        }
    ]

    bulk_result = {
        "results": {
            "ServiceAppointment": {
                "insert_record_indices": [0],
                "insert_result": {
                    "successful_results": [
                        {"sf__Id": "0WOSERVICE001"},
                    ],
                    "failed_results": [],
                }
            },
            "Account": {
                "update_result": {
                    "successful_results": [
                        {"Name": "Acme Corp", "sf__Id": "001ACCOUNT001"},
                    ],
                    "failed_results": [],
                }
            },
        }
    }

    lookup_mapping = {"Account|Name:Acme Corp": "001ACCOUNT001"}

    asyncio.run(
        create_migration_records_from_bulk_results(
            bulk_result=bulk_result,
            original_data=original_data,
            mapping=mapping,
            batch_id="batch-1",
            user_id="user-1",
            lookup_mapping=lookup_mapping,
        )
    )

    assert len(captured_results) == 1
    sf_results = captured_results[0]
    assert "ServiceAppointment" in sf_results
    assert "Account" in sf_results
    assert sf_results["ServiceAppointment"]["id"] == "0WOSERVICE001"
    assert sf_results["Account"]["id"] == "001ACCOUNT001"
