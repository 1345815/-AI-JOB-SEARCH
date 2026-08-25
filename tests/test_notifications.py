import sys
import tempfile
import time
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
sys.path.insert(0, str(WEB))

import server


class NotificationTests(unittest.TestCase):
    """留存触达与跟进提醒：今日待办聚合、站内通知、去重、已读管理。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp()
        server.DB_FILE = Path(cls._tmp) / "test_notif.db"
        server.init_db()

    def _mkuser(self, username="user"):
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
        for t in ("notifications", "applications", "jobs", "users"):
            conn.execute("DELETE FROM " + t)
        conn.commit()
        conn.close()
        self.uid = self._mkuser()

    def _mkapp(self, stage="已收藏", follow_up_at="", job_id="j1", company="公司A", title="岗位A"):
        now = time.strftime("%Y-%m-%d %H:%M")
        with server._DB_LOCK:
            conn = server.db()
            conn.execute(
                "INSERT INTO applications (user_id, job_id, company, title, stage, follow_up_at, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (self.uid, job_id, company, title, stage, follow_up_at, now, now),
            )
            conn.commit()
            conn.close()

    def _mkjob(self, job_id="j1", deadline="2099-01-01", title="岗位A"):
        with server._DB_LOCK:
            conn = server.db()
            conn.execute(
                "INSERT INTO jobs (id, title, company, deadline) VALUES (?,?,?,?)",
                (job_id, title, "公司A", deadline),
            )
            conn.commit()
            conn.close()

    def test_notify_creates_and_unread_count(self):
        server.notify(self.uid, "test", "标题", "内容", "/pipeline")
        self.assertEqual(server.unread_count(self.uid), 1)
        rows = server.list_notifications(self.uid)
        self.assertEqual(rows[0]["title"], "标题")
        self.assertEqual(rows[0]["read"], 0)
        self.assertTrue(rows[0]["time_ago"])

    def test_today_tasks_groups_four_categories(self):
        today = time.strftime("%Y-%m-%d")
        week_later = time.strftime("%Y-%m-%d", time.localtime(time.time() + 3 * 86400))
        self._mkapp(stage="已投递", follow_up_at=today, job_id="j1")
        self._mkapp(stage="面试中", job_id="j2", title="岗位B")
        self._mkapp(stage="已收藏", job_id="j3", title="岗位C")
        self._mkjob(job_id="j4", deadline=week_later, title="岗位D")
        tasks = server.today_tasks(self.uid)
        self.assertEqual(len(tasks["follow_ups"]), 1)
        self.assertEqual(len(tasks["interviews"]), 1)
        self.assertEqual(len(tasks["pending"]), 1)
        self.assertEqual(len(tasks["deadlines"]), 1)
        self.assertEqual(tasks["deadlines"][0]["title"], "岗位D")
        self.assertEqual(tasks["deadlines"][0]["days_left"], 3)

    def test_deadline_notify_generates_once_only(self):
        near = time.strftime("%Y-%m-%d", time.localtime(time.time() + 2 * 86400))
        self._mkjob(job_id="j-near", deadline=near)
        job = server.get_job("j-near")
        server.notify_deadline_if_needed(self.uid, job)
        server.notify_deadline_if_needed(self.uid, job)
        server.notify_deadline_if_needed(self.uid, job)
        rows = server.list_notifications(self.uid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["type"], "deadline")

    def test_deadline_notify_skips_far_future(self):
        far = time.strftime("%Y-%m-%d", time.localtime(time.time() + 30 * 86400))
        self._mkjob(job_id="j-far", deadline=far)
        job = server.get_job("j-far")
        server.notify_deadline_if_needed(self.uid, job)
        self.assertEqual(server.unread_count(self.uid), 0)

    def test_mark_read_and_read_all(self):
        server.notify(self.uid, "a", "t1")
        server.notify(self.uid, "b", "t2")
        rows = server.list_notifications(self.uid)
        server.mark_notification_read(self.uid, rows[0]["id"])
        self.assertEqual(server.unread_count(self.uid), 1)
        server.mark_all_notifications_read(self.uid)
        self.assertEqual(server.unread_count(self.uid), 0)

    def test_notifications_scoped_per_user(self):
        other = self._mkuser("other")
        server.notify(self.uid, "a", "mine")
        server.notify(other, "b", "theirs")
        rows = server.list_notifications(self.uid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "mine")


if __name__ == "__main__":
    unittest.main()
