# 灾备与高可用（DR & HA）

## 备份恢复演练

- **周期**：每次发布前 + 每周一次（可 cron 定时）
  ```bash
  # 每周一凌晨 3 点
  0 3 * * 1 cd /opt/careerpilot && bash scripts/dr/restore_drill.sh --backup-dir web/data/backups --source-db web/data/careerpilot.db
  ```
- **判定标准**：行数一致（全表 COUNT 对比）+ `verify_database` integrity=ok
- **命令**：`restore_drill.sh --verify-only`（只校验备份）或完整恢复对比

## 异地备份（必须）

本地磁盘备份 **不算灾备**（磁盘损坏/机器丢失即全丢）。至少一种：

```bash
# rsync 到另一主机
rsync -avz web/data/backups/ backup-host:/srv/careerpilot-backups/

# 或 scp 定时
0 2 * * * scp web/data/backups/careerpilot-$(date +\%F)*.db backup-host:/srv/backups/

# 或对象存储（rclone 示例）
rclone copy web/data/backups s3:careerpilot-backups --include "careerpilot-*.db"
```

## 高可用形态

- **nginx 被动摘除**（开源版）：`proxy_next_upstream error timeout http_502 http_503` 自动跳过故障 worker
- **主动健康检查**（nginx plus / openresty）：`health_check interval=5s fails=3 passes=2` 自动摘除 /healthz 失败的 worker
- **多 worker**：`WORKERS=2 WORKER_PORTS=8001,8002 python web/run.py` + systemd 守护

## 当前 HA 边界

- SQLite 单写者：多 worker 支持读扩展，写并发提升有限（2-4 worker 为宜）
- 无跨主机主从：本方案是**单机多进程** HA，非多机集群
- 会话/限流/缓存已 DB 共享（04 阶段），多 worker 行为一致
- 备份是最后防线：异地备份 + 定期演练是硬要求
