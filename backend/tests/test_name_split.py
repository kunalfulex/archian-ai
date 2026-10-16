"""Tests for generic split helpers (required-index monotoken placement)."""
from utils.split_field_utils import (
    resolve_split_required_indices,
    split_value_into_parts,
)


def test_monotoken_goes_to_first_required_index():
    parts = split_value_into_parts(
        "EMP20A00013",
        ["FirstName", "LastName"],
        required_indices=[1],
    )
    assert parts == ["", "EMP20A00013"]


def test_monotoken_numeric_goes_to_first_required_index():
    parts = split_value_into_parts(
        "5",
        ["FirstName", "LastName"],
        required_indices=[1],
    )
    assert parts == ["", "5"]


def test_monotoken_without_required_defaults_to_index_zero():
    parts = split_value_into_parts(
        "Ada",
        ["DisplayName", "LegalName"],
    )
    assert parts == ["Ada", ""]


def test_monotoken_uses_first_required_when_multiple():
    parts = split_value_into_parts(
        "X",
        ["A", "B", "C"],
        required_indices=[2, 1],
    )
    assert parts == ["", "", "X"]


def test_two_part_name_splits_normally():
    parts = split_value_into_parts(
        "John Doe",
        ["FirstName", "LastName"],
    )
    assert parts == ["John", "Doe"]


def test_three_part_name_keeps_middle_in_second_segment():
    parts = split_value_into_parts(
        "Mary Ann Lee",
        ["FirstName", "LastName"],
        split_char=" ",
    )
    assert parts == ["Mary", "Ann Lee"]


def test_resolve_required_indices_from_mapping():
    mapping = [
        {"field": "FirstName", "is_required": False},
        {"field": "LastName", "is_required": True},
    ]
    indices = resolve_split_required_indices(
        ["FirstName", "LastName"],
        mapping_items=mapping,
    )
    assert indices == [1]


def test_resolve_required_indices_from_field_metadata():
    meta = {("Contact", "LastName"): {"is_required": True}}
    keys = [("Contact", "FirstName"), ("Contact", "LastName")]
    indices = resolve_split_required_indices(
        ["FirstName", "LastName"],
        field_metadata=meta,
        metadata_keys=keys,
    )
    assert indices == [1]


def test_resolve_required_indices_from_rule():
    rule = {"required_output_fields": ["legal_name"]}
    indices = resolve_split_required_indices(
        ["display_name", "legal_name"],
        rule=rule,
    )
    assert indices == [1]
