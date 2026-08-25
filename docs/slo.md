# SLO（服务水平目标）

> 基于 `/metrics` 数据计算（Prometheus 文本格式，见 `web/observability.py`）。

| 指标 | SLO | 计算方式 |
|---|---|---|
| 可用性 | ≥ 99.5%（月度） | `up == 1` 采样占比；单机场景用 `/healthz` 探测 |
| 延迟 | P95 < 2s | `careerpilot_http_duration_seconds` 直方图 95 分位 |
| 错误率 | 5xx 率 < 1% | `careerpilot_errors_5xx_total / careerpilot_http_requests_total`（窗口 5m） |

## 告警规则（alerts/prometheus.yml）

- **ErrorRateHigh**：5xx 率 > 1% 持续 5m
- **LatencyHigh**：P95 > 2s 持续 10m
- **UptimeDown**：up == 0 持续 1m

## 说明

- 单进程部署可用 `/healthz` 作为存活探针，`/metrics` 供 Prometheus 抓取。
- 结构化日志（JSON lines）输出到 stderr，Docker 场景由容器收集。
- 日志字段白名单：`ts/level/logger/request_id/method/path/status/duration_ms/user_id/msg`，绝不包含 request body、api_key、password、简历内容。
