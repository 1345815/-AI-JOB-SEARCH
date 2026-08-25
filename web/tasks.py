"""异步任务队列：持久化任务表 + 状态查询 + 失败重放 + 执行审计。

纯标准库实现，零第三方依赖：
- tasks 表：任务持久化（pending/running/succeeded/failed）
- task_runs 表：每次执行的审计记录（attempt 级）
- QueueBackend 抽象 + SqliteQueueBackend：默认后端；
  RedisQueueBackend 预留接口（需安装 redis 后可用，见 docstring）

并发安全：claim_next 用 BEGIN IMMEDIATE 事务原子领取，
单进程多线程（_TASK_LOCK）与多进程（SQLite 写锁）均安全。
"""

import json
import threading
import time
import uuid

# ---------------------------------------------------------------- 常量

TASK_STATUS_PENDING = "pending"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_SUCCEEDED = "succeeded"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_CANCELED = "canceled"

VALID_TASK_TYPES = ("resume.generate", "cover_letter.generate", "interview.generate", "search")

MAX_ATTEMPTS_DEFAULT = 3

_TASK_LOCK = threading.RLock()


def _now():
    return time.time()


def new_task_id():
    return uuid.uuid4().hex


def _server_import():
    """兼容三种运行方式，保证拿到同一模块实例：
    python web/server.py / pytest（web 目录在 sys.path）→ import server
    python -m web.server（仅项目根在 sys.path）→ from web import server
    """
    try:
        import server
        return server
    except ImportError:
        from web import server
        return server


# ---------------------------------------------------------------- 队列后端接口


class QueueBackend(object):
    """外部队列适配器接口（Redis/Celery 等）。

    实现此接口即可替换默认 SQLite 后端，业务代码不变。
    """

    def create(self, task_id, user_id, task_type, payload, max_attempts):
        raise NotImplementedError

    def claim_next(self, worker_id):
        raise NotImplementedError

    def finish(self, task_id, result):
        raise NotImplementedError

    def fail(self, task_id, error):
        raise NotImplementedError

    def get(self, task_id):
        raise NotImplementedError

    def list_tasks(self, user_id=None, status=None, limit=50):
        raise NotImplementedError

    def retry(self, task_id, user_id=None):
        raise NotImplementedError

    def record_run(self, task_id, attempt, worker_id, run_status, started_at, finished_at=None, error=""):
        raise NotImplementedError


