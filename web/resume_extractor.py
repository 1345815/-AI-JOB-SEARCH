"""Chinese campus-resume text -> candidate profile extraction.

The local extractor deliberately prefers missing data over invented data. It
understands common Chinese resume sections so it works without an AI provider.
"""

import re

from llm_client import llm_available, request_json


class ExtractionError(Exception):
    pass


PROFILE_FIELDS = [
    "name", "email", "phone", "city", "status", "github", "linkedin",
    "school", "highest_degree", "major", "graduation_date", "english_level",
    "location_preference", "resume_language", "languages", "education",
    "experiences", "projects", "skills", "certifications", "awards",
    "career_goals", "target_sectors", "deal_breakers", "notes",
]

_CITIES = "北京|上海|深圳|广州|杭州|成都|武汉|南京|苏州|西安|合肥|长沙|厦门|珠海|天津|重庆|青岛|郑州|济南"
_HEADERS = {
    "education": ("教育背景", "教育经历", "教育", "学历背景"),
    "experiences": ("实习经历", "工作经历", "实践经历", "校园经历", "社会实践"),
    "projects": ("项目经历", "项目经验", "科研经历", "作品集"),
    "skills": ("专业技能", "技能特长", "技能", "专业能力", "核心技能"),
    "certifications": ("证书", "资格", "语言能力"),
    "goals": ("求职意向", "求职目标", "职业目标", "意向岗位"),
}


def _clean_line(line):
    return re.sub(r"\s+", " ", line).strip(" \t-—–|｜•")


def _lines(text):
    return [_clean_line(line) for line in text.replace("\u3000", " ").splitlines() if _clean_line(line)]


def _sections(lines):
    result = {key: [] for key in _HEADERS}
    active = None
    for line in lines:
        compact = re.sub(r"[：:\s]", "", line)
        found = next((key for key, names in _HEADERS.items() if any(compact == name or (len(compact) <= 10 and name in compact) for name in names)), None)
        if found:
            active = found
        elif active:
            result[active].append(line)
    return result


def _period(value):
    match = re.search(r"((?:19|20)\d{2}(?:[.年/-]\d{1,2})?\s*(?:-|—|至|~|/)\s*(?:(?:19|20)\d{2}(?:[.年/-]\d{1,2})?|至今|现在))", value)
    return match.group(1) if match else ""


def _unique(values):
    output, seen = [], set()
    for value in values:
        value = _clean_line(str(value))
        if value and value.lower() not in seen:
            output.append(value)
            seen.add(value.lower())
    return output


def _source(value):
    return _clean_line(value)[:260]


def _extract_major_heuristic(context):
    value = re.sub(r"[（(][^）)]*[）)]", " ", context)
    value = re.sub(r"[\u4e00-\u9fa5A-Za-z]{2,40}(?:大学|学院|学校|University|College)", " ", value, flags=re.I)
    value = re.sub(r"本科|硕士(?:研究生)?|博士(?:研究生)?|大专|专科|学士", " ", value)
    period = _period(value)
    if period:
        value = value.replace(period, " ")
    value = re.sub(r"(?:19|20)\d{2}(?:[.年/-]\d{1,2})?", " ", value)
    excluded = re.compile(r"^(?:" + _CITIES + r"|全日制|统招|在校|学习|毕业|就读|就读于|获得)$")
    for candidate in re.split(r"[\s|｜,，;；:：、]+", value):
        candidate = candidate.strip("-—–.。()（）")
        if 2 <= len(candidate) <= 12 and re.fullmatch(r"[\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z+\-/]*", candidate) and not excluded.fullmatch(candidate) and not re.search(r"[年月日]", candidate):
            return candidate
    return ""


def _extract_education(lines, section):
    education, sources = [], []
    candidates = section or lines
    for index, line in enumerate(candidates):
        school_match = re.search(r"([\u4e00-\u9fa5A-Za-z]{2,40}(?:大学|学院|学校|University|College))", line, re.I)
        if not school_match:
            continue
        context = " ".join(candidates[index:index + 3])
        school = school_match.group(1)
        degree = re.search(r"(本科|硕士(?:研究生)?|博士(?:研究生)?|大专|专科|学士)", context)
        major = re.search(r"(?:专业|主修)\s*[:：]?\s*([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z、/（）()\- ]{1,30})", context)
        detail = major.group(1).strip(" |｜,，") if major else _extract_major_heuristic(context)
        entry = {"school": school, "degree": degree.group(1) if degree else "", "period": _period(context), "detail": detail[:60]}
        if not any(item["school"] == school for item in education):
            education.append(entry)
            sources.append(context)
    return education, _source(" ".join(sources))


