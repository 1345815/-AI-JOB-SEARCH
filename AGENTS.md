---
framework_version: 1.0.0
---

# Agent Guidelines: AI Job Search

This workspace is structured to manage job search activities, scraper tools, CVs, cover letters, and interview preparation.

## Thin-Pointer Design (Single Source of Truth)

To prevent duplication and configuration drift across different AI agent frameworks (Claude Code, Google Antigravity, Codex, Cursor, Gemini CLI, etc.), this workspace uses a unified thin-pointer design. All agent runtimes should load the canonical specifications and candidate profiles from the files and directories below:

1. **Personal Candidate Profile:**
   - The candidate profile, contact details, education, and target preferences are defined in [CLAUDE.md](CLAUDE.md) and the individual profile methodology files under [.claude/skills/job-application-assistant/](.claude/skills/job-application-assistant/) (specifically `01-*.md` etc.).
2. **Canonical Workflow Specifications:**
   - The step-by-step instructions and triggers for tasks (setup, scrape, rank, apply, upskill, interview) are defined in the [.claude/](.claude/) directory (specifically under `.claude/skills/` and `.claude/commands/`).
   - Do not duplicate these rules or specifications. Treat `.claude/` files as the single source of truth.
3. **Portal Search Skills:**
   - Job-portal search CLIs live under [.agents/skills/](.agents/skills/) in the portable Agent Skills format (with a `SKILL.md` per portal). Codex and Antigravity discover these automatically; the `/scrape` workflow in [.claude/skills/job-scraper/](.claude/skills/job-scraper/) orchestrates them.

## 中国应届生工作流（本仓库已本地化）

- 候选资料与搜索配置默认面向中国应届生：`CLAUDE.md`、`01-candidate-profile.md`、`search-queries.md` 均已改为中文模板，运行 `/setup` 后填写真实信息即可。
- 中文简历与求职信模板已注册为 `china-student`，`/apply` 会默认生成中文 PDF；需要切回英文模板时运行 `/add-template --use default`。
- 中国招聘网站（BOSS直聘、智联、前程无忧等）通常有登录和反爬限制：优先用 WebSearch `site:` 查询，不要尝试绕过登录、验证码或付费墙。
- 校招职位优先看"应届生 / 校招 / 管培生"标签，并提醒网申截止日期。

## WorkBuddy 运行方式

- 启动：运行根目录 `启动求职助手.cmd`，它会用 WorkBuddy 自带的 CodeBuddy CLI 在当前项目目录启动会话。
- WorkBuddy 读取 `AGENTS.md`，并通过 `.codebuddy/commands/` 与 `.codebuddy/skills/` 暴露斜杠命令和技能。
- `.codebuddy/` 下都是薄指针包装，唯一真源仍是 `.claude/` 与 `.agents/`；修改规范请改 `.claude/` 或 `.agents/`，不要改 `.codebuddy/` 包装。
- 首次运行如提示登录，请用 WorkBuddy 账号完成一次登录。
