"""岗位网址解析：抓取页面 → 抽取岗位信息。"""

import re
import socket
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from llm_client import llm_available, request_json


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


def _block_ssrf(url):
    host = urllib.parse.urlparse(url).hostname or ""
    if host in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("不支持解析本地地址")
    if host.startswith("192.168.") or host.startswith("10.") or host.startswith("172."):
        raise ValueError("不支持解析内网地址")
    return True


def fetch_url_text(url):
    _block_ssrf(url)
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
    with urllib.request.urlopen(req, timeout=30) as resp:
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
