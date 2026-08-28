"""智联招聘适配器（只读采集骨架）。

页面：https://sou.zhaopin.com/?jl=530&kw={keyword}
说明：BOSS 适配器验证通过后，用本骨架接入。选择器需按智联实际 DOM 校准，
校准方法：浏览器打开智联搜索页 → F12 检查岗位卡片结构 → 更新下方选择器。
若结构未知，collector 会安全停止。
"""
from .base import BaseAdapter


class ZhilianAdapter(BaseAdapter):
    platform = "zhilian"
    search_url_tpl = "https://sou.zhaopin.com/?jl=530&kw={keyword}"
    card_selector = ".joblist-box__item, .joblist-box .joblist-item"
    max_pages = 2
    delay_range = (4.0, 8.0)
    verify_markers = ["验证码", "verify", "captcha"]
    login_markers = ["请登录", "扫码登录"]
    block_markers = ["访问过于频繁", "频率限制", "请求频繁"]

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
            "title": _txt(".jobinfo__name, .job-name, .contentpile__content__wrapper__item__info__box__jobname"),
            "company": _txt(".companyinfo__name, .company-name, .contentpile__content__wrapper__item__info__box__companyname"),
            "salary": _txt(".salary, .jobinfo__salary, .contentpile__content__wrapper__item__info__box__job__salary"),
            "city": _txt(".jobinfo__area, .job-area, .contentpile__content__wrapper__item__info__box__job__area"),
            "url": _href(".jobinfo a, a.job-name, .contentpile__content__wrapper__item__info a"),
            "description": "",
            "tags": [],
        }
