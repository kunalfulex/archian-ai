from services.migration.transform_state import (
    has_mapping_transform_rules,
    should_apply_mapping_config_transform,
    should_apply_runtime_migration_transform,
)
from services.migration.write_policy import resolve_write_policy


def test_has_mapping_transform_rules():
    assert not has_mapping_transform_rules(None)
    assert not has_mapping_transform_rules({})
    assert has_mapping_transform_rules({"fields": [{"action": "split_2"}]})


def test_runtime_transform_skipped_when_already_transformed():
    rules = {"fields": [{"action": "split_2", "source_field": "Address"}]}
    assert not should_apply_runtime_migration_transform(
        is_transformed_data=True,
        transformation_rules=rules,
    )
    assert should_apply_runtime_migration_transform(
        is_transformed_data=False,
        transformation_rules=rules,
    )


def test_mapping_config_transform_skipped_after_map_persist():
    rules = {"fields": [{"action": "split_2", "source_field": "Address"}]}
    assert not should_apply_mapping_config_transform(
        is_transformed_data=False,
        mapping_config_transform_applied=True,
        transformation_rules=rules,
    )
    assert should_apply_mapping_config_transform(
        is_transformed_data=False,
        mapping_config_transform_applied=False,
        transformation_rules=rules,
    )


def test_write_policy_duplicate_check_drives_upsert_for_gsheets():
    policy = resolve_write_policy(
        destination="google_sheets",
        mapping={"Email": {"column": "Email", "is_duplicate_check": True}},
    )
    assert policy.operation == "upsert"
    assert policy.reason == "mapping_duplicate_check"
