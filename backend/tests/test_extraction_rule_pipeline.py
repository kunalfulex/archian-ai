"""Unit tests for extraction rule pipeline and rule merge."""

import pytest

from handlers.file_handler import apply_cleanup_and_validation_rules, matches_condition
from utils.extraction_rule_pipeline import (
    apply_extraction_rules,
    normalize_transformation_rule,
)
from utils.rule_merge import merge_transformation_rules, merge_rules_for_operation
from utils.extraction_rules_validate import sanitize_transformation_fields


def test_merge_transform_coerces_split_action_to_split_n():
    delta = {
        "transformation_rules": {
            "fields": [
                {
                    "source_fields": ["name"],
                    "target_fields": ["first_name", "last_name"],
                    "action": "split",
                    "delimiter": " ",
                }
            ]
        }
    }
    merged = merge_rules_for_operation({}, delta, "transform")
    fields = merged.get("transformation_rules", {}).get("fields") or []
    assert len(fields) == 1
    assert fields[0].get("action") == "split_2"
    assert fields[0].get("source_field") == "name"
    assert fields[0].get("output_fields") == ["first_name", "last_name"]
    data = [{"Email": "a@b.com", "Phone": "1", "name": "Kunal Fule"}]
    rules = {**merged, "source_schema": list(data[0].keys())}
    out = apply_extraction_rules(data, rules)
    assert out[0].get("first_name") == "Kunal"
    assert out[0].get("last_name") == "Fule"
    assert "name" not in out[0]


def test_merge_transform_coerces_string_field_list_to_keep_rules():
    """LLM column projection: fields: [\"Email\", \"FirstName\"] must drop Id/OwnerId via keep."""
    base = {"transformation_rules": {"fields": []}}
    delta = {
        "transformation_rules": {
            "fields": ["Email", "FirstName", "LastName", "Phone"],
        }
    }
    merged = merge_rules_for_operation(base, delta, "transform")
    fields = merged.get("transformation_rules", {}).get("fields") or []
    assert len(fields) == 1
    assert (fields[0].get("action") or "").lower() == "keep_only"
    assert set(fields[0].get("fields") or []) == {
        "Email",
        "FirstName",
        "LastName",
        "Phone",
    }
    data = [
        {
            "Email": "a@b.com",
            "FirstName": "K",
            "LastName": "F",
            "Id": "x",
            "OwnerId": "o",
            "Phone": "1",
        }
    ]
    rules = {**merged, "source_schema": list(data[0].keys())}
    out = apply_extraction_rules(data, rules)
    assert set(out[0].keys()) == {"Email", "FirstName", "LastName", "Phone"}


def test_merge_transform_coerces_prepend_when_only_source_fields_array():
    """Models often emit prepend like combine() with source_fields: [col] — must not be dropped."""
    base = {"transformation_rules": {"fields": []}}
    delta = {
        "transformation_rules": {
            "fields": [
                {"action": "prepend", "source_fields": ["AccountPhone"], "value": "+44"},
            ]
        }
    }
    merged = merge_rules_for_operation(base, delta, "transform")
    fields = merged.get("transformation_rules", {}).get("fields") or []
    acc = [
        f
        for f in fields
        if (f.get("action") or "").lower() == "prepend"
        and str(f.get("source_field") or "") == "AccountPhone"
    ]
    assert acc, "Expected AccountPhone prepend rule"
    assert acc[0].get("value") == "+44"


def test_normalize_transform_nested_prepend_only_in_transformation_dict():
    """LLM nests source_field and prepend under transformation (no top-level source_field)."""
    base = {"transformation_rules": {"fields": []}}
    delta = {
        "transformation_rules": [
            {
                "action": "transform",
                "transformation": {
                    "prepend": "+1",
                    "source_field": "Phone",
                    "output_field": "Phone",
                },
            },
        ],
    }
    merged = merge_rules_for_operation(base, delta, "transform")
    fields = merged.get("transformation_rules", {}).get("fields") or []
    prep = [f for f in fields if (f.get("action") or "").lower() == "prepend"]
    assert len(prep) == 1
    assert prep[0].get("source_field") == "Phone"
    assert prep[0].get("value") == "+1"


