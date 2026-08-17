import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))

from resume_parser import extract_resume_text  # noqa: E402


def test_txt_utf8(tmp_path):
    p = tmp_path / "resume.txt"
    p.write_text(
        "姓名：张三\n电话：13800138000\n邮箱：zhangsan@example.com\n"
        "教育：示例大学 2020-2024\n技能：Python、数据分析、产品设计、用户运营\n"
        "经历：在示例公司担任产品运营实习生，负责用户增长与活动策划，"
        "通过数据复盘优化转化率，参与多个项目并完成上线。\n",
        encoding="utf-8",
    )
    result = extract_resume_text(str(p))
    assert "张三" in result["text"]
    assert result["warning"] is None


def test_txt_gbk(tmp_path):
    p = tmp_path / "resume.txt"
    p.write_bytes("姓名：李四\n教育：示例大学\n".encode("gbk"))
    result = extract_resume_text(str(p))
    assert "李四" in result["text"]


def test_empty_file_warning(tmp_path):
    p = tmp_path / "empty.txt"
    p.write_text("   \n", encoding="utf-8")
    result = extract_resume_text(str(p))
    assert result["text"] == ""
    assert "扫描件" in (result["warning"] or "")


def test_unsupported_extension(tmp_path):
    p = tmp_path / "resume.png"
    p.write_bytes(b"x")
    with pytest.raises(ValueError):
        extract_resume_text(str(p))
