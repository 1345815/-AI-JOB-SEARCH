# Template: china-student

- **Type:** Cover letter
- **Source extension:** .tex
- **Engine/toolchain:** xelatex
- **Page limit:** 1 page(s)
- **Fonts:** Fandol CJK fonts (installed with ctex package)
- **Class/packages:** article + ctex

## Compile command

    cd cover_letters && xelatex -interaction=nonstopmode <file>.tex

## Style rules

- 使用简体中文，正文不超过 4 段。
- 第一段点明岗位和匹配点；中间段落突出可量化成果；最后一段表达期待。
- 语言自信、具体，不使用空话套话。
- 严格控制在 1 页以内，签名和联系方式必须完整显示在页内。

## Known pitfalls

- 中文必须使用 xelatex + ctex 编译；不要用 pdflatex。
- 如果系统没有 Fandol 字体，改用 `[fontset=windows]` 并确保安装了中文字体。