def _extract_entries(section, kind):
    entries, sources = [], []
    if not section:
        return entries, ""
    date = re.compile(r"(?:19|20)\d{2}(?:[.年/-]\d{1,2})?\s*(?:-|—|至|~|/)\s*(?:(?:19|20)\d{2}(?:[.年/-]\d{1,2})?|至今|现在)")
    starts = [index for index, line in enumerate(section) if date.search(line)]
    for number, start in enumerate(starts):
        block = section[start:(starts[number + 1] if number + 1 < len(starts) else len(section))]
        heading, period = block[0], _period(block[0])
        prefix = date.sub("", heading).strip(" |｜·,，-—")
        points = [_clean_line(line.lstrip("•·-—0123456789.、 ")) for line in block[1:] if len(_clean_line(line)) > 3]
        if kind == "experiences":
            parts = [part.strip() for part in re.split(r"\s*(?:\||｜|·|–|—)\s*", prefix) if part.strip()]
            entry = {"company": (parts[0] if parts else "")[:80], "title": (parts[1] if len(parts) > 1 else prefix)[:80], "period": period, "points": points[:6]}
        else:
            entry = {"title": prefix[:100], "period": period, "points": points[:6]}
        if entry.get("title") and not any(item.get("title") == entry.get("title") and item.get("period") == period for item in entries):
            entries.append(entry)
            sources.extend(block)
    return entries, _source(" ".join(sources))


def _extract_skills(lines, section):
    content = " ".join(section) if section else " ".join(lines)
    hints = [
        "Python", "Java", "Go", "C++", "C#", "SQL", "MySQL", "PostgreSQL",
        "Excel", "Tableau", "Power BI", "React", "Vue", "JavaScript",
        "TypeScript", "Unity", "Godot", "Unreal", "Agent", "RAG",
        "LangChain", "Coze", "Dify", "ComfyUI", "Stable Diffusion",
        "Blender", "Figma", "Photoshop", "Axure", "PRD", "竞品分析",
        "用户访谈", "AB测试", "A/B测试", "短视频", "私域", "投放",
        "用户研究", "数据分析", "机器学习", "深度学习", "大模型", "LLM",
        "Prompt", "项目管理", "运营", "英语",
    ]
    skills = [hint for hint in hints if re.search(re.escape(hint), content, re.I)]
    for line in section:
        if "：" in line or ":" in line:
            skills.extend(re.split(r"[,，;；、/|｜]", re.split(r"[:：]", line, maxsplit=1)[1]))
        skills.extend(re.split(r"[、,，]", line))
    cleaned = [skill for skill in _unique(skills) if 1 < len(skill) <= 24 and not re.search(r"^(熟悉|掌握|了解|具备)$", skill)]
    return cleaned[:30], _source(content)


