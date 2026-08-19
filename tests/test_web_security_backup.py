import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

WEB = Path(__file__).resolve().parents[1] / "web"
sys.path.insert(0, str(WEB))

import db_backup
import job_extractor
import server


class WebSecurityBackupTests(unittest.TestCase):
    def test_ssrf_rejects_private_and_accepts_public_dns(self):
        private = [(2, 1, 6, "", ("127.0.0.1", 80))]
        with mock.patch.object(job_extractor.socket, "getaddrinfo", return_value=private):
            with self.assertRaises(ValueError):
                job_extractor.validate_public_url("http://example.com/job")
        public = [(2, 1, 6, "", ("93.184.216.34", 443))]
        with mock.patch.object(job_extractor.socket, "getaddrinfo", return_value=public):
            self.assertEqual(job_extractor.validate_public_url("https://example.com/job"), "https://example.com/job")

    def test_login_rate_limit_expires_and_can_clear(self):
        key = ("127.0.0.1", "candidate")
        server.clear_login_failures(key)
        for index in range(server.LOGIN_RATE_LIMIT):
            server.record_login_failure(key, now=100 + index)
        self.assertGreater(server.login_rate_status(key, now=110), 0)
        self.assertEqual(server.login_rate_status(key, now=100 + server.LOGIN_RATE_WINDOW_SECONDS + 10), 0)

    def test_global_job_filters(self):
        jobs = [
            {"title": "游戏策划", "company": "甲", "city": "广州", "tags": ["游戏"], "description": "", "posting_type": "校招", "work_type": "全职", "source": "local", "is_demo": False, "deadline": "2099-01-01"},
            {"title": "数据分析", "company": "乙", "city": "上海", "tags": ["SQL"], "description": "", "posting_type": "社招", "work_type": "全职", "source": "web_search", "is_demo": False, "deadline": ""},
        ]
        query = {"q": ["游戏"], "city": ["广州"], "type": ["校招"], "source": ["local"]}
        self.assertEqual([job["title"] for job in server.filter_jobs(jobs, query)], ["游戏策划"])

    def test_search_results_include_saved_job_id(self):
        saved = [{"id": "saved-1", "title": "游戏策划", "company": "甲", "url": "https://example.com/job"}]
        results = [{"id": "candidate-1", "title": "游戏策划", "company": "甲", "url": "https://example.com/job", "source": "local"}]
        with mock.patch.object(server, "list_jobs", return_value=saved):
            marked = server.mark_saved_search_results(results)
        self.assertEqual(marked[0]["saved_job_id"], "saved-1")
        self.assertNotIn("saved_job_id", results[0])

    def test_backup_verify_restore_and_retention(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "careerpilot.db"
            conn = sqlite3.connect(database)
            conn.execute("CREATE TABLE sample(value TEXT)")
            conn.execute("INSERT INTO sample VALUES ('before')")
            conn.commit()
            conn.close()
            backup = db_backup.create_backup(database, root / "backups", retention=1)
            self.assertEqual(db_backup.verify_database(backup)["integrity"], "ok")
            conn = sqlite3.connect(database)
            conn.execute("UPDATE sample SET value='after'")
            conn.commit()
            conn.close()
            restored, safety = db_backup.restore_backup(backup, database)
            conn = sqlite3.connect(restored)
            self.assertEqual(conn.execute("SELECT value FROM sample").fetchone()[0], "before")
            conn.close()
            self.assertTrue(safety.exists())


if __name__ == "__main__":
    unittest.main()
