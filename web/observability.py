"""可观测性：结构化 JSON 日志 + Prometheus 指标 + 健康检查。

纯标准库实现，零第三方依赖：
- get_logger()：JSON lines 日志（ts/level/logger/request_id/method/path/status/duration_ms/user_id/msg）
- metrics：线程安全计数器与直方图，Prometheus 文本协议输出
- 字段白名单外的一切（request body、api_key、password、profile 内容）一律不记录
"""

import json
import logging
import os
import sys
import threading
import time

# ---------------------------------------------------------------- 日志

_LOG_FIELDS = ("ts", "level", "logger", "request_id", "method", "path", "status", "duration_ms", "user_id", "msg")


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            for key in _LOG_FIELDS:
                if key in extra and key not in entry:
                    entry[key] = extra[key]
        return json.dumps(entry, ensure_ascii=False)


def get_logger(name="careerpilot"):
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(os.environ.get("CAREERPILOT_LOG_LEVEL", "INFO").upper())
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


# ---------------------------------------------------------------- 指标

class Metrics:
    """线程安全指标容器：计数器 + 直方图桶。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._counters = {}          # (name, labels_tuple) -> value
        self._histograms = {}        # name -> {labels_tuple: {bucket: count}}
        self._started = time.time()

    def inc(self, name, value=1, **labels):
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + value

    def observe(self, name, value, **labels):
        """直方图：固定桶 [0.005,0.01,0.025,0.05,0.1,0.25,0.5,1,2.5,5,10]。
        每个观测只计入第一个满足 value<=b 的桶（区间计数），输出时再累计。"""
        buckets = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10]
        labels_key = tuple(sorted(labels.items()))
        with self._lock:
            hist = self._histograms.setdefault(name, {})
            entry = hist.setdefault(labels_key, {b: 0 for b in buckets})
            entry["_count"] = entry.get("_count", 0) + 1
            entry["_sum"] = entry.get("_sum", 0) + value
            for b in buckets:
                if value <= b:
                    entry[b] += 1
                    break

    def uptime_seconds(self):
        return time.time() - self._started

    def text_format(self):
        lines = []
        with self._lock:
            for (name, labels), value in sorted(self._counters.items()):
                lines.append("# TYPE %s counter" % name)
                lines.append(_format_sample(name, labels, value))
            for name, entries in sorted(self._histograms.items()):
                lines.append("# TYPE %s histogram" % name)
                for (labels), entry in sorted(entries.items()):
                    cumulative = 0
                    labels_dict = dict(labels)
                    for b in sorted([k for k in entry if isinstance(k, (int, float))]):
                        cumulative += entry[b]
                        lines.append(_format_sample(name + "_bucket", dict(labels_dict, le=str(b)), cumulative))
                    lines.append(_format_sample(name + "_bucket", dict(labels_dict, le="+Inf"), entry.get("_count", 0)))
                    lines.append(_format_sample(name + "_count", labels_dict, entry.get("_count", 0)))
                    lines.append(_format_sample(name + "_sum", labels_dict, entry.get("_sum", 0)))
        return "\n".join(lines) + "\n"


def _format_sample(name, labels, value):
    label_str = ",".join("%s=\"%s\"" % (k, v) for k, v in dict(labels).items())
    suffix = "{%s}" % label_str if label_str else ""
    if isinstance(value, float):
        value_str = repr(value)
    else:
        value_str = str(value)
    return "%s%s %s" % (name, suffix, value_str)


# 全局单例
METRICS = Metrics()


def record_request(method, path, status, duration_ms, request_id=None, user_id=None):
    """记录一次请求的指标与日志。"""
    status_class = str(status // 100) + "xx"
    METRICS.inc("careerpilot_http_requests_total", method=method, status_class=status_class)
    METRICS.observe("careerpilot_http_duration_seconds", duration_ms / 1000.0, method=method)
    if status >= 500:
        METRICS.inc("careerpilot_errors_5xx_total", method=method)
    logger = get_logger()
    logger.info(
        "request",
        extra={
            "extra_fields": {
                "request_id": request_id,
                "method": method,
                "path": path,
                "status": status,
                "duration_ms": int(duration_ms),
                "user_id": user_id,
            }
        },
    )


def text_format_metrics():
    """Prometheus 文本协议输出（含运行时指标）。"""
    base = METRICS.text_format()
    extra = [
        "# TYPE careerpilot_uptime_seconds gauge",
        "careerpilot_uptime_seconds %s" % repr(METRICS.uptime_seconds()),
    ]
    try:
        import sqlite3
        from pathlib import Path
        db_file = Path(os.environ.get("DB_PATH", "")).resolve() if os.environ.get("DB_PATH") else None
        # 由 server 注入 DB 路径（在 server.py 中设置 _DB_FILE_PATH）
        from server import DB_FILE as _db
        if _db and Path(_db).exists():
            extra.append("# TYPE careerpilot_db_size_bytes gauge")
            extra.append("careerpilot_db_size_bytes %d" % Path(_db).stat().st_size)
        extra.append("# TYPE careerpilot_thread_count gauge")
        extra.append("careerpilot_thread_count %d" % threading.active_count())
    except Exception:
        pass
    return base + "\n".join(extra) + "\n"
