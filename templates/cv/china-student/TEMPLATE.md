# Template: china-student

- **Type:** CV
- **Source extension:** .tex
- **Engine/toolchain:** xelatex
- **Page limit:** 2 page(s)
- **Fonts:** Fandol CJK fonts (installed with ctex package)
- **Class/packages:** moderncv (banking/blue) + ctex

## Compile command

    cd cv && xelatex -interaction=nonstopmode <file>.tex

## Style rules

- 使用简体中文，保持现代、简洁的银行风格（banking, blue）。
- 章节顺序固定：教育背景、实习经历、项目经历、校园经历、专业技能、语言能力、获奖情况、自我评价。
- 每个条目尽量量化：数字、工具、成果。
- 简历严格控制在 2 页以内，禁止孤儿标题（标题在页底而内容在下一页）。

## Known pitfalls

- 中文必须使用 xelatex + ctex 编译；不要用 pdflatex。
- `\name{姓名}{}` 中姓名作为一个整体放在第一个参数，第二个参数留空。
