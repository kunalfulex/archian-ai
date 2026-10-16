from services.planning.multi_source import (
    apply_multi_source_hints,
    classification_suggests_multi_source,
    detect_additional_sources,
)
from services.planning.resource_catalog import (
    GoogleSheetRef,
    ResourceCatalog,
    SalesforceConnectionRef,
    SqlConnectionRef,
)


def _catalog():
    return ResourceCatalog(
        salesforce_connections=[
            SalesforceConnectionRef(name="Production", environment_type="production"),
            SalesforceConnectionRef(name="Sandbox", environment_type="sandbox"),
        ],
        sql_connections=[SqlConnectionRef(name="Warehouse DB", connector_type="postgres")],
    )


def test_field_list_and_does_not_trigger_multi_source():
    prompt = (
        "extract first name, last name, email, account name and account phone "
        "from contact object SF 2 org and migrate them to SF 1 org"
    )
    classification = {
        "source": "salesforce",
        "source_name": "SF 2",
        "destination": "salesforce",
        "destination_name": "SF 1",
        "multi_source_extract": False,
    }
    assert not classification_suggests_multi_source(classification)
    assert detect_additional_sources(classification, prompt, _catalog()) == []


def test_cross_org_sf_migrate_is_not_multi_source():
    cat = ResourceCatalog(
        salesforce_connections=[
            SalesforceConnectionRef(name="SF 1"),
            SalesforceConnectionRef(name="SF 2"),
        ]
    )
    classification = {
        "source": "salesforce",
        "source_name": "SF 2",
        "destination": "salesforce",
        "destination_name": "SF 1",
    }
    prompt = (
        "extract first name, last name, email from contact object SF 2 org "
        "and migrate them to SF 1 org"
    )
    assert not classification_suggests_multi_source(classification)
    extras = detect_additional_sources(classification, prompt, cat)
    assert extras == []


def test_detect_additional_sql_source():
    cat = _catalog()
    classification = {
        "source": "salesforce",
        "source_name": "Production",
        "destination": "google_sheets",
        "multi_source_extract": True,
        "extract_sources": [
            {"connector_type": "salesforce", "display_name": "Production"},
            {"connector_type": "sql", "display_name": "Warehouse DB"},
        ],
    }
    prompt = "extract from Production and also from Warehouse DB then export to sheets"
    extras = detect_additional_sources(classification, prompt, cat)
    assert any(e.get("display_name") == "Warehouse DB" for e in extras)


def test_apply_multi_source_sets_planner_field():
    cat = _catalog()
    out = apply_multi_source_hints(
        {
            "source": "salesforce",
            "source_name": "Production",
            "destination": "google_sheets",
            "multi_source_extract": True,
            "extract_sources": [
                {"connector_type": "salesforce", "display_name": "Production"},
                {"connector_type": "sql", "display_name": "Warehouse DB"},
            ],
        },
        "extract from Production and Warehouse DB and export",
        cat,
    )
    assert out.get("planner_additional_sources")


def _sheets_catalog():
    return ResourceCatalog(
        salesforce_connections=[SalesforceConnectionRef(name="SF 1")],
        google_sheet_rows=[
            GoogleSheetRef(spreadsheet_name="data list", sheet_name="Sheet1"),
            GoogleSheetRef(spreadsheet_name="contact list", sheet_name="Sheet1"),
        ],
        has_google_sheets=True,
    )


def test_multi_source_excludes_destination_workbook():
    cat = _sheets_catalog()
    prompt = (
        "extract contact records from SF 1 org and data list workbook, "
        "combine them and export it to contact list sheet"
    )
    classification = {
        "source": "salesforce",
        "source_name": "SF 1",
        "destination": "google_sheets",
        "destination_name": "contact list",
        "multi_source_extract": True,
        "extract_sources": [
            {"connector_type": "salesforce", "display_name": "SF 1"},
            {"connector_type": "google_sheets", "display_name": "data list"},
            {"connector_type": "google_sheets", "display_name": "contact list"},
        ],
    }
    extras = detect_additional_sources(classification, prompt, cat)
    names = [e.get("display_name") for e in extras]
    assert names == ["data list"]
    assert "contact list" not in names


