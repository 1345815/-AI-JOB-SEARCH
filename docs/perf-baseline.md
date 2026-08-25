# 性能基线（Perf Baseline）

> 实跑 `scripts/loadtest/basic_load.py` 采集。下次改动后重跑并更新本表。

| 项目 | 值 |
|---|---|
| 日期 | 2026-08-25 |
| git commit | 9ee12c5（05 阶段后） |
| 运行环境 | Windows 11 本地，Python 3.12 |
| worker 数 | 1（单进程 ThreadingHTTPServer） |
| 并发 | 10 |
| 时长 | 5s |
| 端点 | /healthz + /api/jobs |
| **总请求** | 6166 |
| **QPS** | 1233 |
| **P95** | 20.5ms |
| **P99** | 26.9ms |
| **平均** | 8.5ms |
| **5xx 率** | 0% |

## 结论与瓶颈备注

- 单 worker 下 QPS ~1200，P95 <25ms，满足中小规模（数百用户）部署需求
- 401 响应来自 /api/jobs 未登录场景（正常业务语义，非错误）
- 已知瓶颈：SQLite 单写者限制；写密集场景（大量并发保存评估）吞吐有限，建议 worker 2-4 且避免写放大
- /metrics 与 /healthz 未计入 QPS（本身开销可忽略）

## 重跑命令

```bash
python scripts/loadtest/basic_load.py --base-url http://127.0.0.1:8000 --concurrency 20 --duration 10 --report perf.json
```
