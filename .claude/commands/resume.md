# /resume - Generate an Editable HTML Resume

Use the repository skill `.agents/skills/asu-resume/SKILL.md` for this command. This is the HTML resume path for users who want an editable browser document or a printable PDF; it complements the existing LaTeX `/apply` output and does not replace it.

## Rules

1. Read `.agents/skills/asu-resume/SKILL.md` and its referenced template guidance before drafting.
2. Use only confirmed facts from `.claude/skills/job-application-assistant/01-candidate-profile.md`, `cv/main_example.tex`, `CLAUDE.md`, and user-provided evidence. Mark missing details as `【待补：...】`; never invent metrics, titles, dates, or ownership.
3. Copy `assets/asu/resume-template-editable.html` (or `resume-template-two-page.html` when justified) into `cv/` as `resume_<company>_<role>.html`. Never edit files under `assets/asu/` directly.
4. Keep the HTML genuinely editable, keep the toolbar outside the printed page, and preserve clickable public links and replaceable photo/logo slots.
5. Perform the skill's print-preview and page-balance QA before delivery. Report the output path, selected layout, page count, and any unresolved `待补` fields.
6. Before delivery, run an ATS preflight against the target JD: extract required skills, tools, role terms, education and language requirements; report matched terms, honest gaps, and terms that are present but buried. Place supported high-value terms in the profile, experience, project or skills text using the JD's original wording. Never add a keyword that the candidate cannot substantiate.
7. Verify machine readability: selectable text, normal reading order, explicit section headings, parseable dates and contact details, visible URLs, and no essential information encoded only in images, icons, columns or color.

## 填写提示

- **技能与语言：** 使用逗号或顿号连续填写，优先包含岗位描述中出现且本人确实掌握的关键词。
- **核心优势、辅助技能、职业目标：** 使用分号或顿号连续填写，内容具体简洁，避免空泛形容词。
