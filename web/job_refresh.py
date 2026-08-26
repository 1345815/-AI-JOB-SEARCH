"""岗位库自动刷新。

周期性拉取最近搜索关键词对应的真实岗位，去重入库：
- 新岗位写入；已存在（按 id/url/title+company 去重）自动跳过
- 默认每 24 小时一次，`JOB_REFRESH_INTERVAL_HOURS` 可配置
- 无历史搜索记录时用 `JOB_REFRESH_KEYWORDS`（逗号分隔）兜底
"""

import json
import os
import threading
import time

DEFAULT_KEYWORDS = os.environ.get("JOB_REFRESH_KEYWORDS", "AI,产品经理,游戏,数据分析")


def _recent_keywords(limit=8):
    """从 events 表取最近搜索关键词（去重，按时间倒序）。"""
    try:
        import server
        with server._DB_LOCK:
            conn = server.db()
            rows = conn.execute(
                "SELECT payload FROM events WHERE event_type='job_searched' ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
            conn.close()
    except Exception:
        return []
    keywords, seen = [], set()
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
            kw = (payload.get("keywords") or "").strip()
        except Exception:
            kw = ""
        if kw and kw.lower() not in seen:
            seen.add(kw.lower())
            keywords.append(kw)
        if len(keywords) >= limit:
            break
    return keywords


def _count_jobs(server):
    with server._DB_LOCK:
        conn = server.db()
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        conn.close()
    return total


def refresh_once():
    """拉取一轮岗位并入库。返回 (抓取数, 新入库数)。"""
    import server
    from job_extractor import search_freehire_jobs

    keywords = _recent_keywords()
    if not keywords:
        keywords = [kw.strip() for kw in DEFAULT_KEYWORDS.split(",") if kw.strip()][:3]
    before = _count_jobs(server)
    fetched = 0
    for kw in keywords:
        try:
            results = search_freehire_jobs({"keywords": kw, "limit": 20}, limit=20)
        except Exception:
            continue
        for item in results:
            fetched += 1
            try:
                server.add_job(item)
            except Exception:
                pass
    # add_job 对已存在岗位也返回 id（去重命中），无法区分新增/已有 → 用 count 差值统计新入库
    inserted = _count_jobs(server) - before
    print("[job-refresh] keywords=%d fetched=%d inserted=%d" % (len(keywords), fetched, inserted), flush=True)
    return fetched, inserted


def start_job_refresh_scheduler(interval_hours=None):
    """后台线程：周期性刷新岗位库。"""
    if interval_hours is None:
        interval_hours = float(os.environ.get("JOB_REFRESH_INTERVAL_HOURS", "24"))
    stop = threading.Event()

    def loop():
        # 启动后先等一个完整周期再首次执行（避免与启动流程抢资源）
        stop.wait(interval_hours * 3600)
        while not stop.is_set():
            try:
                refresh_once()
            except Exception as exc:
                print("[job-refresh] error: %s" % exc, flush=True)
            stop.wait(interval_hours * 3600)

    t = threading.Thread(target=loop, name="job-refresh", daemon=True)
    t.start()
    print("[job-refresh] scheduler started, interval=%.0fh" % interval_hours, flush=True)
    return t
