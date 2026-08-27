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


def test_en_dash_date_and_tech_list_lines(monkeypatch):
    """en dash（–）日期 + 'Python · xxx' 技术清单行：条目应分开、period 提取、清单作要点。"""
    monkeypatch.setattr(resume_extractor, "llm_available", lambda: False)
    text = """项目经历
CareerPilot｜企业级多 Agent AI 求职平台 独立开发 · 2026.03 – 至今
Python · SQLite · HTTP API · 自动化测试
简历匹配分析器（AI+NLP 在线工具） 独立开发 · 2025.07 – 至今
Python · Prompt 工程 · NLP 文本匹配
"""
    data = resume_extractor.extract_profile_from_resume(text, 1)["extracted"]
    assert len(data["projects"]) == 2
    assert data["projects"][0]["period"] == "2026.03 – 至今"
    assert data["projects"][1]["period"] == "2025.07 – 至今"
    assert "CareerPilot" in data["projects"][0]["title"]
    # 技术清单行作为要点而非并入标题
    assert any("SQLite" in p for p in data["projects"][0]["points"])
    assert "SQLite" not in data["projects"][0]["title"]


def test_paste_creates_resume_record_and_can_delete(monkeypatch):
    """粘贴文本识别后：创建简历文件记录（列表可见），且可删除。"""
    import tempfile
    import server as server_mod
    tmp_dir = tempfile.mkdtemp()
    monkeypatch.setattr(server_mod, "DB_FILE", __import__("pathlib").Path(tmp_dir) / "t.db")
    monkeypatch.setattr(server_mod, "RESUME_DIR", __import__("pathlib").Path(tmp_dir) / "resumes")
    server_mod.init_db()
    monkeypatch.setattr(resume_extractor, "llm_available", lambda: False)

    with server_mod._DB_LOCK:
        conn = server_mod.db()
        cur = conn.execute("INSERT INTO users (username, role, profile_json) VALUES ('u','user','{}')")
        conn.commit()
        uid = cur.lastrowid
        conn.close()

    class Fake(server_mod.Handler):
        def __init__(self, uid):
            self.path = "/api/profile/resume-import/text"
            self.command = "POST"
            self._sent = None
            self.client_address = ("127.0.0.1", 0)
            self.uid = uid
        def _send(self, code, body, *a, **k):
            self._sent = (code, body)
        def _json_body(self):
            return {"text": "姓名：周八\n邮箱：zb@example.com\n教育背景\n2020.09-2024.06 厦门大学 本科\n专业技能\nPython、SQL\n项目经历\nAI 助手\n使用 Python"}
        def _current_user(self):
            with server_mod._DB_LOCK:
                conn = server_mod.db()
                row = conn.execute("SELECT * FROM users WHERE id=?", (self.uid,)).fetchone()
                conn.close()
            return dict(row) if row else None

    h = Fake(uid)
    server_mod.Handler._api(h, "POST", ["profile", "resume-import", "text"])
    assert h._sent[0] == 200
    resume_id = h._sent[1]["data"].get("resume_id")
    assert resume_id is not None

    # 列表可见
    h2 = Fake(uid); h2.path = "/api/resumes"; h2.command = "GET"
    server_mod.Handler._api(h2, "GET", ["resumes"])
    rows = h2._sent[1]["data"]
    assert any(r["id"] == resume_id for r in rows)

    # 可删除
    h3 = Fake(uid); h3.path = "/api/resumes/%d" % resume_id; h3.command = "DELETE"
    server_mod.Handler._api(h3, "DELETE", ["resumes", str(resume_id)])
    assert h3._sent[0] == 200