def test_merge_filter_op_merges_transformation_rules_add_prefix():
    """Filter-step LLM deltas often include transformation_rules; merge must apply them."""
    base = {
        "transformation_rules": {
            "fields": [
                {
                    "action": "merge",
                    "source_fields": ["FirstName", "LastName"],
                    "output_field": "name",
                },
            ]
        }
    }
    delta = {
        "filter_rules": {
            "conditions": [{"field": "Email", "operator": "valid_email"}],
            "logic": "AND",
        },
        "transformation_rules": [
            {"action": "add_prefix", "field": "Phone", "prefix": "+1"},
        ],
    }
    merged = merge_rules_for_operation(base, delta, "filter")
    fields = merged.get("transformation_rules", {}).get("fields") or []
    prep = [f for f in fields if (f.get("action") or "").lower() == "prepend"]
    assert any(p.get("source_field") == "Phone" and p.get("value") == "+1" for p in prep)


def test_merge_filter_op_merges_transformation_rules_operations_bucket():
    """Filter LLM deltas often use transformation_rules.operations — must flatten into fields."""
    base = {"transformation_rules": {"fields": []}}
    delta = {
        "filter_rules": {
            "conditions": [{"field": "Email", "operator": "valid_email"}],
            "logic": "AND",
        },
        "transformation_rules": {
            "operations": [
                {"field": "AccountPhone", "operation": "add_prefix", "value": "+44"},
            ]
        },
    }
    merged = merge_rules_for_operation(base, delta, "filter")
    fields = merged.get("transformation_rules", {}).get("fields") or []
    prep = [f for f in fields if (f.get("action") or "").lower() == "prepend"]
    assert any(
        p.get("source_field") == "AccountPhone" and p.get("value") == "+44" for p in prep
    )


def test_merge_filter_op_coerces_filter_rules_top_level_array():
    merged = merge_rules_for_operation(
        {},
        {
            "filter_rules": [
                {"field": "Email", "operator": "valid_email"},
                {"field": "AccountName", "operator": "is_not_empty"},
            ],
        },
        "filter",
    )
    conds = (merged.get("filter_rules") or {}).get("conditions") or []
    assert len(conds) == 2


def test_sanitize_coerces_merge_single_field_nested_pattern_to_regex_replace():
    fields = [
        {
            "action": "merge",
            "source_fields": ["AccountPhone"],
            "output_field": "AccountPhone",
            "transformation": {"pattern": r"^\+44", "replacement": "+44"},
        }
    ]
    out = sanitize_transformation_fields(fields)
    assert len(out) == 1
    assert out[0]["action"] == "regex_replace"
    assert out[0]["source_field"] == "AccountPhone"


def test_sanitize_coerces_nested_transform_prepend():
    fields = [
        {
            "action": "transform",
            "transformation": {
                "prepend": "+1",
                "source_field": "Phone",
                "output_field": "Phone",
            },
        }
    ]
    out = sanitize_transformation_fields(fields)
    assert len(out) == 1
    assert out[0]["action"] == "prepend"
    assert out[0]["value"] == "+1"


def test_sanitize_coerces_transform_pattern_to_regex_replace():
    fields = [
        {
            "action": "transform",
            "source_field": "Phone",
            "output_field": "Phone",
            "transformation": {"pattern": "x", "replacement": "y"},
        }
    ]
    out = sanitize_transformation_fields(fields)
    assert len(out) == 1
    assert out[0]["action"] == "regex_replace"
    assert out[0]["pattern"] == "x"
    assert out[0]["replacement"] == "y"


def test_merge_transform_llm_pattern_dict_becomes_regex_replace():
    base = {"transformation_rules": {"fields": []}}
    delta = {
        "transformation_rules": [
            {
                "action": "transform",
                "source_field": "Phone",
                "output_field": "Phone",
                "transformation": {
                    "pattern": r"\+1\+44(\d{3})(\d{3})(\d{4})",
                    "replacement": "+1 ($1) $2-$3",
                },
            },
        ],
    }
    merged = merge_rules_for_operation(base, delta, "transform")
    fields = merged.get("transformation_rules", {}).get("fields") or []
    rr = [f for f in fields if (f.get("action") or "").lower() == "regex_replace"]
    assert len(rr) == 1
    assert "(191)" in rr[0]["replacement"] or "$1" in rr[0]["replacement"]


