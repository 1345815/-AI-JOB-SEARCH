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
    "education": ("教育背景", "教育经历", "教育", "学历背景", "学习经历", "学历"),
    "experiences": ("实习经历", "实习经验", "工作经历", "工作经验", "实践经历", "实践", "校园经历", "社会实践", "社会经历", "任职经历", "职业经历", "实习及工作经历", "工作及实习经历", "实习与工作经历", "实习和项目经历", "个人经历", "暑期实习"),
    "projects": ("项目经历", "项目经验", "项目实践", "项目", "项目作品", "科研经历", "科研项目", "学术项目", "课程项目", "大创项目", "课题研究", "毕业设计", "作品集", "参与项目"),
    "skills": ("专业技能", "技能特长", "技能", "专业能力", "核心技能", "技能证书"),
    "certifications": ("证书", "资格", "语言能力"),
    "goals": ("求职意向", "求职目标", "职业目标", "意向岗位", "求职方向"),
}

# 标题行核心词分类：命中任一同义词即归类，从根上避免枚举遗漏
_SECTION_RULES = [
    ("education", re.compile(r"(?:教育|学历|学习经历|学业)")),
    ("experiences", re.compile(r"(?:实习|工作|实践|任职|职业|校园|社会|暑期).{0,3}(?:经历|经验)")),
    ("projects", re.compile(r"(?:项目|科研|课题|作品|毕设|课程设计)")),
    ("skills", re.compile(r"技能|专业能力|核心能力|特长|技术栈")),
    ("certifications", re.compile(r"证书|资格|语言能力|荣誉|竞赛|获奖")),
    ("goals", re.compile(r"求职意向|职业目标|意向岗位|求职方向|应聘岗位")),
]


def _match_section_title(compact):
    """判断一行是否为章节标题并归类；返回 key 或 None。
    要求：短行 + 非动作词开头（避免把'参与项目'这类正文行误判为标题）。"""
    if not compact or len(compact) > 14:
        return None
    if _ACTION_WORDS.match(compact):
        return None
    for key, pattern in _SECTION_RULES:
        if pattern.search(compact):
            return key
    return None

# 经历/项目条目中的"内容行"特征：动作词开头或长句
_ACTION_WORDS = re.compile(r"^(负责|参与|使用|完成|协助|独立|通过|基于|实现|优化|设计|开发|搭建|跟进|撰写|输出|推动|进行|主导|深度|熟练|掌握|了解|熟悉|统筹|策划|执行|分析|调研|访谈|落地|上线|迭代|维护|支持|协作|组织|管理|运营|测试|解决|撰写过|负责过)")
_COMPANY_MARKER = re.compile(r"(有限公司|有限责任公司|公司|集团|科技|网络|信息|软件|研究院|实验室|工作室|事业部|银行|证券|大学|医院|基金|媒体|文化|教育)")


def _is_content_line(line):
    """内容行：动作词开头 / 长句 / 含句号分号长内容 / 英文开头的技术清单行。"""
    if len(line) > 60:
        return True
    if _ACTION_WORDS.search(line):
        return True
    if ("。" in line or "；" in line or "；" in line) and len(line) > 20:
        return True
    # 技术清单行：'Python · SQLite · …' / 'NLP 文本匹配 · 部署' —— 英文/数字开头且含分隔符
    if re.match(r"^[A-Za-z0-9+#.()（）/（]{1,30}(?:\s*·\s*|\s*/\s*)", line):
        return True
    return False


def _split_company_title(prefix):
    """把 '公司名 职位' 拆成 (company, title)。优先符号分隔，其次公司标记词，再空格启发式。"""
    parts = [p.strip() for p in re.split(r"\s*(?:\||｜|·|–|—)\s*", prefix) if p.strip()]
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    m = None
    for match in _COMPANY_MARKER.finditer(prefix):
        m = match  # 取最后一个标记词（公司名通常以"有限公司/科技/集团"结尾）
    if m:
        company = prefix[:m.end()].strip(" |｜·,，-—")
        title = prefix[m.end():].strip(" |｜·,，-—")
        return company or prefix, title
    space_parts = prefix.split()
    if len(space_parts) >= 2 and len(space_parts[0]) <= 6 and len(space_parts[1]) <= 20:
        return space_parts[0], " ".join(space_parts[1:])
    return prefix, ""


def _clean_line(line):
    return re.sub(r"\s+", " ", line).strip(" \t-—–|｜•")


def _lines(text):
    return [_clean_line(line) for line in text.replace("\u3000", " ").splitlines() if _clean_line(line)]


def _sections(lines):
    result = {key: [] for key in _HEADERS}
    active = None
    for line in lines:
        compact = re.sub(r"[：:\s]", "", line)
        # 终止段标题：自我评价/个人简介等不归入任何章节，内容不混入前段
        if len(compact) <= 10 and re.search(r"自我评价|个人评价|个人简介|自我介绍|关于我|致谢", compact):
            active = None
            continue
        found = _match_section_title(compact)
        if found:
            active = found
        elif active:
            result[active].append(line)
    return result


