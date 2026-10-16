from utils.rule_merge import merge_rules_for_operation, normalize_data_ops_delta_shape


def test_normalize_transform_merge_list_to_fields():
    delta = {
        "transformation_rules": {
            "merge": [
                {
                    "target_field": "Dealer Account",
                    "source_fields": ["DEALERCODE_VC", "DEALERBRANCHCODE_VC"],
                },
                {
                    "target_field": "Customer Account",
                    "source_fields": ["DEALERCODE_VC", "DEALERBRANCHCODE_VC", "CUSTCODE_VC"],
                },
                {
                    "target_field": "CRO User",
                    "source_fields": ["DEALERCODE_VC", "DEALERBRANCHCODE_VC", "CRONO_VC"],
                },
            ]
        }
    }

    out = normalize_data_ops_delta_shape(delta)
    fields = out["transformation_rules"]["fields"]
    assert len(fields) == 3
    assert all((f.get("action") or "").lower() == "merge" for f in fields)
    assert fields[0]["output_field"] == "Dealer Account"


def test_transform_merge_list_applies_multiple_rules():
    base = {}
    delta = {
        "transformation_rules": {
            "merge": [
                {"target_field": "A_B", "source_fields": ["A", "B"]},
                {"target_field": "A_C", "source_fields": ["A", "C"]},
            ]
        }
    }

    out = merge_rules_for_operation(base, delta, "transform")
    fields = out["transformation_rules"]["fields"]
    outputs = {f.get("output_field") for f in fields}
    assert outputs == {"A_B", "A_C"}


def test_filter_dedupe_cleanup_list_shapes_are_coerced():
    out = merge_rules_for_operation(
        {},
        {
            "filter_rules": [
                {"field": "Email", "operator": "valid_email"},
                {"field": "Status", "operator": "equals", "value": "Active"},
            ],
            "deduplication_rules": ["Email", "Phone"],
            "data_cleanup_rules": [
                {"field": "Email", "type": "email", "trim": True},
                {"field": "Phone", "type": "phone", "format": "e164"},
            ],
            "ordering_rules": [{"field": "Email", "direction": "asc"}],
        },
        "multi",
    )

    assert len(out["filter_rules"]["conditions"]) == 2
    assert out["deduplication_rules"]["enabled"] is True
    assert out["deduplication_rules"]["fields"] == ["Email", "Phone"]
    assert out["data_cleanup_rules"]["Email"]["type"] == "email"
    assert out["data_cleanup_rules"]["Phone"]["format"] == "e164"
    assert out["ordering_rules"]["sort"][0]["field"] == "Email"


def test_single_merge_shape_still_works():
    out = merge_rules_for_operation(
        {},
        {
            "transformation_rules": {
                "merge": {
                    "source_fields": ["DEALERCODE_VC", "DEALERBRANCHCODE_VC"],
                    "target_field": "Dealer Account",
                }
            }
        },
        "transform",
    )
    fields = out["transformation_rules"]["fields"]
    assert len(fields) == 1
    assert fields[0]["output_field"] == "Dealer Account"