def test_apply_regex_replace_malformed_double_country_prefix():
    from handlers.file_handler import apply_transformations

    data = [{"Phone": "+1+441914956203"}]
    rules = {
        "fields": [
            {
                "action": "regex_replace",
                "source_field": "Phone",
                "output_field": "Phone",
                "pattern": r"\+1\+44(\d{3})(\d{3})(\d{4})",
                "replacement": "+1 ($1) $2-$3",
            }
        ]
    }
    out = apply_transformations(data, rules)
    assert out[0]["Phone"] == "+1 (191) 495-6203"


def test_apply_extraction_rules_regex_replace_coerced_transform_survives():
    """Rules loaded with legacy action transform + pattern must apply (sanitizer coerces)."""
    data = [{"Phone": "+1+441914956203", "Email": "a@b.com"}]
    rules = {
        "transformation_rules": {
            "fields": [
                {
                    "action": "transform",
                    "source_field": "Phone",
                    "output_field": "Phone",
                    "transformation": {
                        "pattern": r"\+1\+44(\d{3})(\d{3})(\d{4})",
                        "replacement": "+1 ($1) $2-$3",
                    },
                },
            ]
        },
        "filter_rules": {"conditions": [{"field": "Email", "operator": "valid_email"}], "logic": "AND"},
    }
    out = apply_extraction_rules(data, rules)
    assert len(out) == 1
    assert out[0]["Phone"] == "+1 (191) 495-6203"


def test_normalize_merge_source_field_list():
    rule = {
        "action": "merge",
        "source_field": ["FirstName", "LastName"],
        "output_field": "FullName",
    }
    norm = normalize_transformation_rule(rule)
    assert norm["source_fields"] == ["FirstName", "LastName"]
    assert "source_field" not in norm or not isinstance(norm.get("source_field"), list)


def test_merge_transform_nested_transformation_dict_prepend():
    """LLM may emit transformation: { type, value } instead of a string."""
    base = {"transformation_rules": {"fields": []}}
    delta = {
        "transformation_rules": [
            {
                "action": "transform",
                "source_field": "Phone",
                "output_field": "Phone",
                "transformation": {"type": "prepend", "value": "+1"},
            },
        ],
    }
    merged = merge_rules_for_operation(base, delta, "transform")
    fields = merged.get("transformation_rules", {}).get("fields") or []
    prep = [f for f in fields if isinstance(f, dict) and (f.get("action") or "").lower() == "prepend"]
    assert len(prep) == 1
    assert prep[0].get("source_field") == "Phone"
    assert prep[0].get("value") == "+1"


def test_merge_transform_llm_remove_fields_and_transform_prepend():
    """LLM deltas: remove+fields[] and transform+prepend must survive merge and sanitize."""
    base = {"transformation_rules": {"fields": []}}
    delta = {
        "transformation_rules": [
            {"action": "merge", "source_fields": ["FirstName", "LastName"], "output_field": "name"},
            {"action": "remove", "fields": ["Id", "OwnerId"]},
            {
                "action": "transform",
                "source_field": "Phone",
                "output_field": "Phone",
                "transformation": "prepend",
                "value": "+1",
            },
        ],
    }
    merged = merge_rules_for_operation(base, delta, "transform")
    fields = merged.get("transformation_rules", {}).get("fields") or []
    assert any((f.get("action") or "").lower() == "prepend" for f in fields)
    excludes = [
        f.get("source_field")
        for f in fields
        if isinstance(f, dict) and (f.get("action") or "").lower() == "exclude"
    ]
    assert "Id" in excludes and "OwnerId" in excludes


def test_merge_skips_when_source_columns_absent_preserves_name():
    """Stale merge First+Last→name must not clear name when sources are already gone."""
    from handlers.file_handler import apply_transformations

    data = [{"name": "Kunal Fule", "Email": "x@y.com", "Phone": "5127576000"}]
    rules = {
        "fields": [
            {"action": "merge", "source_fields": ["FirstName", "LastName"], "output_field": "name"},
        ]
    }
    out = apply_transformations(data, rules)
    assert len(out) == 1
    assert out[0].get("name") == "Kunal Fule"


