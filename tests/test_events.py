import sys
import tempfile
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
sys.path.insert(0, str(WEB))

import server


class EventTrackingTests(unittest.TestCase):
    """求职漏斗与产品埋点：events 表、record_event、funnel_stats、静默容错。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp()
        server.DB_FILE = Path(cls._tmp) / "test_events.db"
        server.init_db()

    def _mkuser(self, username="eve"):
        with server._DB_LOCK:
            conn = server.db()
            cur = conn.execute(
                "INSERT INTO users (username, email, password_hash, role) VALUES (?,?,?, 'user')",
                (username, username + "@t.local", server.hash_password("pass123")),
            )
            conn.commit()
            uid = cur.lastrowid
            conn.close()
        return uid

    def setUp(self):
        conn = server.db()
        conn.execute("DELETE FROM events")
        conn.execute("DELETE FROM users")
        conn.commit()
        conn.close()
        self.u7 = self._mkuser("eve7")
        self.u8 = self._mkuser("eve8")

    def test_record_event_persists(self):
        server.record_event(self.u7, "job_searched", {"keywords": "游戏策划"})
        conn = server.db()
        row = conn.execute("SELECT * FROM events WHERE user_id=?", (self.u7,)).fetchone()
        conn.close()
        self.assertEqual(row["event_type"], "job_searched")
        self.assertIn("游戏策划", row["payload"])

    def test_funnel_stats_groups_by_type(self):
        server.record_event(self.u7, "job_scored", {"job_id": "j1"})
        server.record_event(self.u7, "job_scored", {"job_id": "j2"})
        server.record_event(self.u7, "job_saved", {"job_id": "j1"})
        server.record_event(self.u8, "job_searched", {})
        stats = server.funnel_stats(user_id=self.u7)
        self.assertEqual(stats["job_scored"], 2)
        self.assertEqual(stats["job_saved"], 1)
        self.assertNotIn("job_searched", stats)
        global_stats = server.funnel_stats()
        self.assertEqual(global_stats["job_scored"], 2)
        self.assertEqual(global_stats["job_searched"], 1)

    def test_record_event_silent_on_failure(self):
        # 事件表不存在时静默，不影响调用方
        conn = server.db()
        conn.execute("DROP TABLE events")
        conn.commit()
        conn.close()
        server.record_event(self.u7, "job_scored", {})  # 不应抛异常
        server.init_db()  # 恢复表

    def test_payload_does_not_leak_sensitive_data(self):
        server.record_event(self.u7, "job_scored", {"job_id": "j1", "overall": 80})
        conn = server.db()
        row = conn.execute("SELECT payload FROM events WHERE user_id=?", (self.u7,)).fetchone()
        conn.close()
        self.assertNotIn("password", row["payload"])
        self.assertNotIn("api_key", row["payload"])

    def test_save_evaluation_auto_tracks_job_scored(self):
        ev = {
            "job_id": "job-x",
            "overall": 72,
            "verdict": "建议申请",
            "dimensions": {},
            "gates": {},
            "strengths": [],
            "gaps": [],
            "summary": "匹配良好",
            "created_at": "2026-08-25",
        }
        server.save_evaluation(self.u7, ev)
        stats = server.funnel_stats(user_id=self.u7)
        self.assertEqual(stats.get("job_scored"), 1)


if __name__ == "__main__":
    unittest.main()
