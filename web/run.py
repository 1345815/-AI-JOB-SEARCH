"""多 worker 启动器（纯标准库 multiprocessing）。

用法：
    python web/run.py                 # WORKERS=1（默认），直跑原端口 8000
    WORKERS=3 WORKER_PORTS=8001,8002,8003 python web/run.py
    WORKERS=3 WORKER_PORTS=8001,8002,8003 HOST=127.0.0.1 python web/run.py

- 父进程：等待子进程，捕获 SIGTERM/SIGINT 后优雅关闭（不发 kill -9）
- worker 崩溃：记录日志并保持父进程存活（由 systemd/进程管理器负责重启）
- 未设置 WORKER_PORTS 时退化为单 worker 直跑原端口（旧行为不变）
"""

import multiprocessing
import os
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))

import server  # noqa: E402


def _run_worker(host, port):
    """子进程入口：与 server.main() 相同的启动逻辑，绑定指定端口。"""
    server.init_db()
    global _worker_pool
    if os.environ.get("TASK_QUEUE_ENABLED", "1") == "1":
        _worker_pool = server.WorkerPool()
        _worker_pool.start()
    if os.environ.get("BACKUP_ENABLED", "1") == "1":
        server.start_backup_scheduler(
            server.DB_FILE,
            Path(os.environ.get("BACKUP_DIR", str(server.DB_FILE.parent / "backups"))),
            float(os.environ.get("BACKUP_INTERVAL_HOURS", "24")),
            int(os.environ.get("BACKUP_RETENTION", "14")),
        )
    httpd = server.ThreadingHTTPServer((host, port), server.Handler)
    print("[worker %d] CareerPilot Web 已启动：http://%s:%d" % (os.getpid(), host, port), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main():
    host = os.environ.get("HOST", "0.0.0.0")
    workers = int(os.environ.get("WORKERS", "1"))
    ports_env = os.environ.get("WORKER_PORTS", "")
    ports = [int(p) for p in ports_env.split(",") if p.strip()] if ports_env else []

    if workers <= 1 or len(ports) != workers:
        # 退化：单 worker 直跑原端口，保持旧行为
        _run_worker(host, int(os.environ.get("PORT", "8000")))
        return

    procs = []
    for port in ports:
        p = multiprocessing.Process(target=_run_worker, args=(host, port), daemon=False)
        p.start()
        procs.append(p)
        print("已启动 worker pid=%d port=%d" % (p.pid, port), flush=True)

    def _shutdown(signum, frame):
        print("\n收到信号 %d，正在优雅关闭 %d 个 worker…" % (signum, len(procs)), flush=True)
        for p in procs:
            if p.is_alive():
                p.terminate()
        for p in procs:
            p.join(timeout=10)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        while True:
            alive = [p for p in procs if p.is_alive()]
            if not alive:
                print("所有 worker 已退出。", flush=True)
                break
            for p in procs:
                if not p.is_alive() and p.exitcode not in (0, None):
                    print("worker pid=%d 异常退出（code=%s），保持父进程存活" % (p.pid, p.exitcode), flush=True)
            time.sleep(2)
    except KeyboardInterrupt:
        _shutdown(signal.SIGINT, None)


if __name__ == "__main__":
    main()
