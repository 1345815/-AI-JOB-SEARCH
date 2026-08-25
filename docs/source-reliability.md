# 外部数据源可靠性（Source Reliability）

## 依赖等级

| 源 | 等级 | 说明 |
|---|---|---|
| FreeHire | 核心 | 关键词搜索主数据源 |
| ATS（Greenhouse/Lever/Ashby） | 增强 | 岗位链接解析 |
| Mokahr | 增强 | 校招岗位详情 |
| web（通用抓取） | 兜底 | URL 解析兜底 |
| company（公司搜索） | 增强 | 按公司搜索 |

## 超时 / 重试 / 熔断参数

| 源 | 超时（env 可覆盖） | 重试 | 熔断阈值 | 恢复时间 |
|---|---|---|---|---|
| freehire | 12s (`FREEHIRE_TIMEOUT`) | 1 | 5 (`CIRCUIT_FAILURE_THRESHOLD`) | 30s (`CIRCUIT_RECOVERY_TIMEOUT`) |
| ats | 15s (`ATS_TIMEOUT`) | 1 | 5 | 30s |
| web | 30s (`WEB_TIMEOUT`) | 1 | 5 | 30s |

统一策略：连接错误/超时/5xx 指数退避重试（0.5s、1s + jitter）；4xx 不重试。

## 故障行为矩阵

| 场景 | 熔断 | 降级 | 用户提示 |
|---|---|---|---|
| FreeHire 挂 | 5 次失败后 open | 返回上次成功缓存（stale） | "外部数据源暂时不可用，已返回上次搜索结果"（degraded=true） |
| ATS 挂 | open | 岗位解析返回 None，URL 解析降级 | 提示解析失败 |
| 全部源挂 | 全 open | 搜索返回本地结果 | 前端显示来源健康状态 |
| 恢复 | half-open 探测 1 次成功 → closed | 自动恢复 | 无感知 |

## 健康端点

- `GET /api/sources/health`（登录用户）：各源 ok/fail/last_ok/last_fail/latency_ms/msg
- 搜索响应 `sources` 字段附带各源健康状态（`health` 子字段）

## 供应商变更预案

换 API 供应商时只改 `web/http_client.py` 中的 source 实现（`get_json`/`fetch_text` 的 source 参数），业务代码不变：
- 新增源：`get_json(url, timeout=..., source="new_source")`
- 调整参数：环境变量覆盖超时/熔断阈值，无需改代码
