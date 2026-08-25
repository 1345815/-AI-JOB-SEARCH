#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""游客数据清理脚本。

删除超过 N 天未活跃的 guest 账号及其全部关联数据。
默认 --dry-run 只列出候选；必须显式加 --commit 才真正删除。
可被 cron / systemd timer 周期调用。

用法：
    python scripts/cleanup_guests.py --days 30 --dry-run   # 列出候选（默认）
    python scripts/cleanup_guests.py --days 30 --commit    # 真正删除
"""

import argparse
import os
import sys
import time
from pathlib import Path

# 兼容两种运行方式：scripts/ 下直接运行 / 项目根运行
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))

import server  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="清理超期未活跃的游客账号")
    parser.add_argument("--days", type=int, default=30, help="超过 N 天未活跃则清理（默认 30）")
    parser.add_argument("--dry-run", action="store_true", help="只列出候选，不删除（默认）")
    parser.add_argument("--commit", action="store_true", help="真正执行删除（必须显式开启）")
    args = parser.parse_args()

    if not args.dry_run and not args.commit:
        print("安全提示：未指定 --commit，本次为 --dry-run 模式。")
        args.dry_run = True

    cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - args.days * 86400))
    with server._DB_LOCK:
        conn = server.db()
        rows = conn.execute(
            "SELECT id, username, created_at, updated_at FROM users WHERE role='guest' AND updated_at < ?",
            (cutoff,),
        ).fetchall()
        conn.close()

    print("候选游客账号（超过 %d 天未活跃）：%d 个" % (args.days, len(rows)))
    for row in rows:
        print("  - #%d %s (注册 %s, 更新 %s)" % (row["id"], row["username"], row["created_at"], row["updated_at"]))

    if args.dry_run:
        print("\n[dry-run] 未删除任何数据。确认后加 --commit 执行。")
        return 0

    deleted = 0
    for row in rows:
        try:
            server.delete_user_data(row["id"])
            deleted += 1
        except Exception as exc:
            print("  ! 删除 #%d %s 失败：%s" % (row["id"], row["username"], exc))
    print("\n已删除 %d 个游客账号及其数据。" % deleted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