def test_prepend_skips_empty_phone():
    from handlers.file_handler import apply_transformations

    data = [{"Phone": "", "Email": "a@b.com"}, {"Phone": "   ", "Email": "b@c.com"}]
    rules = {
        "fields": [{"action": "prepend", "source_field": "Phone", "output_field": "Phone", "value": "+1"}]
    }
    out = apply_transformations(data, rules)
    assert out[0].get("Phone") == ""
    assert out[1].get("Phone") == ""


def test_apply_exclude_bulk_fields_and_prepend():
    """Executor must honor exclude+fields[] (merged shape) and prepend."""
    data = [
        {
            "AccountName": "Acme",
            "Email": "a@b.com",
            "FirstName": "Kunal",
            "LastName": "Fule",
            "Id": "x1",
            "OwnerId": "o1",
            "Phone": "5127576000",
        }
    ]
    schema = list(data[0].keys())
    rules = {
        "transformation_rules": {
            "fields": [
                {
                    "action": "merge",
                    "source_fields": ["FirstName", "LastName"],
                    "output_field": "name",
                },
                {"action": "exclude", "fields": ["Id", "OwnerId"], "source_field": None},
                {"action": "prepend", "source_field": "Phone", "output_field": "Phone", "value": "+1"},
            ]
        },
        "source_schema": schema,
    }
    out = apply_extraction_rules(data, rules)
    assert len(out) == 1
    row = out[0]
    assert row.get("name") == "Kunal Fule"
    assert "Id" not in row and "OwnerId" not in row
    assert row.get("Phone") == "+1 (512) 757-6000"


def test_merge_transformation_dedupes_merge():
    existing = [
        {"action": "keep", "source_field": "Email", "output_field": "Email", "order": 0},
    ]
    new = [
        {
            "action": "merge",
            "source_fields": ["FirstName", "LastName"],
            "output_field": "FullName",
        },
        {
            "action": "merge",
            "source_fields": ["FirstName", "LastName"],
            "output_field": "FullName",
        },
    ]
    merged = merge_transformation_rules(existing, new)
    merge_rules = [r for r in merged if r.get("action") == "merge"]
    assert len(merge_rules) == 1


def test_merge_transformation_rules_dedupes_output_field_case_variants():
    """Two post-extract merges of the same columns with name vs Name must collapse to one rule."""
    fields = [
        {
            "action": "merge",
            "source_fields": ["FirstName", "LastName"],
            "output_field": "name",
            "order": 0,
        },
        {
            "action": "prepend",
            "source_field": "Phone",
            "output_field": "Phone",
            "value": "+1",
            "order": 1,
        },
        {
            "action": "merge",
            "source_fields": ["FirstName", "LastName"],
            "output_field": "Name",
            "order": 2,
        },
    ]
    merged = merge_transformation_rules([], fields)
    merge_rules = [r for r in merged if r.get("action") == "merge"]
    assert len(merge_rules) == 1
    assert merge_rules[0]["output_field"] == "name"


def test_apply_order_filter_after_transform():
    data = [
        {"a": "1", "b": "x"},
        {"a": "2", "b": "y"},
        {"a": "3", "b": "x"},
    ]
    rules = {
        "transformation_rules": {
            "fields": [{"action": "keep", "source_field": "a", "output_field": "a"}]
        },
        "filter_rules": {
            "conditions": [{"field": "b", "operator": "equals", "value": "x"}],
            "logic": "AND",
        },
    }
    out = apply_extraction_rules(data, rules)
    assert len(out) == 2
    assert all(r["b"] == "x" for r in out)


def test_merge_rules_for_operation_multi():
    base = {"transformation_rules": {"fields": []}}
    delta = {
        "filter_rules": {"conditions": [{"field": "Email", "operator": "is_not_empty"}], "logic": "AND"}
    }
    merged = merge_rules_for_operation(base, delta, "filter")
    assert merged.get("filter_rules", {}).get("conditions")


