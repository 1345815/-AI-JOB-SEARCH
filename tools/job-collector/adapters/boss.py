"""BOSS 直聘适配器：搜索页卡片提取。

页面：https://www.zhipin.com/web/geek/job?query={keyword}&city=100010000
选择器基于 BOSS 网页版常见结构；若页面结构变化导致选择器失效，
collector 会安全停止并提示，不会硬碰。
"""
from .base import BaseAdapter, SafeStop


class BossAdapter(BaseAdapter):
    platform = "boss"
    search_url_tpl = "https://www.zhipin.com/web/geek/job?query={keyword}&city=100010000"
    card_selector = ".job-card-wrapper, li.job-card-wrapper, .job-list-box li"
    max_pages = 3
    delay_range = (4.0, 8.0)
    verify_markers = ["验证码", "verify", "captcha", "geetest", "slide"]
    login_markers = ["请登录", "扫码登录", "登录/注册", "登录后查看"]
    block_markers = ["访问过于频繁", "请求频繁", "频率限制", "操作太频繁", "被限制"]
    unknown_markers = ["安全验证"]

    def next_page(self):
        # BOSS 是无限滚动 + 页码按钮
        try:
            pagers = self.page.query_selector_all(".ui-pager .btn-pager, .ui-pagination-next, button.next-page")
            if not pagers:
                # 尝试滚动加载
                self.page.mouse.wheel(0, 6000)
                self.page.wait_for_timeout(2500)
                return True
            clicked = False
            for p in pagers:
                txt = (p.inner_text() or "").strip()
                if txt in ("下一页", ">", "»") or "next" in (p.get_attribute("class") or ""):
                    p.click()
                    self.page.wait_for_timeout(3000)
                    clicked = True
                    break
            return clicked
        except Exception:
            return False

    def parse_card(self, card):
        def _txt(sel):
            el = card.query_selector(sel)
            return (el.inner_text() or "").strip() if el else ""

        def _href(sel):
            el = card.query_selector(sel)
            href = el.get_attribute("href") if el else ""
            if href and href.startswith("/"):
                href = "https://www.zhipin.com" + href
            return href

        title = _txt(".job-name, .job-title, h3.job-name, .job-info .job-name")
        company = _txt(".company-name, .company-text .name, .company-info a")
        salary = _txt(".salary, .job-info .salary, .job-area-wrapper .salary")
        area = _txt(".job-area, .job-area-wrapper .job-area, .job-location")
        link = _href(".job-card-left a, .job-info a, a.job-name")
        # 标签（经验/学历）在 .job-info 下
        tags = []
        for el in card.query_selector_all(".job-info .tag-list li, .job-card-footer li, .job-info li"):
            t = (el.inner_text() or "").strip()
            if t:
                tags.append(t)
        if not link:
            a = card.query_selector("a")
            if a:
                href = a.get_attribute("href") or ""
                if href.startswith("/"):
                    href = "https://www.zhipin.com" + href
                link = href
        desc = _txt(".job-card-footer .job-card-panel, .job-card-footer, .job-description")
        if not desc:
            desc = " ".join(tags)
        return {
            "title": title,
            "company": company,
            "salary": salary,
            "city": area,
            "url": link,
            "description": desc[:500],
            "tags": tags[:6],
        }
