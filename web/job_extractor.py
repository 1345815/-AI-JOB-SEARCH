"""岗位网址解析：抓取页面 → 抽取岗位信息。"""

import ipaddress
import base64
import re
import socket
import time
import urllib.parse
import urllib.request
import hashlib
import json
import html
import os
try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except Exception:
    Cipher = algorithms = modes = None
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from html.parser import HTMLParser

from llm_client import llm_available, request_json

_CACHE_FILE = Path(__file__).with_name("data") / "job_search_cache.json"
_CACHE_TTL_SECONDS = 6 * 3600
_KEYWORD_CACHE_TTL_SECONDS = 15 * 60
_FREEHIRE_API_URL = "https://freehire.me/api/v1/agent/jobs/search"

# 内置平台适配器：用户无需配置，按招聘平台识别，不按公司重复开发。
BUILTIN_ATS_ADAPTERS = (
    {"id": "mokahr", "name": "Mokahr 校招", "status": "ready"},
    {"id": "greenhouse", "name": "Greenhouse", "status": "ready"},
    {"id": "lever", "name": "Lever", "status": "ready"},
    {"id": "ashby", "name": "Ashby", "status": "ready"},
    {"id": "web", "name": "普通招聘网页", "status": "fallback"},
)


def _mokahr_job_from_url(url):
    """读取 Mokahr 校招详情接口。Mokahr 页面正文由前端接口返回并 AES-128-CBC 封装。"""
    parsed = urllib.parse.urlparse(url)
    if "app.mokahr.com" not in (parsed.hostname or "") or "/campus-recruitment/" not in parsed.path:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 3:
        return None
    fragment = urllib.parse.unquote(parsed.fragment or "")
    match = re.search(r"/job/([0-9a-fA-F-]{20,})", fragment)
    if not match:
        return None
    if Cipher is None:
        raise ValueError("当前环境缺少 Mokahr 解密依赖，请安装 cryptography")
    org_id, site_id, job_id = parts[1], parts[2], match.group(1)
    endpoint = urllib.parse.urljoin(f"{parsed.scheme}://{parsed.netloc}", "/api/outer/ats-apply/website/job")
    payload = json.dumps({"orgId": org_id, "siteId": site_id, "jobId": job_id, "isInviteResume": True, "locale": "zh-CN"}).encode()
    req = urllib.request.Request(endpoint, data=payload, method="POST", headers={"Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        response = json.loads(resp.read().decode("utf-8"))
    encrypted, key = response.get("data"), response.get("necromancer")
    if not encrypted or not key:
        raise ValueError("Mokahr 接口未返回岗位数据")
    decryptor = Cipher(algorithms.AES(key.encode()[:16]), modes.CBC(b"\0" * 16)).decryptor()
    raw = decryptor.update(base64.b64decode(encrypted)) + decryptor.finalize()
    text = raw.decode("utf-8", errors="ignore")
    marker = text.find('"data":')
    if marker < 0:
        raise ValueError("Mokahr 岗位数据解密失败")
    data, _ = json.JSONDecoder().raw_decode("{" + text[marker:])
    job = data.get("data") or {}
    description = str(job.get("jobDescription") or job.get("description") or job.get("content") or "")
    if not description:
        description = next((str(value) for value in job.values() if isinstance(value, str) and ("岗位职责" in value or "任职要求" in value or len(value) > 500)), "")
    parser = _TextExtractor(); parser.feed(description)
    local = _local_extract(parser.text())
    if not local.get("description"):
        local["description"] = parser.text()[:3000]
    local.update({
        "title": job.get("name") or job.get("title") or local.get("title"),
        "company": job.get("companyName") or job.get("organizationName") or job.get("orgName") or local.get("company"),
        "posting_type": "校招", "work_type": "全职",
    })
    return _normalize_extracted_job(local, parser.text(), url)


def _public_ats_job_from_url(url):
    """统一处理公开 ATS 岗位接口；按平台识别，不按公司写死。"""
    parsed = urllib.parse.urlparse(url); host = (parsed.hostname or "").lower(); parts = [p for p in parsed.path.split("/") if p]
    platform, board, job_id = None, None, None
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io") and len(parts) >= 3 and parts[0] != "embed":
        platform, board, job_id = "greenhouse", parts[0], parts[2]
    elif host == "jobs.lever.co" and len(parts) >= 2:
        platform, board, job_id = "lever", parts[0], parts[1]
    elif host == "jobs.ashbyhq.com" and len(parts) >= 2:
        platform, board, job_id = "ashby", parts[0], parts[1]
    if not platform:
        return None
    if platform == "greenhouse":
        endpoint = f"https://boards-api.greenhouse.io/v1/boards/{urllib.parse.quote(board)}/jobs/{urllib.parse.quote(job_id)}"
    elif platform == "lever":
        endpoint = f"https://api.lever.co/v0/postings/{urllib.parse.quote(board)}/{urllib.parse.quote(job_id)}"
    else:
        endpoint = f"https://api.ashbyhq.com/posting-api/job-board/{urllib.parse.quote(board)}/{urllib.parse.quote(job_id)}"
    req = urllib.request.Request(endpoint, headers={"Accept": "application/json", "User-Agent": "CareerPilot/1.0"})
    try:
        from http_client import get_json
        timeout = int(os.environ.get("ATS_TIMEOUT", "15"))
        data = get_json(endpoint, headers={"Accept": "application/json"}, timeout=timeout, retries=1, source="ats")
    except Exception:
        return None
    description = data.get("content") or data.get("description") or data.get("job_description") or data.get("descriptionHtml") or ""
    parser = _TextExtractor(); parser.feed(str(description))
    text = parser.text()
    local = _local_extract(text)
    local.update({"title": data.get("title") or local.get("title"), "company": data.get("company_name") or data.get("company") or board, "city": data.get("location", {}).get("name") if isinstance(data.get("location"), dict) else data.get("categories", {}).get("location", "") if isinstance(data.get("categories"), dict) else "", "description": text, "posting_type": "社会招聘", "work_type": "远程" if data.get("workplace_type") == "remote" else "全职"})
    return _normalize_extracted_job(local, text, url)


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg", "iframe"):
            self.skip += 1
        if tag in ("h1", "h2", "h3", "p", "li", "br", "div", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg", "iframe"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if self.skip == 0 and data.strip():
            self.parts.append(data)

    def text(self):
        raw = "".join(self.parts)
        return re.sub(r"\n{2,}", "\n", raw).strip()


def validate_public_url(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("仅支持不含凭据的 HTTP/HTTPS 公网链接")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise ValueError("岗位链接域名无法解析") from exc
    if not addresses:
        raise ValueError("岗位链接域名没有可用地址")
    for address in addresses:
        if not ipaddress.ip_address(address.split("%", 1)[0]).is_global:
            raise ValueError("禁止访问本机、内网或保留网络地址")
    return url


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_url_text(url):
    validate_public_url(url)
    from http_client import fetch_text
    timeout = int(os.environ.get("WEB_TIMEOUT", "30"))
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    raw = fetch_text(url, headers=headers, timeout=timeout, retries=1, source="web",
                     opener=urllib.request.build_opener(_SafeRedirectHandler()), raw_bytes=True)
    for encoding in ("utf-8", "gbk", "gb18030"):
        try:
            html_text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        html_text = raw.decode("utf-8", errors="replace")
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.I | re.S)
    page_title = _clean(html.unescape(title_match.group(1))) if title_match else ""
    parser = _TextExtractor()
    try:
        parser.feed(html_text)
    except Exception:
        pass
    extracted = parser.text()
    return (page_title + "\n" + extracted).strip() if page_title and page_title not in extracted[:200] else extracted


def _clean(value):
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _local_extract(text):
    title = ""
    title_match = re.search(r"^(.{2,60})$", text[:600], re.M)
    if title_match:
        title = title_match.group(1).strip()
    company = ""
    company_match = re.search(r"([\u4e00-\u9fa5A-Za-z0-9]{2,30}(?:公司|集团|科技|网络|银行|有限))", text[:3000])
    if company_match:
        company = company_match.group(1)

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    requirements = []
    description_lines = []
    in_req = False
    for line in lines:
        if re.search(r"(任职要求|岗位要求|职位要求|任职资格|岗位职责|工作职责|Requirements|要求)", line, re.I):
            in_req = True
            continue
        if in_req:
            if re.match(r"^[\d①②③④⑤⑥⑦⑧⑨⑩．.、\-*·]\s*", line) or len(line) >= 8:
                requirements.append(line)
            if re.search(r"(福利|薪资|投递|联系方式|工作地点)", line):
                in_req = False
        elif len(line) >= 12 and "要求" not in line:
            description_lines.append(line)

    return {
        "title": title[:80],
        "company": company[:60],
        "requirements": requirements[:20],
        "description": _clean("\n".join(description_lines[:20]))[:2000],
    }


def extract_job_from_url(url):
    mokahr_job = _mokahr_job_from_url(url)
    if mokahr_job:
        return mokahr_job
    ats_job = _public_ats_job_from_url(url)
    if ats_job:
        return ats_job
    text = fetch_url_text(url)
    if not text or len(text) < 100:
        raise ValueError("页面内容过少，可能是登录页或反爬限制")
    lower_text = text.lower()
    blocked_markers = ("请登录后查看", "登录后查看", "验证后继续", "access denied", "captcha", "robot check")
    job_markers = ("岗位职责", "任职要求", "职位要求", "job description", "responsibilities", "requirements")
    if any(marker in lower_text for marker in blocked_markers) and not any(marker.lower() in lower_text for marker in job_markers):
        raise ValueError("页面被登录或反爬拦截，无法读取岗位正文；请打开公开岗位详情页")

    if llm_available():
        try:
            system = (
                "你是严谨的招聘信息结构化抽取器，只输出一个 JSON 对象，不要 Markdown、解释或额外字段。"
                '字段必须为 {"title":"","company":"","city":"","posting_type":"","work_type":"",'
                '"salary":"","deadline":"","tags":[],"requirements":[],"description":""}。'
                "title 只填岗位名称，不要拼接公司名；company 填招聘方；city 填工作地点。"
                "posting_type 只能填校招/社会招聘/实习/未知，work_type 只能填全职/兼职/实习/远程/未知。"
                "deadline 只填页面明确写出的截止日期，格式 YYYY-MM-DD，无法确认就留空。"
                "requirements 只放任职资格、技能、学历、经验等硬性要求，逐条拆分；description 只概括岗位职责；"
                "tags 只提取页面明确出现的技能标签。页面没有的信息必须留空，禁止猜测、补全或编造。"
            )
            user = (
                "请从下面的公开岗位页面文本中抽取信息。优先选择岗位详情正文，忽略导航、推荐岗位、登录提示和页脚。"
                "如果页面是登录页、列表页或反爬提示页，请将 title/company 留空，不要伪造。\n\n"
                + text[:18000]
            )
            raw = request_json(system, user)
            if isinstance(raw, dict):
                return _normalize_extracted_job(raw, text, url)
        except Exception:
            # AI 通道任何失败（HTTPError/超时/解析失败）都降级本地规则，不把错误抛给用户
            pass

    return _normalize_extracted_job(_local_extract(text), text, url)


def _normalize_extracted_job(raw, text="", url=""):
    """统一 AI 和本地抽取结果，避免“解析成功”却生成空壳岗位。"""
    raw = raw if isinstance(raw, dict) else {}
    title = _clean(raw.get("title"))[:100]
    company = _clean(raw.get("company"))[:80]
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not title:
        for line in lines[:12]:
            if 2 <= len(line) <= 80 and not re.search(r"(登录|注册|隐私|首页|职位列表)", line, re.I):
                title = line[:100]
                break
    if not company and url:
        host = urllib.parse.urlparse(url).hostname or ""
        company = host.removeprefix("www.").split(".")[0][:80]
    requirements = raw.get("requirements") if isinstance(raw.get("requirements"), list) else []
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    result = {
        "title": title,
        "company": company,
        "city": _clean(raw.get("city"))[:100],
        "posting_type": _clean(raw.get("posting_type"))[:30] or "未知",
        "work_type": _clean(raw.get("work_type"))[:30] or "未知",
        "salary": _clean(raw.get("salary"))[:80],
        "deadline": _clean(raw.get("deadline"))[:20],
        "tags": [_clean(item)[:40] for item in tags if _clean(item)][:15],
        "requirements": [_clean(item)[:240] for item in requirements if _clean(item)][:20],
        "description": _clean(raw.get("description"))[:3000],
    }
    if not result["title"] and not result["description"]:
        raise ValueError("页面中没有识别到岗位详情，可能是登录页、列表页或反爬页面")
    return result

def search_jobs(query: dict, settings: dict) -> list[dict]:
    """Local matches plus explicitly marked LLM suggestions, never fake live results."""
    terms=(query.get("keywords") or "").lower().split(); city=(query.get("city") or "").strip(); limit=min(max(int(query.get("limit",20)),1),50)
    local=[]
    for job in json.loads((Path(__file__).with_name("data") / "jobs_seed.json").read_text(encoding="utf-8")):
        text=" ".join([job.get("title",""),job.get("company",""),job.get("description","")," ".join(job.get("tags",[]))]).lower()
        if (not terms or all(t in text for t in terms)) and (not city or city in job.get("city","")): local.append({**job,"source":"local"})
    real_results = search_freehire_jobs(query, limit)
    if not llm_available(): return decorate_search_results((real_results + local)[:limit])
    cache_key = "keyword:" + json.dumps({"keywords": query.get("keywords", ""), "city": query.get("city", ""), "limit": limit}, ensure_ascii=False, sort_keys=True)
    try:
        if _CACHE_FILE.exists():
            store = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            cached = store.get(cache_key)
            if cached and time.time() - float(cached.get("fetched_at", 0)) < _KEYWORD_CACHE_TTL_SECONDS:
                return decorate_search_results((real_results + local + cached.get("results", []))[:limit])
    except Exception:
        pass
    try:
        raw=request_json("你是行业 HR 助手，只输出 JSON。", "返回 {jobs:[{title,company,city,posting_type,work_type,salary,description,requirements,url,tags}]}。只能给出合理候选，不能声称实时抓取。查询："+json.dumps(query,ensure_ascii=False))
        suggested=[]
        for job in raw.get("jobs",[])[:limit]:
            if job.get("title") and job.get("company"): suggested.append({**job,"id":"job-"+hashlib.sha256((job.get("url") or job["title"]+job["company"]).encode()).hexdigest()[:16],"source":"llm_suggested"})
        try:
            store = json.loads(_CACHE_FILE.read_text(encoding="utf-8")) if _CACHE_FILE.exists() else {}
            store[cache_key] = {"fetched_at": time.time(), "results": suggested}
            _CACHE_FILE.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        return decorate_search_results((real_results + local + suggested)[:limit])
    except Exception: return decorate_search_results((real_results + local)[:limit])


def decorate_search_results(results: list[dict]) -> list[dict]:
    """统一搜索结果质量标记，帮助用户快速判断“能不能信、是否值得先看”。"""
    unique, seen = [], set()
    for job in results or []:
        if not isinstance(job, dict):
            continue
        url = str(job.get("url") or "").strip().lower().rstrip("/")
        key = url or (str(job.get("title") or "").strip().lower(), str(job.get("company") or "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        source = str(job.get("source") or "")
        source_score = {"freehire": 92, "web_search": 84, "URL解析": 88, "local": 58, "llm_suggested": 30}.get(source, 50)
        completeness = 0
        for field in ("title", "company", "url", "description"):
            if job.get(field):
                completeness += 1
        if job.get("requirements") or job.get("tags"):
            completeness += 1
        completeness_score = round(completeness / 5 * 100)
        freshness_score, freshness_label = 55, "发布时间待确认"
        posted = str(job.get("posted_at") or "")[:10]
        if posted:
            try:
                age = max(0, (time.time() - time.mktime(time.strptime(posted, "%Y-%m-%d"))) / 86400)
                freshness_score = 100 if age <= 7 else 82 if age <= 30 else 60 if age <= 90 else 35
                freshness_label = "近 7 天" if age <= 7 else "近 30 天" if age <= 30 else "较早发布"
            except (TypeError, ValueError, OverflowError):
                pass
        quality = round(source_score * 0.55 + completeness_score * 0.30 + freshness_score * 0.15)
        reasons = []
        if source in ("freehire", "web_search", "URL解析"):
            reasons.append("真实来源")
        elif source == "llm_suggested":
            reasons.append("AI 生成候选，需打开原帖核实")
        if completeness_score < 70:
            reasons.append("岗位信息不完整")
        if freshness_label != "发布时间待确认":
            reasons.append(freshness_label)
        unique.append({**job, "quality_score": quality, "quality_label": "优先查看" if quality >= 80 else "建议核实" if quality >= 55 else "谨慎参考", "freshness_label": freshness_label, "quality_reasons": reasons})
    return unique


def search_freehire_jobs(query: dict, limit=20) -> list[dict]:
    """从公开 ATS 聚合接口获取真实岗位；失败时返回空列表，不阻塞主搜索。"""
    keywords = (query.get("keywords") or "").strip()
    if not keywords:
        return []
    params = {"q": keywords, "limit": str(min(max(int(limit), 1), 25))}
    city = (query.get("city") or "").strip()
    if city:
        params["city"] = city
    cache_key = "freehire:" + json.dumps(params, ensure_ascii=False, sort_keys=True)
    try:
        if _CACHE_FILE.exists():
            store = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            cached = store.get(cache_key)
            if cached and time.time() - float(cached.get("fetched_at", 0)) < _KEYWORD_CACHE_TTL_SECONDS:
                return decorate_search_results(cached.get("results", [])[:limit])
    except Exception:
        pass
    url = os.environ.get("FREEHIRE_API_URL", _FREEHIRE_API_URL) + "?" + urllib.parse.urlencode(params)
    try:
        from http_client import get_json
        timeout = int(os.environ.get("FREEHIRE_TIMEOUT", "12"))
        payload = get_json(url, headers={"Accept": "application/json"}, timeout=timeout, retries=1, source="freehire")
        rows = payload.get("data") or payload.get("results") or []
        results = []
        for row in rows[:limit]:
            if not isinstance(row, dict) or not row.get("title") or not row.get("url"):
                continue
            description = html.unescape(str(row.get("description") or ""))
            parser = _TextExtractor()
            try:
                parser.feed(description)
                description = parser.text()
            except Exception:
                description = re.sub(r"<[^>]+>", " ", description)
            location = str(row.get("location") or "")
            results.append({
                "id": "freehire-" + str(row.get("public_slug") or hashlib.sha256(str(row.get("url")).encode()).hexdigest()[:16]),
                "title": str(row.get("title"))[:100],
                "company": str(row.get("company") or "未知公司")[:80],
                "city": location[:100],
                "posting_type": "社会招聘",
                "work_type": "远程" if "remote" in location.lower() else "全职",
                "experience": str((row.get("enrichment") or {}).get("experience_years_min") or ""),
                "salary": "",
                "deadline": "",
                "tags": [str(skill) for skill in (row.get("skills") or [])[:12]],
                "url": str(row.get("url")),
                "description": description[:6000],
                "requirements": [],
                "source": "freehire",
                "source_name": "FreeHire ATS 聚合",
                "posted_at": row.get("posted_at") or row.get("created_at") or "",
            })
        try:
            store = json.loads(_CACHE_FILE.read_text(encoding="utf-8")) if _CACHE_FILE.exists() else {}
            store[cache_key] = {"fetched_at": time.time(), "results": results}
            _CACHE_FILE.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        return decorate_search_results(results)
    except Exception:
        return []


def search_company_jobs(company, city="", limit=8):
    """按公司名联网搜索真实岗位（仅 AI 模式）。

    流程：LLM 生成候选 URL（白名单来源）→ 抓取 → 详情链接提取 → 逐页抽取 →
    6 小时缓存。全程只返回真实抓取成功的页面，绝不编造 URL。
    返回 {"jobs": [...], "skipped": [{"url","reason"}], "cached": bool}。
    """
    limit = min(max(int(limit), 1), 10)
    cache_key = (company + "|" + city).strip("|").lower()

    # 缓存命中（6 小时内）
    try:
        if _CACHE_FILE.exists():
            store = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            entry = store.get(cache_key)
            if entry and time.time() - float(entry.get("fetched_at", 0)) < _CACHE_TTL_SECONDS:
                return {"jobs": entry.get("results", [])[:limit], "skipped": [], "cached": True}
    except Exception:
        pass

    if not llm_available():
        return {"jobs": [], "skipped": [{"url": "", "reason": "未配置 LLM，无法生成候选页面 URL"}], "cached": False}

    # 阶段一：LLM 生成候选 URL（来源白名单，禁止编造）
    try:
        raw = request_json(
            "你是中国校招岗位搜索助手，只输出 JSON。",
            "为招聘公司生成可访问的岗位搜索页/详情页 URL 候选。公司：" + company +
            "。来源仅限：公司官网招聘页、牛客网、BOSS直聘、拉勾、智联、前程无忧、实习僧、应届生求职网。" +
            '返回 {"urls":[{"url":"...","note":"官网直达|搜索入口"}]}，最多 6 个。禁止编造 URL，不确定就少给。',
        )
    except Exception as exc:
        return {"jobs": [], "skipped": [{"url": "", "reason": "LLM 生成候选失败：" + str(exc)[:120]}], "cached": False}

    candidates = []
    for item in (raw.get("urls", []) if isinstance(raw, dict) else []):
        url = str(item.get("url") or "").strip()
        if url.startswith(("http://", "https://")):
            candidates.append({"url": url, "note": str(item.get("note") or "")})

    # 阶段二：抓取候选页，提取同域岗位详情链接（总抓取 ≤10 页）
    skipped = []
    detail_urls = []

    def fetch_candidate(cand):
        try:
            text = fetch_url_text(cand["url"])
        except Exception as exc:
            return cand, None, "抓取失败：" + str(exc)[:80]
        if not text or len(text) < 100:
            return cand, None, "页面内容过少，可能是登录页或反爬限制"
        host = urllib.parse.urlparse(cand["url"]).netloc
        links = re.findall(
            r'https?://[^\s"\'<>]+(?:/job/|/position/|/zp/job/|/jobs?/\d)[^\s"\'<>]*',
            text,
        )
        detail = [link.rstrip(".,;，。") for link in links if urllib.parse.urlparse(link).netloc == host]
        return cand, (detail[:8] if detail else [cand["url"]]), None

    with ThreadPoolExecutor(max_workers=min(4, max(1, len(candidates[:6])))) as pool:
        futures = [pool.submit(fetch_candidate, cand) for cand in candidates[:6]]
        for future in as_completed(futures):
            cand, found, error = future.result()
            if error:
                skipped.append({"url": cand["url"], "reason": error})
            elif found:
                detail_urls.extend(found)
            if len(detail_urls) >= 10:
                detail_urls = detail_urls[:10]

    unique, seen = [], set()
    for url in detail_urls[:10]:
        if url not in seen:
            seen.add(url)
            unique.append(url)

    # 阶段三：逐页抽取结构化岗位信息
    def extract_one(url):
        try:
            parsed = extract_job_from_url(url)
            if not parsed.get("title"):
                return url, None, "未解析到岗位标题"
            return url, parsed, None
        except Exception as exc:
            return url, None, "抽取失败：" + str(exc)[:80]

    jobs = []
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(unique)))) as pool:
        futures = [pool.submit(extract_one, url) for url in unique]
        for future in as_completed(futures):
            url, parsed, error = future.result()
            if error:
                skipped.append({"url": url, "reason": error})
                continue
            jobs.append({
            "title": str(parsed.get("title") or "未命名岗位")[:80],
            "company": str(parsed.get("company") or company)[:60],
            "city": city,
            "posting_type": "未知",
            "work_type": "全职",
            "salary": "",
            "deadline": "",
            "tags": ["联网搜索"],
            "url": url,
            "description": str(parsed.get("description") or "")[:2000],
            "requirements": [str(r) for r in (parsed.get("requirements") or [])][:20],
            "source": "web_search",
            })

    # 写缓存（仅岗位信息，不含个人数据）
    try:
        store = {}
        if _CACHE_FILE.exists():
            store = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        store[cache_key] = {"fetched_at": time.time(), "results": jobs}
        _CACHE_FILE.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    return {"jobs": jobs[:limit], "skipped": skipped, "cached": False}