def _period(value):
    match = re.search(r"((?:19|20)\d{2}(?:[.年/-]\d{1,2})?\s*(?:-|—|–|至|~|～|/)\s*(?:(?:19|20)\d{2}(?:[.年/-]\d{1,2})?|至今|现在))", value)
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
    date = re.compile(r"(?:19|20)\d{2}(?:[.年/-]\d{1,2})?\s*(?:-|—|–|至|~|～|/)\s*(?:(?:19|20)\d{2}(?:[.年/-]\d{1,2})?|至今|现在)")
    starts = [index for index, line in enumerate(section) if date.search(line)]
    if not starts:
        # 无日期格式：标题行（非内容行、短行）作为条目起点；上一行是内容行时开始新条目
        starts = []
        for index, line in enumerate(section):
            if _is_content_line(line):
                continue
            if index == 0 or _is_content_line(section[index - 1]):
                starts.append(index)
        if not starts:
            starts = [0]
    for number, start in enumerate(starts):
        block = section[start:(starts[number + 1] if number + 1 < len(starts) else len(section))]
        # 无日期时：合并连续的标题行作 heading，动作词/长句行作要点
        if not any(date.search(line) for line in block):
            heading_lines, points = [], []
            for line in block:
                if _is_content_line(line):
                    points.append(_clean_line(line.lstrip("•·-—0123456789.、 ")))
                else:
                    heading_lines.append(line)
            heading = _clean_line(" ".join(heading_lines))
            period = ""
        else:
            heading, period = block[0], _period(block[0])
            heading = date.sub("", heading).strip(" |｜·,，-—")
            points = [_clean_line(line.lstrip("•·-—0123456789.、 ")) for line in block[1:] if len(_clean_line(line)) > 3]
        points = [p for p in points if len(p) > 2][:6]
        if not heading:
            continue
        # 解析 period 为起止日期（"2026.06 – 2026.08" / "2026.03 – 至今"）
        start_date, end_date, current = "", "", False
        period_match = re.search(r"((?:19|20)\d{2}(?:[.年/-]\d{1,2})?)\s*(?:-|—|–|至|~|～|/)\s*((?:19|20)\d{2}(?:[.年/-]\d{1,2})?|至今|现在)", period)
        if period_match:
            start_date = period_match.group(1)
            end_value = period_match.group(2)
            if end_value in ("至今", "现在"):
                current = True
            else:
                end_date = end_value
        description = "\n".join(points)
        if kind == "experiences":
            company, title = _split_company_title(heading)
            entry = {"company": company[:80], "title": (title or heading)[:80], "role": (title or heading)[:80],
                     "period": period, "points": points, "description": description,
                     "start_date": start_date, "end_date": end_date, "current": current}
        else:
            entry = {"title": heading[:100], "name": heading[:100], "role": "",
                     "period": period, "points": points, "description": description,
                     "start_date": start_date, "end_date": end_date, "current": current}
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
    skills = [hint for hint in hints if re.search(r"[\u4e00-\u9fa5A-Za-z+#.]?" + re.escape(hint) + r"[\u4e00-\u9fa5A-Za-z+#.]?", content, re.I)]
    # 仅从"标题行：技能列表"或带分隔符的行提取，避免把普通句子拆成技能
    for line in section:
        if "：" in line or ":" in line:
            left, _, right = line.partition(":") if ":" in line else line.partition("：")
            left = left.strip(" |｜·,，-—")
            if len(left) <= 12 and not re.search(r"[。！？.!?]", right):
                skills.extend(re.split(r"[,，;；、/|｜\s]+", right.strip()))
        elif len(line) <= 60 and re.search(r"[,，、/]", line) and not re.search(r"[。！？.!?]", line):
            skills.extend(re.split(r"[,，、/|｜\s]+", line))
    cleaned = [skill for skill in _unique(skills) if 1 < len(skill) <= 24 and not re.search(r"^(熟悉|掌握|了解|具备)$", skill) and not re.search(r"[。；;！？]", skill)]
    return cleaned[:30], _source(content)


def _extract_notes(lines):
    """提取 个人概述/自我评价/个人简介 段内容 → notes（可多段合并）。"""
    notes = []
    active = False
    for line in lines:
        compact = re.sub(r"[：:\s]", "", line)
        if len(compact) <= 10 and re.search(r"个人概述|自我评价|个人简介|自我介绍|关于我", compact):
            active = True
            continue
        if active:
            if len(compact) <= 10 and re.search(r"教育背景|教育经历|实习经历|实习经验|工作经历|工作经验|项目经历|项目经验|专业技能|技能特长|竞赛|获奖|证书|荣誉|求职意向", compact):
                active = False  # 结束当前段，继续找下一段个人概述/自我评价
                continue
            if len(line) > 2:
                notes.append(line)
    return "\n".join(notes).strip()[:1200]


