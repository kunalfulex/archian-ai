"""Linker scoring with category / description signals."""
from services.enterprise_graph.inventory import SourceEntity
from services.enterprise_graph.linker import score_pair


def test_category_match_boosts_score():
    b = SourceEntity(
        "m2",
        "sql",
        "customers",
        "customers",
        "customer",
        column_names=["email", "name"],
        category="Customer Records",
        category_description="Customer account master data",
        object_name="Account",
    )
    a = SourceEntity(
        "m1",
        "salesforce",
        "Account",
        "Account",
        "account",
        column_names=["email", "name"],
        category="Customer Records",
        category_description="Business accounts and hierarchies",
        object_name="Account",
    )
    score, evidence = score_pair(a, b)
    assert score >= 0.75
    assert evidence.get("category_match", 0) > 0
