# /doctor - CareerPilot-CN 环境与数据体检

运行一次只读体检，帮助用户在 `/setup`、`/scrape`、`/resume` 或 `/apply` 前发现问题。除非用户明确要求修复，否则本命令不写入、不删除、不移动任何文件。

## 检查项

1. **基础目录：** `documents/`、`documents/applications/`、`cv/`、`cover_letters/`、`templates/` 是否存在。
2. **候选人档案：** `CLAUDE.md`、`.claude/skills/job-application-assistant/01-candidate-profile.md`、`cv/main_example.tex` 是否存在；标出仍有 `[YOUR_*]`、`[待补]` 或空白的字段。
3. **简历模板：** 检查激活模板的 manifest、源文件和编译命令；检查 ASu HTML 模板是否存在且包含可编辑区域和打印样式。
4. **工具链：** 检查 Python、Bun，以及当前激活模板需要的 `lualatex`/`xelatex`/`typst`/`pdftotext`。缺失时说明影响范围，不把可选依赖误报为阻塞项。
5. **状态文件：** 如果存在，检查 `job_search_tracker.csv`、`job_scraper/seen_jobs.json` 和 `gmail_sync/state.json` 是否能解析；检查 CSV 表头是否符合 `/outcome` 的标准 schema。
6. **申请追踪：** 检查 `documents/applications/**/run-manifest.json` 是否为合法 JSON、`schema_version` 是否受支持、引用的材料路径是否存在；缺失 manifest 只标记警告，不阻塞旧申请。
7. **隐私与版本控制：** 检查个人资料、申请归档、tracker 和同步状态是否被 `.gitignore` 排除；发现风险时只报告路径和建议，不打印个人信息内容。

## 输出

按 `通过 / 警告 / 阻塞` 分组。每个警告或阻塞都包含影响的命令和一条修复建议。最后给出最短下一步，例如“先运行 `/setup`”或“先安装 LaTeX 编译器”。