def test_merge_transform_remove_invalid_email_maps_to_filter_not_column_exclude():
    """LLM often emits remove+Email+condition invalid; that must not become exclude Email (drops column before filter)."""
    base = {"transformation_rules": {"fields": []}}
    delta = {
        "transformation_rules": [
            {"action": "remove", "source_field": "Email", "condition": "invalid"},
        ]
    }
    merged = merge_rules_for_operation(base, delta, "transform")
    tr_fields = merged.get("transformation_rules", {}).get("fields") or []
    assert not any(
        isinstance(f, dict)
        and str(f.get("source_field", "")).lower() == "email"
        and (f.get("action") or "").lower() == "exclude"
        for f in tr_fields
    )
    conds = (merged.get("filter_rules") or {}).get("conditions") or []
    assert any(
        c.get("field") == "Email" and c.get("operator") == "valid_email" for c in conds if isinstance(c, dict)
    )


def test_apply_invalid_email_row_semantics_keeps_valid_rows():
    """Repro: transform then filter; Email must remain through transform so valid_email can run."""
    data = []
    for i in range(2):
        data.append(
            {
                "AccountName": "A",
                "Email": "not-an-email",
                "FirstName": "F",
                "LastName": "L",
                "Id": f"id{i}",
                "OwnerId": "ow",
                "Phone": "1",
            }
        )
    for i in range(14):
        data.append(
            {
                "AccountName": "B",
                "Email": f"ok{i}@example.com",
                "FirstName": "F",
                "LastName": "L",
                "Id": f"jd{i}",
                "OwnerId": "ow",
                "Phone": "1",
            }
        )
    rules = {
        "transformation_rules": {
            "fields": [
                {"action": "exclude", "source_field": "Id"},
                {"action": "exclude", "source_field": "OwnerId"},
                {
                    "action": "merge",
                    "source_fields": ["FirstName", "LastName"],
                    "output_field": "Name",
                },
                {"action": "exclude", "source_field": "Email", "condition": "invalid"},
            ]
        },
        "filter_rules": {
            "conditions": [{"field": "Email", "operator": "valid_email"}],
            "logic": "AND",
        },
    }
    out = apply_extraction_rules(data, rules)
    assert len(out) == 14
    assert all("Name" in r for r in out)
    assert all("Email" in r for r in out)


def test_apply_extraction_rules_filter_before_merge_preserves_name_based_filters():
    """Filter-first: row filters on FirstName/LastName run before merge removes those columns."""
    data = [
        {"FirstName": "A", "LastName": "B", "Email": "a@b.co"},
        {"FirstName": "", "LastName": "X", "Email": "x@y.co"},
    ]
    rules = {
        "transformation_rules": {
            "fields": [
                {
                    "action": "merge",
                    "source_fields": ["FirstName", "LastName"],
                    "output_field": "FullName",
                },
            ]
        },
        "filter_rules": {
            "conditions": [
                {"field": "FirstName", "operator": "is_not_empty"},
                {"field": "LastName", "operator": "is_not_empty"},
            ],
            "logic": "AND",
        },
    }
    # Transform-first: is_not_empty on FirstName/LastName is dropped (columns merged away).
    out_merge_first = apply_extraction_rules(data, rules, transform_before_filter=True)
    assert len(out_merge_first) == 2

    out_filter_first = apply_extraction_rules(data, rules, transform_before_filter=False)
    assert len(out_filter_first) == 1
    assert out_filter_first[0].get("FullName") == "A B"
    assert "FirstName" not in out_filter_first[0]


def test_filter_in_operator_strings():
    data = [{"s": "a"}, {"s": "b"}, {"s": "A"}]
    rules = {
        "filter_rules": {
            "conditions": [{"field": "s", "operator": "in", "value": ["a", "x"]}],
            "logic": "AND",
        }
    }
    out = apply_extraction_rules(data, rules)
    assert len(out) == 2
    assert {r["s"] for r in out} == {"a", "A"}


def test_filter_not_in_operator():
    data = [{"s": "a"}, {"s": "b"}, {"s": "c"}]
    rules = {
        "filter_rules": {
            "conditions": [{"field": "s", "operator": "not_in", "value": ["b"]}],
            "logic": "AND",
        }
    }
    out = apply_extraction_rules(data, rules)
    assert len(out) == 2
    assert {r["s"] for r in out} == {"a", "c"}


def test_filter_in_numeric_coercion():
    data = [{"n": "10"}, {"n": 10}, {"n": "11"}]
    rules = {
        "filter_rules": {
            "conditions": [{"field": "n", "operator": "in", "value": [10, 12]}],
            "logic": "AND",
        }
    }
    out = apply_extraction_rules(data, rules)
    assert len(out) == 2


