"""岗位网址解析：抓取页面 → 抽取岗位信息。"""

import ipaddress
import re
import socket
import time
import urllib.parse
import urllib.request
import hashlib
import json
from pathlib import Path
from html.parser import HTMLParser

from llm_client import llm_available, request_json

_CACHE_FILE = Path(__file__).with_name("data") / "job_search_cache.json"
_CACHE_TTL_SECONDS = 6 * 3600


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
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
    )
    with urllib.request.build_opener(_SafeRedirectHandler()).open(req, timeout=30) as resp:
        raw = resp.read(2 * 1024 * 1024)
    for encoding in ("utf-8", "gbk", "gb18030"):
        try:
            html_text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        html_text = raw.decode("utf-8", errors="replace")
    parser = _TextExtractor()
    try:
        parser.feed(html_text)
    except Exception:
        pass
    return parser.text()


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
    text = fetch_url_text(url)
    if not text or len(text) < 100:
        raise ValueError("页面内容过少，可能是登录页或反爬限制")

    if llm_available():
        try:
            system = (
                "你是岗位信息抽取器。只输出 JSON，不要解释。"
                '字段仅限 {"title","company","requirements","description"}。'
                "requirements 为任职要求字符串数组，description 为岗位职责简述。"
                "页面没有的信息输出空字符串或空数组，禁止编造。"
            )
            user = "请从以下页面文本抽取岗位信息：\n\n" + text[:12000]
            raw = request_json(system, user)
            if isinstance(raw, dict):
                return {
                    "title": str(raw.get("title") or "")[:80],
                    "company": str(raw.get("company") or "")[:60],
                    "requirements": [str(r) for r in raw.get("requirements", []) if str(r).strip()][:20],
                    "description": str(raw.get("description") or "")[:2000],
                }
        except RuntimeError:
            pass

    return _local_extract(text)

def search_jobs(query: dict, settings: dict) -> list[dict]:
    """Local matches plus explicitly marked LLM suggestions, never fake live results."""
    terms=(query.get("keywords") or "").lower().split(); city=(query.get("city") or "").strip(); limit=min(max(int(query.get("limit",20)),1),50)
    local=[]
    for job in json.loads((Path(__file__).with_name("data") / "jobs_seed.json").read_text(encoding="utf-8")):
        text=" ".join([job.get("title",""),job.get("company",""),job.get("description","")," ".join(job.get("tags",[]))]).lower()
        if (not terms or all(t in text for t in terms)) and (not city or city in job.get("city","")): local.append({**job,"source":"local"})
    if not llm_available(): return local[:limit]
    try:
        raw=request_json("你是行业 HR 助手，只输出 JSON。", "返回 {jobs:[{title,company,city,posting_type,work_type,salary,description,requirements,url,tags}]}。只能给出合理候选，不能声称实时抓取。查询："+json.dumps(query,ensure_ascii=False))
        suggested=[]
        for job in raw.get("jobs",[])[:limit]:
            if job.get("title") and job.get("company"): suggested.append({**job,"id":"job-"+hashlib.sha256((job.get("url") or job["title"]+job["company"]).encode()).hexdigest()[:16],"source":"llm_suggested"})
        return (local+suggested)[:limit]
    except Exception: return local[:limit]


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
    for cand in candidates[:6]:
        if len(detail_urls) >= 10:
            break
        try:
            text = fetch_url_text(cand["url"])
        except Exception as exc:
            skipped.append({"url": cand["url"], "reason": "抓取失败：" + str(exc)[:80]})
            continue
        if not text or len(text) < 100:
            skipped.append({"url": cand["url"], "reason": "页面内容过少，可能是登录页或反爬限制"})
            continue
        host = urllib.parse.urlparse(cand["url"]).netloc
        links = re.findall(
            r'https?://[^\s"\'<>]+(?:/job/|/position/|/zp/job/|/jobs?/\d)[^\s"\'<>]*',
            text,
        )
        detail = [link.rstrip(".,;，。") for link in links if urllib.parse.urlparse(link).netloc == host]
        if detail:
            detail_urls.extend(detail[:8])
        else:
            detail_urls.append(cand["url"])  # 无子链接则把该页当详情页

    unique, seen = [], set()
    for url in detail_urls[:10]:
        if url not in seen:
            seen.add(url)
            unique.append(url)

    # 阶段三：逐页抽取结构化岗位信息
    jobs = []
    for url in unique:
        try:
            parsed = extract_job_from_url(url)
        except Exception as exc:
            skipped.append({"url": url, "reason": "抽取失败：" + str(exc)[:80]})
            continue
        if not parsed.get("title"):
            skipped.append({"url": url, "reason": "未解析到岗位标题"})
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
