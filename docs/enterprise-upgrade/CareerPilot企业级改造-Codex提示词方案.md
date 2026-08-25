# CareerPilot 企业级改造 · Codex 提示词方案（合集）

> 由总入口 README 与 7 份分阶段提示词合并而成，单文件完整版。

---

# CareerPilot 企业级改造 · Codex 提示词总入口

> 目标：把 `ai-job-search`（CareerPilot-CN Web 版）从"单进程 + SQLite + 标准库"的可用原型，渐进式升级为企业级可部署系统。
> 本目录是**总入口大文档**：路线图、关键代码定位、全局约束、使用指南 + 7 份可直接复制给 Codex 的分阶段提示词。

---

## 一、现状快照（2026-08-24 已验证）

| 维度 | 现状 | 代码定位 |
|---|---|---|
| 服务模型 | 单进程 `ThreadingHTTPServer`，无 worker 池 | `web/server.py:27`、`:2486` `serve_forever()` |
| 数据库 | SQLite 单文件，`check_same_thread=False` + 进程内 RLock | `web/server.py:218` `db()`、`:52` `_DB_LOCK` |
| Schema | 11 张表，`init_db()` 内 `executescript` 建表 | `web/server.py:230-406` |
| 迁移 | 手写 `PRAGMA table_info` + 逐列 `ALTER TABLE` | `web/server.py:368-376` |
| 会话 | DB 表存储 + cookie `careerpilot_session` | `web/server.py:246`、`:454` `create_session`、`:474` `get_user_by_token` |
| 限流 | 仅登录失败限流，进程级 dict | `web/server.py:430-452` `_LOGIN_FAILURES` |
| 缓存 | 评分结果落 `evaluations` 表；搜索缓存仅内存标志 | `web/server.py:535`、`:1963` |
| 日志 | `log_message` 仅控制 stderr 输出，无结构化 | `web/server.py:1429-1431` |
| 设置/密钥 | `api_key` 明文存 `settings.json`，API 只回 `has_key` | `web/server.py:42`、`:1877-1897` |
| 认证 | `hash_password`(secrets 盐) + `role` 字段雏形 | `web/server.py:411-417`、`:242` |
| 外部数据源 | urllib 直连 Freehire/Greenhouse/Lever/Ashby/Mokahr，无重试/熔断 | `web/job_extractor.py:386`、`:85`、`:39`、`:163` |
| 备份 | SQLite backup API 已成型（备份/恢复/保留/校验） | `web/db_backup.py:26/47/12/71` |
| 测试 | 24 个测试文件，全量通过 | `tests/` |

---

## 二、七个差距 → 七阶段路线

| 阶段 | 对应差距 | 一句话目标 | 前置依赖 |
|---|---|---|---|
| **01 可观测性** | 缺日志/指标/追踪/告警/SLO | 一切改造的"测量基线"：结构化日志 + `/metrics` + 请求 ID + SLO 仪表 | 无 |
| **02 迁移与发布流程** | 缺正式迁移/回滚/发布 | schema 变更从手写 ALTER 升级为版本化 up/down 迁移 + 回滚 SOP | 01（发布观察） |
| **03 无状态化与水平扩展** | 单进程/单库不能扩容 | 多 worker + SQLite WAL + 静态分离 + 反代健康检查，单机 scale-up | 02（迁移纪律） |
| **04 分布式会话/缓存/限流** | 多实例一致性 | 会话、限流、缓存从进程内迁到共享存储（DB 优先，Redis 可选） | 03 |
| **05 RBAC/审计/SSO/密钥** | 缺安全合规能力 | 权限矩阵 + `audit_log` 表 + OIDC SSO（可选）+ 密钥移出 settings.json | 02（audit 表迁移） |
| **06 压测/演练/高可用/灾备** | 缺验证与韧性演练 | 压测基线 + 混沌脚本 + 备份恢复 SOP + 多实例高可用 | 03、04 |
| **07 外部数据源治理** | Freehire 供应链依赖 | 统一 HTTP 客户端：重试/退避/熔断/降级缓存/源健康面板 | 01（健康指标） |

**依赖关系**：01 → 02 → 03 → 04 → 05/06；07 可与 03 之后并行。每个阶段独立 commit、可单独回滚。

---

## 三、全局约束（喂给 Codex 时所有阶段都必须遵守）

1. **不破坏现有测试**：`tests/` 24 个文件全量通过；每次改动后必须跑 `pytest -q` 与 `python -m compileall web`。
2. **不破坏 API 契约**：`web/static/` 前端依赖的 `/api/*` 路由、字段名、返回结构保持不变；只允许新增路由。
3. **最小依赖原则**：优先标准库；新增依赖必须给出理由并写入 `requirements.txt`。生产机为**离线 bundle 部署**（`pip download` 打包拷贝），新增依赖必须同步进 bundle 流程。
4. **Python 版本**：容器为 `python:3.12-slim`（Dockerfile），本地有 Python 3.8。新代码若用 3.9+ 语法（如 `list[str]`），文件头部加 `from __future__ import annotations`，保证 3.8 可编译。
5. **兼容降级**：04 阶段 Redis 为可选项，默认走 SQLite 共享表方案；不允许强制引入外部服务导致单机部署不可用。
6. **安全底线**：`api_key` 不得新增日志输出；密码哈希不得改动算法（`hash_password` L411 已验证）；所有新增接口保持现有 `X-Frame-Options`/`nosniff` 安全头。
7. **commit 纪律**：每阶段完成后 `git add` 对应文件、单条 commit、写好 message；回滚以 commit 为单位，不 `reset --hard`。
8. **Codex 工作目录**：以仓库根目录 `AI-JOB-SEARCH/` 为工作区执行，测试用 `pytest.ini` 配置的项目本地 tmp 目录。

---

## 四、怎么用这套提示词

| 用法 | 操作 |
|---|---|
| **单阶段执行** | 把对应 `NN-*.md` 的「📋 提示词正文」整段复制给 Codex CLI（`codex` 在当前仓库根目录执行） |
| **批量规划** | 把本 README 的「二、路线图」+「三、全局约束」+ 目标阶段正文一起给 Codex |
| **验收** | 每份提示词正文末尾的「验收」段是硬性条件，Codex 完成后逐条核对，不满足就让它继续 |
| **脱离 Codex 自检** | 每份的「🛠 自检命令」段可在终端直接跑 |

> Codex 适配说明：提示词正文采用"目标 → 必读上下文（文件:行号）→ 实施 → 约束 → 交付物 → 验收"结构，自含上下文，不依赖会话历史；Codex 拿到单份文件即可独立作业。

---

## 五、文件索引

| 文件 | 内容 | 预估耗时 |
|---|---|---|
| `README.md` | 本文件（总入口） | — |
| `01-可观测性提示词.md` | 结构化日志 + /metrics + 请求 ID + SLO | 1 天 |
| `02-数据库迁移与发布流程提示词.md` | 版本化迁移 + 回滚 + 发布检查清单 | 1 天 |
| `03-无状态化与水平扩展提示词.md` | 多 worker + WAL + 反代 + 健康检查 | 1-2 天 |
| `04-分布式会话缓存限流提示词.md` | 会话/限流/缓存共享化 | 1-2 天 |
| `05-RBAC审计SSO密钥管理提示词.md` | 权限矩阵 + 审计 + SSO + 密钥 | 2 天 |
| `06-压测故障演练高可用灾备提示词.md` | 压测 + 混沌 + 恢复 SOP + HA | 2 天 |
| `07-外部数据源治理提示词.md` | 统一 HTTP + 熔断降级 + 源健康 | 1-2 天 |

**建议执行顺序**：01 → 02 → 03 → 04 → 05 → 06，07 插在 03 之后并行；每完成一个阶段先跑全量测试再进下一阶段。

