import sys
import tempfile
import time
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
sys.path.insert(0, str(WEB))

import authz
import server


class AuthzTests(unittest.TestCase):
    """RBAC 权限矩阵 + 审计日志。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp()
        server.DB_FILE = Path(cls._tmp) / "test_authz.db"
        server.init_db()

    def setUp(self):
        conn = server.db()
        for t in ("audit_log", "users"):
            conn.execute("DELETE FROM " + t)
        conn.commit()
        conn.close()

    def test_permission_matrix(self):
        self.assertTrue(authz.has_permission("guest", "jobs.view"))
        self.assertFalse(authz.has_permission("guest", "settings.manage"))
        self.assertTrue(authz.has_permission("user", "applications.manage"))
        self.assertFalse(authz.has_permission("user", "audit.view"))
        self.assertTrue(authz.has_permission("admin", "audit.view"))
        self.assertTrue(authz.has_permission("admin", "admin.all"))
        # 未知角色回退 user
        self.assertTrue(authz.has_permission("superuser", "jobs.view"))
        self.assertFalse(authz.has_permission(None, "settings.manage"))

    def test_audit_writes_and_lists(self):
        server.audit("login.success", user_id=7, ip="127.0.0.1", meta={"k": "v"})
        server.audit("settings.update", user_id=7, meta={"keys": ["model"]})
        rows = server.list_audit(limit=10)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["action"], "settings.update")
        self.assertEqual(rows[0]["meta"], {"keys": ["model"]})
        rows_action = server.list_audit(limit=10, action="login.success")
        self.assertEqual(len(rows_action), 1)
        self.assertIn("ts", rows[0])

    def test_audit_does_not_contain_sensitive_payload(self):
        server.audit("settings.update", user_id=7, meta={"keys": ["api_key"], "value": "sk-secret"})
        rows = server.list_audit()
        self.assertNotIn("sk-secret", str(rows))

    def test_audit_silent_on_missing_table(self):
        conn = server.db()
        conn.execute("DROP TABLE audit_log")
        conn.commit()
        conn.close()
        server.audit("x.y", user_id=1)  # 不应抛异常
        # 重新建表（迁移记录仍存在，手动补建恢复现场）
        conn = server.db()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, user_id INTEGER,
                action TEXT NOT NULL, resource TEXT DEFAULT '', resource_id TEXT DEFAULT '',
                ip TEXT DEFAULT '', user_agent TEXT DEFAULT '', meta_json TEXT DEFAULT '{}')"""
        )
        conn.commit()
        conn.close()

    def test_migration_003_table_exists(self):
        conn = server.db()
        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        self.assertIn("audit_log", tables)


if __name__ == "__main__":
    unittest.main()