def test_filter_in_empty_list_matches_nothing():
    data = [{"s": "a"}]
    rules = {
        "filter_rules": {
            "conditions": [{"field": "s", "operator": "in", "value": []}],
            "logic": "AND",
        }
    }
    out = apply_extraction_rules(data, rules)
    assert len(out) == 0


def test_matches_condition_unknown_operator_strict(monkeypatch):
    monkeypatch.setenv("DATA_OPS_STRICT_FILTER_OPERATORS", "true")
    # Reload-dependent: file_handler reads env at call time
    rec = {"f": "x"}
    cond = {"field": "f", "operator": "totally_unknown", "value": "y"}
    assert matches_condition(rec, cond) is False


def test_matches_condition_unknown_operator_lenient(monkeypatch):
    """Unknown operators fail closed even without DATA_OPS_STRICT_FILTER_OPERATORS."""
    monkeypatch.delenv("DATA_OPS_STRICT_FILTER_OPERATORS", raising=False)
    rec = {"f": "x"}
    cond = {"field": "f", "operator": "totally_unknown", "value": "y"}
    assert matches_condition(rec, cond) is False


def test_merge_filter_drops_unknown_operator():
    merged = merge_rules_for_operation(
        {},
        {
            "filter_rules": {
                "conditions": [{"field": "x", "operator": "bogus_op", "value": 1}],
                "logic": "AND",
            }
        },
        "filter",
    )
    assert merged.get("filter_rules", {}).get("conditions") == []


def test_filter_regex_matches():
    data = [{"s": "hello world"}, {"s": "nope"}]
    rules = {
        "filter_rules": {
            "conditions": [{"field": "s", "operator": "regex_matches", "value": r"world$"}],
            "logic": "AND",
        }
    }
    out = apply_extraction_rules(data, rules)
    assert len(out) == 1
    assert out[0]["s"] == "hello world"


def test_filter_date_between():
    data = [{"d": "2026-01-10"}, {"d": "2026-02-01"}, {"d": "bad"}]
    rules = {
        "filter_rules": {
            "conditions": [
                {
                    "field": "d",
                    "operator": "date_between",
                    "value": ["2026-01-01", "2026-01-31"],
                }
            ],
            "logic": "AND",
        }
    }
    out = apply_extraction_rules(data, rules)
    assert len(out) == 1
    assert out[0]["d"] == "2026-01-10"


def test_cleanup_trim():
    data = [{"Code": "  x  "}]
    rules = {"Code": {"trim": True}}
    out = apply_cleanup_and_validation_rules(data, [{"data_cleanup_rules": rules}])
    assert out[0]["Code"] == "x"


def test_cleanup_date_to_iso():
    data = [{"d": "01/15/2026"}]
    rules = {"d": {"type": "date", "input_formats": ["%m/%d/%Y"], "to_iso": True}}
    out = apply_cleanup_and_validation_rules(data, [{"data_cleanup_rules": rules}])
    assert out[0]["d"] == "2026-01-15"


def test_cleanup_regex_replace():
    data = [{"t": "aa-bb-cc"}]
    rules = {"t": {"regex_replace": {"pattern": "-", "replacement": ".", "max_count": 2}}}
    out = apply_cleanup_and_validation_rules(data, [{"data_cleanup_rules": rules}])
    assert out[0]["t"] == "aa.bb.cc"


def test_cleanup_phone_e164():
    pytest.importorskip("phonenumbers")
    data = [{"p": "(415) 555-2671"}]
    rules = {"p": {"type": "phone", "format": "e164", "default_region": "US"}}
    out = apply_cleanup_and_validation_rules(data, [{"data_cleanup_rules": rules}])
    assert out[0]["p"] == "+14155552671"


def test_cleanup_country_alpha2():
    pytest.importorskip("pycountry")
    data = [{"c": "United States"}]
    rules = {"c": {"normalize_country": "alpha2"}}
    out = apply_cleanup_and_validation_rules(data, [{"data_cleanup_rules": rules}])
    assert out[0]["c"] == "US"


def test_is_empty_true_when_field_missing():
    assert matches_condition({"a": 1}, {"field": "missing", "operator": "is_empty"}) is True