---

## 六、验收总纲（所有阶段通用）

```bash
cd AI-JOB-SEARCH
pytest -q                                   # 全量测试，必须 0 failed
python -m compileall web                    # 语法编译检查
curl -s localhost:8000/api/...              # 冒烟关键路由
```

每阶段另有专属验收命令（见各文件），全部通过才算完成。

---

# 分阶段提示词：01-可观测性提示词.md

---

# 01 · 可观测性改进提示词（Codex）

## 🩺 根因诊断

| 坑 | 当前表现 | 排查命令 |
|---|---|---|
| 无结构化日志 | 只有 `log_message` 一行控制 stderr 输出，无 JSON/字段/级别 | `grep -n "log_message\|print(" web/server.py` |
| 无法关联单次请求 | 日志无 request_id，多用户并发时无法串联一次请求的完整链路 | 看 `web/server.py:1429-1431` |
| 无指标 | 没有 `/metrics`、`/healthz` 端点，无法接入 Prometheus/Grafana | `curl -s localhost:8000/metrics`（404） |
| 无延迟/错误统计 | 无请求耗时、5xx 计数、P95 统计 | 无任何计数器代码 |
| 无告警与 SLO | 没有阈值规则、没有可用性/延迟目标定义 | 仓库无 `alerts/`、无 `docs/slo.md` |

**最常见组合**：① + ③ + ④ —— 先补结构化日志与指标端点，SLO/告警基于它们才能落地。

## 📋 提示词正文（整段复制给 Codex）

```text
请在当前工作区（AI-JOB-SEARCH 仓库根目录）实施"可观测性改造"。你是资深后端工程师，只做本任务，不做无关重构。

# 目标
为这个纯标准库的单进程 HTTP 服务（web/server.py，ThreadingHTTPServer）补齐四件事：
1. 结构化 JSON 日志（含 request_id、method、path、status、duration_ms、user_id）
2. 指标端点 /metrics（Prometheus 文本格式）与健康检查 /healthz
3. 请求级追踪：每个请求生成/透传 request_id（X-Request-Id），贯穿日志
4. SLO 定义文档 + Prometheus 告警规则文件

# 必读上下文（先读再动手）
- web/server.py:1426 class Handler（BaseHTTPRequestHandler）；:1429 log_message 覆盖点
- web/server.py:1433 _send()（所有响应的出口，适合记状态码与耗时）
- web/server.py:1807 _api()（API 分发入口，适合记录 user_id）
- web/server.py:1774 _session_token() / :1777 _current_user()（拿 user_id 的既有方式）
- web/server.py:2486-2490 __main__ 服务器启动段
- web/server.py:218 db() 与 :52 _DB_LOCK（指标里需要 DB 大小时参考）
- Dockerfile 与 docker-compose.yml（确认日志输出到 stdout/stderr 即可，不要写死日志路径）
- tests/test_web_security_backup.py（了解既有安全头断言，避免破坏）

# 你必须新增/修改的内容
1. 新建 web/observability.py：
   - get_logger() 返回 logging.Logger，输出 JSON lines（key=value 或 json.dumps），handler 用 RotatingFileHandler 输出到 stderr（Docker 场景）且支持环境变量 CAREERPILOT_LOG_LEVEL（默认 INFO）
   - 字段白名单：ts、level、logger、request_id、method、path、status、duration_ms、user_id、msg；禁止记录 request body、api_key、password、profile_json 内容
   - metrics 容器：线程安全的计数器/直方图桶（requests_total{method,status_class}、duration_seconds 直方图、errors_5xx_total、active_requests、uptime_seconds、db_size_bytes、thread_count）；纯标准库实现，不引入第三方
   - text_format_metrics() 输出 Prometheus 文本协议（# HELP/# TYPE + 采样行）
2. 修改 web/server.py：
   - Handler.__init__ 或 do_* 入口处生成 request_id：优先读 X-Request-Id 头，否则 uuid4().hex[:12]，存入 self.request_id
   - _send() 内：记录 status_class、耗时（用 time.perf_counter）、写入结构化日志一行
   - _api() 内：成功解析用户后把 user_id 传给日志（可用 self 属性传递）
   - 新增路由：GET /healthz → {"status":"ok"}；GET /metrics → text_format_metrics()，Content-Type: text/plain; version=0.0.4
   - 注意 /metrics 与 /healthz 不做登录拦截（供负载均衡与采集器）
3. 新建 alerts/prometheus.yml：告警规则（ErrorRateHigh：5xx 率 >1% 持续 5m；LatencyHigh：P95 >2s 持续 10m；UptimeDown：up==0）
4. 新建 docs/slo.md：定义并写明计算方法——可用性 ≥99.5%（月度）、P95 延迟 <2s、5xx 错误率 <1%（均基于 /metrics 数据）

# 约束
- 只允许新增路由 /metrics 与 /healthz，不得改动现有 /api/* 行为与返回结构
- 不引入任何第三方依赖（requirements.txt 不得新增）
- 不记录任何敏感字段（api_key/password/profile 内容），不得把 request body 写进日志
- 不改密码哈希、不动数据库 schema
- 保持 web/server.py 仍是标准库可运行：python -m web.server 或原启动脚本必须照常工作

# 期望交付物
- web/observability.py（新文件）
- web/server.py（改动：request_id + 日志 + /metrics + /healthz）
- alerts/prometheus.yml（新文件）
- docs/slo.md（新文件）
- 上述全部通过既有测试 + 新增最少 2 个 pytest（tests/test_observability.py：/healthz 返回 200；/metrics 含 careerpilot_ 前缀指标）

# 验收（全部通过才算完成）
cd AI-JOB-SEARCH
python -m pytest tests/test_observability.py tests/test_web_security_backup.py -q   # 新老测试全绿
python -m pytest -q                                                                  # 全量测试 0 failed
python -m compileall web
# 启动服务后：
curl -s localhost:8000/healthz                                        # {"status":"ok"}
curl -s localhost:8000/metrics | grep careerpilot_http_requests_total # 有指标
curl -s -H "X-Request-Id: abc123" localhost:8000/healthz              # 日志中出现 request_id=abc123
# 日志检查（stderr 或日志文件）：
# 1) 每条请求一行 JSON，含 duration_ms 与 status
# 2) grep -c "api_key" 日志输出 0
```

## 🛠 自检命令（脱离 Codex 手动验证）

```bash
cd AI-JOB-SEARCH
python web/server.py --host 127.0.0.1 --port 8000 &   # 按项目实际启动方式
curl -s localhost:8000/metrics | head -20
curl -s localhost:8000/healthz
grep -c api_key /tmp/careerpilot.log 2>/dev/null || echo "无敏感字段泄漏"
```

## 💡 高级选项

- 后续接 Prometheus + Grafana：`docker-compose.yml` 追加 prometheus/grafana 两个 service，抓取 `app:8000/metrics`；本阶段只需保证 /metrics 输出合规。
- 若想先看效果，指标也可先输出为 JSON（`/metrics?format=json`），Prometheus 文本格式保持不变。

---

# 分阶段提示词：02-数据库迁移与发布流程提示词.md

---

# 02 · 数据库迁移与发布流程提示词（Codex）

## 🩺 根因诊断

| 坑 | 当前表现 | 排查命令 |
|---|---|---|
| 无版本化迁移 | 建表全在 `init_db()` 的 `executescript`，schema 变更靠手写 `PRAGMA table_info` + `ALTER TABLE` | `web/server.py:230-406`、`:368-376` |
| 无法回滚 | 没有 down 迁移，改坏 schema 只能靠备份恢复 | 仓库无迁移目录、无版本表 |
| 无发布纪律 | 无发布检查清单，改 schema 直接改 `init_db`，老库升级路径脆弱 | 读 `DEPLOY.md`、`docker-compose.yml` 无发布步骤 |
| 无 dry-run | 迁移无法预演，生产上跑挂只能事后救 | 无任何 migrate 命令入口 |
| 无迁移测试 | 新老库升级、回滚行为无自动化覆盖 | `ls tests/` 无 migrations 相关 |

