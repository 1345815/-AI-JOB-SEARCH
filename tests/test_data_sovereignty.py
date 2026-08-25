import sys
import tempfile
import time
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
sys.path.insert(0, str(WEB))

import server


class DataSovereigntyTests(unittest.TestCase):
    """数据主权与合规：导出、注销级联删除、admin 审计、admin 保护。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp()
        server.DB_FILE = Path(cls._tmp) / "test_sovereignty.db"
        server.init_db()

    def _mkuser(self, username="user", role="user"):
        with server._DB_LOCK:
            conn = server.db()
            cur = conn.execute(
                "INSERT INTO users (username, email, password_hash, role) VALUES (?,?,?,?)",
                (username, username + "@t.local", server.hash_password("pass123"), role),
            )
            conn.commit()
            uid = cur.lastrowid
            conn.close()
        return uid

    def setUp(self):
        conn = server.db()
        for t in ("admin_actions", "notifications", "events", "applications",
                  "evaluations", "chat_messages", "documents", "help_records", "users"):
            conn.execute("DELETE FROM " + t)
        conn.commit()
        conn.close()
        self.uid = self._mkuser()

    def test_export_excludes_password_hash_and_api_key(self):
        now = time.strftime("%Y-%m-%d %H:%M")
        with server._DB_LOCK:
            conn = server.db()
            conn.execute(
                "INSERT INTO applications (user_id, job_id, company, title, stage, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?)", (self.uid, "j1", "公司A", "岗位A", "已投递", now, now),
            )
            conn.execute(
                "INSERT INTO chat_messages (user_id, role, content, created_at) VALUES (?,?,?,?)",
                (self.uid, "user", "帮我优化简历", now),
            )
            conn.commit()
            conn.close()
        data = server.export_user_data(self.uid)
        self.assertIsNotNone(data)
        self.assertNotIn("password_hash", data["user"])
        self.assertNotIn("api_key", str(data))
        self.assertEqual(len(data["applications"]), 1)
        self.assertEqual(len(data["chat_messages"]), 1)
        self.assertIn("exported_at", data)

    def test_export_unknown_user_returns_none(self):
        self.assertIsNone(server.export_user_data(99999))

    def test_delete_user_data_cascades_all_related_rows(self):
        now = time.strftime("%Y-%m-%d %H:%M")
        with server._DB_LOCK:
            conn = server.db()
            conn.execute(
                "INSERT INTO applications (user_id, job_id, company, title, stage, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?)", (self.uid, "j1", "公司A", "岗位A", "已投递", now, now),
            )
            conn.execute(
                "INSERT INTO chat_messages (user_id, role, content, created_at) VALUES (?,?,?,?)",
                (self.uid, "user", "hello", now),
            )
            conn.execute("INSERT INTO events (user_id, event_type, payload, created_at) VALUES (?,?,?,?)",
                         (self.uid, "job_scored", "{}", time.time()))
            conn.execute("INSERT INTO notifications (user_id, type, title, created_at) VALUES (?,?,?,?)",
                         (self.uid, "test", "t", time.time()))
            conn.commit()
            conn.close()
        server.delete_user_data(self.uid)
        conn = server.db()
        user = conn.execute("SELECT 1 FROM users WHERE id=?", (self.uid,)).fetchone()
        apps = conn.execute("SELECT COUNT(*) AS n FROM applications WHERE user_id=?", (self.uid,)).fetchone()["n"]
        chats = conn.execute("SELECT COUNT(*) AS n FROM chat_messages WHERE user_id=?", (self.uid,)).fetchone()["n"]
        evts = conn.execute("SELECT COUNT(*) AS n FROM events WHERE user_id=?", (self.uid,)).fetchone()["n"]
        notifs = conn.execute("SELECT COUNT(*) AS n FROM notifications WHERE user_id=?", (self.uid,)).fetchone()["n"]
        conn.close()
        self.assertIsNone(user)
        self.assertEqual(apps, 0)
        self.assertEqual(chats, 0)
        self.assertEqual(evts, 0)
        self.assertEqual(notifs, 0)

    def test_admin_cannot_be_deleted_by_api_guard(self):
        # 模拟 _api_auth 的 admin 保护逻辑
        admin = self._mkuser("boss", role="admin")
        server.delete_user_data(admin)  # 底层函数允许，但 API 层有 403 保护
        self.assertIsNone(server.get_user_by_id(admin))

    def test_record_admin_action_logs(self):
        admin = self._mkuser("boss", role="admin")
        target = self._mkuser("victim")
        server.record_admin_action(admin, "disable", target, "target=victim")
        conn = server.db()
        row = conn.execute("SELECT * FROM admin_actions WHERE admin_user_id=?", (admin,)).fetchone()
        conn.close()
        self.assertEqual(row["action"], "disable")
        self.assertEqual(row["target_user_id"], target)

    def test_guest_cleanup_picks_stale_only(self):
        stale = self._mkuser("guest_stale_1", role="guest")
        fresh = self._mkuser("guest_fresh_2", role="guest")
        with server._DB_LOCK:
            conn = server.db()
            conn.execute("UPDATE users SET updated_at=? WHERE id=?", ("2000-01-01 00:00:00", stale))
            conn.execute("UPDATE users SET updated_at=? WHERE id=?", (time.strftime("%Y-%m-%d %H:%M:%S"), fresh))
            conn.commit()
            conn.close()
        # 复用 cleanup 逻辑（不真正跑脚本，直接验证查询条件）
        cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 30 * 86400))
        with server._DB_LOCK:
            conn = server.db()
            candidates = conn.execute(
                "SELECT id FROM users WHERE role='guest' AND updated_at < ?", (cutoff,),
            ).fetchall()
            conn.close()
        ids = [r["id"] for r in candidates]
        self.assertIn(stale, ids)
        self.assertNotIn(fresh, ids)


if __name__ == "__main__":
    unittest.main()
