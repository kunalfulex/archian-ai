"""File attach → Salesforce org pick must keep pipeline and pin token_id."""
from __future__ import annotations

from services.file_extraction.pending_upload import pin_file_upload_on_classification
from services.planning.clarification_apply import apply_clarification_selections
from services.planning.connector_guards import classification_source_endpoint_resolved
from services.planning.gap_detector import _connector_clarifications
from services.planning.plan_builder import build_plan_steps
from services.planning.resource_catalog import ResourceCatalog, SalesforceConnectionRef
from services.planning.schemas import Clarification, ClarificationOption, ExecutionPlan, ResourceRef
from services.planning.scope_validator import validate_plan_scope_ops


def test_pinned_file_without_name_is_source_ready():
    cls = {
        "source": "file",
        "_file_upload_pinned": True,
        "_pending_file_ref": {"file_key": "pending/u/f.xlsx", "filename": "Contact.xlsx"},
    }
    assert classification_source_endpoint_resolved(cls) is True


def test_validate_keeps_file_migrate_pipeline_when_pinned():
    """Regression: do not strip extract/map/migrate after SF org pick for attached files."""
    pin_file_upload_on_classification(
        {
            "destination": "salesforce",
            "destination_name": "Production Org",
            "_destination_endpoint_clarified": True,
        },
        filename="Contact_Migration_Data.xlsx",
    )
    # Simulate thin resume state without source_name (pin flag only).
    thin = {
        "source": "file",
        "_file_upload_pinned": True,
        "destination": "salesforce",
        "destination_name": "Production Org",
        "_destination_endpoint_clarified": True,
    }
    ops = validate_plan_scope_ops(
        ["extract", "data_ops", "map", "migrate"],
        classification=thin,
        parsed_goal={
            "operations": ["extract", "data_ops", "map", "migrate"],
            "data_operations": ["cleanup"],
            "needs_destination": True,
        },
    )
    assert "extract" in ops
    assert "map" in ops
    assert "migrate" in ops


def test_sf_org_options_include_token_id():
    catalog = ResourceCatalog(
        salesforce_connections=[
            SalesforceConnectionRef(name="Production Org", token_id="tok-prod"),
            SalesforceConnectionRef(name="Dev Org", token_id="tok-dev"),
        ]
    )
    cls = {
        "source": "file",
        "source_name": "Contact.xlsx",
        "_file_upload_pinned": True,
        "_source_endpoint_clarified": True,
        "extract_fields_confirmed": True,
        "destination": "salesforce",
        "operations": ["extract", "map", "migrate"],
        "_plan_scope": {
            "operations": ["extract", "map", "migrate"],
            "needs_destination": True,
        },
    }
    clars = _connector_clarifications(cls, catalog, "migrate to salesforce")
    sf = next(c for c in clars if c.clarification_type == "pick_salesforce_org")
    ids = {o.resource_ref.connector_id for o in sf.options if o.resource_ref}
    assert "tok-prod" in ids
    assert "tok-dev" in ids


def test_pick_salesforce_org_sets_destination_connector_id():
    plan = ExecutionPlan(
        plan_id="p-sf",
        status="awaiting_clarification",
        clarifications=[
            Clarification(
                id="c-sf",
                clarification_type="pick_salesforce_org",
                prompt="Which Salesforce org?",
                options=[
                    ClarificationOption(
                        id="sf_destination_Production Org",
                        label="Production Org",
                        resource_ref=ResourceRef(
                            connector_type="salesforce",
                            display_name="Production Org",
                            connector_id="tok-prod",
                        ),
                    ),
                ],
                required=True,
            ),
        ],
    )
    out, _ = apply_clarification_selections(
        {
            "source": "file",
            "source_name": "Contact.xlsx",
            "_file_upload_pinned": True,
            "destination": "salesforce",
        },
        plan,
        [{"clarification_id": "c-sf", "option_id": "sf_destination_Production Org"}],
    )
    assert out["destination"] == "salesforce"
    assert out["destination_name"] == "Production Org"
    assert out["destination_connector_id"] == "tok-prod"
    assert out.get("_destination_endpoint_clarified") is True


def test_file_to_sf_org_pick_builds_migrate_plan():
    cls = pin_file_upload_on_classification(
        {
            "intent": "migrate_data",
            "mode": "migrate",
            "destination": "salesforce",
            "operations": ["extract", "data_ops", "map", "migrate"],
        },
        filename="Contact_Migration_Data.xlsx",
    )
    cls["_pending_file_ref"] = {
        "file_key": "pending-upload/u/c/Contact_Migration_Data.xlsx",
        "filename": "Contact_Migration_Data.xlsx",
    }
    plan = ExecutionPlan(
        plan_id="p-file-sf",
        status="awaiting_clarification",
        clarifications=[
            Clarification(
                id="c-sf",
                clarification_type="pick_salesforce_org",
                prompt="Which Salesforce org?",
                options=[
                    ClarificationOption(
                        id="sf_destination_Production Org",
                        label="Production Org",
                        resource_ref=ResourceRef(
                            connector_type="salesforce",
                            display_name="Production Org",
                            connector_id="tok-prod",
                        ),
                    ),
                    ClarificationOption(
                        id="sf_destination_Dev Org",
                        label="Dev Org",
                        resource_ref=ResourceRef(
                            connector_type="salesforce",
                            display_name="Dev Org",
                            connector_id="tok-dev",
                        ),
                    ),
                ],
                required=True,
            ),
        ],
        metadata={
            "goal": {
                "operations": ["extract", "data_ops", "map", "migrate"],
                "data_operations": ["cleanup"],
                "needs_destination": True,
                "needs_source": False,
            }
        },
    )
    out, updated = apply_clarification_selections(
        cls,
        plan,
        [{"clarification_id": "c-sf", "option_id": "sf_destination_Production Org"}],
    )
    out["_plan_scope"] = {
        "operations": ["extract", "data_ops", "map", "migrate"],
        "data_operations": ["cleanup"],
        "needs_destination": True,
    }
    out["operations"] = list(out["_plan_scope"]["operations"])
    built = build_plan_steps(
        out,
        prompt="Please clean and migrate the data to Salesforce",
        plan=updated,
    )
    tokens = built.metadata.get("pipeline_steps") or []
    assert "extract" in tokens
    assert "map" in tokens
    assert "migrate" in tokens
    assert built.steps
    assert out["destination_connector_id"] == "tok-prod"