**最常见组合**：① + ② —— 先建立"版本表 + up/down 目录 + CLI"，再把现有 `init_db` 固化为基线。

## 📋 提示词正文（整段复制给 Codex）

```text
请在当前工作区（AI-JOB-SEARCH 仓库根目录）实施"数据库迁移与发布流程"。你是资深后端工程师，只做本任务，不做无关重构。

# 目标
为 SQLite 数据库（web/server.py 管理，单文件 careerpilot.db）建立：
1. 轻量版本化迁移框架（纯标准库）：版本表 + web/migrations/ 目录 + up/down + CLI
2. 发布检查清单与回滚 SOP 文档
3. 迁移的自动化测试

# 必读上下文（先读再动手）
- web/server.py:218 db()（连接函数，check_same_thread=False）
- web/server.py:230-406 init_db()（现建表逻辑 = 基线 schema；:368-376 是手写 ALTER 迁移的现例）
- web/server.py:52 _DB_LOCK（进程内锁，迁移同样要用，避免并发）
- web/server.py:42 SETTINGS_FILE / :43 DB_FILE（路径来源，迁移 CLI 复用）
- web/db_backup.py:26 create_backup / :47 restore_backup / :12 verify_database（回滚与发布前备份直接复用）
- tests/（了解既有测试风格，新增测试放 tests/）

# 你必须新增/修改的内容
1. 新建 web/migrations.py：
   - 常量 MIGRATIONS_DIR = web/migrations/
   - ensure_version_table()：建表 schema_migrations(version TEXT PRIMARY KEY, name TEXT, applied_at TEXT)
   - list_migrations()：扫描 MIGRATIONS_DIR 下 `NNNN_<name>.sql` 与 `NNNN_<name>.down.sql`，按 NNNN 升序
   - migrate(conn, target=None)：对未应用版本逐个执行 up SQL（事务包裹）；target 用于 --target 精确版本
   - rollback(conn, steps=1)：按倒序执行 down SQL（每版本先找 NNNN_<name>.down.sql）
   - status(conn)：输出已应用/未应用列表
   - CLI（__main__ 或 scripts/migrate.py）：`up|down|status|dry-run [--target NNNN] [--db PATH]`；dry-run 只打印将要执行的 SQL 不落库
2. 新建 web/migrations/001_app_followup_columns.sql 与 001_app_followup_columns.down.sql：
   - up：把 init_db 里现有手写 ALTER 逻辑（:368-376 的三个列 contact/follow_up_at/attachment_name）固化进迁移（IF 不存在的检查方式保留）
   - down：`ALTER TABLE applications DROP COLUMN`（SQLite 3.35+ 支持）或说明需重建表
3. 修改 web/server.py 的 init_db()：
   - 保留现有 executescript 建表（视为 000 基线，不拆动，避免破坏老库）
   - init_db() 末尾调用 web.migrations.migrate(db())，保证老库启动时自动补齐后续版本
   - 保留 :368-376 的兼容分支不动也行，但注释标注"已由 001 迁移覆盖，仅保留给 000 基线库"——二选一，优先保留兼容分支
4. 新建 docs/release-checklist.md（发布检查清单，含勾选框）：
   ① 备份：python -m web.db_backup 或复用 start_backup_scheduler 说明
   ② 迁移预演：python -m web.migrations dry-run
   ③ 部署代码 → ④ 执行迁移 up → ⑤ 冒烟（/healthz + 关键 API）→ ⑥ 观察告警窗口（对应 01 阶段 SLO）→ ⑦ 异常时按 docs/rollback.md 回滚
5. 新建 docs/rollback.md（回滚 SOP）：
   - 回滚触发条件（迁移报错 / 冒烟失败 / 5xx 上升）
   - 步骤：停新版本 → db_backup.restore_backup 到上一份备份 → python -m web.migrations down 1 → 起旧版本 → 验证
   - 注明：迁移已提交的事务无法自动撤销，down 迁移必须手工编写，禁止空 down
6. 新增 tests/test_migrations.py：
   - 临时库跑 up：schema_migrations 记录存在、目标表列存在
   - 幂等：重复跑 up 不重复应用
   - 回滚：down 后列消失、版本记录删除
   - dry-run：不写库

# 约束
- 不引入 alembic/任何第三方迁移库，纯标准库 sqlite3 + 文件扫描
- 不修改任何现有表的列定义（只新增迁移文件 + init_db 尾部调用）
- 迁移文件禁止包含业务数据（seed 数据仍走原 load_seed_jobs 逻辑，不迁移）
- 每个 up 必须有对应 down；down 不允许为空
- 保证老数据库文件（已有数据的 careerpilot.db）启动即平滑升级，不丢数据

# 期望交付物
- web/migrations.py（新）
- web/migrations/001_app_followup_columns.sql + .down.sql（新）
- web/server.py（init_db 末尾接 migrate）
- docs/release-checklist.md、docs/rollback.md（新）
- tests/test_migrations.py（新）

# 验收（全部通过才算完成）
cd AI-JOB-SEARCH
python -m pytest tests/test_migrations.py -q                    # 新增迁移测试全绿
python -m pytest -q                                             # 全量 0 failed
python -m compileall web
# 手工验证（用副本，勿动真实库）：
cp data/careerpilot.db /tmp/cp_backup.db
python -m web.migrations status --db /tmp/cp_backup.db         # 列出未应用版本
python -m web.migrations up --db /tmp/cp_backup.db             # 应用成功，schema_migrations 有 001
python -m web.migrations status --db /tmp/cp_backup.db         # 全部 applied
python -m web.migrations down 1 --db /tmp/cp_backup.db         # 回滚 1 步成功
python -m web.migrations dry-run --db /tmp/cp_backup.db        # 输出 SQL 但不改动
```

## 🛠 自检命令（脱离 Codex 手动验证）

```bash
cd AI-JOB-SEARCH
python -m web.migrations status            # 生产/本地库版本状态
sqlite3 data/careerpilot.db "SELECT * FROM schema_migrations;"
```

## 💡 高级选项

- 迁移文件后续可扩展为 `.py` 格式（up/down 函数），便于写数据迁移；当前 SQL 格式够用。
- 若未来换 PostgreSQL：迁移器接口（list/applied/up/down）保持，替换连接层即可；本阶段不引入。

---

# 分阶段提示词：03-无状态化与水平扩展提示词.md

---

# 03 · 无状态化与水平扩展提示词（Codex）

## 🩺 根因诊断

| 坑 | 当前表现 | 排查命令 |
|---|---|---|
| 单进程服务 | `ThreadingHTTPServer` 一个进程吃满单核，无法横向扩容 | `web/server.py:2486-2490` |
| SQLite 默认配置 | 未开 WAL，多进程读写会 `database is locked`；每次请求新建连接无复用 | `web/server.py:218-227`（无 PRAGMA） |
| 进程内锁 | `_DB_LOCK` RLock 只保护单进程，多 worker 下无意义 | `web/server.py:52` |
| 无健康检查接入点 | 负载均衡/反代无探活（01 阶段已加 /healthz，此处接入） | `curl localhost:8000/healthz` |
| 静态与应用同进程 | 静态文件由 Handler 输出，反代直出可省应用资源 | `web/server.py:1791 _serve_static` |
| 无多 worker 启动入口 | 只有一个 `python web/server.py`，无 WORKERS 环境变量 | `web/server.py:2497 __main__` |

