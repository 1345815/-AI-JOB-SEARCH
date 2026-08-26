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


def test_non_json_retries_then_falls_back_to_local(monkeypatch):
    """AI 两次失败后不再抛错，降级到本地规则并标记 fallback。"""
    monkeypatch.setattr(resume_extractor, "llm_available", lambda: True)
    calls = {"n": 0}

    def fail(system, user):
        calls["n"] += 1
        raise RuntimeError("bad json")

    monkeypatch.setattr(resume_extractor, "request_json", fail)
    text = "姓名：钱七\n邮箱：q@example.com\n技能：Python、运营\n"
    result = resume_extractor.extract_profile_from_resume(text, 1)
    assert calls["n"] == 2
    assert result["fallback"] is True
    assert "fallback_reason" in result
    assert result["extracted"].get("name") == "钱七"
    assert result["extracted"].get("email") == "q@example.com"


def test_local_fallback_without_llm(monkeypatch):
    monkeypatch.setattr(resume_extractor, "llm_available", lambda: False)
    text = "姓名：赵六\n邮箱：z@example.com\n技能：Python、运营\n"
    result = resume_extractor.extract_profile_from_resume(text, 1)
    assert result["extracted"].get("name") == "赵六"
    assert result["extracted"].get("email") == "z@example.com"


def test_local_extracts_chinese_student_resume_sections(monkeypatch):
    monkeypatch.setattr(resume_extractor, "llm_available", lambda: False)
    text = """张同学
手机：13800138000 | 邮箱：student@example.com
求职意向：产品经理 / AI 产品实习生
教育背景
2023.09 - 2027.06 北京理工大学 本科 专业：信息管理与信息系统
实习经历
2025.06 - 2025.09 北京快速科技有限公司 | 产品实习生
• 访谈 20 位用户，输出需求文档并推动上线
项目经历
2024.10 - 2025.01 校园求职助手
• 使用 Python 和 React 完成简历分析功能
专业技能
Python、SQL、Figma、数据分析、项目管理
语言能力
CET-6
"""
    result = resume_extractor.extract_profile_from_resume(text, 1)
    data = result["extracted"]
    assert data["school"] == "北京理工大学"
    assert data["highest_degree"] == "本科"
    assert data["graduation_date"] == "2027年毕业"
    assert data["english_level"] == "CET-6"
    assert data["experiences"][0]["title"] == "产品实习生"
    assert data["projects"][0]["title"] == "校园求职助手"
    assert "Python" in data["skills"]["strong"]
    assert result["source_text"]["education"]


def test_name_without_姓名字段_and_phone_with_spaces(monkeypatch):
    """行首姓名 + 特征后缀；带空格/分隔符手机号。"""
    monkeypatch.setattr(resume_extractor, "llm_available", lambda: False)
    text = """李雷
求职意向：AI 游戏策划
电话：139 1234 5678
邮箱：lilei@example.com
教育背景
2021.09 - 2025.06 华中科技大学 数字媒体技术 本科
专业技能
Unity、C#、Python
"""
    result = resume_extractor.extract_profile_from_resume(text, 1)
    data = result["extracted"]
    assert data["name"] == "李雷"
    assert data["phone"] == "13912345678"
    assert data["school"] == "华中科技大学"
    assert data["major"] == "数字媒体技术"
    assert "Unity" in data["skills"]["strong"]


def test_experience_segmentation_without_dates(monkeypatch):
    """无日期格式的简历：按公司/项目名特征分段。"""
    monkeypatch.setattr(resume_extractor, "llm_available", lambda: False)
    text = """王强
求职意向：产品运营
邮箱：wq@example.com
实习经历
腾讯科技有限公司 产品运营实习生
负责用户增长与活动策划
通过数据复盘优化转化率
项目经历
校园二手交易平台
使用 Python 完成推荐系统
"""
    result = resume_extractor.extract_profile_from_resume(text, 1)
    data = result["extracted"]
    assert len(data["experiences"]) == 1
    assert "腾讯" in data["experiences"][0]["company"]
    assert len(data["projects"]) == 1
    assert "二手交易" in data["projects"][0]["title"]


def test_skill_extraction_does_not_split_sentences(monkeypatch):
    """技能提取不把普通句子拆成技能。"""
    monkeypatch.setattr(resume_extractor, "llm_available", lambda: False)
    text = """赵敏
邮箱：zm@example.com
专业技能
Python、SQL、数据分析
在实习期间负责用户增长数据分析
"""
    result = resume_extractor.extract_profile_from_resume(text, 1)
    skills = result["extracted"]["skills"]["strong"]
    assert "Python" in skills
    assert "SQL" in skills
    # 普通句子不应被拆进技能
    assert not any("在实习期间" in s or "负责用户增长数据分析" == s for s in skills)


