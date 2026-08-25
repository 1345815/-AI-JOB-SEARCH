import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

WEB = Path(__file__).resolve().parents[1] / "web"
sys.path.insert(0, str(WEB))

import http_client


class HttpStatus:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, n=-1):
        return b'{"ok": true}'


class HttpStatus404(HttpStatus):
    status = 404


class HttpStatus500(HttpStatus):
    status = 500


class HttpClientTests(unittest.TestCase):
    """统一 HTTP 客户端：重试、4xx 不重试、熔断、健康上报。"""

    def setUp(self):
        http_client._BREAKERS.clear()
        http_client._HEALTH.clear()

    def test_retry_succeeds_after_transient_failures(self):
        calls = {"n": 0}

        def fake_urlopen(req, timeout=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise urllib_error_urlerror()
            return HttpStatus()

        with mock.patch.object(http_client.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = http_client.get_json("http://example.com/api", timeout=5, retries=2, backoff=0.01, jitter=0, source="test-src")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(calls["n"], 3)

    def test_4xx_no_retry(self):
        calls = {"n": 0}

        def fake_urlopen(req, timeout=None):
            calls["n"] += 1
            import urllib.error
            raise urllib.error.HTTPError("http://example.com", 404, "Not Found", {}, None)

        with mock.patch.object(http_client.urllib.request, "urlopen", side_effect=fake_urlopen):
            with self.assertRaises(RuntimeError):
                http_client.get_json("http://example.com/api", timeout=5, retries=2, backoff=0.01, jitter=0, source="test-4xx")
        self.assertEqual(calls["n"], 1)
        self.assertEqual(http_client.health_snapshot()["test-4xx"]["ok"], False)

    def test_circuit_breaker_opens_and_short_circuits(self):
        breaker = http_client.CircuitBreaker(failure_threshold=3, recovery_timeout=60)
        for _ in range(3):
            breaker.on_failure()
        self.assertEqual(breaker.state, "open")
        self.assertFalse(breaker.allow())
        breaker.on_success()  # half-open 探测成功
        self.assertEqual(breaker.state, "closed")
        self.assertTrue(breaker.allow())

    def test_get_json_respects_circuit_open(self):
        with mock.patch.object(http_client.urllib.request, "urlopen", side_effect=RuntimeError("down")):
            for _ in range(5):
                try:
                    http_client.get_json("http://example.com/api", timeout=5, retries=0, source="cb-src")
                except RuntimeError:
                    pass
        # 熔断打开后不再发请求
        with mock.patch.object(http_client.urllib.request, "urlopen", side_effect=AssertionError("不应发请求")) as m:
            try:
                http_client.get_json("http://example.com/api", timeout=5, retries=0, source="cb-src")
            except RuntimeError as e:
                self.assertIn("circuit open", str(e))

    def test_health_snapshot(self):
        http_client.report("freehire", True, latency_ms=123)
        http_client.report("freehire", False, "HTTP 500", latency_ms=200)
        snap = http_client.health_snapshot()
        self.assertIn("freehire", snap)
        self.assertEqual(snap["freehire"]["latency_ms"], 200)
        self.assertFalse(snap["freehire"]["ok"])
        self.assertIsNotNone(snap["freehire"]["last_fail"])


def urllib_error_urlerror():
    import urllib.error
    return urllib.error.URLError("conn reset")


if __name__ == "__main__":
    unittest.main()
