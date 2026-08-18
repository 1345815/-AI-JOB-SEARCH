import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))

from form_extractor import extract_form  # noqa: E402
from form_filler import build_fill_plan  # noqa: E402


def test_extracts_known_recruiting_fields_from_html():
    form = extract_form(
        '<label for="candidate_name">姓名</label><input id="candidate_name">'
        '<label for="identity">身份证号</label><input id="identity">'
        '<label for="english">英语等级</label><select id="english"></select>'
    )
    keys = {field["key"] for field in form["fields"]}
    assert {"name", "id_card", "english_level"} <= keys


def test_sensitive_fields_require_manual_confirmation():
    plan = build_fill_plan(
        "demo",
        [
            {"label": "姓名", "key": "name", "type": "text"},
            {"label": "身份证号", "key": "id_card", "type": "text"},
            {"label": "紧急联系人电话", "key": "emergency_contact_phone", "type": "text"},
        ],
        {"name": "张同学", "id_card": "110101199001011234", "emergency_contact_phone": "13800000000"},
    )
    assert plan["mappings"][0]["value"] == "张同学"
    assert plan["mappings"][1]["value"] is None
    assert plan["mappings"][1]["manual_confirmation"] is True
    assert plan["mappings"][2]["value"] is None
