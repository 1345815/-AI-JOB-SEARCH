# 部署与水平扩展（Deploy & Scale）

## 单机多 worker 部署

### 1. 启动 worker

```bash
# 3 个 worker，端口 8001-8003
WORKERS=3 WORKER_PORTS=8001,8002,8003 HOST=127.0.0.1 python web/run.py

# 或单 worker（旧行为）
python web/run.py
```

### 2. nginx 反代

1. 安装 nginx
2. 按 `nginx.conf.example` 配置 upstream（填入 worker 端口）与 `/healthz`、`/static`、`/` 规则
3. `nginx -t && systemctl reload nginx`

### 3. 进程守护（systemd 示例）

```ini
[Unit]
Description=CareerPilot Web
After=network.target

[Service]
WorkingDirectory=/opt/careerpilot
Environment=WORKERS=3
Environment=WORKER_PORTS=8001,8002,8003
ExecStart=/usr/bin/python3 /opt/careerpilot/web/run.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

## Worker 数建议

- 上限：≤ CPU 核数
- SQLite 写密集场景：2-4 为宜，过多 worker 会加剧写锁竞争
- 读多写少：可到 CPU 核数

## 当前边界（务必知晓）

1. **SQLite WAL** 支持多进程并发读写（`journal_mode=WAL` + `busy_timeout=5000`），但**写吞吐有限**——单写者场景性能最好
2. **会话/限流/缓存**仍是进程内/单库共享：04 阶段（分布式会话缓存限流）落地前，多 worker 下 `_LOGIN_FAILURES` 等进程级状态不跨 worker（登录限流在单 worker 内生效）
3. `touch_session` 在多 worker 下有写放大，可接受（每个请求一次 UPDATE）
4. 备份：`db_backup` 的 SQLite backup API **兼容 WAL**。验证：
   ```bash
   python -m web.db_backup   # 生成备份
   # 校验：verify_database 返回 integrity=ok
   ```

## 备份与 WAL 兼容性

SQLite `VACUUM INTO` / backup API 在 WAL 模式下正常工作（会包含 WAL 中未 checkpoint 的数据）。无需额外处理。
