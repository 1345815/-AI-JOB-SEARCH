import sys
import tempfile
import threading
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
sys.path.insert(0, str(WEB))

import server


class DbPragmaTests(unittest.TestCase):
    """无状态化：WAL 模式、PRAGMA、多线程并发写。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp()
        server.DB_FILE = Path(cls._tmp) / "test_wal.db"
        server.init_db()
        with server._DB_LOCK:
            conn = server.db()
            conn.execute("INSERT INTO users (username, role) VALUES ('wal_test', 'user')")
            conn.commit()
            conn.close()

    def test_journal_mode_is_wal(self):
        conn = server.db()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        self.assertEqual(mode.lower(), "wal")

    def test_prgamas_effective(self):
        conn = server.db()
        self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 1)  # NORMAL
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
        conn.close()

    def test_concurrent_writes_no_locked(self):
        errors = []

        def run(i):
            try:
                for j in range(20):
                    conn = server.db()
                    conn.execute(
                        "INSERT INTO help_records (user_id, record_type, title, content, record_date, created_at, updated_at)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (1, "测试", "thread-%d-%d" % (i, j), "x", "2026-08-25", "2026-08-25", "2026-08-25"),
                    )
                    conn.commit()
                    conn.close()
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertFalse(errors, errors)
        conn = server.db()
        n = conn.execute("SELECT COUNT(*) AS n FROM help_records WHERE user_id=1").fetchone()["n"]
        conn.close()
        self.assertEqual(n, 80)


if __name__ == "__main__":
    unittest.main()
