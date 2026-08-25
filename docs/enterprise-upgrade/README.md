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