def test_real_resume_fields_for_ai_intern_target(monkeypatch):
    """真实简历（马育琪 DataAgent）核心字段完整识别——以 AI 应用实习生为目标的场景。"""
    monkeypatch.setattr(resume_extractor, "llm_available", lambda: False)
    text = """马育琪
求职方向：Data Agent 应用研发（实习 / 校招）
1466588439@qq.com | 19103716492 | 郑州 | github.com/1345815
中原工学院 · 飞行器控制与信息工程 · 本科 · 2027 届
实习经历
多益网络 · 灵活就业岗 灵活就业 · 2026.06 – 2026.08
- 在游戏公司一线实战，多平台完整跑通「推广 → 注册 → 转化」获客链路
猿辅导 · 学习规划师 实习生 · 2026.01 – 2026.06
- 日均 10+ 场一对一咨询，收集用户真实需求并结构化反馈
项目经历
CareerPilot｜企业级多 Agent AI 求职平台 独立开发 · 2026.03 – 至今
Python · SQLite · HTTP API · 自动化测试
- 独立设计并开发企业级 AI 求职平台，覆盖 7 大功能模块
简历匹配分析器（AI+NLP 在线工具） 独立开发 · 2025.07 – 至今
Python · Prompt 工程 · NLP 文本匹配
- 独立设计并落地 Prompt 驱动的 3 维评估体系
专业技能
编程语言 Python（熟练）、C/C++（熟悉）
数据分析 Pandas · NumPy · Matplotlib
竞赛与获奖
全国未来飞行器设计大赛 · 河南赛区 省级一等奖｜2024
自我评价
自主驱动 · 快速验证型：从 0 到 1 独立完成多款产品上线
"""
    data = resume_extractor.extract_profile_from_resume(text, 1)["extracted"]
    assert data["name"] == "马育琪"
    assert data["phone"] == "19103716492"
    assert data["city"] == "郑州"
    assert data["graduation_date"] == "2027年毕业"
    assert data["career_goals"] == ["Data Agent 应用研发（实习 / 校招）"]
    assert data["school"] == "中原工学院"
    assert len(data["experiences"]) == 2
    assert len(data["projects"]) == 2
    assert any("一等奖" in c for c in data.get("certifications", []))
    # 自我评价不应混入获奖
    assert not any("自主驱动" in c for c in data.get("certifications", []))


def test_resume_end_section_self_review_not_leak(monkeypatch):
    """自我评价作为终止段：内容不混入前一章节。"""
    monkeypatch.setattr(resume_extractor, "llm_available", lambda: False)
    text = """张三
邮箱：z@example.com
项目经历
AI 助手
使用 Python 开发
自我评价
责任心强，学习能力强
"""
    data = resume_extractor.extract_profile_from_resume(text, 1)["extracted"]
    assert len(data["projects"]) == 1
    assert data["projects"][0]["title"] == "AI 助手"
    assert not any("责任心" in p for p in data["projects"][0].get("points", []))


