import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
sys.path.insert(0, str(WEB))

import migrations


def _mk_conn():
    tmp = tempfile.mkdtemp()
    conn = sqlite3.connect(str(Path(tmp) / "mig.db"))
    conn.row_factory = sqlite3.Row
    return conn


class MigrationTests(unittest.TestCase):
    """版本化迁移：应用、幂等、回滚、dry-run、CLI。"""

    def setUp(self):
        self.conn = _mk_conn()
        self.conn.execute(
            "CREATE TABLE applications (id INTEGER PRIMARY KEY, user_id INTEGER)"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_migrate_applies_and_records(self):
        done = migrations.migrate(self.conn)
        self.assertEqual(done, ["001", "002"])
        rows = self.conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        self.assertEqual([r[0] for r in rows], ["001", "002"])
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(applications)").fetchall()}
        self.assertIn("follow_up_at", cols)
        self.assertIn("contact", cols)
        self.assertIn("attachment_name", cols)
        tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertIn("login_attempts", tables)
        self.assertIn("cache_entries", tables)

    def test_migrate_idempotent(self):
        migrations.migrate(self.conn)
        done2 = migrations.migrate(self.conn)
        self.assertEqual(done2, [])  # 第二次不重复应用
        rows = self.conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]
        self.assertEqual(rows, 2)

    def test_rollback_removes_version_record(self):
        migrations.migrate(self.conn)
        rolled = migrations.rollback(self.conn, steps=1)
        self.assertEqual(rolled, ["002"])
        rows = self.conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]
        self.assertEqual(rows, 1)

    def test_dry_run_does_not_write(self):
        migrations.ensure_version_table(self.conn)
        sql = migrations._dry_run_sql()
        self.assertIn("001", sql)
        rows = self.conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]
        self.assertEqual(rows, 0)

    def test_status_marks_applied(self):
        migrations.migrate(self.conn)
        status = migrations.status(self.conn)
        self.assertEqual(len(status), 2)
        self.assertTrue(all(item[2] for item in status))  # (version, name, applied)


if __name__ == "__main__":
    unittest.main()
