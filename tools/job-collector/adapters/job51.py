"""前程无忧 51job 适配器（只读采集骨架）。

页面：https://we.51job.com/pc/search?keyword={keyword}&searchType=2
说明：与智联相同，选择器按 51job 实际 DOM 校准后使用。
"""
from .base import BaseAdapter


class Job51Adapter(BaseAdapter):
    platform = "51job"
    search_url_tpl = "https://we.51job.com/pc/search?keyword={keyword}&searchType=2"
    card_selector = ".joblist-box .e, .joblist .job, .joblist-box .joblist-item"
    max_pages = 2
    delay_range = (4.0, 8.0)
    verify_markers = ["验证码", "verify", "captcha"]
    login_markers = ["请登录", "扫码登录"]
    block_markers = ["访问过于频繁", "频率限制", "请求频繁", "请求过于频繁"]

    def parse_card(self, card):
        def _txt(sel):
            el = card.query_selector(sel)
            return (el.inner_text() or "").strip() if el else ""

        def _href(sel):
            el = card.query_selector(sel)
            href = el.get_attribute("href") if el else ""
            if href and href.startswith("//"):
                href = "https:" + href
            return href

        return {
            "title": _txt(".jname, .job-title, .jobname"),
            "company": _txt(".cname, .company-name, .companyname"),
            "salary": _txt(".sal, .job-salary, .salary"),
            "city": _txt(".area, .job-area, .work-area"),
            "url": _href(".job-primary a, a.jname, .jname"),
            "description": "",
            "tags": [],
        }
