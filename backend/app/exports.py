from io import BytesIO
from html import escape
from pathlib import Path
from urllib.parse import urlsplit
from markdown_it import MarkdownIt
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Preformatted
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import textwrap


def inline(tokens):
    out = []
    links = []
    for t in tokens or []:
        if t.type == "text":
            out.append(escape(t.content))
        elif t.type in ("softbreak", "hardbreak"):
            out.append("<br/>")
        elif t.type == "code_inline":
            out.append('<font face="Courier">' + escape(t.content) + "</font>")
        elif t.type == "strong_open":
            out.append("<b>")
        elif t.type == "strong_close":
            out.append("</b>")
        elif t.type == "em_open":
            out.append("<i>")
        elif t.type == "em_close":
            out.append("</i>")
        elif t.type == "link_open":
            href = t.attrGet("href") or ""
            links.append(urlsplit(href).scheme in ("http", "https", "mailto"))
            out.append(
                '<a href="' + escape(href, quote=True) + '" color="#8C6239">'
                if urlsplit(href).scheme in ("http", "https", "mailto")
                else "<span>"
            )
        elif t.type == "link_close":
            out.append("</a>" if links.pop() else "</span>")
        elif t.type == "image":
            out.append(escape(t.content))
    return "".join(out)


def pdf_bytes(title, markdown):
    base = Path("/usr/share/fonts/truetype/dejavu")
    font = "Helvetica"
    if (base / "DejaVuSans.ttf").exists():
        for name, file in [
            ("Body", "DejaVuSans.ttf"),
            ("Body-Bold", "DejaVuSans-Bold.ttf"),
            ("Body-Italic", "DejaVuSans-Oblique.ttf"),
            ("Body-BoldItalic", "DejaVuSans-BoldOblique.ttf"),
        ]:
            candidate = base / file
            if not candidate.exists():
                candidate = base / "DejaVuSans.ttf"
            pdfmetrics.registerFont(TTFont(name, str(candidate)))
        pdfmetrics.registerFontFamily(
            "Body", normal="Body", bold="Body-Bold", italic="Body-Italic", boldItalic="Body-BoldItalic"
        )
        font = "Body"
    styles = getSampleStyleSheet()
    for st in styles.byName.values():
        st.fontName = font
    styles["BodyText"].fontSize = 10
    styles["BodyText"].leading = 15
    styles.add(
        ParagraphStyle(
            "CodeWrap",
            fontName="Courier",
            fontSize=8,
            leading=11,
            backColor=colors.HexColor("#f1f5f9"),
            borderPadding=8,
        )
    )
    buf = BytesIO()
    story = [Paragraph(escape(title), styles["Title"]), Spacer(1, 14)]
    tokens = MarkdownIt("commonmark", {"html": False}).parse(markdown)
    heading = None
    bullet = 0
    for t in tokens:
        if t.type == "heading_open":
            heading = min(int(t.tag[1:]), 3)
        elif t.type == "heading_close":
            heading = None
        elif t.type == "list_item_open":
            bullet += 1
        elif t.type == "list_item_close":
            bullet = max(0, bullet - 1)
        elif t.type == "inline":
            story.append(
                Paragraph(
                    inline(t.children),
                    styles[f"Heading{heading}"] if heading else styles["BodyText"],
                    bulletText="•" if bullet else None,
                )
            )
            story.append(Spacer(1, 5))
        elif t.type in ("fence", "code_block"):
            for line in t.content.splitlines():
                for wrapped in textwrap.wrap(line, width=88, replace_whitespace=False, drop_whitespace=False) or [" "]:
                    story.append(Preformatted(wrapped, styles["CodeWrap"]))
            story.append(Spacer(1, 10))

    def footer(canvas, doc):
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawString(44, 25, "AI Research • Export")
        canvas.drawRightString(550, 25, str(doc.page))

    SimpleDocTemplate(buf, rightMargin=44, leftMargin=44, topMargin=44, bottomMargin=44, title=title).build(
        story, onFirstPage=footer, onLaterPages=footer
    )
    return buf.getvalue()