def test_is_not_empty_false_when_field_missing():
    assert matches_condition({"a": 1}, {"field": "missing", "operator": "is_not_empty"}) is False


def test_equals_false_when_field_missing():
    assert matches_condition({"a": 1}, {"field": "missing", "operator": "equals", "value": "x"}) is False


def test_apply_ordering_sort_and_limit():
    data = [
        {"x": 2, "y": "b"},
        {"x": 1, "y": "a"},
        {"x": 1, "y": "c"},
    ]
    rules = {
        "ordering_rules": {
            "sort": [
                {"field": "x", "direction": "asc"},
                {"field": "y", "direction": "asc"},
            ],
            "limit": 2,
        }
    }
    out = apply_extraction_rules(data, rules)
    assert len(out) == 2
    assert out[0]["y"] == "a"
    assert out[1]["y"] == "c"


def test_apply_ordering_sort_without_limit_when_disabled():
    data = [{"x": i} for i in range(10)]
    rules = {
        "ordering_rules": {
            "sort": [{"field": "x", "direction": "desc"}],
            "limit": 3,
        }
    }
    out = apply_extraction_rules(data, rules, apply_ordering_limit=False)
    assert len(out) == 10
    assert out[0]["x"] == 9


def test_merge_ordering_rules_overlay():
    from utils.rule_merge import merge_ordering_rules

    a = merge_ordering_rules(None, {"sort": [{"field": "x", "direction": "asc"}], "limit": 5})
    assert a["limit"] == 5
    b = merge_ordering_rules(a, {"limit": 10})
    assert b["limit"] == 10
    assert b.get("sort")


def test_prepend_us_country_skips_when_international_uk():
    from handlers.file_handler import apply_transformations

    data = [
        {"Phone": "+44 191 495 6203", "AccountPhone": "+441914956203"},
    ]
    rules = {
        "fields": [
            {"action": "prepend", "source_field": "Phone", "output_field": "Phone", "value": "+1"},
            {
                "action": "prepend",
                "source_field": "AccountPhone",
                "output_field": "AccountPhone",
                "value": "+1",
            },
        ]
    }
    out = apply_transformations(data, rules)
    phone_c = "".join((out[0].get("Phone") or "").split())
    ap_c = "".join((out[0].get("AccountPhone") or "").split())
    assert phone_c.startswith("+44") and "+1+" not in phone_c
    assert ap_c.startswith("+44") and "+1+" not in ap_c


def test_regex_replace_caret_plus_one_repairs_double_country_on_uk():
    from handlers.file_handler import apply_transformations

    data = [{"Phone": "+441914956203"}]
    rules = {
        "fields": [
            {
                "action": "regex_replace",
                "source_field": "Phone",
                "pattern": "^",
                "replacement": "+1",
            },
        ]
    }
    out = apply_transformations(data, rules)
    compact = "".join((out[0].get("Phone") or "").split())
    assert compact.startswith("+44")
    assert "+1+" not in compact


def test_merge_transform_accepts_llm_top_level_merge_regex_buckets():
    """GPT sometimes returns {\"merge\": {...}, \"regex_replace\": {...}} instead of fields[]."""
    from utils.rule_merge import merge_rules_for_operation

    delta = {
        "transformation_rules": {
            "merge": {"source_fields": ["FirstName", "LastName"], "target_field": "name"},
            "regex_replace": {"field": "Phone", "pattern": "^", "replacement": "+1"},
        }
    }
    merged = merge_rules_for_operation({}, delta, "transform")
    fields = merged.get("transformation_rules", {}).get("fields") or []
    assert len(fields) >= 2
    acts = {(f.get("action") or "").lower() for f in fields}
    assert "merge" in acts
    assert "regex_replace" in acts


def test_merge_filter_coerces_operation_combine_and_add_prefix():
    """Filter-step LLM often uses operation= instead of action= — sanitize must not drop rules."""
    from utils.rule_merge import merge_rules_for_operation

    delta = {
        "transformation_rules": [
            {"operation": "combine", "source_fields": ["F", "L"], "target_field": "full"},
            {"operation": "add_prefix", "field": "Phone", "prefix": "+1"},
        ]
    }
    merged = merge_rules_for_operation({}, delta, "filter")
    fields = merged.get("transformation_rules", {}).get("fields") or []
    assert len(fields) == 2
