"""Tests for Google Sheets column selection from user prompts."""

import pandas as pd

from utils.gsheets_column_selection import (
    build_keep_only_transformation_rules,
    extract_field_concepts_from_prompt,
    match_headers_to_concepts,
    prompt_requests_specific_columns,
    restrict_dataframe_to_prompt_columns,
)


def test_prompt_requests_specific_columns_multi_source():
    prompt = (
        "extract contact records like first name, last name and email from SF 1 org "
        "and same fields from data list workbook, combine them and export"
    )
    assert prompt_requests_specific_columns(prompt) is True


def test_extract_field_concepts_from_multi_source_prompt():
    prompt = (
        "extract contact records like first name, last name and email from SF 1 org "
        "and same fields from data list workbook"
    )
    concepts = extract_field_concepts_from_prompt(prompt)
    assert "first_name" in concepts
    assert "last_name" in concepts
    assert "email" in concepts


def test_match_headers_salesforce_style_columns():
    headers = [
        "Id",
        "FirstName",
        "LastName",
        "Email",
        "Phone",
        "Jigsaw",
        "Lead__c",
    ]
    matched = match_headers_to_concepts(
        headers, ["first_name", "last_name", "email"]
    )
    assert matched == ["FirstName", "LastName", "Email"]


def test_restrict_dataframe_keeps_only_requested_columns():
    df = pd.DataFrame(
        {
            "FirstName": ["A"],
            "LastName": ["B"],
            "Email": ["a@b.com"],
            "Phone": [""],
            "Jigsaw": [""],
        }
    )
    prompt = (
        "extract first name, last name and email from data list workbook "
        "and same fields from SF 1"
    )
    out, kept = restrict_dataframe_to_prompt_columns(
        df, prompt, source_name="data list workbook"
    )
    assert kept == ["FirstName", "LastName", "Email"]
    assert list(out.columns) == kept


def test_restrict_dataframe_unchanged_when_no_field_request():
    df = pd.DataFrame({"A": [1], "B": [2]})
    out, kept = restrict_dataframe_to_prompt_columns(df, "extract all data from sheet")
    assert kept is None
    assert list(out.columns) == ["A", "B"]


def test_build_keep_only_transformation_rules():
    rules = build_keep_only_transformation_rules(["FirstName", "Email"])
    fields = rules["transformation_rules"]["fields"]
    assert fields[0]["action"] == "keep_only"
    assert fields[0]["fields"] == ["FirstName", "Email"]
