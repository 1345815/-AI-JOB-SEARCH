"""岗位采集适配器基类：平台适配器继承并实现 selectors/start_url/parse_card。

安全原则（与 BossHunter 一致）：
- 检测到验证码、登录墙、频率限制或未知页面结构时立即安全停止，绝不绕过
- 每个请求之间随机延时，限制页数，保持低频
"""
import random
import time


class SafeStop(Exception):
    """安全停止：验证码/登录墙/频率限制/未知结构。"""


class BaseAdapter:
    platform = "base"

    # 子类覆盖
    search_url_tpl = ""      # 搜索页 URL 模板，{keyword} 占位
    card_selector = ""       # 岗位卡片选择器
    selectors = {}           # 字段选择器：title/company/city/salary/link/desc
    max_pages = 3
    delay_range = (3.0, 6.0)

    # 安全停止信号
    verify_markers = ["验证码", "verify", "captcha"]
    login_markers = ["请登录", "登录后查看", "扫码登录", "立即登录"]
    block_markers = ["访问过于频繁", "请求频繁", "频率限制", "请稍后再试", "操作太频繁"]
    unknown_markers = []     # 平台特有未知结构标记

    def __init__(self, page, keyword):
        self.page = page
        self.keyword = keyword

    def _rand_delay(self):
        time.sleep(random.uniform(*self.delay_range))

    def check_safety(self):
        """检测安全信号，命中即抛 SafeStop。"""
        url = (self.page.url or "").lower()
        for m in self.verify_markers:
            if m.lower() in url:
                raise SafeStop(f"{self.platform}：检测到验证码，安全停止")
        try:
            body = self.page.content()
        except Exception:
            return
        text = body[:4000]
        for m in self.verify_markers:
            if m.lower() in text:
                raise SafeStop(f"{self.platform}：检测到验证码，安全停止")
        for m in self.login_markers:
            if m in text:
                raise SafeStop(f"{self.platform}：检测到登录墙，请在浏览器中登录后重试")
        for m in self.block_markers:
            if m in text:
                raise SafeStop(f"{self.platform}：检测到频率限制，安全停止")
        for m in self.unknown_markers:
            if m in text:
                raise SafeStop(f"{self.platform}：页面结构异常（{m}），安全停止")

    def search(self):
        """打开搜索页并返回 URL。"""
        url = self.search_url_tpl.format(keyword=self.keyword)
        self.page.goto(url, timeout=45000, wait_until="domcontentloaded")
        self._rand_delay()
        self.check_safety()
        return url

    def next_page(self):
        """翻页；无下一页返回 False。子类可选覆盖。"""
        return False

    def collect(self):
        """采集主循环：翻页提取卡片，返回岗位列表。"""
        jobs = []
        seen_urls = set()
        for page_idx in range(1, self.max_pages + 1):
            if page_idx > 1:
                if not self.next_page():
                    break
                self._rand_delay()
                self.check_safety()
            try:
                self.page.wait_for_selector(self.card_selector, timeout=15000)
            except Exception:
                # 可能结构变化或没有结果
                text = self.page.content()[:1500]
                if "暂无" in text or "没有找到" in text or "无相关" in text:
                    break
                raise SafeStop(f"{self.platform}：未找到岗位卡片，页面结构可能已变化")
            cards = self.page.query_selector_all(self.card_selector)
            if not cards:
                raise SafeStop(f"{self.platform}：卡片为空，页面结构可能已变化")
            for card in cards:
                try:
                    item = self.parse_card(card)
                except Exception:
                    continue
                if not item or not item.get("title") or not item.get("company"):
                    continue
                url = item.get("url", "")
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                item["platform"] = self.platform
                item["source"] = self.platform
                jobs.append(item)
            self._rand_delay()
        return jobs

    def parse_card(self, card):
        """从卡片元素提取字段。子类必须实现。"""
        raise NotImplementedError