**最常见组合**：① + ② + ④ —— 先让"单机多 worker + WAL + 反代探活"跑通，达成单机 scale-up；跨机 scale-out 留给 04/06。

## 📋 提示词正文（整段复制给 Codex）

```text
请在当前工作区（AI-JOB-SEARCH 仓库根目录）实施"无状态化与水平扩展（单机多 worker）"。你是资深后端工程师，只做本任务，不做无关重构。

# 目标
1. SQLite 进入 WAL 模式 + 合理 PRAGMA + 连接复用，支撑多进程并发读写不报 locked
2. 提供多 worker 启动入口（默认零第三方依赖，multiprocessing 实现），WORKERS 环境变量可调
3. 提供 nginx 反代配置示例（/healthz 探活、/static 直出、/api 反代）
4. 部署扩容文档 docs/deploy-scale.md

# 必读上下文（先读再动手）
- web/server.py:218-227 db()（连接函数；改为 WAL 等 PRAGMA 的关键位置）
- web/server.py:230 init_db()（迁移器在 init_db 末尾已接入，db() 改动后要复验迁移仍工作）
- web/server.py:2481-2496 main() 与 __main__（服务器启动段；run.py 将复用此逻辑）
- web/server.py:1791 _serve_static()（静态输出现状，代码层保持不动）
- web/server.py:52 _DB_LOCK（保留给单进程场景，多 worker 下注明失效）
- web/db_backup.py:26/47（备份与恢复用 SQLite backup API，验证 WAL 模式下仍正常）
- docker-compose.yml / Dockerfile / DEPLOY.md（部署现状与约束）
- tests/test_web_security_backup.py（了解既有断言）

# 你必须新增/修改的内容
1. 修改 web/server.py 的 db()：
   - 每次新建连接后执行 PRAGMA：
     - journal_mode=WAL
     - synchronous=NORMAL
     - foreign_keys=ON
     - busy_timeout=5000
   - 用 threading.local 缓存每线程连接（conn 复用于该线程内多次调用），close 时按线程清理
   - 保持 check_same_thread=False 与返回 sqlite3.Row 行为不变（备份工具依赖）
   - 注意：journal_mode=WAL 在空库首次执行会写日志，勿在事务中执行
2. 新建 web/run.py（多 worker 启动器，纯标准库）：
   - 读环境变量 WORKERS（默认 1，>1 时启用多进程）、PORT、HOST
   - 用 multiprocessing 起 N 个 worker 子进程，每个子进程执行与现在 __main__ 相同的启动逻辑（import web.server 后调 main 或 server.serve_forever）
   - 父进程职责：等待子进程、捕获 SIGTERM/SIGINT 后向子进程发终止信号并等待退出（优雅关闭，不 kill -9）
   - worker 崩溃时记录日志并保持父进程存活（不自动拉起也行，文档说明由 systemd/进程管理器负责重启——二选一，优先文档说明）
   - 端口策略：默认每个 worker 同端口无法直接多进程绑定；改为支持 WORKER_PORTS=8001,8002 显式分配，未设置时退化为单 worker 直跑原端口——保证旧行为不变
3. 新建 nginx.conf.example（示例，非自动部署）：
   - upstream careerpilot（列出 worker 端口）
   - location /healthz → 反代任一 worker（供探活）
   - location /static/ 或 assets → root 直出 + expires 7d（注意项目静态目录实际路径，以 web/static/ 为准）
   - location / → proxy_pass upstream；带 X-Forwarded-For 等标准头
4. 新建 docs/deploy-scale.md：
   - 单机多 worker 部署步骤（含 nginx 安装/配置、WORKER_PORTS 分配）
   - worker 数建议：≤ CPU 核数；SQLite 写密集场景建议 2-4，避免写锁竞争
   - 明确写出当前边界：SQLite WAL 支持多进程读写但写吞吐有限；会话/限流仍是 04 阶段处理（本阶段多 worker 下 touch_session 写放大可接受）
   - WAL 与备份兼容：db_backup 的 backup API 兼容 WAL，附验证命令
5. 新增 tests/test_db_prisma.py（或并入 test_migrations.py）：
   - 临时库 db() 后 PRAGMA journal_mode 返回 wal
   - busy_timeout/foreign_keys 生效断言
   - 多线程并发各取连接读写不抛 locked（4 线程 × 20 次写）

# 约束
- 不新增任何第三方依赖（不引入 gunicorn/uwsgi；requirements.txt 不变）
- 不改变现有单进程启动行为：`python web/server.py` 或原有启动脚本必须照常工作
- 不修改任何 API 路由与返回结构
- 不动数据库 schema（迁移归 02 阶段管）
- 静态资源代码层不动，只提供 nginx 直出示例

# 期望交付物
- web/server.py（db() PRAGMA + thread-local 连接复用）
- web/run.py（多 worker 启动器）
- nginx.conf.example（新）
- docs/deploy-scale.md（新）
- tests/test_db_prisma.py（新）

# 验收（全部通过才算完成）
cd AI-JOB-SEARCH
python -m pytest tests/test_db_prisma.py -q              # 新测试全绿
python -m pytest -q                                      # 全量 0 failed
python -m compileall web
# 手工验证：
python -m web.run WORKERS=1                              # 单 worker 行为不变（用项目原启动参数核对）
WORKERS=2 WORKER_PORTS=8001,8002 python -m web.run &    # 双 worker
curl -s localhost:8001/healthz && curl -s localhost:8002/healthz   # 两个 worker 均 200
curl -s localhost:8001/metrics | grep careerpilot        # 指标端点正常
# WAL + 备份验证（副本库）：
sqlite3 /tmp/cp_backup.db "PRAGMA journal_mode;"         # 返回 wal
python -c "import sys; sys.path.insert(0,'web'); from db_backup import create_backup; create_backup('/tmp/cp_backup.db','/tmp/bk',3)"  # 备份成功
```

## 🛠 自检命令（脱离 Codex 手动验证）

```bash
cd AI-JOB-SEARCH
WORKERS=2 WORKER_PORTS=8001,8002 python -m web.run &
sleep 1 && curl -s localhost:8001/healthz && curl -s localhost:8002/healthz
kill %1   # 验证优雅退出（观察日志无报错）
```

## 💡 高级选项

- 若未来要跨机 scale-out：SQLite 单文件是硬边界，需迁移 PostgreSQL（迁移器接口 02 阶段已预留）；本阶段只交付单机多 worker。
- 静态资源若量大，可把 `web/static/` 挂对象存储/CDN，nginx 直出已是最低成本方案。

---

# 分阶段提示词：04-分布式会话缓存限流提示词.md

---

# 04 · 分布式会话 / 缓存 / 限流提示词（Codex）

## 🩺 根因诊断

| 坑 | 当前表现 | 排查命令 |
|---|---|---|
| 限流是进程级 | `_LOGIN_FAILURES` 内存 dict，多 worker 各自计数，攻击者可分摊绕过 | `web/server.py:430-452`（`_LOGIN_LOCK` 仅进程内） |
| 会话写放大 | `touch_session` 每次请求都 UPDATE `expires_at`，多 worker 下 SQLite 写锁竞争 | `web/server.py:488-494` |
| 会话无清理 | `DELETE FROM sessions WHERE expires_at < now` 只在 `create_session` 顺手做一次，无定期清理 | `web/server.py:459` |
| 缓存无统一层 | 评分缓存落 `evaluations` 表（共享✓），但搜索缓存只是返回标志 `cached`，无 TTL 存储 | `web/server.py:1963`、`job_extractor.py:310 search_jobs` |
| 时间存储混乱 | `expires_at` 存 `localtime` 字符串，跨时区/跨机器语义脆弱 | `web/server.py:493` |