class SqliteQueueBackend(QueueBackend):
    """默认后端：tasks / task_runs 两张 SQLite 表。"""

    def create(self, task_id, user_id, task_type, payload, max_attempts):
        server = _server_import()
        conn = server.db()
        conn.execute(
            "INSERT INTO tasks (id, user_id, task_type, status, input_json, max_attempts, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (task_id, user_id, task_type, TASK_STATUS_PENDING, json.dumps(payload, ensure_ascii=False),
             max_attempts, _now()),
        )
        conn.commit()
        conn.close()

    def claim_next(self, worker_id):
        server = _server_import()
        conn = server.db()
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id, user_id, task_type, input_json, attempts FROM tasks"
            " WHERE status=? AND attempts < max_attempts"
            " ORDER BY created_at, id LIMIT 1",
            (TASK_STATUS_PENDING,),
        ).fetchone()
        if not row:
            conn.rollback()
            conn.close()
            return None
        conn.execute(
            "UPDATE tasks SET status=?, worker_id=?, started_at=?, attempts=attempts+1 WHERE id=?",
            (TASK_STATUS_RUNNING, worker_id, _now(), row["id"]),
        )
        conn.commit()
        conn.close()
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "task_type": row["task_type"],
            "input": json.loads(row["input_json"]),
            "attempt": row["attempts"] + 1,
        }

    def finish(self, task_id, result):
        server = _server_import()
        conn = server.db()
        conn.execute(
            "UPDATE tasks SET status=?, result_json=?, finished_at=? WHERE id=?",
            (TASK_STATUS_SUCCEEDED, json.dumps(result, ensure_ascii=False), _now(), task_id),
        )
        conn.commit()
        conn.close()

    def fail(self, task_id, error):
        server = _server_import()
        conn = server.db()
        row = conn.execute(
            "SELECT attempts, max_attempts FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        if row and row["attempts"] < row["max_attempts"]:
            # 自动重试：未达上限 → 回到 pending，保留 error 供前端提示
            conn.execute(
                "UPDATE tasks SET status=?, error=?, worker_id='', started_at=NULL, finished_at=NULL WHERE id=?",
                (TASK_STATUS_PENDING, error[0:4000], task_id),
            )
        else:
            # 已达上限 → failed 终态，等待手动重放（retry_task）
            conn.execute(
                "UPDATE tasks SET status=?, error=?, finished_at=? WHERE id=?",
                (TASK_STATUS_FAILED, error[0:4000], _now(), task_id),
            )
        conn.commit()
        conn.close()

    def get(self, task_id):
        server = _server_import()
        conn = server.db()
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def list_tasks(self, user_id=None, status=None, limit=50):
        server = _server_import()
        conn = server.db()
        sql = "SELECT * FROM tasks WHERE 1=1"
        params = []
        if user_id is not None:
            sql += " AND user_id=?"
            params.append(user_id)
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(min(int(limit), 200))
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def retry(self, task_id, user_id=None):
        server = _server_import()
        conn = server.db()
        if user_id is not None:
            row = conn.execute(
                "SELECT id FROM tasks WHERE id=? AND user_id=?", (task_id, user_id)
            ).fetchone()
            if not row:
                conn.close()
                return False
        cur = conn.execute(
            "UPDATE tasks SET status=?, error='', result_json='', attempts=0,"
            " worker_id='', started_at=NULL, finished_at=NULL WHERE id=? AND status IN (?,?)",
            (TASK_STATUS_PENDING, task_id, TASK_STATUS_FAILED, TASK_STATUS_CANCELED),
        )
        ok = cur.rowcount > 0
        conn.commit()
        conn.close()
        return ok

    def record_run(self, task_id, attempt, worker_id, run_status, started_at, finished_at=None, error=""):
        server = _server_import()
        conn = server.db()
        conn.execute(
            "INSERT INTO task_runs (task_id, attempt, worker_id, run_status, started_at, finished_at, error)"
            " VALUES (?,?,?,?,?,?,?)",
            (task_id, attempt, worker_id, run_status, started_at, finished_at, error[0:4000]),
        )
        conn.commit()
        conn.close()


class RedisQueueBackend(QueueBackend):
    """外部队列适配器示例：Redis 实现（预留接口）。

    使用前提：环境已安装 redis 依赖（python -m pip install redis），
    并配置 REDIS_URL（默认 redis://localhost:6379/0）。
    未安装/未配置时该后端不可用，业务代码通过 QUEUE_BACKEND 切换。
    本文件不强制引入 redis，import 延迟到构造时。
    """

    def __init__(self, redis_url=None):
        try:
            import redis  # 延迟导入
        except ImportError:
            raise RuntimeError("RedisQueueBackend 需要 redis 依赖：pip install redis")
        self._redis = redis.from_url(redis_url or "redis://localhost:6379/0")
        self._prefix = "careerpilot:task:"

    # -- 队列数据结构：Hash 存任务，ZSet 按 created_at 排序做 pending 队列 --
    def _key(self, task_id):
        return self._prefix + task_id

    def create(self, task_id, user_id, task_type, payload, max_attempts):
        p = self._redis.pipeline()
        p.hset(self._key(task_id), mapping={
            "id": task_id, "user_id": user_id, "task_type": task_type,
            "status": TASK_STATUS_PENDING, "input_json": json.dumps(payload, ensure_ascii=False),
            "attempts": 0, "max_attempts": max_attempts, "created_at": _now(),
        })
        p.zadd(self._prefix + "queue", {task_id: _now()})
        p.execute()

    def claim_next(self, worker_id):
        while True:
            members = self._redis.zrange(self._prefix + "queue", 0, 0)
            if not members:
                return None
            task_id = members[0]
            if not self._redis.hsetnx(self._key(task_id), "worker_id", worker_id):
                # 被别的 worker 抢走或已完成，重新取队首
                self._redis.zrem(self._prefix + "queue", task_id)
                continue
            pipe = self._redis.pipeline()
            pipe.hset(self._key(task_id), mapping={
                "status": TASK_STATUS_RUNNING, "started_at": _now(),
            })
            pipe.hincrby(self._key(task_id), "attempts", 1)
            pipe.zrem(self._prefix + "queue", task_id)
            pipe.execute()
            data = self._redis.hgetall(self._key(task_id))
            return {
                "id": data[b"id"].decode(),
                "user_id": int(data.get(b"user_id", b"0").decode()),
                "task_type": data[b"task_type"].decode(),
                "input": json.loads(data[b"input_json"].decode()),
                "attempt": int(data[b"attempts"]),
            }

    def finish(self, task_id, result):
        self._redis.hset(self._key(task_id), mapping={
            "status": TASK_STATUS_SUCCEEDED,
            "result_json": json.dumps(result, ensure_ascii=False),
            "finished_at": _now(),
        })

    def fail(self, task_id, error):
        data = self._redis.hgetall(self._key(task_id))
        if not data:
            return
        attempts = int(data.get(b"attempts", b"0"))
        max_attempts = int(data.get(b"max_attempts", b"3"))
        mapping = {"error": error[0:4000]}
        if attempts < max_attempts:
            mapping.update({"status": TASK_STATUS_PENDING, "worker_id": "", "finished_at": ""})
            self._redis.zadd(self._prefix + "queue", {task_id: _now()})
        else:
            mapping.update({"status": TASK_STATUS_FAILED, "finished_at": _now()})
        self._redis.hset(self._key(task_id), mapping=mapping)

    def get(self, task_id):
        data = self._redis.hgetall(self._key(task_id))
        if not data:
            return None
        out = {k.decode(): (v.decode() if isinstance(v, bytes) else v) for k, v in data.items()}
        return out

    def list_tasks(self, user_id=None, status=None, limit=50):
        keys = self._redis.keys(self._prefix + "*")
        rows = []
        for k in keys:
            data = self._redis.hgetall(k)
            if not data:
                continue
            row = {kk.decode(): (vv.decode() if isinstance(vv, bytes) else vv) for kk, vv in data.items()}
            if user_id is not None and int(row.get("user_id", -1)) != user_id:
                continue
            if status and row.get("status") != status:
                continue
            rows.append(row)
        rows.sort(key=lambda r: float(r.get("created_at", 0)), reverse=True)
        return rows[: min(int(limit), 200)]

    def retry(self, task_id, user_id=None):
        key = self._key(task_id)
        data = self._redis.hgetall(key)
        if not data:
            return False
        row = {k.decode(): (v.decode() if isinstance(v, bytes) else v) for k, v in data.items()}
        if user_id is not None and int(row.get("user_id", -1)) != user_id:
            return False
        if row.get("status") not in (TASK_STATUS_FAILED, TASK_STATUS_CANCELED):
            return False
        self._redis.hset(key, mapping={"status": TASK_STATUS_PENDING, "error": "", "result_json": "",
                                       "attempts": 0, "worker_id": "", "finished_at": ""})
        self._redis.zadd(self._prefix + "queue", {task_id: _now()})
        return True

    def record_run(self, task_id, attempt, worker_id, run_status, started_at, finished_at=None, error=""):
        self._redis.rpush(self._prefix + "runs:" + task_id, json.dumps({
            "attempt": attempt, "worker_id": worker_id, "run_status": run_status,
            "started_at": started_at, "finished_at": finished_at, "error": error,
        }, ensure_ascii=False))


# ---------------------------------------------------------------- 任务管理器

_backend = None
_backend_lock = threading.Lock()


def get_backend():
    """返回队列后端实例。QUEUE_BACKEND=redis 时返回 Redis 实现，默认 SQLite。"""
    global _backend
    with _backend_lock:
        if _backend is None:
            import os
            if os.environ.get("QUEUE_BACKEND", "").lower() == "redis":
                _backend = RedisQueueBackend(os.environ.get("REDIS_URL"))
            else:
                _backend = SqliteQueueBackend()
        return _backend


def set_backend(backend):
    """测试注入用：替换队列后端。"""
    global _backend
    with _backend_lock:
        _backend = backend


def create_task(user_id, task_type, payload, max_attempts=MAX_ATTEMPTS_DEFAULT):
    if task_type not in VALID_TASK_TYPES:
        raise ValueError("不支持的 task_type: %s" % task_type)
    task_id = new_task_id()
    get_backend().create(task_id, user_id, task_type, payload, max_attempts)
    return task_to_dict({
        "id": task_id, "user_id": user_id, "task_type": task_type,
        "status": TASK_STATUS_PENDING, "input_json": json.dumps(payload, ensure_ascii=False),
        "result_json": "", "error": "", "attempts": 0, "max_attempts": max_attempts,
        "worker_id": "", "created_at": _now(), "started_at": None, "finished_at": None,
    })


def claim_next(worker_id):
    return get_backend().claim_next(worker_id)


def finish_task(task_id, result):
    get_backend().finish(task_id, result)


def fail_task(task_id, error):
    get_backend().fail(task_id, error)


def get_task(task_id, user_id=None):
    row = get_backend().get(task_id)
    if not row:
        return None
    if user_id is not None and row.get("user_id") != user_id:
        return None
    return task_to_dict(row)


def list_tasks(user_id=None, status=None, limit=50):
    rows = get_backend().list_tasks(user_id=user_id, status=status, limit=limit)
    return [task_to_dict(r) for r in rows]


def retry_task(task_id, user_id=None):
    return get_backend().retry(task_id, user_id=user_id)


def record_run(task_id, attempt, worker_id, run_status, started_at, finished_at=None, error=""):
    get_backend().record_run(task_id, attempt, worker_id, run_status, started_at, finished_at, error)


def task_to_dict(row):
    """DB 行 → 对外脱敏 dict（不暴露内部字段差异）。"""
    return {
        "id": row.get("id"),
        "task_type": row.get("task_type"),
        "status": row.get("status"),
        "attempts": row.get("attempts"),
        "max_attempts": row.get("max_attempts"),
        "created_at": row.get("created_at"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "error": row.get("error") or "",
        "result": _safe_json(row.get("result_json")) if row.get("status") == TASK_STATUS_SUCCEEDED else None,
        "input": _safe_json(row.get("input_json")),
    }


def _safe_json(text):
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None
