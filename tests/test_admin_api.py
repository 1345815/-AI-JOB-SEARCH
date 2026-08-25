import sys
import tempfile
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
sys.path.insert(0, str(WEB))

import server


class AdminApiTests(unittest.TestCase):
    """管理后台与运营洞察：admin 角色、用户治理、overview 聚合、停用拦截。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp()
        server.DB_FILE = Path(cls._tmp) / "test_admin.db"
        server.init_db()

    def setUp(self):
        conn = server.db()
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM users")
        conn.execute("DELETE FROM jobs")
        conn.execute("DELETE FROM applications")
        conn.execute("DELETE FROM evaluations")
        conn.execute("DELETE FROM tasks")
        conn.commit()
        conn.close()

    def _mkuser(self, username, role="user", disabled=0):
        with server._DB_LOCK:
            conn = server.db()
            cur = conn.execute(
                "INSERT INTO users (username, email, password_hash, role, disabled) VALUES (?,?,?,?,?)",
                (username, username + "@test.local", server.hash_password("pass123"), role, disabled),
            )
            conn.commit()
            uid = cur.lastrowid
            conn.close()
        return uid

    def test_is_admin_checks_role(self):
        self.assertTrue(server.is_admin({"role": "admin"}))
        self.assertFalse(server.is_admin({"role": "user"}))
        self.assertFalse(server.is_admin({"role": "guest"}))
        self.assertFalse(server.is_admin(None))

    def test_list_users_excludes_password_hash(self):
        self._mkuser("alice")
        self._mkuser("bob", role="admin")
        rows = server.list_users()
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertNotIn("password_hash", row)
            self.assertIn("username", row)
            self.assertIn("disabled", row)

    def test_list_users_query_and_paging(self):
        self._mkuser("zhangsan")
        self._mkuser("lisi")
        rows = server.list_users(query="zhang")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["username"], "zhangsan")
        rows2 = server.list_users(limit=1, offset=0)
        self.assertEqual(len(rows2), 1)

    def test_set_user_disabled_revokes_sessions(self):
        uid = self._mkuser("carol")
        conn = server.db()
        conn.execute(
            "INSERT INTO sessions (user_id, token, created_at, expires_at) VALUES (?,?,?,?)",
            (uid, "tok-1", "2026-01-01", "2099-01-01"),
        )
        conn.commit()
        conn.close()
        server.set_user_disabled(uid, True)
        row = server.get_user_by_id(uid)
        self.assertEqual(row["disabled"], 1)
        remaining = server.db().execute("SELECT COUNT(*) AS n FROM sessions WHERE user_id=?", (uid,)).fetchone()["n"]
        self.assertEqual(remaining, 0)
        server.set_user_disabled(uid, False)
        self.assertEqual(server.get_user_by_id(uid)["disabled"], 0)

    def test_set_user_role_promote_demote(self):
        uid = self._mkuser("dave")
        server.set_user_role(uid, "admin")
        self.assertEqual(server.get_user_by_id(uid)["role"], "admin")
        server.set_user_role(uid, "user")
        self.assertEqual(server.get_user_by_id(uid)["role"], "user")

    def test_admin_overview_aggregates_counts(self):
        self._mkuser("eve", role="admin")
        self._mkuser("frank")
        conn = server.db()
        conn.execute(
            "INSERT INTO jobs (id, title, company, source) VALUES ('j1','AI工程师','某公司','测试')"
        )
        conn.execute(
            "INSERT INTO applications (user_id, job_id, company, title, stage) VALUES (?, 'j1', '某公司', 'AI工程师', '已投递')",
            (1,),
        )
        conn.commit()
        conn.close()
        ov = server.admin_overview()
        self.assertEqual(ov["users_total"], 2)
        self.assertEqual(ov["jobs_total"], 1)
        self.assertEqual(ov["applications_total"], 1)
        self.assertEqual(ov["applications_by_stage"].get("已投递"), 1)
        self.assertIn("db_size_bytes", ov)
        self.assertIn("llm_enabled", ov)
        self.assertGreater(ov["db_size_bytes"], 0)

    def test_user_public_exposes_disabled(self):
        pub = server.user_public({"id": 1, "username": "g", "role": "user", "disabled": 1, "profile_json": "{}"})
        self.assertTrue(pub["disabled"])
        self.assertNotIn("password_hash", pub)


if __name__ == "__main__":
    unittest.main()
