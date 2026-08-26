# JobHuntBot 集成（求职投递工作流 + 本地看板）

## 来源与许可

- 上游：https://github.com/DanielPan12/JobHuntBot （MIT License，源自 Yvonne He 的 ApplyPilot 改编）
- 本仓库保留上游 `LICENSE`（含原始版权声明，符合 MIT 要求）
- 集成位置：`tools/jobhuntbot/`（完整独立子目录，未做代码改动）

## 它是什么

AI Agent 驱动的**求职投递工作流** + 本地 CSV 进度看板：
- `SKILL.md`：Agent 核心工作流（初始化/找岗位/分类/投递/跟进的安全约定）
- `dashboard/`：零依赖 Node 静态看板（**无需 npm install**），CSV 存储（Excel 可直接编辑）
- `templates/` `references/`：候选人画像、投递规则、简历路由、安全边界等模板

## 与 CareerPilot 的关系（无冲突设计）

| 维度 | CareerPilot（web/） | JobHuntBot（tools/jobhuntbot/） |
|---|---|---|
| 语言/运行时 | Python 3.12 容器 | Node.js（仅看板，可选） |
| 数据 | SQLite（careerpilot.db） | 本地 CSV（job_pool/application_log…） |
| 端口 | 8000 | 8420（独立） |
| 依赖 | requirements.txt | 零外部依赖 |
| 定位 | 岗位搜索/评分/简历库/跟踪 SaaS | 投递执行工作流 + 看板（Agent 驱动） |

互不干扰：不共享代码、不共享数据、不共享端口、不共享依赖。

## 使用方式

1. 启动看板：双击 `tools/jobhuntbot/dashboard/start-dashboard.bat`（或 `.sh`），浏览器访问 `http://localhost:8420/dashboard.html`
2. 初始化工作流：让 AI Agent 按 `SKILL.md` 执行"初始化求职工作流"
3. 个人材料放入 `tools/jobhuntbot/my-materials/`（已 gitignore，不会提交）
4. 安全边界：Agent 不得猜测身份/薪资、绕过反爬、未经确认提交——见 `references/safety-and-boundaries.md`

## 与简历库联动（可选增强）

CareerPilot 简历库识别出的结构化档案（JSON），可手动作为 JobHuntBot `templates/candidate_profile.template.json` 的填写参考，减少重复录入。两者数据不自动同步，保持解耦。

## 维护

- 上游更新：重新 clone `DanielPan12/JobHuntBot` 覆盖 `tools/jobhuntbot/` 内容（保留自己的 CSV 数据）
- 本仓库不修改上游代码；如需改动，标注为本地 patch 并说明
