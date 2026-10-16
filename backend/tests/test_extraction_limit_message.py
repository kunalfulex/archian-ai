from utils.extraction_limit_message import (
    credit_cap_applied,
    credit_cap_extraction_message,
)


def test_credit_cap_applied():
    assert credit_cap_applied(original_count=5000, can_extract=500) is True
    assert credit_cap_applied(original_count=500, can_extract=500) is False
    assert credit_cap_applied(original_count=100, can_extract=500) is False


def test_credit_cap_extraction_message():
    msg = credit_cap_extraction_message(extracted_count=500, original_count=5000)
    assert "500" in msg
    assert "5000" in msg
    assert "plan credit limit" in msg.lower()