def test_text_import_builds_plan_and_draft(monkeypatch):
    """粘贴文本识别：生成 plan 并落 pending 草稿。"""
    import tempfile
    import server as server_mod
    tmp_dir = tempfile.mkdtemp()
    monkeypatch.setattr(server_mod, "DB_FILE", __import__("pathlib").Path(tmp_dir) / "t.db")
    server_mod.init_db()
    monkeypatch.setattr(resume_extractor, "llm_available", lambda: False)

    with server_mod._DB_LOCK:
        conn = server_mod.db()
        cur = conn.execute("INSERT INTO users (username, role, profile_json) VALUES ('u','user','{}')")
        conn.commit()
        uid = cur.lastrowid
        conn.close()

    class Fake(server_mod.Handler):
        def __init__(self):
            self.path = "/api/profile/resume-import/text"
            self.command = "POST"
            self._sent = None
            self.client_address = ("127.0.0.1", 0)
            self.uid = uid
        def _send(self, code, body, *a, **k):
            self._sent = (code, body)
        def _json_body(self):
            return {"text": "姓名：周八\n邮箱：zb@example.com\n学校：厦门大学\n专业：计算机科学与技术\n技能：Python、数据分析\n教育背景\n2020.09-2024.06 厦门大学 本科\n项目经历\n校园招聘助手\n使用 Python 完成简历解析功能"}
        def _current_user(self):
            with server_mod._DB_LOCK:
                conn = server_mod.db()
                row = conn.execute("SELECT * FROM users WHERE id=?", (self.uid,)).fetchone()
                conn.close()
            return dict(row) if row else None

    h = Fake()
    server_mod.Handler._api(h, "POST", ["profile", "resume-import", "text"])
    code, body = h._sent
    assert code == 200
    plan = body["data"]
    assert plan["summary"]["name"] == "周八"
    assert plan["summary"]["email"] == "zb@example.com"
    # 草稿已落库
    with server_mod._DB_LOCK:
        conn = server_mod.db()
        row = conn.execute("SELECT * FROM resume_import_drafts WHERE user_id=? AND status='pending'", (uid,)).fetchone()
        conn.close()
    assert row is not None


def test_multi_experience_without_dates_segments_correctly(monkeypatch):
    """无日期多条目（公司+职位分行）应全部识别，company/title 拆开。"""
    monkeypatch.setattr(resume_extractor, "llm_available", lambda: False)
    text = """王五
邮箱：ww@example.com
实习经历
字节跳动
产品运营实习生
负责用户增长
腾讯科技
数据分析实习生
负责埋点统计
"""
    data = resume_extractor.extract_profile_from_resume(text, 1)["extracted"]
    assert len(data["experiences"]) == 2
    companies = [e["company"] for e in data["experiences"]]
    assert "字节跳动" in companies[0]
    assert "腾讯科技" in companies[1]
    assert data["experiences"][0]["title"] == "产品运营实习生"
    assert data["experiences"][1]["title"] == "数据分析实习生"


def test_variant_section_title_project_practice(monkeypatch):
    """变体标题"项目实践"也应识别。"""
    monkeypatch.setattr(resume_extractor, "llm_available", lambda: False)
    text = """陈二
邮箱：ce@example.com
项目实践
AI 简历助手
使用 Python 开发
"""
    data = resume_extractor.extract_profile_from_resume(text, 1)["extracted"]
    assert len(data["projects"]) == 1
    assert "简历助手" in data["projects"][0]["title"]


def test_company_title_split_with_space(monkeypatch):
    """空格分隔的'公司名 职位'应拆开（无公司标记词）。"""
    monkeypatch.setattr(resume_extractor, "llm_available", lambda: False)
    text = """李四
邮箱：ls@example.com
工作经历
字节跳动 产品运营实习生
负责用户增长与活动策划
"""
    data = resume_extractor.extract_profile_from_resume(text, 1)["extracted"]
    assert data["experiences"][0]["company"] == "字节跳动"
    assert data["experiences"][0]["title"] == "产品运营实习生"


def test_company_title_split_with_date_and_suffix(monkeypatch):
    """日期开头 + '腾讯科技有限公司 职位'：company 应含完整后缀。"""
    monkeypatch.setattr(resume_extractor, "llm_available", lambda: False)
    text = """赵六
邮箱：zl@example.com
实习经历
2023.06-2023.09 腾讯科技有限公司 产品运营实习生
负责短视频内容运营
"""
    data = resume_extractor.extract_profile_from_resume(text, 1)["extracted"]
    assert data["experiences"][0]["company"] == "腾讯科技有限公司"
    assert data["experiences"][0]["title"] == "产品运营实习生"
    assert data["experiences"][0]["period"] == "2023.06-2023.09"


def test_synonym_section_titles_all_recognized(monkeypatch):
    """同义词标题（实习经验/工作经验/课题研究/实践经历）全部识别，不因用词不同失败。"""
    monkeypatch.setattr(resume_extractor, "llm_available", lambda: False)
    text = """张三
邮箱：z@example.com
实习经验
字节跳动 产品实习生
负责用户增长
工作经验
腾讯科技有限公司 运营
负责内容运营
课题研究
大模型检索增强生成
负责 RAG 链路搭建
实践经历
校园创业团队 队长
负责团队管理
"""
    data = resume_extractor.extract_profile_from_resume(text, 1)["extracted"]
    assert len(data["experiences"]) == 3  # 实习经验 + 工作经验 + 实践经历
    assert "字节跳动" in data["experiences"][0]["company"]
    assert "腾讯科技" in data["experiences"][1]["company"]
    assert len(data["projects"]) == 1  # 课题研究
    assert "检索增强" in data["projects"][0]["title"]


def test_body_line_with_项目_word_does_not_split_section(monkeypatch):
    """正文含'参与项目'不应被误判为新的项目章节。"""
    monkeypatch.setattr(resume_extractor, "llm_available", lambda: False)
    text = """钱七
邮箱：q@example.com
实习经历
字节跳动 产品实习生
负责用户增长
参与项目：用户增长专题
负责渠道投放
"""
    data = resume_extractor.extract_profile_from_resume(text, 1)["extracted"]
    assert len(data["experiences"]) == 1
    assert data["experiences"][0]["company"] == "字节跳动"