**最常见组合**：① + ② + ③ —— 限流入表 + 会话写放大治理 + 过期清理，是"多实例一致性"的最小完整闭环。

## 📋 提示词正文（整段复制给 Codex）

```text
请在当前工作区（AI-JOB-SEARCH 仓库根目录）实施"分布式会话/缓存/限流"。你是资深后端工程师，只做本任务，不做无关重构。本阶段默认不引入 Redis，全部用 SQLite 共享表实现（03 阶段已开 WAL，多进程共享可读可写）。

# 目标
1. 登录限流从进程内存迁到 DB 表，跨 worker 共享
2. 会话滑动过期改为"写入限频"，降低每次请求的 UPDATE；新增过期会话定期清理任务
3. 统一缓存接口 web/cache.py（DB 实现 + TTL），搜索缓存接入；评估缓存（evaluations 表）保持不变
4. 可选：通用 API 限流中间件（默认关闭，环境变量开关）

# 必读上下文（先读再动手）
- web/server.py:430 login_rate_status / :441 record_login_failure / :449 clear_login_failures（迁库的三个函数）
- web/server.py:454 create_session / :466 delete_session / :474 get_user_by_token / :488 touch_session
- web/server.py:246 sessions 表结构、:218 db()、:52 _DB_LOCK
- web/server.py:1963 搜索响应里的 cached 标志（接入缓存层的落点）
- web/job_extractor.py:310 search_jobs（搜索入口，缓存 key 应在 query+settings 归一化后计算）
- 02 阶段产物：web/migrations.py 与 web/migrations/ 目录（新表必须走迁移，禁止手写 CREATE TABLE）
- web/db_backup.py:71 start_backup_scheduler（参考其定时器风格实现 cleanup 定时任务）

# 你必须新增/修改的内容
1. 新建迁移 web/migrations/002_distributed_state.sql + .down.sql：
   - up：
     CREATE TABLE IF NOT EXISTS login_attempts (
       key TEXT PRIMARY KEY, timestamps_json TEXT NOT NULL, updated_at REAL NOT NULL
     );
     CREATE TABLE IF NOT EXISTS cache_entries (
       cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL, expires_at REAL NOT NULL
     );
   - down：DROP TABLE login_attempts; DROP TABLE cache_entries;
   - （版本号 002，遵循 02 阶段迁移器规则；timestamp 用 REAL（unix 秒）统一存储）
2. 修改 web/server.py 限流三函数（签名不变，内部改 DB）：
   - record_login_failure(key)：读行 → 追加 time.time() → 写回（用 UPDATE ... 单语句 + busy_timeout 处理竞争；可接受极少丢窗口）
   - login_rate_status(key)：读行过滤窗口内时间戳，返回剩余秒数（语义与现有一致）
   - clear_login_failures(key)：DELETE
   - 保留 _LOGIN_LOCK 作为进程内并发保护（跨进程由 SQLite 事务兜底）
3. 修改 touch_session（写放大治理）：
   - 增加 SESSION_TOUCH_THRESHOLD_SECONDS（默认 300）：仅当 now - 上次 expires_at 值 > 阈值时才 UPDATE，否则直接返回
   - get_user_by_token 内的过期判断改为用时间戳比较（兼容现有 localtime 字符串：先把现有列值统一转成可比较格式——用一个 UPDATE 迁移或读取时用 strptime 转换，二选一，优先读取时转换，避免大迁移）
4. 新增 web/cache.py：
   - class CacheBackend: get(key)->Optional[str]; set(key, value, ttl_seconds)->None; delete(key)->None
   - class DbCacheBackend(CacheBackend)：读写 cache_entries 表，expires_at=now+ttl；get 时检查过期并惰性删除
   - 模块级默认实例 db_cache = DbCacheBackend()
   - 搜索缓存接入：search_jobs 结果 json.dumps 后 set(key=hash(query,settings 白名单字段), ttl=3600)；命中时响应仍带 cached=true
5. 新增 web/cleanup.py（或并入 cache.py）：
   - cleanup_expired(now=None)：DELETE FROM sessions WHERE expires_at 过期（兼容转换）；DELETE FROM login_attempts WHERE updated_at < now-窗口；DELETE FROM cache_entries WHERE expires_at < now
   - start_cleanup_scheduler(interval_seconds=3600)：threading.Timer 循环，服务器 main 启动时调用（参考 start_backup_scheduler 风格）
6. 可选（建议交付）：通用 API 限流——web/ratelimit.py 提供 rate_limit(key_prefix, limit, window) 装饰器；仅在环境变量 API_RATE_LIMIT>0 时启用，默认关闭，不改变现有行为
7. 新增 tests/test_distributed_state.py：
   - 双连接模拟双 worker：conn1 record 5 次失败，conn2 login_rate_status 返回锁定剩余秒数
   - touch_session 阈值内不产生 UPDATE（比对 sqlite 版本或行 updated 时间）
   - cache set/get/expire 行为
   - cleanup 后过期行被删除

# 约束
- 不引入 Redis 或任何第三方缓存/限流库；requirements.txt 不变
- 限流/会话对外行为语义不变：现有 tests 全绿是底线
- 新表只走迁移框架（002），禁止在 init_db 手写
- 时间统一用 unix 秒（REAL）存新表；旧 sessions.expires_at 字符串在读取时转换，不做大迁移
- 通用 API 限流默认关闭，不得影响现有路由

# 期望交付物
- web/migrations/002_distributed_state.sql + .down.sql
- web/server.py（限流迁库 + touch_session 限频 + cleanup 接入）
- web/cache.py、web/cleanup.py（新；ratelimit.py 可选）
- tests/test_distributed_state.py（新）

# 验收（全部通过才算完成）
cd AI-JOB-SEARCH
python -m pytest tests/test_distributed_state.py -q      # 新测试全绿
python -m pytest -q                                      # 全量 0 failed
python -m compileall web
python -m web.migrations up --db /tmp/cp_backup.db       # 002 迁移应用成功（schema_migrations 出现 002）
# 手工验证（两个终端，模拟双进程共享限流）：
python -c "import sys; sys.path.insert(0,'.'); from web import server; [server.record_login_failure('k') for _ in range(5)]; print(server.login_rate_status('k'))"   # 输出 >0（锁定中）
python -c "import sys; sys.path.insert(0,'.'); from web import server; print(server.login_rate_status('k'))"   # 新进程读到同样锁定 → 证明跨进程共享
```

## 🛠 自检命令（脱离 Codex 手动验证）

```bash
cd AI-JOB-SEARCH
sqlite3 data/careerpilot.db "SELECT COUNT(*) FROM login_attempts;"   # 有行
sqlite3 data/careerpilot.db "SELECT COUNT(*) FROM cache_entries;"    # 搜索后可缓存命中
```

## 💡 高级选项

- Redis 版实现（未来）：`RedisCacheBackend` 与 `RedisRateLimiter` 实现同一接口，配置 `CACHE_BACKEND=redis` 切换；接口已在本阶段定义好，切换不侵入业务代码。
- 会话若仍嫌 DB 写多，可切 signed-cookie 无状态会话（token 内嵌过期时间 + HMAC 签名，服务端零存储），作为 05 阶段安全改造的备选，本阶段不动。

---

# 分阶段提示词：05-RBAC审计SSO密钥管理提示词.md

---

# 05 · RBAC / 审计 / SSO / 密钥管理提示词（Codex）

## 🩺 根因诊断

