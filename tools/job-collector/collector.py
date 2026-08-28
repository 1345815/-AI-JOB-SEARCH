"""本地岗位采集器 CLI：用本地 Chrome（你的登录态）串行采集招聘平台岗位。

用法：
  python collector.py --platform boss --keyword "AI 产品" --pages 3
  python collector.py --platform all --keyword "游戏策划" --pages 2

安全：验证码/登录墙/频率限制/未知结构 → 自动安全停止，绝不绕过。
输出：jobs/{platform}-{keyword}-{时间戳}.json（已去重）
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from adapters.base import SafeStop  # noqa: E402
from adapters.boss import BossAdapter  # noqa: E402
from adapters.zhilian import ZhilianAdapter  # noqa: E402
from adapters.job51 import Job51Adapter  # noqa: E402

ADAPTERS = {
    "boss": BossAdapter,
    "zhilian": ZhilianAdapter,
    "51job": Job51Adapter,
}


def make_page():
    from playwright.sync_api import sync_playwright
    # 持久化用户数据目录：登录态保存，之后不用重复登录
    user_data = HERE / ".browser-data"
    user_data.mkdir(exist_ok=True)
    p = sync_playwright().start()
    browser = p.chromium.launch_persistent_context(
        user_data_dir=str(user_data),
        channel="chrome",
        headless=False,
        viewport={"width": 1440, "height": 900},
    )
    return p, browser


def main():
    ap = argparse.ArgumentParser(description="本地岗位采集器（安全模式）")
    ap.add_argument("--platform", default="boss", help="boss / zhilian / 51job / all")
    ap.add_argument("--keyword", required=True, help="搜索关键词")
    ap.add_argument("--pages", type=int, default=2, help="每个平台最大页数")
    ap.add_argument("--city", default="", help="城市（部分平台用于 URL 拼接，预留）")
    args = ap.parse_args()

    platforms = ["boss", "zhilian", "51job"] if args.platform == "all" else [args.platform]
    p, ctx = make_page()
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    all_jobs = []
    seen = set()

    print(f"🔍 开始采集 [{args.keyword}] 关键词，平台：{', '.join(platforms)}")
    print("（将打开本地 Chrome，请确保已登录对应招聘平台）")
    try:
        for name in platforms:
            adapter_cls = ADAPTERS.get(name)
            if not adapter_cls:
                print(f"⚠️ 未知平台 {name}，跳过")
                continue
            adapter = adapter_cls(page, args.keyword)
            adapter.max_pages = args.pages
            print(f"\n--- {name} 采集开始（最多 {args.pages} 页）---")
            try:
                adapter.search()
                jobs = adapter.collect()
            except SafeStop as e:
                print(f"🛑 安全停止：{e}")
                continue
            except Exception as e:
                print(f"⚠️ {name} 采集异常：{e}")
                continue
            fresh = 0
            for j in jobs:
                key = (j.get("url") or j.get("title") + j.get("company", "")).strip()
                if key and key in seen:
                    continue
                seen.add(key)
                all_jobs.append(j)
                fresh += 1
            print(f"✅ {name}：共 {len(jobs)} 条，新增 {fresh} 条")
    finally:
        ctx.close()
        p.stop()

    if not all_jobs:
        print("\n没有采集到岗位。请检查：浏览器是否已登录平台？页面是否出现验证码？")
        return 1

    out_dir = HERE / "jobs"
    out_dir.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_file = out_dir / f"{args.keyword}-{stamp}.json"
    out_file.write_text(json.dumps(all_jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n📦 共采集 {len(all_jobs)} 条去重岗位 → {out_file}")
    print("下一步：python import_jobs.py --file <文件> --base http://你的服务器:8000")
    return 0


if __name__ == "__main__":
    sys.exit(main())
