"""Render CareerPilot Markdown documents as polished Chinese A4 PDFs."""

import io
import re
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

FONT_NAME = "STSong-Light"
pdfmetrics.registerFont(UnicodeCIDFont(FONT_NAME))


def _mixed_text(text):
    parts = re.split(r"([A-Za-z][A-Za-z0-9+./_-]*)", text)
    return "".join(f'<font name="Helvetica">{escape(part)}</font>' if re.fullmatch(r"[A-Za-z][A-Za-z0-9+./_-]*", part or "") else escape(part) for part in parts)


def _inline(text):
    parts = re.split(r"(\*\*.+?\*\*)", text.strip())
    return "".join("<b>" + _mixed_text(part[2:-2]) + "</b>" if part.startswith("**") and part.endswith("**") else _mixed_text(part) for part in parts)


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("ChineseTitle", parent=base["Title"], fontName=FONT_NAME, fontSize=22, leading=30, alignment=TA_CENTER, textColor=colors.HexColor("#17202A"), spaceAfter=9 * mm),
        "h2": ParagraphStyle("ChineseH2", parent=base["Heading2"], fontName=FONT_NAME, fontSize=14, leading=21, textColor=colors.HexColor("#143D59"), spaceBefore=6 * mm, spaceAfter=2.5 * mm, borderColor=colors.HexColor("#B8C7D1"), borderWidth=0, borderPadding=(0, 0, 2 * mm, 0)),
        "h3": ParagraphStyle("ChineseH3", parent=base["Heading3"], fontName=FONT_NAME, fontSize=11.5, leading=18, textColor=colors.HexColor("#2C3E50"), spaceBefore=3 * mm, spaceAfter=1.5 * mm),
        "body": ParagraphStyle("ChineseBody", parent=base["BodyText"], fontName=FONT_NAME, fontSize=10.5, leading=17, textColor=colors.HexColor("#263238"), spaceAfter=2.2 * mm),
        "bullet": ParagraphStyle("ChineseBullet", parent=base["BodyText"], fontName=FONT_NAME, fontSize=10.5, leading=17, leftIndent=0, textColor=colors.HexColor("#263238")),
    }


def markdown_story(content):
    styles = _styles()
    story, bullets = [], []

    def flush_bullets():
        if bullets:
            items = [ListItem(Paragraph(item, styles["bullet"]), leftIndent=3 * mm) for item in bullets]
            story.append(ListFlowable(items, bulletType="bullet", start="circle", leftIndent=5 * mm, bulletFontName=FONT_NAME, bulletFontSize=7, spaceAfter=2.5 * mm))
            bullets.clear()

    for raw in str(content or "").splitlines():
        line = raw.strip()
        if not line:
            flush_bullets()
            story.append(Spacer(1, 1.5 * mm))
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        bullet = re.match(r"^(?:[-*]\s+|·\s*)(.+)$", line)
        if heading:
            flush_bullets()
            level = len(heading.group(1))
            story.append(Paragraph(_inline(heading.group(2)), styles["title" if level == 1 else "h2" if level == 2 else "h3"]))
        elif bullet:
            bullets.append(_inline(bullet.group(1)))
        else:
            flush_bullets()
            story.append(Paragraph(_inline(line), styles["body"]))
    flush_bullets()
    return story


def render_document_pdf(content, title="CareerPilot 文档"):
    output = io.BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm, topMargin=18 * mm, bottomMargin=17 * mm, title=title, author="CareerPilot")

    def decorate(canvas, doc):
        canvas.saveState()
        canvas.setFont(FONT_NAME, 8)
        canvas.setFillColor(colors.HexColor("#78909C"))
        canvas.drawString(18 * mm, A4[1] - 10 * mm, "求职文档")
        canvas.drawRightString(A4[0] - 18 * mm, 9 * mm, f"第 {doc.page} 页")
        canvas.restoreState()

    document.build(markdown_story(content), onFirstPage=decorate, onLaterPages=decorate)
    return output.getvalue()