def _local_extract(text):
    extracted, confidence, sources, unrecognized = {}, {}, {}, []
    lines, sections = _lines(text), _sections(_lines(text))
    # 个人简介：个人概述/自我评价 段
    notes = _extract_notes(lines)
    if notes:
        extracted["notes"] = notes
        confidence["notes"] = "medium"
        sources["notes"] = _source(notes[:260])
    # 姓名：优先 "姓名/Name：XXX"；其次行首 2-4 字中文（后面跟着求职意向/电话/邮箱等特征）
    name = re.search(r"姓\s*名\s*[:：]\s*([\u4e00-\u9fa5]{2,4}|[A-Za-z][A-Za-z .'-]{1,40})", text)
    if not name:
        name = re.search(r"([\u4e00-\u9fa5]{2,4})\s*[:：|｜]\s*(?:求职意向|意向岗位|目标岗位|电话|手机|邮箱|E-?mail|现居|所在)", text)
    if not name:
        for line in lines[:10]:
            if re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", line):
                name = re.match(r"(.+)", line)
                break
    if name:
        extracted["name"] = name.group(1).strip(); confidence["name"] = "high" if "姓名" in text else "medium"; sources["name"] = _source(name.group(0))
    # 电话：容忍空格/括号/分隔符；优先精确 11 位，其次宽松匹配
    phone_match = re.search(r"(?<!\d)(1[3-9]\d{9})(?!\d)", text)
    if not phone_match:
        phone_match = re.search(r"(?<!\d)(1[3-9])[\s()-]*(\d)[\s()-]*(\d)[\s()-]*(\d)[\s()-]*(\d)[\s()-]*(\d)[\s()-]*(\d)[\s()-]*(\d)[\s()-]*(\d)[\s()-]*(\d)(?!\d)", text)
        if phone_match:
            digits = phone_match.group(0).replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
            phone_match = re.match(r"(.+)", digits)
    for key, match in (("email", re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)), ("phone", phone_match), ("github", re.search(r"https?://(?:www\.)?github\.com/[\w.-]+", text, re.I))):
        if match:
            extracted[key] = match.group(0); confidence[key] = "high"; sources[key] = _source(match.group(0))
    city = re.search(r"(?:现居|所在(?:地|城市)?|居住地|工作城市)\s*[:：]?\s*(" + _CITIES + r")", text)
    if not city:
        # 简历头部竖线分隔：'邮箱 | 电话 | 郑州 | github'——限定在含邮箱/电话的行内提取，避免正文误匹配
        header_lines = [line for line in lines[:5] if re.search(r"@|1[3-9]\d{9}", line)]
        for line in header_lines:
            m = re.search(r"[|｜]\s*(" + _CITIES + r")\s*[|｜]", line)
            if m:
                city = m
                break
    if city:
        extracted["city"] = city.group(1); confidence["city"] = "high"; sources["city"] = _source(city.group(0))
    goal = re.search(r"(?:求职意向|意向岗位|求职目标|职业目标|求职方向|应聘岗位|应聘方向|期望岗位)\s*[:：]?\s*([^\n]{2,80})", text)
    if goal:
        value = goal.group(1).strip(" |｜,，；;")
        # 保护括号内的 "/"（如"实习 / 校招"），避免被拆碎
        protected = re.sub(r"([（(][^）)]*)/", r"\1<SLASH>", value)
        goals = _unique(re.split(r"[,，、|｜/]", protected))
        goals = [g.replace("<SLASH>", "/").strip() for g in goals if g.strip()]
        extracted["career_goals"], extracted["status"] = goals, value
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
        if not end:
            # 支持 "2027 届" 等格式的毕业时间（原始行里提取）
            end = re.search(r"((?:19|20)\d{2})\s*届", education_source)
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
    certificates = [line for line in sections["certifications"] if re.search(r"CET|雅思|托福|证书|资格|大赛|奖|荣誉", line, re.I)]
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
    """Return a reviewable extraction; it never writes a profile directly.

    AI 通道失败时自动降级到本地规则，绝不把"识别不可用"抛给用户。
    返回值含 fallback 标志（True = 本地规则结果，AI 不可用）。
    """
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
                return {"extracted": extracted, "confidence": confidence, "unrecognized": unrecognized, "source_text": sources, "summary": _summary(extracted), "fallback": False}
            except Exception as exc:
                # 捕获所有异常（HTTPError/URLError/超时/解析失败等），确保 AI 失败必降级本地规则
                last_error = exc
        # AI 通道不可用：降级到本地规则，而不是把错误抛给用户
        extracted, confidence, unrecognized, sources = _local_extract(text)
        result = {"extracted": extracted, "confidence": confidence, "unrecognized": unrecognized, "source_text": sources, "summary": _summary(extracted), "fallback": True}
        if last_error:
            result["fallback_reason"] = str(last_error)[:160]
        return result
    extracted, confidence, unrecognized, sources = _local_extract(text)
    return {"extracted": extracted, "confidence": confidence, "unrecognized": unrecognized, "source_text": sources, "summary": _summary(extracted), "fallback": False}
