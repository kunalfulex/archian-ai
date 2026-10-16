from services.data_assessment.assessment_planner import plan_entity_order
from services.data_assessment.models import EntityRef


def test_plan_entity_order_salesforce():
    entities = [
        EntityRef("Contact", "Contact"),
        EntityRef("Account", "Account"),
        EntityRef("Custom__c", "Custom"),
    ]
    ordered = plan_entity_order(entities, "salesforce")
    assert ordered[0].entity_id == "Account"
    assert ordered[1].entity_id == "Contact"


def test_plan_entity_order_sql_alphabetical():
    entities = [
        EntityRef("zebra", "zebra"),
        EntityRef("alpha", "alpha"),
    ]
    ordered = plan_entity_order(entities, "sql")
    assert [e.entity_id for e in ordered] == ["alpha", "zebra"]
