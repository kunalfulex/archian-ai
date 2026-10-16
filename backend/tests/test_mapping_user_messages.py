from utils.mapping_user_messages import (
    is_mapping_destination_clarification,
    mapping_destination_clarification_message,
)


def test_spreadsheet_detection_failure_is_clarification_not_error():
    msg = "Which Google Sheet should we use? Your connected workbooks: contact list."
    assert is_mapping_destination_clarification(msg)


def test_salesforce_clarification_asks_for_specific_destination_target():
    msg = mapping_destination_clarification_message(
        destination_name="Production 1",
        platform="salesforce",
        document_type="User Management",
    )
    assert "Production 1" in msg
    assert "Contact" in msg
    assert "Please be more specific about where in the destination" in msg
    assert is_mapping_destination_clarification(msg)
