# 混沌演练场景（Chaos Scenarios）

> 所有演练默认 `--dry-run`；对真实实例执行必须显式 `--target` + `--commit`。
> 影响面只限指定实例。

## 场景 1：DB 锁（长事务）

- **触发**：`python scripts/chaos/chaos_db_lock.py --target http://127.0.0.1:8000 --db-path web/data/careerpilot.db --commit`
- **预期行为**：长事务期间，依赖 DB 的请求变慢（busy_timeout=5000 排队），不应大量 5xx
- **判定标准**：压测错误率 <5%（最多 P95 上升）
- **恢复**：ROLLBACK 自动完成，无需干预
- **影响范围**：写请求短暂排队；读请求（WAL 模式）基本不受影响

## 场景 2：外部数据源超时

- **触发**：`FREEHIRE_API_URL=http://10.255.255.1:9 python scripts/chaos/chaos_source_timeout.py --target http://127.0.0.1:8000 --commit`（重启服务后）
- **预期行为**：搜索接口在超时（12s）内降级返回错误信息，进程不挂死
- **判定标准**：搜索返回 502/错误 JSON，进程存活，/healthz 仍 200
- **恢复**：恢复 FREEHIRE_API_URL 并重启服务
- **影响范围**：仅搜索类接口；其余功能不受影响

## 场景 3：worker 崩溃

- **前置**：多 worker 启动 `WORKERS=2 WORKER_PORTS=8001,8002 python web/run.py`
- **触发**：`python scripts/chaos/chaos_worker_kill.py --target http://127.0.0.1:8001 --commit`
- **预期行为**：被 kill 的 worker 停止服务；nginx 被动摘除（proxy_next_upstream）；剩余 worker 正常
- **判定标准**：其余 worker 端口请求正常；父进程存活；日志记录异常退出
- **恢复**：systemd/进程管理器自动拉起（Restart=on-failure），或手动重启
- **影响范围**：被 kill worker 上的在途请求中断，其余无感

## 通用安全要求

- 演练前确保已备份：`python -m web.db_backup` 或 schedule 已产出最新备份
- 生产环境演练需在业务低峰期，且有回滚预案