def test_apply_multi_source_uses_classifier_extract_sources():
    cat = _sheets_catalog()
    prompt = (
        "extract contact records from SF 1 org and data list workbook, "
        "combine them and export it to contact list sheet"
    )
    classification = {
        "source": "salesforce",
        "source_name": "SF 1",
        "destination": "google_sheets",
        "destination_name": "contact list",
        "multi_source_extract": True,
        "extract_sources": [
            {"connector_type": "salesforce", "display_name": "SF 1"},
            {"connector_type": "google_sheets", "display_name": "data list"},
            {"connector_type": "google_sheets", "display_name": "contact list"},
        ],
    }
    out = apply_multi_source_hints(classification, prompt, cat)
    names = [e.get("display_name") for e in out.get("planner_additional_sources") or []]
    assert names == ["data list"]


def test_llm_multi_source_extract_sources():
    cat = _catalog()
    classification = {
        "source": "salesforce",
        "source_name": "Production",
        "destination": "google_sheets",
        "multi_source_extract": True,
        "extract_sources": [
            {"connector_type": "salesforce", "display_name": "Production"},
            {"connector_type": "sql", "display_name": "Warehouse DB"},
        ],
    }
    extras = detect_additional_sources(classification, "merge Production and Warehouse DB", cat)
    assert any(e.get("display_name") == "Warehouse DB" for e in extras)
    assert all(e.get("display_name") != "Production" for e in extras)


def test_llm_cross_org_migrate_not_multi_source():
    cat = ResourceCatalog(
        salesforce_connections=[
            SalesforceConnectionRef(name="SF 1"),
            SalesforceConnectionRef(name="SF 2"),
        ]
    )
    classification = {
        "source": "salesforce",
        "source_name": "SF 2",
        "destination": "salesforce",
        "destination_name": "SF 1",
        "multi_source_extract": False,
        "extract_sources": None,
    }
    prompt = (
        "extract first name, last name, email from contact object SF 2 org "
        "and migrate them to SF 1 org"
    )
    extras = detect_additional_sources(classification, prompt, cat)
    assert extras == []


def test_llm_multi_source_paraphrase_without_regex():
    cat = ResourceCatalog(
        salesforce_connections=[
            SalesforceConnectionRef(name="Production"),
            SalesforceConnectionRef(name="Sandbox"),
        ],
        sql_connections=[SqlConnectionRef(name="Warehouse DB")],
    )
    classification = {
        "source": "salesforce",
        "source_name": "Production",
        "destination": "sql",
        "destination_name": "Warehouse DB",
        "multi_source_extract": True,
        "extract_sources": [
            {"connector_type": "salesforce", "display_name": "Production"},
            {"connector_type": "salesforce", "display_name": "Sandbox"},
        ],
    }
    # No obvious regex multi-source markers.
    prompt = "Merge contacts from our two Salesforce environments into the warehouse database"
    extras = detect_additional_sources(classification, prompt, cat)
    assert any(e.get("display_name") == "Sandbox" for e in extras)


def test_classification_suggests_multi_source_from_llm_fields():
    assert classification_suggests_multi_source({"multi_source_extract": True})
    assert classification_suggests_multi_source(
        {
            "extract_sources": [
                {"connector_type": "salesforce", "display_name": "A"},
                {"connector_type": "salesforce", "display_name": "B"},
            ]
        }
    )
    assert not classification_suggests_multi_source({"multi_source_extract": False})


def test_apply_multi_source_strips_llm_fields():
    cat = _catalog()
    out = apply_multi_source_hints(
        {
            "source": "salesforce",
            "source_name": "Production",
            "destination": "google_sheets",
            "multi_source_extract": True,
            "extract_sources": [
                {"connector_type": "salesforce", "display_name": "Production"},
                {"connector_type": "sql", "display_name": "Warehouse DB"},
            ],
        },
        "extract from Production and Warehouse DB",
        cat,
    )
    assert out.get("planner_additional_sources")
    assert "multi_source_extract" not in out
    assert "extract_sources" not in out
