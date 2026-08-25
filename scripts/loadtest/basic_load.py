#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""基础压测脚本（纯标准库，无第三方依赖）。

用法：
    python scripts/loadtest/basic_load.py --base-url http://127.0.0.1:8000 --concurrency 20 --duration 10
    python scripts/loadtest/basic_load.py --base-url http://127.0.0.1:8000 --endpoints /healthz /api/jobs --report perf.json

输出：控制台表格 + 可选 JSON 报告。
退出码：错误率 > 5% 时非 0（CI 可用）。
"""

import argparse
import concurrent.futures
import json
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request


def _request(url):
    t0 = time.perf_counter()
    status = 0
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
    except urllib.error.HTTPError as e:
        status = e.code
    except Exception:
        status = 0
    return status, (time.perf_counter() - t0) * 1000


def run_load(base_url, endpoints, concurrency, duration):
    urls = [base_url.rstrip("/") + ep for ep in endpoints]
    stop = threading.Event()
    results = []
    lock = threading.Lock()

    def worker():
        while not stop.is_set():
            for url in urls:
                status, ms = _request(url)
                with lock:
                    results.append((status, ms))

    threads = [threading.Thread(target=worker) for _ in range(concurrency)]
    for t in threads:
        t.start()
    deadline = time.time() + duration
    while time.time() < deadline:
        time.sleep(0.1)
    stop.set()
    for t in threads:
        t.join()

    total = len(results)
    latencies = sorted(r[1] for r in results)
    statuses = [r[0] for r in results]
    errors = [s for s in statuses if s == 0 or s >= 500]
    p95 = latencies[int(total * 0.95) - 1] if total else 0
    p99 = latencies[int(total * 0.99) - 1] if total else 0
    return {
        "total": total,
        "qps": round(total / duration, 2),
        "p95_ms": round(p95, 1),
        "p99_ms": round(p99, 1),
        "avg_ms": round(statistics.mean(latencies), 1) if latencies else 0,
        "errors": len(errors),
        "error_rate": round(len(errors) / total * 100, 2) if total else 0,
        "statuses": {str(s): statuses.count(s) for s in sorted(set(statuses))},
    }


def main():
    parser = argparse.ArgumentParser(description="CareerPilot 基础压测")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--duration", type=int, default=10)
    parser.add_argument("--endpoints", nargs="*", default=["/healthz", "/api/jobs", "/api/today-tasks"])
    parser.add_argument("--report", default="")
    args = parser.parse_args()

    print("压测开始：%s  并发=%d  时长=%ds" % (args.base_url, args.concurrency, args.duration))
    print("端点：%s" % ", ".join(args.endpoints))
    result = run_load(args.base_url, args.endpoints, args.concurrency, args.duration)

    print("\n结果：")
    print("  总请求  : %d" % result["total"])
    print("  QPS     : %s" % result["qps"])
    print("  P95     : %sms" % result["p95_ms"])
    print("  P99     : %sms" % result["p99_ms"])
    print("  平均    : %sms" % result["avg_ms"])
    print("  错误    : %d (%.2f%%)" % (result["errors"], result["error_rate"]))
    print("  状态分布: %s" % json.dumps(result["statuses"]))

    if args.report:
        result["base_url"] = args.base_url
        result["concurrency"] = args.concurrency
        result["duration"] = args.duration
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print("报告已写入：%s" % args.report)

    if result["error_rate"] > 5:
        print("错误率超过 5%，退出码 1", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
