import pytest

from services.google_sheets.join_ranges import join_records
from services.planning.multi_source import _resolve_join_key


def test_resolve_join_key_email():
    lk, rk = _resolve_join_key(
        join_key="Email",
        left_headers=["Email", "FirstName"],
        right_headers=["Email", "Phone"],
    )
    assert lk == rk == "Email"


def test_resolve_join_key_missing_raises():
    with pytest.raises(ValueError, match="Join key is required"):
        _resolve_join_key(
            join_key=None,
            left_headers=["Email"],
            right_headers=["Email"],
        )


def test_join_records_merge():
    left = [{"Email": "a@b.com", "Name": "Ann"}]
    right = [{"Email": "a@b.com", "Score": 1}]
    headers, rows = join_records(
        ["Email", "Name"],
        left,
        ["Email", "Score"],
        right,
        "Email",
        "Email",
    )
    assert rows[0]["Name"] == "Ann"
    assert rows[0]["Score"] == 1
