# CareerPilot-CN Web 部署指南

CareerPilot Web 是零第三方依赖的多用户求职助手，支持本地运行、Docker 部署、云服务器部署和局域网访问。

## 功能

- 注册 / 登录 / 游客体验 / 游客转正
- 共享岗位池与五维匹配评分
- 按用户隔离的申请进度、简历/求职信、面试准备与 AI 对话
- 可选 OpenAI 兼容 LLM 增强
- 简历与求职信可下载 Markdown、直接下载 PDF 或使用浏览器打印

## 本地开发

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python web/server.py
```

访问 `http://127.0.0.1:8000`。

## Docker 部署

```bash
cp .env.example .env
# 编辑 .env，设置端口与可选 LLM 配置
docker compose up -d --build
```

访问 `http://localhost:8000`。

## 云服务器部署

1. 安装 Docker 与 Docker Compose。
2. 克隆仓库到服务器。
3. 复制并编辑配置：

```bash
cp .env.example .env
docker compose up -d --build
```

4. 配置反向代理（推荐 Nginx/Caddy），将域名 HTTPS 流量转发到 `127.0.0.1:8000`。
5. 防火墙只开放 80/443；如果临时直接访问，开放 8000。

Nginx 最小示例：

```nginx
server {
  listen 80;
  server_name your-domain.com;

  location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}
```

## 局域网访问

默认 `HOST=0.0.0.0`，同一局域网设备访问：

```text
http://<服务器IP>:8000
```

Windows 防火墙如拦截，请允许 Python 或 8000 端口入站。

## 数据与备份

运行数据默认保存在 `web/data/careerpilot.db`，Docker 下映射到宿主机 `./data`。

服务默认每天通过 SQLite 在线备份 API 创建一份完整备份，保存在
`data/backups/`，并保留最近 14 份。可在 `.env` 中调整：

```bash
BACKUP_ENABLED=1
BACKUP_INTERVAL_HOURS=24
BACKUP_RETENTION=14
```

手动创建并校验备份：

```bash
python web/db_backup.py backup --database data/careerpilot.db --output data/backups --retention 14
python web/db_backup.py verify data/backups/careerpilot-YYYYMMDD-HHMMSS.db
```

恢复前必须停止 CareerPilot，避免运行中的连接继续写入。恢复命令会先把当前
数据库改名保存为 `.pre-restore-时间戳`，再校验并恢复指定备份：

```bash
docker compose stop app
python web/db_backup.py restore data/backups/careerpilot-YYYYMMDD-HHMMSS.db --database data/careerpilot.db
docker compose start app
```

## 安全配置

- 外部岗位链接只允许解析到公网 IP 的 HTTP/HTTPS 地址，重定向也会重新校验。
- 同一来源和账号默认 15 分钟内最多失败登录 5 次。
- 非文件上传请求体默认限制为 1MB。
- 响应包含 CSP、防 iframe 嵌入、Referrer Policy 与权限策略安全头。

对应环境变量：`LOGIN_RATE_LIMIT`、`LOGIN_RATE_WINDOW_SECONDS`、
`MAX_JSON_BODY_BYTES`。公网部署仍建议使用 HTTPS 反向代理，不要长期直接暴露
8000 端口。

## 默认档案模板

新用户档案为空，会提示先完善个人资料。可参考 `templates/profile_template.json` 查看字段结构。
