"""后台任务 Worker 池：消费 tasks 队列，执行 handler，写执行审计。

- 默认随服务器进程启动（daemon 线程），WORKER_CONCURRENCY 控制并发数
- 每次执行写 task_runs 审计（started/succeeded/failed + worker_id + attempt）
- 优雅关闭：stop() 后线程在空闲轮询间隙退出
"""

import logging
import os
import threading
import time
import uuid

try:
    import tasks as task_queue
    from task_handlers import HANDLERS, TaskError
except ImportError:  # python -m web.server 包模式：web 目录不在 sys.path
    from web import tasks as task_queue
    from web.task_handlers import HANDLERS, TaskError

logger = logging.getLogger("careerpilot.worker")


class WorkerPool(object):
    def __init__(self, concurrency=None, poll_interval=1.0):
        self._concurrency = concurrency if concurrency is not None else int(os.environ.get("WORKER_CONCURRENCY", "2"))
        self._poll_interval = poll_interval
        self._stop = threading.Event()
        self._threads = []
        self._worker_id = "w-" + uuid.uuid4().hex[:8]

    def start(self):
        for i in range(max(1, self._concurrency)):
            t = threading.Thread(
                target=self._loop, name="task-worker-%d" % i, daemon=True
            )
            t.start()
            self._threads.append(t)
        logger.info("task worker pool started: concurrency=%d", self._concurrency)

    def stop(self):
        self._stop.set()
        for t in self._threads:
            t.join(timeout=3.0)

    # ------------------------------------------------------------ 主循环

    def _loop(self):
        while not self._stop.is_set():
            try:
                task = task_queue.claim_next(self._worker_id)
            except Exception as exc:  # 队列层异常不打死 worker
                logger.exception("claim_next failed: %s", exc)
                task = None
            if task:
                self._execute(task)
            else:
                time.sleep(self._poll_interval)

    def _execute(self, task):
        task_id = task["id"]
        attempt = task["attempt"]
        started = time.time()
        task_queue.record_run(task_id, attempt, self._worker_id, "started", started)
        task["input"]["user_id"] = task.get("user_id")
        try:
            handler = HANDLERS.get(task["task_type"])
            if handler is None:
                raise TaskError("未知任务类型: %s" % task["task_type"])
            result = handler(task["input"])
            task_queue.finish_task(task_id, result)
            task_queue.record_run(task_id, attempt, self._worker_id, "succeeded",
                                  started, finished_at=time.time())
            logger.info("task %s (%s) succeeded", task_id, task["task_type"])
        except Exception as exc:
            error = str(exc) or exc.__class__.__name__
            task_queue.fail_task(task_id, error)
            task_queue.record_run(task_id, attempt, self._worker_id, "failed",
                                  started, finished_at=time.time(), error=error)
            logger.warning("task %s (%s) failed: %s", task_id, task["task_type"], error)
