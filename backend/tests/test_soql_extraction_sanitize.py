"""SOQL extraction sanitization for data_ops vs extract query."""
from utils.soql_extraction_sanitize import (
    apply_soql_extraction_post_process,
    fix_duplicate_soql_select_aliases,
    prompt_requests_post_extract_data_ops,
)


def test_prompt_detects_data_ops_from_plan_scope():
    goal = {
        "operations": ["extract", "map", "migrate"],
        "data_operations": ["dedupe", "filter"],
    }
    assert prompt_requests_post_extract_data_ops(
        goal=goal,
        classification={"active_job_id": "job-1", "plan_route": "fresh_path"},
    )


def test_strip_group_by_and_where_for_data_ops_prompt():
    soql = (
        "SELECT Id, FirstName, LastName, Email, Account.Name, Account.Phone, Phone, OwnerId "
        "FROM Contact WHERE Email != null AND LastName != null "
        "GROUP BY Id, FirstName, LastName, Email, Account.Name, Account.Phone, Phone, OwnerId"
    )
    p = "extract contacts, no duplicates and invalid email"
    goal = {"data_operations": ["dedupe", "filter"]}
    out = apply_soql_extraction_post_process(
        soql,
        p,
        classification={"active_job_id": "job-1"},
        goal=goal,
    )
    assert "GROUP BY" not in out.upper()
    assert "WHERE" not in out.upper()
    assert "Account.Phone" in out
    assert ", Phone," not in out.replace("Account.Phone", "")


def test_fix_duplicate_phone_alias():
    soql = (
        "SELECT Id, FirstName, LastName, Email, Account.Name, Account.Phone, Phone "
        "FROM Contact"
    )
    out = fix_duplicate_soql_select_aliases(soql)
    assert "Account.Phone" in out
    assert ", Phone" not in out
    assert "FROM Contact" in out
