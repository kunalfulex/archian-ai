"""Tests for enterprise graph report rollup."""
from services.enterprise_graph.report_builder import build_enterprise_report_snapshot


def test_rollup_two_members():
    snapshot = build_enterprise_report_snapshot(
        graph={"id": "g1", "name": "Test landscape"},
        member_results=[
            {
                "member_id": "m1",
                "display_name": "SQL DB",
                "connector_type": "sql",
                "role": "source",
                "report": {
                    "scores": {
                        "overall": 80,
                        "entities_assessed": 10,
                        "dimensions": {
                            "completeness": 80,
                            "quality": 85,
                            "consistency": 70,
                            "structure": 82,
                            "warehouse_readiness": 78,
                        },
                    },
                    "sections": {"data_gaps": {"findings": []}},
                    "entities": [{"entity_id": "t1", "quality_report": {"summary": {"data_quality_score": 90}}}],
                },
            },
            {
                "member_id": "m2",
                "display_name": "Sheets",
                "connector_type": "google_sheets",
                "role": "source",
                "report": {
                    "scores": {
                        "overall": 60,
                        "entities_assessed": 5,
                        "dimensions": {
                            "completeness": 60,
                            "quality": 55,
                            "consistency": 65,
                            "structure": 50,
                            "warehouse_readiness": 58,
                        },
                    },
                    "sections": {"data_gaps": {"findings": [{"category": "metadata", "entity_id": "*"}]}},
                    "entities": [],
                },
            },
        ],
        depth="partial",
    )
    assert snapshot["scores"]["sources_included"] == 2
    assert snapshot["scores"]["overall"] == 70.0


def test_lake_proposal_section_in_snapshot():
    snapshot = build_enterprise_report_snapshot(
        graph={"id": "g1", "name": "Lake graph"},
        member_results=[],
        depth="partial",
        lake_proposal_section={
            "proposal_id": "p1",
            "bronze_table_count": 3,
            "silver_view_count": 2,
        },
    )
    lake = snapshot["sections"]["lake_proposal"]
    assert lake["proposal_id"] == "p1"
    assert lake["bronze_table_count"] == 3


def test_cross_links_in_snapshot():
    links = [
        {
            "source_member_id": "m1",
            "target_member_id": "m2",
            "source_entity_id": "Account",
            "target_entity_id": "customers",
            "confidence": 0.9,
        }
    ]
    snapshot = build_enterprise_report_snapshot(
        graph={"id": "g1", "name": "G"},
        member_results=[],
        depth="partial",
        cross_links=links,
        mapped_entity_clusters=1,
    )
    assert snapshot["sections"]["connected_environment"]["cross_links"] == links
    assert snapshot["scores"]["mapped_entities"] == 1
    assert snapshot["scores"]["cross_links_count"] == 1
