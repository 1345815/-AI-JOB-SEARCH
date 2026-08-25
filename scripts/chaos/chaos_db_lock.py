#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""混沌演练 1：DB 锁（长事务）期间观察服务行为。

预期：busy_timeout=5000 下请求最多变慢，不应大量 5xx。
用法：
    python scripts/chaos/chaos_db_lock.py --dry-run                # 演练流程说明
    python scripts/chaos/chaos_db_lock.py --target http://127.0.0.1:8000 --db-path /path/careerpilot.db --commit
"""

import argparse
import json
import sqlite3
import sys
import time
import urllib.request


def _probe(base, path="/healthz"):
    try:
        t0 = time.perf_counter()
        with urllib.request.urlopen(base + path, timeout=15) as resp:
            return resp.status, (time.perf_counter() - t0) * 1000
    except Exception as e:
        return 0, 0


def main():
    parser = argparse.ArgumentParser(description="DB 锁混沌演练")
    parser.add_argument("--target", default="http://127.0.0.1:8000")
    parser.add_argument("--db-path", default="")
    parser.add_argument("--dry-run", action="store_true", help="只说明演练流程（默认）")
    parser.add_argument("--commit", action="store_true", help="真正执行（必须显式开启）")
    args = parser.parse_args()

    if args.dry_run or not args.commit or not args.db_path:
        print("演练流程（--dry-run）：")
        print("  1. 在 %s 上开长事务：BEGIN IMMEDIATE; sleep 15; ROLLBACK" % (args.db_path or "<db-path>"))
        print("  2. 期间压测：python scripts/loadtest/basic_load.py --base-url %s" % args.target)
        print("  3. 判定：请求不应大量 5xx，最多变慢（busy_timeout=5000 生效）")
        print("  4. 恢复：等待 ROLLBACK 自动完成")
        return 0

    conn = sqlite3.connect(args.db_path)
    print("打开长事务（15s）…")
    conn.execute("BEGIN IMMEDIATE")
    time.sleep(15)
    conn.rollback()
    conn.close()
    print("已回滚。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
