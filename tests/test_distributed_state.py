import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
sys.path.insert(0, str(WEB))

import cache
import server


class DistributedStateTests(unittest.TestCase):
    """分布式会话/缓存/限流：DB 共享限流、写放大治理、缓存 TTL、过期清理。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp()
        server.DB_FILE = Path(cls._tmp) / "test_dist.db"
        server.init_db()

    def setUp(self):
        conn = server.db()
        for t in ("login_attempts", "cache_entries", "sessions", "users"):
            conn.execute("DELETE FROM " + t)
        conn.commit()
        conn.close()

    def _mkuser(self):
        with server._DB_LOCK:
            conn = server.db()
            cur = conn.execute("INSERT INTO users (username, role) VALUES ('dist_test', 'user')")
            conn.commit()
            uid = cur.lastrowid
            conn.close()
        return uid

    def test_rate_limit_shared_across_connections(self):
        # 模拟双 worker：conn1 记录失败，conn2 读到锁定
        key = ("127.0.0.1", "candidate")
        server.clear_login_failures(key)
        for i in range(server.LOGIN_RATE_LIMIT):
            server.record_login_failure(key, now=100 + i)
        self.assertGreater(server.login_rate_status(key, now=110), 0)
        # 窗口过期后解锁
        self.assertEqual(server.login_rate_status(key, now=100 + server.LOGIN_RATE_WINDOW_SECONDS + 10), 0)
        server.clear_login_failures(key)
        self.assertEqual(server.login_rate_status(key, now=110), 0)

    def test_touch_session_write_amplification_control(self):
        uid = self._mkuser()
        token = server.create_session(uid, max_age=2592000)
        # 首次 touch：剩余充足（>阈值且>1天），不应刷新 expires_at
        before = server.db().execute("SELECT expires_at FROM sessions WHERE token=?", (token,)).fetchone()["expires_at"]
        server.touch_session(token)
        after = server.db().execute("SELECT expires_at FROM sessions WHERE token=?", (token,)).fetchone()["expires_at"]
        self.assertEqual(before, after)
        # 强制把 expires_at 调近，touch 应刷新
        server.db().execute("UPDATE sessions SET expires_at=? WHERE token=?", (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + 60)), token))
        server.db().commit()
        server.touch_session(token)
        refreshed = server.db().execute("SELECT expires_at FROM sessions WHERE token=?", (token,)).fetchone()["expires_at"]
        self.assertGreater(time.mktime(time.strptime(refreshed, "%Y-%m-%d %H:%M:%S")), time.time() + 3600)

    def test_cache_set_get_and_expire(self):
        cache.db_cache.set("k1", "v1", ttl_seconds=3600)
        self.assertEqual(cache.db_cache.get("k1"), "v1")
        cache.db_cache.set("k2", "v2", ttl_seconds=-1)
        self.assertIsNone(cache.db_cache.get("k2"))
        cache.db_cache.delete("k1")
        self.assertIsNone(cache.db_cache.get("k1"))

    def test_cleanup_removes_expired_rows(self):
        uid = self._mkuser()
        token = server.create_session(uid, max_age=2592000)
        conn = server.db()
        conn.execute("UPDATE sessions SET expires_at='2000-01-01 00:00:00' WHERE token=?", (token,))
        conn.execute("INSERT INTO cache_entries (cache_key, payload, expires_at) VALUES ('expired','x',?)", (time.time() - 10,))
        conn.commit()
        conn.close()
        removed = cache.cleanup_expired()
        self.assertGreaterEqual(removed, 2)
        conn = server.db()
        n = conn.execute("SELECT COUNT(*) AS n FROM sessions WHERE token=?", (token,)).fetchone()["n"]
        m = conn.execute("SELECT COUNT(*) AS n FROM cache_entries WHERE cache_key='expired'").fetchone()["n"]
        conn.close()
        self.assertEqual(n, 0)
        self.assertEqual(m, 0)

    def test_migration_002_tables_exist(self):
        conn = server.db()
        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        self.assertIn("login_attempts", tables)
        self.assertIn("cache_entries", tables)


if __name__ == "__main__":
    unittest.main()
