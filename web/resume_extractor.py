"""简历文本 → 档案 JSON 抽取（LLM + 本地兜底）。"""

import json
import re

from llm_client import llm_available, request_json


class ExtractionError(Exception):
    pass

PROFILE_FIELDS = [
    "name", "email", "phone", "city", "status", "github", "linkedin",
    "location_preference", "resume_language", "languages", "education",
    "experiences", "projects", "skills", "certifications", "awards",
    "career_goals", "target_sectors", "deal_breakers", "notes",
]

_SKILL_HINTS = [
    "python", "java", "go", "javascript", "typescript", "react", "vue",
    "sql", "数据分析", "机器学习", "深度学习", "ai", "llm", "prompt",
    "产品", "运营", "增长", "用户研究", "项目管理", "excel", "word",
    "photoshop", "figma", "剪辑", "文案", "英语", "cet-4", "cet-6",
]


def _snippet(text, value, max_len=80):
    if not value:
        return ""
    v = str(value)
    pos = text.find(v)
    if pos < 0:
        for token in re.findall(r"[^\s，。；、]{2,}", v):
            pos = text.find(token)
            if pos >= 0:
                break
    if pos < 0:
        return ""
    start = max(0, pos - 30)
    end = min(len(text), pos + max_len)
    return text[start:end].replace("\n", " ").strip()


def _local_extract(text):
    extracted = {}
    confidence = {}
    unrecognized = []

    name_match = re.search(r"姓\s*名\s*[:：]\s*(\S+)", text) or re.search(
        r"^\s*([\u4e00-\u9fa5]{2,4})\s*(?:求职简历|个人简历|简历|Resume)", text
    )
    first_line = next((l.strip() for l in text.splitlines() if l.strip()), "")
    if name_match:
        extracted["name"] = name_match.group(1)
        confidence["name"] = "high"
    elif first_line and len(first_line) <= 4:
        extracted["name"] = first_line
        confidence["name"] = "medium"

    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
    if email:
        extracted["email"] = email.group(0)
        confidence["email"] = "high"
    phone = re.search(r"(?<!\d)(1[3-9]\d{9})(?!\d)", text)
    if phone:
        extracted["phone"] = phone.group(0)
        confidence["phone"] = "high"
    city = re.search(r"(?:城市|所在地|现居)\s*[:：]?\s*([\u4e00-\u9fa5]{2,6})", text)
    if city:
        extracted["city"] = city.group(1)
        confidence["city"] = "high"
    github = re.search(r"https?://(?:www\.)?github\.com/[\w-]+", text)
    if github:
        extracted["github"] = github.group(0)
        confidence["github"] = "high"

    skills = []
    skill_line = re.search(r"(?:专业技能|技能|擅长|Skills)\s*[:：]?\s*(.+)", text, re.IGNORECASE)
    if skill_line:
        parts = re.split(r"[,，;；、/|]|\s{2,}", skill_line.group(1))
        for part in parts:
            p = part.strip().strip("。；，")
            if 1 <= len(p) <= 30 and p not in skills:
                skills.append(p)
    if skills:
        extracted["skills"] = {"strong": skills, "moderate": [], "weak": []}
        confidence["skills.strong"] = "medium"

    education = []
    for line in text.splitlines():
        school_match = re.search(r"([\u4e00-\u9fa5]{2,}(?:大学|学院|学校))", line)
        if not school_match:
            continue
        school = school_match.group(1)
        if any(school in (e.get("school") or "") for e in education):
            continue
        period_match = re.search(
            r"((?:19|20)\d{2}\s*[-—/.年]+\s*(?:(?:19|20)\d{2}|至今|现在|今))",
            line,
        )
        degree_match = re.search(r"(本科|硕士|博士|大专|学士)", line)
        detail = re.sub(r"^(教育|学校|学历|院校)\s*[:：]\s*", "", line.replace(school, ""))
        if period_match:
            detail = detail.replace(period_match.group(1), "")
        detail = re.sub(r"[\s\-—–|｜]+", " ", detail).strip(" :：")
        education.append({
            "school": school,
            "degree": degree_match.group(1) if degree_match else "",
            "period": period_match.group(1) if period_match else "",
            "detail": detail[:80],
        })
    if education:
        extracted["education"] = education
        confidence["education"] = "medium"

    experiences = []
    exp_pattern = re.compile(
        r"^\s*(.+?)\s*[|｜]\s*(.+?(?:公司|集团|科技|网络|银行|有限|事务所))"
        r"(?:\s*[（(]([^)）]*)[)）])?\s*$"
    )
    for line in text.splitlines():
        m = exp_pattern.match(line)
        if m:
            experiences.append({
                "title": m.group(1).strip(),
                "company": m.group(2).strip(),
                "period": (m.group(3) or "").strip(),
                "points": [],
            })
        elif re.search(r"(公司|实习|任职|就职)", line) and re.search(r"(19|20)\d{2}", line):
            unrecognized.append(line.strip())
    if experiences:
        extracted["experiences"] = experiences
        confidence["experiences"] = "medium"

    for line in text.splitlines():
        s = line.strip()
        if re.search(r"证书|CET|雅思|托福", s):
            extracted.setdefault("certifications", [])
            cleaned = re.sub(r"^[\s\-—–|｜]*", "", s).strip()
            if cleaned not in extracted["certifications"]:
                extracted["certifications"].append(cleaned)
            confidence.setdefault("certifications", "medium")
        elif re.search(r"(一等奖|二等奖|三等奖|获奖|冠军|优秀)", s):
            extracted.setdefault("awards", [])
            cleaned = re.sub(r"^(获奖|奖项)\s*[:：]\s*", "", s).strip()
            if cleaned not in extracted["awards"]:
                extracted["awards"].append(cleaned)
            confidence.setdefault("awards", "medium")

    return extracted, confidence, unrecognized


def _sanitize(data):
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in PROFILE_FIELDS}


def extract_profile_from_resume(text, user_id):
    """返回 {"extracted", "confidence", "unrecognized"}，不直接写库。"""
    extracted = {}
    confidence = {}
    unrecognized = []

    if llm_available():
        last_error = None
        for _ in range(2):
            try:
                system = (
                    "你是简历信息抽取器。只输出 JSON 对象，不要任何解释。"
                    "简历中没有的信息输出 null，禁止编造或推测。"
                    "数字、百分比、时间必须原样保留。"
                    "字段仅限：" + ", ".join(PROFILE_FIELDS) + "。"
                    "skills 结构为 {\"strong\":[],\"moderate\":[],\"weak\":[]}。"
                )
                user = "请抽取以下简历：\n\n" + text[:12000]
                raw = request_json(system, user)
                extracted = _sanitize(raw)
                confidence = {k: "high" for k in extracted}
                unrecognized = raw.get("unrecognized", []) if isinstance(raw.get("unrecognized"), list) else []
                break
            except RuntimeError as exc:
                last_error = exc
        else:
            raise ExtractionError(f"LLM 返回无法解析的 JSON：{str(last_error)[:200]}")
    else:
        extracted, confidence, unrecognized = _local_extract(text)

    return {
        "extracted": extracted,
        "confidence": confidence,
        "unrecognized": unrecognized,
        "source_text": {
            k: _snippet(text, v) for k, v in extracted.items() if v not in (None, "", [], {})
        },
    }
