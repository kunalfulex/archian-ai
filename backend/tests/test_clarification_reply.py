from services.planning.clarification_compose import resolve_clarification_display_message
from services.planning.clarification_reply import parse_clarification_reply


def _pick_workbook_clar():
    return [
        {
            "id": "c1",
            "clarification_type": "pick_workbook",
            "required": True,
            "options": [
                {
                    "id": "gs_contact list",
                    "label": "contact list (Sheet1)",
                    "resource_ref": {"display_name": "contact list"},
                },
                {"id": "gs_create_new", "label": "Create new spreadsheet", "create_new": True},
            ],
        }
    ]


def test_parse_workbook_name_from_chat():
    got = parse_clarification_reply("use contact list", _pick_workbook_clar())
    assert got == [{"clarification_id": "c1", "option_id": "gs_contact list"}]


def test_parse_create_new_via_option_label():
    got = parse_clarification_reply("Create new spreadsheet", _pick_workbook_clar())
    assert got[0]["option_id"] == "gs_create_new"


def test_parse_create_workbook_name_bare_title():
    clar = [
        {
            "id": "c2",
            "clarification_type": "create_workbook_name",
            "required": True,
            "options": [],
        }
    ]
    got = parse_clarification_reply("SF Contacts Export", clar)
    assert got[0]["text_value"] == "SF Contacts Export"


def test_parse_create_workbook_name_step():
    clar = [
        {
            "id": "c2",
            "clarification_type": "create_workbook_name",
            "required": True,
            "options": [],
        }
    ]
    got = parse_clarification_reply("SF Contacts Export", clar)
    assert got[0]["text_value"] == "SF Contacts Export"


def test_format_picker_prompt_only_no_duplicate_list():
    clar = _pick_workbook_clar()
    clar[0]["prompt"] = "Which Google Sheet should we use for this export?"
    msg = resolve_clarification_display_message(
        clar,
        classification={},
        user_prompt="",
        message=clar[0]["prompt"],
    )
    assert "Google Sheet" in msg
    assert "contact list" not in msg