def _local_extract(text):
    extracted, confidence, sources, unrecognized = {}, {}, {}, []
    lines, sections = _lines(text), _sections(_lines(text))
    name = re.search(r"姓\s*名\s*[:：]\s*([\u4e00-\u9fa5]{2,4}|[A-Za-z][A-Za-z .'-]{1,40})", text)
    if not name:
        candidate = next((line for line in lines[:10] if re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", line)), "")
        name = re.match(r"(.+)", candidate) if candidate else None
    if name:
        extracted["name"] = name.group(1).strip(); confidence["name"] = "high" if "姓名" in text else "medium"; sources["name"] = _source(name.group(0))
    for key, match in (("email", re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)), ("phone", re.search(r"(?<!\d)(1[3-9]\d{9})(?!\d)", text)), ("github", re.search(r"https?://(?:www\.)?github\.com/[\w.-]+", text, re.I))):
        if match:
            extracted[key] = match.group(0); confidence[key] = "high"; sources[key] = _source(match.group(0))
    city = re.search(r"(?:现居|所在(?:地|城市)?|居住地|工作城市)\s*[:：]?\s*(" + _CITIES + r")", text)
    if city:
        extracted["city"] = city.group(1); confidence["city"] = "high"; sources["city"] = _source(city.group(0))
    goal = re.search(r"(?:求职意向|意向岗位|求职目标|职业目标)\s*[:：]?\s*([^\n]{2,80})", text)
    if goal:
        value = goal.group(1).strip(" |｜,，；;")
        extracted["career_goals"], extracted["status"] = _unique(re.split(r"[,，/、|｜]", value)), value
        confidence["career_goals"] = confidence["status"] = "high"; sources["career_goals"] = sources["status"] = _source(goal.group(0))
    locations = re.search(r"(?:意向城市|期望城市|工作地点)\s*[:：]?\s*([^\n]{2,80})", text)
    if locations:
        extracted["location_preference"] = locations.group(1).strip(); confidence["location_preference"] = "high"; sources["location_preference"] = _source(locations.group(0))
    education, education_source = _extract_education(lines, sections["education"])
    if education:
        extracted["education"] = education; confidence["education"] = "high" if sections["education"] else "medium"; sources["education"] = education_source
        latest = education[0]
        for key, value in (("school", latest["school"]), ("highest_degree", latest["degree"]), ("major", latest["detail"])):
            if value:
                extracted[key] = value; confidence[key] = confidence["education"]; sources[key] = education_source
        end = re.search(r"(?:-|—|至|~|/)\s*((?:19|20)\d{2})(?:[.年/-]\d{1,2})?", latest["period"])
        if end:
            extracted["graduation_date"] = end.group(1) + "年毕业"; confidence["graduation_date"] = confidence["education"]; sources["graduation_date"] = education_source
    for field, section_key, kind in (("experiences", "experiences", "experiences"), ("projects", "projects", "projects")):
        entries, source = _extract_entries(sections[section_key], kind)
        if entries:
            extracted[field] = entries; confidence[field] = "high"; sources[field] = source
    skills, skill_source = _extract_skills(lines, sections["skills"])
    if skills:
        extracted["skills"] = {"strong": skills, "moderate": [], "weak": []}; confidence["skills.strong"] = "high" if sections["skills"] else "low"; sources["skills.strong"] = skill_source
    english = re.search(r"(?:CET[- ]?[46]|大学英语[四六]级|英语[四六46]级|英语四六级|雅思\s*\d(?:\.\d)?|托福\s*\d{2,3})", text, re.I)
    if english:
        extracted["english_level"] = english.group(0).upper().replace(" ", ""); confidence["english_level"] = "high"; sources["english_level"] = _source(english.group(0))
    certificates = [line for line in sections["certifications"] if re.search(r"CET|雅思|托福|证书|资格", line, re.I)]
    if certificates:
        extracted["certifications"] = _unique(certificates); confidence["certifications"] = "medium"; sources["certifications"] = _source(" ".join(certificates))
    return extracted, confidence, unrecognized, sources


def _sanitize(data):
    return {key: value for key, value in data.items() if key in PROFILE_FIELDS} if isinstance(data, dict) else {}


_CONFIDENCE_LEVELS = {"low": 0, "medium": 1, "high": 2}
_HIGH_CONFIDENCE_FIELDS = {"name", "phone", "email"}
_MEDIUM_CONFIDENCE_FIELDS = {
    "school", "highest_degree", "major", "graduation_date", "english_level",
}
_LOW_CONFIDENCE_FIELDS = {
    "experiences", "projects", "skills", "certifications", "awards", "career_goals",
}
_SUMMARY_FIELDS = (
    "name", "phone", "email", "school", "highest_degree", "major",
    "graduation_date", "english_level", "status",
)


def _base_confidence(path):
    root = path.split(".", 1)[0]
    if root in _HIGH_CONFIDENCE_FIELDS:
        return "high"
    if root in _MEDIUM_CONFIDENCE_FIELDS:
        return "medium"
    if root in _LOW_CONFIDENCE_FIELDS:
        return "low"
    return "medium"


def _summary(extracted):
    return {field: extracted.get(field) or None for field in _SUMMARY_FIELDS}


def extract_profile_from_resume(text, user_id):
    """Return a reviewable extraction; it never writes a profile directly."""
    if llm_available():
        last_error = None
        for _ in range(2):
            try:
                system = ("你是中国校招简历信息抽取器。只输出 JSON 对象，不要解释。简历没有明确写出的信息必须为 null，禁止猜测。数字、时间和公司名原样保留。education/experiences/projects 要保留结构化条目与要点；skills 为 strong/moderate/weak 数组。除这些档案字段外，仅额外返回 confidence_overrides 和 source_text 两个对象。confidence_overrides 的键是字段路径、值仅为 high/medium/low；只有原文明确标注该字段时才上调。source_text 的键与字段路径对应，值必须逐字引用该字段所在的一句话，不得概括或编造。档案字段仅限：" + ", ".join(PROFILE_FIELDS))
                raw = request_json(system, "请抽取以下简历：\n\n" + text[:12000])
                extracted = _sanitize(raw)
                paths = []
                for key, value in extracted.items():
                    if value in (None, "", [], {}):
                        continue
                    if key == "skills" and isinstance(value, dict):
                        paths.extend("skills." + level for level, items in value.items() if items)
                    else:
                        paths.append(key)
                overrides = raw.get("confidence_overrides", {})
                overrides = overrides if isinstance(overrides, dict) else {}
                confidence = {}
                for path in paths:
                    base = _base_confidence(path)
                    override = overrides.get(path)
                    confidence[path] = max(
                        (base, override if override in _CONFIDENCE_LEVELS else base),
                        key=lambda level: _CONFIDENCE_LEVELS[level],
                    )
                raw_sources = raw.get("source_text", {})
                raw_sources = raw_sources if isinstance(raw_sources, dict) else {}
                sources = {
                    path: _clean_line(str(raw_sources[path]))[:120]
                    for path in paths if raw_sources.get(path)
                }
                unrecognized = raw.get("unrecognized", []) if isinstance(raw.get("unrecognized"), list) else []
                return {"extracted": extracted, "confidence": confidence, "unrecognized": unrecognized, "source_text": sources, "summary": _summary(extracted)}
            except RuntimeError as exc:
                last_error = exc
        raise ExtractionError("AI 简历识别暂时不可用：" + str(last_error)[:160])
    extracted, confidence, unrecognized, sources = _local_extract(text)
    return {"extracted": extracted, "confidence": confidence, "unrecognized": unrecognized, "source_text": sources, "summary": _summary(extracted)}