| 坑 | 当前表现 | 排查命令 |
|---|---|---|
| RBAC 只有雏形 | `users.role` 字段存在但无权限矩阵、无资源级校验，任何登录用户可调所有 API | `web/server.py:242`、`_api()` L1807 无权限检查 |
| 无审计日志 | 登录失败、settings 变更、数据删除等敏感操作无任何可追溯记录 | 仓库无 audit 相关表/代码 |
| 密钥明文落盘 | `api_key` 明文存 `settings.json`，权限依赖 OS 文件系统 | `web/server.py:42`、`:1877-1897` |
| 无 SSO | 只有本地账号密码登录，无法对接企业 IdP（OIDC） | `web/server.py:411-417` 密码哈希（本阶段不动） |
| 无合规能力 | 无数据保留策略、无审计保留/导出，无法回答"谁在何时改了什么" | `docs/` 无 security/compliance 文档 |

**最常见组合**：① + ② + ③ —— 先做"权限矩阵 + 审计表 + 密钥去明文"，SSO 作为可选增强。

## 📋 提示词正文（整段复制给 Codex）

```text
请在当前工作区（AI-JOB-SEARCH 仓库根目录）实施"RBAC/审计/密钥管理（SSO 可选）"。你是资深后端工程师，只做本任务，不做无关重构。

# 目标
1. 完整 RBAC：角色枚举 + 权限矩阵 + require_permission 校验，复用现有 users.role 字段
2. 审计日志：audit_log 表 + 关键动作埋点 + admin 查询端点
3. 密钥管理：api_key 不再依赖 settings.json 明文（环境变量优先），settings API 永不回显明文
4. SSO（可选交付）：OIDC 客户端抽象 + 配置文档，默认关闭、零影响

# 必读上下文（先读再动手）
- web/server.py:242 users.role（现有角色字段，默认 'guest'）
- web/server.py:171 load_settings（api_key 读取 @:175 已支持 os.environ LLM_API_KEY）/ :191 save_settings
- web/server.py:1877-1897 settings API 的 api_key 处理（has_key 逻辑保留）
- web/server.py:411 hash_password / :417 verify_password（本阶段禁止改动）
- web/server.py:1777 _current_user / :498 user_public（用户对象与脱敏）
- web/server.py:1807 _api() 分发（审计埋点与 admin 端点的落点）
- web/server.py:430-452 登录限流（登录成功/失败审计埋点在此附近；login 路由定位方式：grep -n "login\|password" web/server.py）
- 02 阶段迁移框架 web/migrations.py（新表必须走迁移）

# 你必须新增/修改的内容
1. 新建迁移 web/migrations/003_audit_log.sql + .down.sql：
   - up：
     CREATE TABLE IF NOT EXISTS audit_log (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       ts REAL NOT NULL,
       user_id INTEGER,
       action TEXT NOT NULL,
       resource TEXT DEFAULT '',
       resource_id TEXT DEFAULT '',
       ip TEXT DEFAULT '',
       user_agent TEXT DEFAULT '',
       meta_json TEXT DEFAULT '{}'
     );
     CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts DESC);
   - down：DROP TABLE audit_log;
2. 新建 web/authz.py：
   - ROLES = {"guest": [...], "user": [...], "admin": [...]}（权限项如 jobs.view、evaluations.manage、settings.manage、audit.view、admin.all）
   - has_permission(role, perm)、require_permission(perm) 装饰器（装饰 _api 内的 handler 方法或分发时校验）
   - 默认映射：guest = 现有匿名能力（种子岗位查看/登录注册），user = 自身数据管理，admin = settings/audit/用户管理
   - 用 403 表示已登录但无权限，401 表示未登录（保持既有错误格式：{"error": ...}）
3. 修改 web/server.py：
   - 新增 audit(action, resource="", resource_id="", user_id=None, ip="", ua="", meta=None)：写 audit_log（失败不抛出，避免影响主流程）
   - 埋点（至少）：登录成功（action=login.success）、登录失败（login.failure）、settings 变更（settings.update，meta 不含值只含变更键名）、profile 变更（profile.update）、任意 DELETE（data.delete，resource+resource_id）
   - 新增 GET /api/admin/audit?limit=100&action=xxx：仅 admin（require_permission("audit.view")），返回脱敏列表（不含 body）
   - settings 的 api_key：读取优先级 os.environ["LLM_API_KEY"] > settings.json；save_settings 永不写明文 api_key（保留 has_key）；GET 永远只回 has_key（现状已如此，保持）
4. 新建 web/sso.py（可选，但必须交付）：
   - class OIDCClient（authorize_url / exchange_code / userinfo），纯标准库 urllib 实现
   - 配置从环境变量 OIDC_ISSUER / OIDC_CLIENT_ID / OIDC_CLIENT_SECRET 读取；未配置时 login 流程走原密码登录，SSO 登录入口不渲染
   - 不引入第三方 OIDC 库；实现以"可对接标准 OIDC Provider"为验收
5. 新建 docs/security.md：
   - 密钥管理规范：api_key 用环境变量注入（docker-compose 已支持 LLM_API_KEY），禁止写入 settings.json / 日志 / git
   - 审计保留策略：audit_log 默认保留 180 天，过期由 04 阶段 cleanup 扩展清理（本阶段文档声明）
   - 威胁模型简述与缓解对照（XSS/CSRF/越权/密钥泄漏）
6. 新增 tests/test_authz.py：
   - guest 访问 /api/admin/audit → 403（或 401）
   - admin 访问 → 200
   - 登录失败会写 audit_log（action=login.failure）
   - settings GET 不含明文 api_key
   - 迁移 003 应用后表存在

# 约束
- 禁止改动 hash_password / verify_password（L411/417）与密码存储格式
- 不破坏现有登录/注册流程与 guest 能力
- 审计日志禁止记录：password、api_key、request body、profile_json 内容
- SSO 默认关闭且不引入第三方依赖；未配置环境变量时行为与现状完全一致
- 新表只走迁移框架（003）

# 期望交付物
- web/migrations/003_audit_log.sql + .down.sql
- web/authz.py、web/sso.py（新）
- web/server.py（审计埋点 + admin 端点 + settings key 处理）
- docs/security.md（新）
- tests/test_authz.py（新）

# 验收（全部通过才算完成）
cd AI-JOB-SEARCH
python -m pytest tests/test_authz.py -q                # 新测试全绿
python -m pytest -q                                    # 全量 0 failed
python -m compileall web
python -m web.migrations up --db /tmp/cp_backup.db     # 003 应用成功
# 手工验证：
# 1) 未登录访问 admin 端点：
curl -s -o /dev/null -w "%{http_code}" localhost:8000/api/admin/audit        # 401 或 403
# 2) 登录后（替换 token）：
curl -s -H "Cookie: careerpilot_session=<admin_token>" localhost:8000/api/admin/audit?limit=10   # 200 JSON
# 3) 审计落库：
sqlite3 data/careerpilot.db "SELECT action, COUNT(*) FROM audit_log GROUP BY action;"
# 4) 无明文 key 泄漏：
curl -s localhost:8000/api/settings | grep -c api_key   # 只应出现 "has_key" 键，无真实值
grep -rn "api_key.*=.*sk-" web/ 2>/dev/null | grep -v has_key || echo "无明文 key 硬编码"
```

## 🛠 自检命令（脱离 Codex 手动验证）

```bash
cd AI-JOB-SEARCH
sqlite3 data/careerpilot.db "SELECT ts, user_id, action, resource FROM audit_log ORDER BY id DESC LIMIT 10;"
```

## 💡 高级选项

- SSO 对接真实 IdP：配置 OIDC_ISSUER/CLIENT_ID/CLIENT_SECRET 后，/api/auth/oidc 提供跳转；docs/sso.md 给出 Keycloak/Auth0 对接步骤。
- 更严的密钥管理：若未来需要"加密存储"，在 web/sso.py 同级提供 KMS/Env 双模式，不落明文；当前环境变量方案已满足最小合规。

---

