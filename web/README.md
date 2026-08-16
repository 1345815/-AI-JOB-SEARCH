# CareerPilot-CN Web 版（多用户）

这是 CareerPilot-CN 的多用户 Web 前端，把岗位搜索、匹配评估、简历/求职信定制、面试准备、申请进度管理搬进浏览器，并支持账号体系与数据隔离。

## 特点

- 零第三方 Python 依赖，单文件后端 + 标准库 SQLite。
- 注册、登录、游客体验、游客转正（数据原地保留）。
- 用户档案、申请记录、文档、面试包、对话按用户隔离；岗位池共享。
- 默认本地智能模式；可选 OpenAI 兼容 API 增强。
- 支持 Docker / 云服务器 / 局域网部署。

## 本地启动

```bash
python web/server.py
```

访问 `http://127.0.0.1:8000`。Windows 也可以双击根目录 `启动Web版.cmd`。

## 部署

完整步骤见 [DEPLOY.md](../DEPLOY.md)。

```bash
cp .env.example .env
docker compose up -d --build
```

## 数据与隐私

- 运行数据默认保存在 `web/data/careerpilot.db`。
- `.env`、数据库、私有档案与 API 设置均已被 `.gitignore` 排除。
- 新用户默认空档案，会引导先完善个人资料。
- 示例岗位库仅用于展示产品能力，不保证真实有效。
