import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))

from job_extractor import _local_extract, _normalize_extracted_job, extract_job_from_url  # noqa: E402


def test_local_extract_job():
    text = (
        "AI产品经理（校招）\n"
        "示例科技有限公司\n"
        "岗位职责：负责AI产品需求分析与方案设计，推进跨团队协作。\n"
        "任职要求：\n"
        "1. 2027届本科及以上学历\n"
        "2. 熟悉AI工具链\n"
        "3. 有产品实习经验优先\n"
    )
    job = _local_extract(text)
    assert job["title"]
    assert job["company"]
    assert len(job["requirements"]) >= 3


def test_local_extract_no_requirements():
    job = _local_extract("只有简单页面内容，没有任职要求分区。")
    assert job["requirements"] == []


def test_normalize_extracted_job_keeps_structured_fields_and_falls_back_company():
    job = _normalize_extracted_job(
        {"title": "AI 产品经理", "requirements": ["熟悉 Python"], "tags": ["Python"]},
        "AI 产品经理\n岗位职责：负责产品方案设计",
        "https://example.com/jobs/ai-product",
    )
    assert job["title"] == "AI 产品经理"
    assert job["company"] == "example"
    assert job["requirements"] == ["熟悉 Python"]
    assert job["tags"] == ["Python"]
    assert job["posting_type"] == "未知"


def test_normalize_extracted_job_rejects_empty_result():
    import pytest
    with pytest.raises(ValueError, match="没有识别到岗位详情"):
        _normalize_extracted_job({}, "登录后查看")


def test_extract_rejects_blocked_page(monkeypatch):
    import pytest
    monkeypatch.setattr("job_extractor.fetch_url_text", lambda url: "登录后查看完整职位信息。" + ("提示 " * 60))
    with pytest.raises(ValueError, match="登录或反爬拦截"):
        extract_job_from_url("https://example.com/job")
