import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
sys.path.insert(0, str(WEB))

import server
import tasks
import worker


class TaskQueueTests(unittest.TestCase):
    """异步任务队列：持久化、原子领取、状态流转、失败重放、审计、权限。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp()
        server.DB_FILE = Path(cls._tmp) / "test_tasks.db"
        server.init_db()
        tasks.set_backend(tasks.SqliteQueueBackend())

    def setUp(self):
        conn = server.db()
        conn.execute("DELETE FROM task_runs")
        conn.execute("DELETE FROM tasks")
        conn.commit()
        conn.close()

    def _create(self, task_type="resume.generate", payload=None, max_attempts=3):
        return tasks.create_task(7, task_type, payload or {"job_id": "job-1", "profile": {}}, max_attempts)

    def test_create_task_is_persisted_pending(self):
        t = self._create()
        row = tasks.get_task(t["id"], user_id=7)
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["task_type"], "resume.generate")
        self.assertEqual(row["attempts"], 0)
        self.assertEqual(row["input"]["job_id"], "job-1")

    def test_claim_next_atomic_no_duplicate(self):
        for _ in range(8):
            self._create(payload={"job_id": "job-%d" % _})
        claimed = []
        lock = threading.Lock()
        barrier = threading.Barrier(8)

        def run():
            barrier.wait()
            task = tasks.claim_next("w-test")
            if task:
                with lock:
                    claimed.append(task["id"])

        threads = [threading.Thread(target=run) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(claimed), 8)
        self.assertEqual(len(set(claimed)), 8, "任务被重复领取")

    def test_worker_success_flow_and_audit(self):
        t = self._create()
        claimed = tasks.claim_next("w-test")
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["user_id"], 7)
        original = worker.HANDLERS["resume.generate"]
        worker.HANDLERS["resume.generate"] = lambda data: {"content": "定制简历内容"}
        try:
            worker.WorkerPool(concurrency=1)._execute(claimed)
        finally:
            worker.HANDLERS["resume.generate"] = original
        row = tasks.get_task(t["id"], user_id=7)
        self.assertEqual(row["status"], "succeeded")
        self.assertEqual(row["result"]["content"], "定制简历内容")
        conn = server.db()
        runs = conn.execute("SELECT run_status FROM task_runs WHERE task_id=?", (t["id"],)).fetchall()
        conn.close()
        statuses = sorted(r["run_status"] for r in runs)
        self.assertEqual(statuses, ["started", "succeeded"])

    def test_worker_failure_flow_and_audit(self):
        t = self._create(max_attempts=1)  # 1 次失败即达上限 → failed 终态
        claimed = tasks.claim_next("w-test")
        original = worker.HANDLERS["resume.generate"]
        worker.HANDLERS["resume.generate"] = lambda data: (_ for _ in ()).throw(RuntimeError("LLM 超时"))
        try:
            worker.WorkerPool(concurrency=1)._execute(claimed)
        finally:
            worker.HANDLERS["resume.generate"] = original
        row = tasks.get_task(t["id"], user_id=7)
        self.assertEqual(row["status"], "failed")
        self.assertIn("LLM 超时", row["error"])
        conn = server.db()
        runs = conn.execute("SELECT run_status FROM task_runs WHERE task_id=?", (t["id"],)).fetchall()
        conn.close()
        statuses = sorted(r["run_status"] for r in runs)
        self.assertEqual(statuses, ["failed", "started"])

    def test_worker_failure_auto_retry_keeps_pending(self):
        """自动重试：未达 max_attempts 时失败 → 回到 pending 并可再次领取。"""
        t = self._create(max_attempts=3)
        claimed = tasks.claim_next("w-test")
        original = worker.HANDLERS["resume.generate"]
        worker.HANDLERS["resume.generate"] = lambda data: (_ for _ in ()).throw(RuntimeError("临时故障"))
        try:
            worker.WorkerPool(concurrency=1)._execute(claimed)
        finally:
            worker.HANDLERS["resume.generate"] = original
        row = tasks.get_task(t["id"], user_id=7)
        self.assertEqual(row["status"], "pending", "未达上限失败应自动回到 pending")
        self.assertIn("临时故障", row["error"])
        self.assertEqual(row["attempts"], 1)
        again = tasks.claim_next("w-test")
        self.assertIsNotNone(again, "自动重试后应可再次领取")
        self.assertEqual(again["id"], t["id"])

    def test_retry_failed_task_resets(self):
        t = self._create(max_attempts=1)
        tasks.claim_next("w-test")
        tasks.fail_task(t["id"], "boom")
        self.assertEqual(tasks.get_task(t["id"], user_id=7)["status"], "failed")
        self.assertTrue(tasks.retry_task(t["id"], user_id=7))
        row = tasks.get_task(t["id"], user_id=7)
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["attempts"], 0)
        self.assertEqual(row["error"], "")

    def test_retry_rejected_for_others(self):
        t = self._create(max_attempts=1)
        tasks.claim_next("w-test")
        tasks.fail_task(t["id"], "boom")
        self.assertFalse(tasks.retry_task(t["id"], user_id=999))
        self.assertEqual(tasks.get_task(t["id"], user_id=999), None)

    def test_max_attempts_stops_claim(self):
        t = self._create(max_attempts=2)
        for _ in range(2):
            claimed = tasks.claim_next("w-test")
            self.assertIsNotNone(claimed)
            tasks.fail_task(t["id"], "again")
        self.assertIsNone(tasks.claim_next("w-test"), "达到 max_attempts 后不应再被领取")
        row = tasks.get_task(t["id"], user_id=7)
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["attempts"], 2)

    def test_list_tasks_scoped_by_user(self):
        tasks.create_task(1, "resume.generate", {"job_id": "j1", "profile": {}})
        tasks.create_task(2, "interview.generate", {"job_id": "j2", "profile": {}})
        rows = tasks.list_tasks(user_id=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["task_type"], "resume.generate")


if __name__ == "__main__":
    unittest.main()