# 分阶段提示词：06-压测故障演练高可用灾备提示词.md

---

# 06 · 压测 / 故障演练 / 高可用 / 灾备提示词（Codex）

## 🩺 根因诊断

| 坑 | 当前表现 | 排查命令 |
|---|---|---|
| 无压测基线 | 不知道当前能扛多少 QPS/并发，无法设定扩容目标 | 仓库无 loadtest 脚本、无性能报告 |
| 无故障演练 | 没验证过 DB 锁、数据源超时、worker 崩溃时的行为 | 仓库无 chaos 脚本 |
| 备份不可验证 | `db_backup.py` 有 restore 函数但无定期恢复演练，备份"看似有、实未知" | `web/db_backup.py:47 restore_backup` 无演练配套 |
| 单点部署 | 单机单 worker，无探活摘除（03 阶段已有多 worker，本阶段补 HA 形态） | `web/run.py`、`nginx.conf.example`（03 产物） |
| 灾备缺失 | 备份在本地磁盘，无异地副本与恢复 SOP | 无 `scripts/dr/`、无 `docs/dr.md` |

**最常见组合**：① + ③ —— 先出"压测脚本 + 基线报告"，再补"恢复演练"；这两个是最快见效、最能发现问题的。

## 📋 提示词正文（整段复制给 Codex）

```text
请在当前工作区（AI-JOB-SEARCH 仓库根目录）实施"压测/故障演练/高可用/灾备"。你是资深 SRE 兼后端工程师，只做本任务，不做无关重构。所有脚本默认 dry-run，绝不默认动生产库。

# 目标
1. 压测：标准库压测脚本 + 基线报告模板（QPS/P95/5xx），跑出当前基线
2. 故障演练：三个混沌场景脚本（DB 锁 / 数据源超时 / worker 崩溃）+ 演练文档
3. 灾备：备份恢复演练脚本 + 周期 SOP + 异地备份建议
4. 高可用形态：nginx 主动健康检查（自动摘除故障 worker）+ docs/ha.md 说明当前 HA 边界

# 必读上下文（先读再动手）
- 03 阶段产物：web/run.py（WORKERS/WORKER_PORTS 多 worker 启动）、nginx.conf.example
- 01 阶段产物：/metrics 与 /healthz（压测观测与探活端点）
- web/db_backup.py:26 create_backup / :47 restore_backup / :12 verify_database（恢复演练直接复用）
- web/job_extractor.py:404 FREEHIRE_API_URL（os.environ 可覆盖 → 用于数据源超时演练）
- web/server.py:218 db()（WAL + busy_timeout=5000，DB 锁演练利用长事务触发锁等待）
- docker-compose.yml / DEPLOY.md（部署形态）

# 你必须新增的内容（全部新文件，不改业务代码）
1. scripts/loadtest/basic_load.py（纯标准库，不引入 locust）：
   - 参数：--base-url --concurrency --duration --endpoints（默认 /healthz /api/jobs /api/search 列表）
   - 实现：ThreadPoolExecutor 并发循环请求，统计 total/QPS/P95/P99/错误率/5xx 数
   - 输出：控制台表格 + JSON 报告（--report path）
   - 退出码：错误率 > 5% 时非 0（CI 可用）
2. scripts/loadtest/locustfile.py（可选标准 locust 格式，注明需 pip install locust，不进 requirements.txt）
3. docs/perf-baseline.md 模板（必须含：日期、git commit、worker 数、并发数、QPS、P95、P99、5xx 率、结论与瓶颈备注）——先用 basic_load.py 实跑一次填上当前基线
4. scripts/chaos/（每个脚本必须 --dry-run 默认值，--target 指定服务地址，绝不默认连生产）：
   - chaos_db_lock.py：开一个长事务（BEGIN IMMEDIATE; sleep 15; ROLLBACK）期间压测，观察 busy_timeout 下的行为与 5xx；判定：请求不应大量 5xx，最多变慢
   - chaos_source_timeout.py：设置 FREEHIRE_API_URL 指向不可达地址（如 http://10.255.255.1:9）后调 /api/search，观察 12s 超时与错误处理；判定：搜索接口降级返回错误而非挂死进程
   - chaos_worker_kill.py：kill 一个 worker 子进程（WORKER_PORTS 里指定），观察父进程与 nginx 行为；判定：nginx 探活摘除后剩余 worker 正常服务
5. docs/chaos-scenarios.md：每个场景写 触发命令 / 预期行为 / 判定标准 / 恢复命令 / 影响范围
6. scripts/dr/restore_drill.sh（bash）：
   - 找最新备份（backup dir 内 careerpilot-*.db）→ restore_backup 到 /tmp/dr_restore.db → 与原库行数对比（各表 COUNT）→ 不一致则 exit 1
   - 参数：--backup-dir --source-db --verify-only
7. docs/dr.md：
   - 恢复演练周期：每次发布前 + 每周一次（可 cron）
   - 异地备份建议：rsync/scp 到另一主机，或对象存储（给出命令示例）；本地磁盘备份不算灾备
   - 演练判定标准：行数一致 + verify_database 通过
8. 更新 nginx.conf.example：
   - upstream 加 health_check（或 proxy_next_upstream error timeout http_502 http_503）实现故障 worker 自动摘除
   - 注明需 nginx plus 或 openresty 才支持主动 health_check；开源版用被动摘除

# 约束
- 全部新文件，禁止修改 web/server.py 等业务代码（除非发现 bug，报告即可）
- 不引入第三方依赖进 requirements.txt（locust 仅在 locustfile.py 注释里说明）
- 所有脚本默认 dry-run / 只读副本；对真实库、生产库的操作必须显式 --target 且文档警告
- 混沌演练影响面只限 --target 指定的实例

# 期望交付物
- scripts/loadtest/basic_load.py、scripts/loadtest/locustfile.py
- docs/perf-baseline.md（已填当前实测基线）
- scripts/chaos/chaos_db_lock.py、chaos_source_timeout.py、chaos_worker_kill.py
- docs/chaos-scenarios.md
- scripts/dr/restore_drill.sh、docs/dr.md
- nginx.conf.example（更新）

# 验收（全部通过才算完成）
cd AI-JOB-SEARCH
# 1) 压测脚本可跑（对本地测试实例）：
python scripts/loadtest/basic_load.py --base-url http://localhost:8000 --concurrency 20 --duration 10
# 输出含 QPS/P95/错误率；错误率>5% 退出码非 0
# 2) 恢复演练（副本数据）：
bash scripts/dr/restore_drill.sh --backup-dir data/backups --source-db /tmp/cp_backup.db --verify-only
# exit 0 且输出各表行数一致
# 3) 混沌脚本 dry-run 无副作用：
python scripts/chaos/chaos_db_lock.py --dry-run          # 只打印将执行内容，不操作
python scripts/chaos/chaos_source_timeout.py --dry-run
python scripts/chaos/chaos_worker_kill.py --dry-run
# 4) nginx 配置语法：
nginx -t -c nginx.conf.example 2>/dev/null || echo "本机无 nginx，跳过（语法按官方文档核对）"
# 5) 全量测试不回归（业务代码未动，应仍绿）：
python -m pytest -q
```

## 🛠 自检命令（脱离 Codex 手动验证）

```bash
cd AI-JOB-SEARCH
python scripts/loadtest/basic_load.py --base-url http://localhost:8000 --concurrency 10 --duration 5
bash scripts/dr/restore_drill.sh --verify-only --backup-dir data/backups
```

## 💡 高级选项

- 压测结果接 01 阶段 /metrics 交叉验证：压测期间 `curl /metrics | grep p95`，确认报告与指标一致。
- 若未来上多机：把 restore_drill.sh 扩展为"异地副本拉取 → 演练恢复 → 数据校验"流水线；本阶段先夯实单机演练纪律。

