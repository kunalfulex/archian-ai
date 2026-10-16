"""Row-vs-column language sanitization for post-extract transform proposals."""

from post_extract.rules_proposer import (
    _prompt_suggests_row_presence_filter_not_column_drop,
    _schema_fields_hinted_for_presence,
    sanitize_transform_delta_row_vs_column_language,
)


def test_prompt_row_language_detects_records_without_field():
    assert _prompt_suggests_row_presence_filter_not_column_drop(
        "remove the record without account name"
    )
    assert _prompt_suggests_row_presence_filter_not_column_drop(
        "drop rows missing Email"
    )


def test_prompt_column_language_not_treated_as_row_filter():
    assert not _prompt_suggests_row_presence_filter_not_column_drop(
        "remove the AccountName column from the extract"
    )


def test_hint_maps_without_account_name_to_schema_field():
    schema = ["AccountName", "Email", "Phone"]
    hinted = _schema_fields_hinted_for_presence(
        "remove the record without account name",
        schema,
    )
    assert hinted == {"AccountName"}


def test_sanitize_strips_remove_accountname_list_form():
    parsed = {
        "transformation_rules": [
            {"action": "remove", "fields": ["AccountName"]},
        ]
    }
    out = sanitize_transform_delta_row_vs_column_language(
        "remove the record without account name",
        ["AccountName", "Email"],
        dict(parsed),
        operation_id="transform",
    )
    assert "transformation_rules" not in out


def test_sanitize_strips_exclude_wrapped_fields_form():
    parsed = {
        "transformation_rules": {
            "fields": [
                {"action": "exclude", "source_field": "AccountName"},
            ]
        }
    }
    out = sanitize_transform_delta_row_vs_column_language(
        "remove rows without account name",
        ["AccountName", "Email"],
        dict(parsed),
        operation_id="transform",
    )
    assert "transformation_rules" not in out


def test_sanitize_keeps_unrelated_removal_when_targets_do_not_match_hint():
    parsed = {
        "transformation_rules": [
            {"action": "remove", "fields": ["ScratchPad"]},
        ]
    }
    out = sanitize_transform_delta_row_vs_column_language(
        "remove the record without account name",
        ["AccountName", "Email", "ScratchPad"],
        dict(parsed),
        operation_id="transform",
    )
    fields = out["transformation_rules"]
    assert isinstance(fields, list) and len(fields) == 1


def test_sanitize_keeps_merge_drops_only_matching_removal():
    parsed = {
        "transformation_rules": [
            {
                "action": "merge",
                "source_fields": ["FirstName", "LastName"],
                "output_field": "name",
            },
            {"action": "remove", "fields": ["AccountName"]},
        ]
    }
    out = sanitize_transform_delta_row_vs_column_language(
        "remove records without account name",
        ["AccountName", "FirstName", "LastName"],
        dict(parsed),
        operation_id="transform",
    )
    kept = out["transformation_rules"]
    assert isinstance(kept, list) and len(kept) == 1
    assert (kept[0].get("action") or "").lower() == "merge"


def test_non_transform_operation_noop():
    parsed = {"transformation_rules": [{"action": "remove", "fields": ["AccountName"]}]}
    out = sanitize_transform_delta_row_vs_column_language(
        "remove the record without account name",
        ["AccountName"],
        dict(parsed),
        operation_id="filter",
    )
    assert out == parsed
