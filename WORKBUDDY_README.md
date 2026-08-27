# WorkBuddy 项目交接说明

## 项目定位

CareerPilot-CN 是面向中国应届生的 AI 求职助手。项目基于开源 AI Job Search 框架进行中文校招本地化和产品化改造，目标是把简历整理、岗位搜索、岗位匹配、简历定制、投递跟踪和面试准备串成一个可复用流程。

项目仓库：<https://github.com/1345815/-AI-JOB-SEARCH>

## 当前已完成的改动

- 将候选人资料、搜索配置和岗位申请流程适配为中文校招场景。
- 将 `/setup` 改为简历库建档流程：从 `documents/cv/` 读取多份历史简历，提取、去重、交叉核对并增量更新档案。
- 增加中文应届生简历模板、可编辑 HTML 简历和 PDF 导出能力。
- 增加 `/doctor`、`/board`、`/resume` 等命令，分别用于环境检查、行动队列和简历生成。
- 增加多用户 Web 版，包含注册登录、游客转正、简历导入、岗位搜索、匹配评分、投递看板、数据分析和岗位同步。
- 增加用户数据隔离、审计日志、数据库迁移、备份恢复和安全检查。
- 增加 Docker 部署、Windows 启动脚本、测试用例和 CI 配置。

## WorkBuddy 使用方式

启动脚本：

```text
启动求职助手.cmd
```

Web 版启动：

```powershell
python web/server.py
```

启动后访问 `http://127.0.0.1:8000`，Windows 也可以双击 `启动Web版.cmd`。

常用流程：

```text
/doctor  ->  /setup  ->  /scrape  ->  /rank  ->  /apply
```

投递后使用 `/outcome` 记录结果，使用 `/board` 查看待办，使用 `/html-report` 查看求职漏斗。

## 重要目录

- `.claude/`：规范和命令的唯一真源，修改流程时优先改这里。
- `.codebuddy/`：WorkBuddy 薄指针包装，只负责转发到 `.claude/`，不要在这里复制业务规则。
- `.agents/skills/`：岗位网站、简历生成等可复用技能。
- `documents/cv/`：用户历史简历库，是 `/setup` 的主要资料入口。
- `web/`：多用户 Web 后端、前端、评分、简历导入和数据处理。
- `web/data/`：运行数据库和个人运行数据，不应提交到 Git。
- `assets/asu/`、`templates/`：中文简历模板和模板资源。
- `tests/`：Web、简历解析、岗位提取、评分和安全相关测试。

## 后续工作要求

1. 继续开发前先阅读 `AGENTS.md`、`CLAUDE.md` 和相关 `.claude/commands/` 文件。
2. 不要修改 `.codebuddy/` 中的薄指针内容来改变规则；规范统一维护在 `.claude/` 或 `.agents/`。
3. 不要把数据库、个人简历、API 密钥、备份快照或临时目录提交到 Git。
4. 新增功能要同时补充中文说明、启动方式和必要测试。
5. 任何简历、求职信和岗位匹配结论都必须基于用户真实资料，禁止编造经历或技能。
6. 修改完成后优先运行相关测试，并检查 Web 页面和 PDF 输出是否正常。

## 仓库边界

唯一开发目录是：

```text
E:\WorkBuddy\repos\-AI-JOB-SEARCH
```

`E:\WorkBuddy\backup\AI-JOB-SEARCH-backup-20260822-1748` 仅用于回溯，不要在备份目录继续开发或启动服务。

## 重要恢复事实

- Codex 原有的 `web/agents/` 提交对象已丢失，磁盘文件和备份中也不存在。当前已按现有测试契约重新恢复核心多 Agent 编排包：包含 Agent 注册协议、并发队列、内置 Agent 和 `CareerPilotOrchestrator.execute/execute_async`。它恢复了核心编排能力，但不等同于原提交中的全部业务实现；分布式持久化仍由 `web/tasks.py` 和 `web/worker.py` 负责。
- `authz`、`cache`、`job_extractor` 等两边都修改过的文件，当前保留的是生产主线版本。这是有意取舍，因为 Codex 版本基于旧主线，直接合并可能覆盖生产修复。
- 后续 Codex 改动应及时提交并推送，不要长期积攒未推送提交。也可以让 Codex 直接在本 E 盘仓库工作，避免再次发生分支和目录分裂。
- `codex-backup/` 仅作为备份保留。确认当前主线和资产无误后，再由用户决定是否删除；WorkBuddy 不要擅自删除。

## 参考文档

- `README.md`：项目完整说明和功能介绍
- `SETUP.md`：命令行环境配置
- `web/README.md`：Web 版使用说明
- `DEPLOY.md`：Docker 和服务器部署
- `PRODUCT_ROADMAP.md`：产品后续规划
- `REPOSITORY_UNIFICATION.md`：仓库合并记录
