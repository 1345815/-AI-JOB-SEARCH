import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

WEB = Path(__file__).resolve().parents[1] / "web"
sys.path.insert(0, str(WEB))

import observability


class ObservabilityTests(unittest.TestCase):
    """可观测性：指标容器、Prometheus 输出、日志字段白名单。"""

    def setUp(self):
        # 重置全局指标，避免用例间污染
        observability.METRICS._counters.clear()
        observability.METRICS._histograms.clear()

    def test_counter_and_histogram(self):
        m = observability.Metrics()
        m.inc("careerpilot_http_requests_total", method="GET", status_class="2xx")
        m.inc("careerpilot_http_requests_total", method="GET", status_class="2xx")
        m.observe("careerpilot_http_duration_seconds", 0.05, method="GET")
        text = m.text_format()
        self.assertIn("careerpilot_http_requests_total{method=\"GET\",status_class=\"2xx\"} 2", text)
        self.assertIn("careerpilot_http_duration_seconds_bucket{method=\"GET\",le=\"0.1\"} 1", text)
        self.assertIn("careerpilot_http_duration_seconds_bucket{method=\"GET\",le=\"+Inf\"} 1", text)
        self.assertIn("careerpilot_http_duration_seconds_sum{method=\"GET\"} 0.05", text)

    def test_metrics_thread_safe(self):
        m = observability.Metrics()
        import threading
        errors = []

        def run():
            try:
                for _ in range(200):
                    m.inc("req", method="GET")
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertFalse(errors)
        text = m.text_format()
        self.assertIn("req{method=\"GET\"} 1600", text)

    def test_record_request_logs_json_without_sensitive_data(self):
        with mock.patch.object(observability, "get_logger") as mock_logger:
            logger = mock_logger.return_value
            observability.record_request("POST", "/api/chat", 200, 12.5, request_id="rid1", user_id=7)
            args = logger.info.call_args
            extra = args[1]["extra"]["extra_fields"]
            self.assertEqual(extra["request_id"], "rid1")
            self.assertEqual(extra["user_id"], 7)
            self.assertEqual(extra["status"], 200)
            # 白名单内字段
            self.assertNotIn("body", extra)
            self.assertNotIn("api_key", extra)

    def test_metrics_uptime(self):
        self.assertGreaterEqual(observability.METRICS.uptime_seconds(), 0)

    def test_healthz_and_metrics_routes(self):
        # 验证 server 路由注册（通过导入 server 检查 do_GET 分支）
        import server
        src = Path(server.__file__).read_text(encoding="utf-8")
        self.assertIn('path == "/healthz"', src)
        self.assertIn('path == "/metrics"', src)
        self.assertIn("X-Request-Id", src)


if __name__ == "__main__":
    unittest.main()
