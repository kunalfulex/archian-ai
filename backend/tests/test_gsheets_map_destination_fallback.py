"""Google Sheets mapping must resolve destination workbook by name."""

from utils.document_category_match import _parse_gsheets_headers, normalize_doc_label


def test_parse_gsheets_headers_nested_headers_format():
    schema = {
        "headers": [
            {"name": "First Name", "type": "string", "position": 0},
            {"name": "Email", "type": "string", "position": 1},
        ]
    }
    assert _parse_gsheets_headers(schema) == {"first name", "email"}


def test_normalize_doc_label_strips_trailing_period():
    assert normalize_doc_label("Contact List.") == normalize_doc_label("Contact List")
