"""统一缓存接口（SQLite 实现 + TTL）+ 过期清理调度。

纯标准库实现：
- DbCacheBackend：读写 cache_entries 表（expires_at=now+ttl），get 时检查过期并惰性删除
- cleanup_expired()：清理过期 sessions / login_attempts / cache_entries
- start_cleanup_scheduler()：后台定时清理（参考 db_backup.start_backup_scheduler 风格）
"""

import json
import threading
import time

_TICK = 60


def _db():
    try:
        import server
        return server.db()
    except Exception:
        import sqlite3
        conn = sqlite3.connect(":memory:")
        return conn


class DbCacheBackend(object):
    def get(self, key):
        try:
            conn = _db()
            row = conn.execute(
                "SELECT payload, expires_at FROM cache_entries WHERE cache_key=?", (key,)
            ).fetchone()
            if not row:
                conn.close()
                return None
            if row["expires_at"] < time.time():
                conn.execute("DELETE FROM cache_entries WHERE cache_key=?", (key,))
                conn.commit()
                conn.close()
                return None
            payload = row["payload"]
            conn.close()
            return payload
        except Exception:
            return None

    def set(self, key, value, ttl_seconds=3600):
        try:
            conn = _db()
            conn.execute(
                "INSERT INTO cache_entries (cache_key, payload, expires_at) VALUES (?,?,?)"
                " ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload, expires_at=excluded.expires_at",
                (key, value, time.time() + ttl_seconds),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    def delete(self, key):
        try:
            conn = _db()
            conn.execute("DELETE FROM cache_entries WHERE cache_key=?", (key,))
            conn.commit()
            conn.close()
        except Exception:
            pass


db_cache = DbCacheBackend()


def cleanup_expired(now=None):
    now = now or time.time()
    removed = 0
    try:
        import server
        conn = server.db()
        # 过期会话（expires_at 为 'YYYY-MM-DD HH:MM:SS' 文本）
        cur = conn.execute("DELETE FROM sessions WHERE expires_at < datetime('now', 'localtime')")
        removed += cur.rowcount
        # 过期限流记录（窗口之外）
        cur = conn.execute("DELETE FROM login_attempts WHERE updated_at < ?", (now - server.LOGIN_RATE_WINDOW_SECONDS,))
        removed += cur.rowcount
        # 过期缓存
        cur = conn.execute("DELETE FROM cache_entries WHERE expires_at < ?", (now,))
        removed += cur.rowcount
        conn.commit()
        conn.close()
    except Exception:
        pass
    return removed


def _scheduler_loop(interval_seconds, stop_event):
    while not stop_event.wait(interval_seconds):
        try:
            cleanup_expired()
        except Exception:
            pass


def start_cleanup_scheduler(interval_seconds=3600):
    stop_event = threading.Event()
    t = threading.Thread(target=_scheduler_loop, args=(interval_seconds, stop_event), daemon=True)
    t.start()
    return stop_event
