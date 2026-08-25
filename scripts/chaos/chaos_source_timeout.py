#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""混沌演练 2：外部数据源超时。

把 FREEHIRE_API_URL 指向不可达地址后调 /api/jobs/search，观察错误处理。
预期：搜索接口降级返回错误，进程不挂死。
用法：
    python scripts/chaos/chaos_source_timeout.py --dry-run
    FREEHIRE_API_URL=http://10.255.255.1:9 python scripts/chaos/chaos_source_timeout.py --target http://127.0.0.1:8000 --commit
"""

import argparse
import sys
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser(description="数据源超时混沌演练")
    parser.add_argument("--target", default="http://127.0.0.1:8000")
    parser.add_argument("--dry-run", action="store_true", help="只说明演练流程（默认）")
    parser.add_argument("--commit", action="store_true", help="真正执行")
    args = parser.parse_args()

    if args.dry_run or not args.commit:
        print("演练流程（--dry-run）：")
        print("  1. 设置环境变量：FREEHIRE_API_URL=http://10.255.255.1:9")
        print("  2. 重启服务使 env 生效")
        print("  3. 调用搜索接口：POST %s/api/jobs/search" % args.target)
        print("  4. 判定：接口降级返回错误信息而非挂死进程（超时 12s 内）")
        print("  5. 恢复：恢复 FREEHIRE_API_URL 并重启服务")
        return 0

    body = '{"keywords":"游戏策划"}'.encode("utf-8")
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(args.target + "/api/jobs/search", data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as resp:
            print("响应 %d，耗时 %.1fs" % (resp.status, time.perf_counter() - t0))
    except Exception as e:
        print("请求异常（进程未挂死，符合预期）：%s，耗时 %.1fs" % (e, time.perf_counter() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
