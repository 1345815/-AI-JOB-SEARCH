import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))

from profile_merger import apply_paths, build_merge_plan  # noqa: E402


def test_empty_profile_full_fill():
    plan = build_merge_plan({}, {"name": "张三", "email": "z@example.com"}, {"name": "high", "email": "high"})
    assert any(i["field_path"] == "name" for i in plan["fills"])
    assert len(plan["updates"]) == 0


def test_conflict_not_auto_decided():
    current = {"name": "李四"}
    plan = build_merge_plan(current, {"name": "张三"}, {"name": "high"})
    assert any(i["field_path"] == "name" for i in plan["updates"])
    assert not plan["fills"]


def test_low_confidence_skipped():
    plan = build_merge_plan({}, {"city": "上海"}, {"city": "low"})
    assert any(i["field_path"] == "city" for i in plan["skipped"])


def test_apply_only_accepted_paths():
    plan = build_merge_plan({}, {"name": "王五", "email": "w@example.com"}, {"name": "high", "email": "high"})
    profile, applied = apply_paths({}, plan, ["name"])
    assert profile.get("name") == "王五"
    assert "email" not in profile
    assert applied == 1
