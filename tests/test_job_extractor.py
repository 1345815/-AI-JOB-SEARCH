import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))

from job_extractor import _local_extract  # noqa: E402


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
