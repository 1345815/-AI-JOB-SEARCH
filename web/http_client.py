"""统一外部 HTTP 客户端：重试（指数退避+jitter）、超时配置化、每源熔断器、健康注册表。

纯标准库 urllib 实现，无第三方依赖：
- get_json / fetch_text：带重试的统一抓取入口
- CircuitBreaker：失败率超阈值熔断，半开探测恢复
- report()：把每次结果上报到健康注册表，供 /api/sources/health 读取
"""

import json
import os
import random
import threading
import time
import urllib.error
import urllib.request

DEFAULT_UA = "CareerPilot/1.0"

# 健康注册表：source -> {"ok": bool, "msg": str, "last_ok": ts, "last_fail": ts, "latency_ms": int, "count": int}
_HEALTH = {}
_HEALTH_LOCK = threading.Lock()


def health_snapshot():
    with _HEALTH_LOCK:
        return {k: dict(v) for k, v in _HEALTH.items()}


def report(source, ok, msg="", latency_ms=None):
    """上报一次源访问结果（幂等、线程安全）。"""
    if not source:
        return
    now = time.time()
    with _HEALTH_LOCK:
        entry = _HEALTH.setdefault(source, {"ok": True, "msg": "", "last_ok": None, "last_fail": None, "latency_ms": None, "count": 0})
        entry["ok"] = ok
        entry["msg"] = str(msg)[:200]
        entry["count"] = entry.get("count", 0) + 1
        if ok:
            entry["last_ok"] = now
        else:
            entry["last_fail"] = now
        if latency_ms is not None:
            entry["latency_ms"] = int(latency_ms)


class CircuitBreaker(object):
    """每源熔断器。state: closed / open / half_open。"""

    def __init__(self, failure_threshold=None, recovery_timeout=None):
        self.failure_threshold = failure_threshold if failure_threshold is not None else int(os.environ.get("CIRCUIT_FAILURE_THRESHOLD", "5"))
        self.recovery_timeout = recovery_timeout if recovery_timeout is not None else int(os.environ.get("CIRCUIT_RECOVERY_TIMEOUT", "30"))
        self._lock = threading.Lock()
        self._failures = 0
        self._state = "closed"   # closed / open / half_open
        self._opened_at = 0.0

    def allow(self):
        with self._lock:
            if self._state == "open":
                if time.time() - self._opened_at >= self.recovery_timeout:
                    self._state = "half_open"
                    return True
                return False
            return True

    def on_success(self):
        with self._lock:
            self._failures = 0
            if self._state in ("half_open", "open"):
                self._state = "closed"

    def on_failure(self):
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._state = "open"
                self._opened_at = time.time()

    @property
    def state(self):
        return self._state


_BREAKERS = {}
_BREAKERS_LOCK = threading.Lock()


def get_breaker(source):
    with _BREAKERS_LOCK:
        if source not in _BREAKERS:
            _BREAKERS[source] = CircuitBreaker()
        return _BREAKERS[source]


def _retry_delay(attempt, backoff, jitter):
    return min(backoff * (2 ** (attempt - 1)), 8) + random.uniform(0, jitter)


def _should_retry(status):
    """5xx/连接错误/超时重试；4xx 不重试。status 0 表示连接/超时异常。"""
    return status == 0 or status >= 500


def get_json(url, headers=None, timeout=12, retries=2, backoff=0.5, jitter=0.1, source="unknown"):
    """GET JSON，带重试 + 熔断。成功返回 dict；全部失败抛 RuntimeError。"""
    breaker = get_breaker(source)
    if not breaker.allow():
        report(source, False, "circuit open")
        raise RuntimeError("source %s: circuit open" % source)
    last_err = None
    for attempt in range(1, retries + 2):
        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status >= 400:
                    _handle_http_error(resp, attempt, retries, source)
                    last_err = RuntimeError("source %s: HTTP %d" % (source, resp.status))
                    break
                data = json.loads(resp.read().decode("utf-8"))
                breaker.on_success()
                report(source, True, latency_ms=(time.perf_counter() - t0) * 1000)
                return data
        except urllib.error.HTTPError as e:
            if not _should_retry(e.code) or attempt > retries:
                breaker.on_failure()
                report(source, False, "HTTP %d" % e.code, latency_ms=(time.perf_counter() - t0) * 1000)
                raise RuntimeError("source %s: HTTP %d" % (source, e.code))
            last_err = e
        except Exception as e:
            last_err = e
            if attempt > retries:
                breaker.on_failure()
                report(source, False, str(e)[:100], latency_ms=(time.perf_counter() - t0) * 1000)
                break
        time.sleep(_retry_delay(attempt, backoff, jitter))
    raise RuntimeError("source %s: %s" % (source, last_err or "unknown"))


def _handle_http_error(resp, attempt, retries, source):
    if not _should_retry(resp.status) or attempt > retries:
        get_breaker(source).on_failure()
        report(source, False, "HTTP %d" % resp.status)
        raise RuntimeError("source %s: HTTP %d" % (source, resp.status))


def fetch_text(url, headers=None, timeout=30, retries=1, backoff=0.5, jitter=0.1, source="unknown", opener=None, max_bytes=None, raw_bytes=False):
    """GET 文本，带重试。失败抛 RuntimeError。
    opener 可注入自定义 Handler（如 SSRF 防护的 SafeRedirectHandler）。
    raw_bytes=True 时返回原始 bytes（供调用方自行解码）。"""
    breaker = get_breaker(source)
    if not breaker.allow():
        report(source, False, "circuit open")
        raise RuntimeError("source %s: circuit open" % source)
    last_err = None
    for attempt in range(1, retries + 2):
        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_UA, **(headers or {})})
            if opener is not None:
                resp = opener.open(req, timeout=timeout)
            else:
                resp = urllib.request.urlopen(req, timeout=timeout)
            with resp:
                if resp.status >= 400:
                    _handle_http_error(resp, attempt, retries, source)
                    last_err = RuntimeError("source %s: HTTP %d" % (source, resp.status))
                    break
                raw = resp.read(max_bytes or (2 * 1024 * 1024))
                breaker.on_success()
                report(source, True, latency_ms=(time.perf_counter() - t0) * 1000)
                if raw_bytes:
                    return raw
                return raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if not _should_retry(e.code) or attempt > retries:
                breaker.on_failure()
                report(source, False, "HTTP %d" % e.code, latency_ms=(time.perf_counter() - t0) * 1000)
                raise RuntimeError("source %s: HTTP %d" % (source, e.code))
            last_err = e
        except Exception as e:
            last_err = e
            if attempt > retries:
                breaker.on_failure()
                report(source, False, str(e)[:100], latency_ms=(time.perf_counter() - t0) * 1000)
                break
        time.sleep(_retry_delay(attempt, backoff, jitter))
    raise RuntimeError("source %s: %s" % (source, last_err or "unknown"))
