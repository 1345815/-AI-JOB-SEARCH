#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""混沌演练 3：kill 一个 worker。

用法（需多 worker 模式）：
    WORKERS=2 WORKER_PORTS=8001,8002 python web/run.py
    python scripts/chaos/chaos_worker_kill.py --dry-run
    python scripts/chaos/chaos_worker_kill.py --target http://127.0.0.1:8001 --commit   # kill 8001 的 worker
"""

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.request


def _find_pid_on_port(port):
    if os.name == "nt":
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[1].endswith(":%d" % port) and parts[3] == "LISTENING":
                return int(parts[4])
        return None
    out = subprocess.run(["ss", "-lpn"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if ":%d " % port in line:
            import re
            m = re.search(r"pid=(\d+)", line)
            if m:
                return int(m.group(1))
    return None


def main():
    parser = argparse.ArgumentParser(description="worker kill 混沌演练")
    parser.add_argument("--target", default="http://127.0.0.1:8001", help="要 kill 的 worker 端口")
    parser.add_argument("--dry-run", action="store_true", help="只说明演练流程（默认）")
    parser.add_argument("--commit", action="store_true", help="真正执行")
    args = parser.parse_args()

    port = int(args.target.rsplit(":", 1)[-1])

    if args.dry_run or not args.commit:
        print("演练流程（--dry-run）：")
        print("  1. 多 worker 启动：WORKERS=2 WORKER_PORTS=8001,8002 python web/run.py")
        print("  2. 本脚本 kill %s 端口的 worker（pid 由端口反查）" % args.target)
        print("  3. 判定：nginx 被动摘除后剩余 worker 正常服务；父进程保持存活")
        print("  4. 恢复：systemd/进程管理器自动拉起，或手动重启")
        return 0

    pid = _find_pid_on_port(port)
    if not pid:
        print("未找到 %s 端口的监听进程" % port, file=sys.stderr)
        return 1
    print("kill worker pid=%d（端口 %d）" % (pid, port))
    os.kill(pid, signal.SIGTERM if os.name != "nt" else signal.SIGTERM)
    time.sleep(1)
    try:
        with urllib.request.urlopen(args.target, timeout=3) as resp:
            print("worker 仍在响应（可能被自动拉起）：%d" % resp.status)
    except Exception:
        print("worker 已停止响应（符合预期）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
