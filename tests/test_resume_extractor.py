import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))

import resume_extractor  # noqa: E402


def test_llm_normal_and_filters_extra_fields(monkeypatch):
    monkeypatch.setattr(resume_extractor, "llm_available", lambda: True)

    def fake_request(system, user):
        return {
            "name": "王五",
            "email": "w@example.com",
            "hacker_field": "应被过滤",
            "unrecognized": ["无法归类的内容"],
        }

    monkeypatch.setattr(resume_extractor, "request_json", fake_request)
    result = resume_extractor.extract_profile_from_resume("简历内容", 1)
    assert "hacker_field" not in result["extracted"]
    assert result["extracted"]["name"] == "王五"
    assert result["unrecognized"] == ["无法归类的内容"]


def test_non_json_retries_then_raises(monkeypatch):
    monkeypatch.setattr(resume_extractor, "llm_available", lambda: True)
    calls = {"n": 0}

    def fail(system, user):
        calls["n"] += 1
        raise RuntimeError("bad json")

    monkeypatch.setattr(resume_extractor, "request_json", fail)
    with pytest.raises(resume_extractor.ExtractionError):
        resume_extractor.extract_profile_from_resume("简历内容", 1)
    assert calls["n"] == 2


def test_local_fallback_without_llm(monkeypatch):
    monkeypatch.setattr(resume_extractor, "llm_available", lambda: False)
    text = "姓名：赵六\n邮箱：z@example.com\n技能：Python、运营\n"
    result = resume_extractor.extract_profile_from_resume(text, 1)
    assert result["extracted"].get("name") == "赵六"
    assert result["extracted"].get("email") == "z@example.com"