def test_ai_http_503_falls_back_to_local(monkeypatch):
    """AI 通道抛 HTTPError 503（生产真实故障）→ 降级本地规则，不抛异常。"""
    import urllib.error
    monkeypatch.setattr(resume_extractor, "llm_available", lambda: True)
    calls = {"n": 0}

    def fail(system, user):
        calls["n"] += 1
        raise urllib.error.HTTPError("http://llm", 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(resume_extractor, "request_json", fail)
    text = "姓名：王五\n邮箱：w@example.com\n求职方向：AI 应用实习生\n"
    result = resume_extractor.extract_profile_from_resume(text, 1)
    assert calls["n"] == 2
    assert result["fallback"] is True
    assert "503" in result.get("fallback_reason", "")
    assert result["extracted"]["name"] == "王五"
    assert result["extracted"]["email"] == "w@example.com"


def test_notes_extracted_from_overview_and_self_review(monkeypatch):
    """个人概述/自我评价应提取到 notes（个人简介）。"""
    monkeypatch.setattr(resume_extractor, "llm_available", lambda: False)
    text = """张三
邮箱：z@example.com
个人概述
独立开发企业级 AI 平台，具备 Agent 工作流编排经验
关注系统准确性与稳定性
实习经历
字节跳动 产品实习生
负责用户增长
自我评价
责任心强，学习能力强
"""
    data = resume_extractor.extract_profile_from_resume(text, 1)["extracted"]
    notes = data.get("notes", "")
    assert "企业级 AI 平台" in notes
    assert "责任心强" in notes  # 多段合并
    assert "字节跳动" not in notes  # 不混入经历内容


def test_generate_resume_ai_channel_and_fallback(monkeypatch):
    """简历生成：AI 可用走定制通道；AI 失败/未配置回退本地模板。"""
    import server as server_mod
    job = {"title": "AI 应用研发实习生", "company": "转转", "description": "负责 LLM Agent 应用开发、Prompt 优化、数据分析"}
    profile = {
        "name": "马育琪", "status": "Data Agent 应用研发", "city": "郑州",
        "notes": "独立开发企业级多 Agent AI 平台",
        "skills": {"strong": ["Python", "Agent", "LLM"]},
        "education": [{"school": "中原工学院", "degree": "本科", "period": "2023-2027"}],
        "experiences": [{"company": "多益网络", "title": "灵活就业岗", "period": "2026.06-08", "points": ["跑通推广转化链路"]}],
        "projects": [{"title": "CareerPilot", "period": "2026.03-至今", "points": ["6 Agent 工作流"]}],
        "certifications": ["省级一等奖"], "languages": [],
    }
    # AI 可用 → 用 AI 输出
    monkeypatch.setattr(server_mod, "llm_available", lambda: True)
    monkeypatch.setattr(server_mod, "llm_chat", lambda messages, system=None: "# 马育琪 · 个人简历\n\n## 核心优势\n针对 AI 应用研发实习生深度定制的概述内容，强调 LLM Agent 工程与 Prompt 优化能力，匹配岗位关键词。\n\n## 项目经历\n- CareerPilot：企业级多 Agent 平台，6 Agent 工作流")
    r1 = server_mod.generate_resume(job, profile)
    assert "深度定制的概述内容" in r1
    # AI 抛错 → 回退模板
    def boom(messages, system=None):
        raise RuntimeError("LLM down")
    monkeypatch.setattr(server_mod, "llm_chat", boom)
    r2 = server_mod.generate_resume(job, profile)
    assert "## 核心优势" in r2 and "AI 定制内容" not in r2
    # 未配置 → 模板
    monkeypatch.setattr(server_mod, "llm_available", lambda: False)
    r3 = server_mod.generate_resume(job, profile)
    assert "## 项目经历" in r3


def test_generate_greeting_ai_and_fallback(monkeypatch):
    """投递招呼语：AI 生成 + 未配置回退模板；服务商预设齐全。"""
    import server as server_mod
    job = {"title": "AI 应用研发实习生", "company": "转转", "description": "负责 LLM Agent 应用开发"}
    profile = {"name": "马育琪", "status": "Data Agent 应用研发", "skills": {"strong": ["Python"]}}
    # AI 可用 → AI 输出
    monkeypatch.setattr(server_mod, "llm_available", lambda: True)
    monkeypatch.setattr(server_mod, "llm_chat", lambda m, system=None: "您好，看到贵司在做 LLM Agent 应用开发，我的实践非常对口，希望有机会聊聊！")
    r1 = server_mod.generate_greeting(job, profile)
    assert "Multi" not in r1 and "聊" in r1 or "您好" in r1
    # 未配置 → 兜底模板
    monkeypatch.setattr(server_mod, "llm_available", lambda: False)
    r2 = server_mod.generate_greeting(job, profile)
    assert "马育琪" in r2
    # 服务商预设
    assert "deepseek" in server_mod.AI_PROVIDER_PRESETS
    assert server_mod.AI_PROVIDER_PRESETS["deepseek"]["base_url"].startswith("https://")
    assert len(server_mod.AI_PROVIDER_PRESETS) >= 5


def test_score_job_two_stage_ai_deep(monkeypatch):
    """两阶段评分：deep=True 且达标时 AI 深度校准；低分/未配置不触发；批量调用默认本地。"""
    import server as server_mod
    job = {"id": "j1", "title": "AI 应用研发实习生", "company": "转转",
           "description": "负责 LLM Agent 应用开发、Prompt 优化、数据分析，熟悉 Python",
           "requirements": ["Python", "Agent"]}
    profile = {"name": "马育琪", "status": "Data Agent", "city": "郑州",
               "skills": {"strong": ["Python", "Agent", "LLM"]},
               "projects": [{"title": "CareerPilot", "points": ["6 Agent 工作流"]}],
               "experiences": [{"title": "实习", "company": "多益", "points": ["推广"]}],
               "career_goals": ["AI 应用研发"]}
    # 默认（批量搜索）不触发 AI
    monkeypatch.setattr(server_mod, "llm_available", lambda: True)
    ev0 = server_mod.score_job(job, profile)
    assert not ev0.get("ai")
    # deep + AI → 深度校准
    monkeypatch.setattr(server_mod, "llm_chat", lambda m, system=None: '{"overall_adjust": 5, "strengths": ["多 Agent 契合"], "gaps": ["无线上流量"], "advice": "突出编排亮点"}')
    ev1 = server_mod.score_job(job, profile, deep=True)
    assert ev1["ai"]["used"] and ev1["ai"]["adjust"] == 5
    assert any("多 Agent" in s for s in ev1["strengths"])
    # 低分不触发
    low = dict(job, title="门卫保安", description="小区门岗执勤、访客登记", requirements=["身体健康"])
    ev2 = server_mod.score_job(low, profile, deep=True)
    assert not ev2.get("ai")
    # AI 失败 → 保留本地
    def boom(m, system=None):
        raise RuntimeError("down")
    monkeypatch.setattr(server_mod, "llm_chat", boom)
    ev3 = server_mod.score_job(job, profile, deep=True)
    assert not ev3.get("ai") and ev3["overall"] > 0


def test_followup_reply_diagnose(monkeypatch):
    """跟进消息 / 回复分析 / 系统体检：AI 通道与本地兜底。"""
    import server as server_mod
    app = {"title": "AI 应用研发实习生", "company": "转转", "stage": "面试中", "notes": "HR 说下周面试"}
    # 跟进：AI
    monkeypatch.setattr(server_mod, "llm_available", lambda: True)
    monkeypatch.setattr(server_mod, "llm_chat", lambda m, system=None: "您好，想跟进面试进展，谢谢！")
    assert "跟进" in server_mod.generate_follow_up(app, {"name": "马育琪"})
    # 跟进：兜底
    monkeypatch.setattr(server_mod, "llm_available", lambda: False)
    assert "马育琪" in server_mod.generate_follow_up(app, {"name": "马育琪"})
    # 回复分析：AI
    monkeypatch.setattr(server_mod, "llm_available", lambda: True)
    monkeypatch.setattr(server_mod, "llm_chat", lambda m, system=None: '{"intent": "积极", "advice": "准备面试"}')
    assert server_mod.analyze_reply("约面试", app)["intent"] == "积极"
    # 回复分析：本地
    monkeypatch.setattr(server_mod, "llm_available", lambda: False)
    assert server_mod.analyze_reply("暂不合适", app)["intent"] == "消极"
    # 系统体检：返回列表且有 ok/warn 项
    items = server_mod.diagnose_system(None)
    assert len(items) >= 3 and all(i.get("name") and i.get("status") in ("ok", "warn", "fail") for i in items)


def test_resume_modes_prompt_injection(monkeypatch):
    """简历 5 档位：mode 正确注入 prompt；默认 standard 无额外要求。"""
    import server as server_mod
    job = {"title": "AI 应用研发实习生", "company": "转转", "description": "负责 LLM Agent 应用开发"}
    profile = {"name": "马育琪", "status": "Data Agent", "skills": {"strong": ["Python"]},
               "projects": [{"title": "CareerPilot", "points": ["6 Agent"]}], "experiences": []}
    seen = {}
    def fake_llm(messages, system=None):
        seen["sys"] = system or ""
        return "# 马育琪\n\n## 核心优势\nok\n\n## 项目经历\n- x\n\n## 教育背景\n- e\n\n## 专业技能\n- s\n\n## 证书与获奖\n- c"
    monkeypatch.setattr(server_mod, "llm_available", lambda: True)
    monkeypatch.setattr(server_mod, "llm_chat", fake_llm)
    assert len(server_mod.RESUME_MODES) == 5
    server_mod.generate_resume(job, profile, mode="ats")
    assert "ATS 机筛" in seen["sys"]
    server_mod.generate_resume(job, profile, mode="star")
    assert "STAR" in seen["sys"]
    server_mod.generate_resume(job, profile)
    assert "档位要求" not in seen["sys"]


def test_extract_campus_info(monkeypatch):
    """校招公告 AI 提取：多条目解析 / 无 AI 空 / 空文本空。"""
    import server as server_mod
    monkeypatch.setattr(server_mod, "llm_available", lambda: True)
    monkeypatch.setattr(server_mod, "llm_chat", lambda m, system=None:
        '[{"company":"美团","title":"2027届校招","ptype":"校招","location":"北京","deadline":"2027-01-15",'
        '"link":"https://campus.meituan.com","note":""},'
        '{"company":"字节","title":"实习","ptype":"实习","location":"深圳","deadline":"","link":"","note":""}]')
    items = server_mod.extract_campus_info("美团2027届校招，截止2027-01-15，https://campus.meituan.com；字节实习")
    assert len(items) == 2 and items[0]["company"] == "美团" and items[0]["deadline"] == "2027-01-15"
    assert items[1]["ptype"] == "实习"
    monkeypatch.setattr(server_mod, "llm_available", lambda: False)
    assert server_mod.extract_campus_info("文本") == []
    monkeypatch.setattr(server_mod, "llm_available", lambda: True)
    assert server_mod.extract_campus_info("") == []


def test_ext_token_lifecycle(monkeypatch):
    """插件令牌：生成幂等 / 轮换失效 / 坏 token 拒绝 / 脱敏档案。"""
    import server as server_mod
    server_mod.init_db()
    with server_mod._DB_LOCK:
        conn = server_mod.db()
        cur = conn.execute("INSERT INTO users (username, password_hash) VALUES (?,?)", ("extlife", "x"))
        uid = cur.lastrowid; conn.commit(); conn.close()
    t1 = server_mod.get_ext_token(uid)
    assert t1.startswith("cp_ext_")
    assert server_mod.get_ext_token(uid) == t1  # 幂等
    t2 = server_mod.rotate_ext_token(uid)
    assert t2 != t1
    assert server_mod.get_user_by_ext_token("bad") is None
    assert server_mod.get_user_by_ext_token("cp_ext_") is None
    pf = server_mod.profile_for_ext({"profile_json": '{"name":"马育琪","phone":"1","education":[{"school":"中原工学院","degree":"本科","detail":"飞行器"}]}'})
    assert pf["name"] == "马育琪" and pf["school"] == "中原工学院" and pf["degree"] == "本科"
    assert "profile_json" not in str(pf)  # 不含敏感字段