---

# 分阶段提示词：07-外部数据源治理提示词.md

---

# 07 · 外部数据源治理提示词（Codex）

## 🩺 根因诊断

| 坑 | 当前表现 | 排查命令 |
|---|---|---|
| 裸 urllib 直连 | 4 处直连点，各自写超时，无重试/退避 | `web/job_extractor.py:39`（Mokahr）、`:85`（Greenhouse/Lever/Ashby）、`:163`（fetch_url_text）、`:386`（Freehire）、`:451`（company jobs） |
| 无熔断 | 某个源持续故障时每个请求都白等超时，拖慢搜索 | 以上各点无失败率统计 |
| 无降级 | Freehire 挂了 → 搜索整体失败，用户拿不到任何结果 | `web/server.py:1963` 搜索响应无兜底逻辑 |
| 源健康不可见 | `search_source_health` 只是标签映射，无真实健康状态 | `web/server.py:630-638` |
| 配置硬编码 | 超时 12s/15s/20s/30s 写死在代码里，无法按环境调整 | `web/job_extractor.py:57/105/175/407` |

**最常见组合**：① + ② + ③ —— 统一客户端 + 熔断 + 降级缓存是"供应链韧性"的最小闭环。

## 📋 提示词正文（整段复制给 Codex）

```text
请在当前工作区（AI-JOB-SEARCH 仓库根目录）实施"外部数据源治理"。你是资深后端工程师，只做本任务，不做无关重构。目标：Freehire/Greenhouse/Lever/Ashby/Mokahr 等外部 ATS 源任意一个故障时，系统不挂、不白等、用户仍能拿到（缓存或降级）结果。

# 目标
1. 统一 HTTP 客户端：重试（指数退避 + jitter）、超时配置化、可注入测试
2. 每源熔断器：失败率超阈值熔断，半开探测恢复
3. 降级缓存：搜索失败时返回上次成功结果（stale 标志）
4. 源健康状态：真实统计 + /api/sources/health 端点

# 必读上下文（先读再动手）
- web/job_extractor.py:39 _mokahr_job_from_url（timeout=20 @:57）
- web/job_extractor.py:85 _public_ats_job_from_url（greenhouse/lever/ashby，timeout=15 @:105）
- web/job_extractor.py:163 fetch_url_text（通用抓取，timeout=30 @:175）
- web/job_extractor.py:386 search_freehire_jobs（FREEHIRE_API_URL 可被环境变量覆盖 @:404，timeout=12 @:407）
- web/job_extractor.py:451 search_company_jobs（内部 fetch_candidate 回调 @:495）
- web/server.py:630 search_source_health（现标签映射，改造为真实健康统计）
- web/server.py:1963 搜索响应（加降级字段的落点）
- 04 阶段产物：web/cache.py（DbCacheBackend，降级缓存直接复用）

# 你必须新增/修改的内容
1. 新建 web/http_client.py（纯标准库 urllib 封装）：
   - get_json(url, headers=None, timeout=12, retries=2, backoff=0.5, jitter=0.1, source="unknown") -> dict
   - fetch_text(url, headers=None, timeout=30, retries=1, ...) -> str
   - 重试：对连接错误/超时/5xx 指数退避（0.5s、1s + 随机 jitter），4xx 不重试
   - CircuitBreaker 类：per-source 单例注册；failure_threshold（默认 5）/ recovery_timeout（默认 30s）；state: closed/open/half_open；half-open 时放行 1 个探测请求
   - report(source, ok: bool, msg="")：上报到模块级健康注册表（供 /api/sources/health 读取）
   - 所有请求带统一 User-Agent（沿用 CareerPilot/1.0）
2. 修改 web/job_extractor.py：
   - 上述 5 个抓取点全部改走 http_client.get_json/fetch_text，source 参数分别传 "freehire"/"mokahr"/"ats"/"web"/"company"
   - 超时改为环境变量可覆盖：FREEHIRE_TIMEOUT/ATS_TIMEOUT/WEB_TIMEOUT，默认保持现有时长
   - 熔断 open 时：立即返回空结果 + source 健康标记 fail，不发起真实请求
3. 修改 web/server.py：
   - search_source_health 改为读 http_client 健康注册表（ok/fail/last_ok/last_fail/msg），无数据时回退现标签
   - 搜索失败降级：search_jobs 抛错或返回空时，用 DbCacheBackend 读上次成功缓存（key 与 04 阶段一致），命中则返回 data + sources 标注 stale，并在响应加 "degraded": true
   - 新增 GET /api/sources/health（user 可见）：返回各源 ok/fail/latency_ms/最近状态
4. 新建 docs/source-reliability.md：
   - 每个源的依赖等级（Freehire=核心搜索源、ATS=增强、web=兜底）、超时/重试/熔断参数表
   - 故障时行为矩阵：源挂 → 熔断 → 降级缓存 → 用户提示
   - 供应商变更预案：换 API 时只改 http_client 的 source 实现
5. 新增 tests/test_http_client.py：
   - 重试成功：mock 前 2 次抛 URLError，第 3 次成功，调用 3 次
   - 4xx 不重试：mock 返回 404，调用 1 次
   - 熔断：连续 6 次失败后第 7 次直接短路（不发请求）
   - 降级：search_jobs 失败时返回缓存 stale 结果（用临时 DB 的 DbCacheBackend）

# 约束
- 不引入 requests 等第三方依赖；保留 urllib，统一封装在 http_client.py
- 不改变搜索 API 的返回结构（只允许新增字段 degraded/stale/sources）
- 默认超时不劣于现状（20/15/30/12s）；熔断参数可用环境变量覆盖
- 降级缓存复用 04 阶段 cache_entries，不新建表（无需迁移）
- 源健康信息禁止包含第三方密钥/URL 内部参数

# 期望交付物
- web/http_client.py（新）
- web/job_extractor.py（抓取点改造）
- web/server.py（sources health 端点 + 搜索降级）
- docs/source-reliability.md（新）
- tests/test_http_client.py（新）

# 验收（全部通过才算完成）
cd AI-JOB-SEARCH
python -m pytest tests/test_http_client.py -q        # 新测试全绿
python -m pytest -q                                   # 全量 0 failed
python -m compileall web
# 手工验证（本地起服务后）：
# 1) 源健康端点：
curl -s localhost:8000/api/sources/health             # 各源 ok/fail 状态 JSON
# 2) 熔断+降级（用 06 阶段 chaos_source_timeout 或手动设假地址）：
FREEHIRE_API_URL=http://10.255.255.1:9 python -m web.run &
curl -s "localhost:8000/api/search?q=python"          # 返回 200；若此前有缓存则 data 非空 + degraded=true/stale
# 3) 恢复正常（去掉假地址重启）后：
curl -s localhost:8000/api/sources/health             # freehire 回到 ok（半开探测成功）
# 4) 抓取点不再裸 urllib：
grep -n "urllib.request.urlopen" web/job_extractor.py || echo "所有直连点已收敛到 http_client"
```

## 🛠 自检命令（脱离 Codex 手动验证）

```bash
cd AI-JOB-SEARCH
curl -s localhost:8000/api/sources/health
FREEHIRE_API_URL=http://10.255.255.1:9 python -m web.run &   # 模拟源故障
curl -s "localhost:8000/api/search?q=ai" | python -m json.tool | grep -E "degraded|stale|sources"
```

## 💡 高级选项

- 源健康面板：后续可在前端加"数据源健康"卡片，读 /api/sources/health 轮询展示（本阶段后端已就绪）。
- 更严格 SLA：若 Freehire 是核心，可加"连续 N 次失败 → 站内公告/告警"（接 01 阶段 alertmanager）。
