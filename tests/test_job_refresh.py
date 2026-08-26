import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

WEB = Path(__file__).resolve().parents[1] / "web"
sys.path.insert(0, str(WEB))

import job_refresh
import job_extractor
import server


class JobRefreshTests(unittest.TestCase):
    """岗位库自动刷新：关键词来源、去重入库、幂等。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp()
        server.DB_FILE = Path(cls._tmp) / "test_job_refresh.db"
        server.init_db()

    def setUp(self):
        with server._DB_LOCK:
            conn = server.db()
            for t in ("events", "jobs"):
                conn.execute("DELETE FROM " + t)
            conn.execute("DELETE FROM users")
            conn.execute("INSERT INTO users (id, username, role) VALUES (1, 'u', 'user')")
            conn.commit()
            conn.close()

    def _seed_events(self, *keywords):
        with server._DB_LOCK:
            conn = server.db()
            for i, kw in enumerate(keywords):
                conn.execute(
                    "INSERT INTO events (user_id, event_type, payload, created_at) VALUES (1, 'job_searched', ?, ?)",
                    ('{"keywords":"%s"}' % kw, 100 + i),
                )
            conn.commit()
            conn.close()

    def test_recent_keywords_deduplicated(self):
        self._seed_events("AI", "游戏", "AI")
        kws = job_refresh._recent_keywords()
        self.assertEqual(kws, ["AI", "游戏"])  # 最新优先 + 去重

    def test_refresh_inserts_once_and_idempotent(self):
        self._seed_events("AI")

        def fake_search(query, limit=20):
            return [
                {"title": "AI 产品经理", "company": "转转", "city": "北京", "url": "https://x.com/job1", "source": "freehire", "description": "负责 AI 应用", "tags": []},
                {"title": "游戏策划", "company": "网易", "city": "广州", "url": "https://x.com/job2", "source": "freehire", "description": "游戏设计", "tags": []},
            ]

        with mock.patch.object(job_extractor, "search_freehire_jobs", side_effect=fake_search):
            fetched1, inserted1 = job_refresh.refresh_once()
            fetched2, inserted2 = job_refresh.refresh_once()
        self.assertEqual(fetched1, 2)
        self.assertEqual(inserted1, 2)
        self.assertEqual(inserted2, 0)  # 幂等

    def test_default_keywords_when_no_history(self):
        with mock.patch.object(job_extractor, "search_freehire_jobs", return_value=[]) as m:
            job_refresh.refresh_once()
        self.assertTrue(m.called)


if __name__ == "__main__":
    unittest.main()
