"""Report generation: Markdown, PDF and DOCX from one analysed paper."""

import io
import re
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor
from fpdf import FPDF

from . import citations

VERDICT_LABEL = {
    "open": "STILL OPEN",
    "partial": "PARTIALLY ADDRESSED",
    "addressed": "ADDRESSED",
    "unverified": "UNVERIFIED",
}

VERDICT_RGB = {
    "open": (200, 45, 45),
    "partial": (190, 130, 20),
    "addressed": (30, 140, 70),
    "unverified": (110, 110, 110),
}


def _latin(text: str) -> str:
    """fpdf's core fonts are latin-1 only; keep the closest printable form."""
    if not text:
        return ""
    replacements = {
        "’": "'", "‘": "'", "“": '"', "”": '"',
        "–": "-", "—": "-", "…": "...", " ": " ",
        "−": "-", "×": "x", "≤": "<=", "≥": ">=",
        "≈": "~", "→": "->", "•": "-",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return text.encode("latin-1", "replace").decode("latin-1")


def _fields(paper: dict[str, Any]) -> tuple[dict, dict, list, dict]:
    analysis = paper.get("analysis") or {}
    metadata = analysis.get("metadata") or {}
    grounding = paper.get("grounding") or {}
    gaps = grounding.get("gaps") or analysis.get("gaps") or []
    return analysis, metadata, gaps, grounding


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------


def markdown(paper: dict[str, Any]) -> str:
    analysis, metadata, gaps, grounding = _fields(paper)
    styles = citations.build(metadata)

    lines = [
        f"# {metadata.get('title', paper.get('filename', 'Paper'))}",
        "",
        f"**Authors:** {', '.join(metadata.get('authors') or []) or 'not stated'}  ",
        f"**Year:** {metadata.get('year', 'n.d.')}  ",
        f"**Venue:** {metadata.get('venue') or 'not stated'}  ",
        f"**Domain:** {metadata.get('domain') or 'not stated'}  ",
        f"**Pages:** {paper.get('pages', '-')}",
        "",
        "## Summary",
        "",
        analysis.get("summary", {}).get("detailed", ""),
        "",
        "## Objective",
        "",
        analysis.get("objective", ""),
        "",
        "## Method",
        "",
        analysis.get("method", ""),
        "",
        "## Datasets",
        "",
        "\n".join(f"- {d}" for d in analysis.get("datasets") or []) or "- not stated",
        "",
        "## Key findings",
        "",
        "\n".join(f"- {f}" for f in analysis.get("findings") or []) or "- not stated",
        "",
        "## Stated limitations",
        "",
        "\n".join(f"- {x}" for x in analysis.get("stated_limitations") or []) or "- not stated",
        "",
        "## Research gaps",
        "",
    ]

    for index, gap in enumerate(gaps, start=1):
        verdict = gap.get("verdict", "unverified")
        lines += [
            f"### Gap {index} - {gap.get('title', '')}",
            "",
            f"**Verdict:** {VERDICT_LABEL.get(verdict, verdict)}  ",
            f"**Category:** {gap.get('category', '-')}  ",
            f"**Confidence:** {gap.get('confidence', '-')}  ",
            f"**Origin:** {gap.get('origin', '-')}",
            "",
            gap.get("description", ""),
            "",
            f"*Evidence in paper:* {gap.get('evidence_from_paper', '')}",
            "",
        ]
        if gap.get("reasoning"):
            lines += [f"*Literature check:* {gap['reasoning']}", ""]
        for work in gap.get("evidence") or []:
            authors = ", ".join(work.get("authors") or [])[:80]
            lines.append(
                f"- {authors} ({work.get('year', 'n.d.')}). *{work.get('title')}*. "
                f"{work.get('venue') or ''} {work.get('url') or ''}".strip()
            )
        if gap.get("what_remains"):
            lines += ["", f"**What remains:** {gap['what_remains']}", ""]
        lines.append("")

    lines += ["## Citation", ""]
    for style in ("ieee", "apa", "mla", "chicago"):
        lines += [f"**{style.upper()}** — {styles[style]}", ""]
    lines += ["```bibtex", styles["bibtex"], "```", ""]

    if grounding:
        lines += [
            "---",
            "",
            f"*Gap verdicts were checked against {grounding.get('corpus_size', 0)} papers "
            f"retrieved from OpenAlex, published from {grounding.get('paper_year', 'n.d.')} "
            "onward. This is a bounded corpus, not the whole literature.*",
        ]

    return "\n".join(lines)


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------


class _Report(FPDF):
    title_text = "ResearchAdvisorLM Report"

    def header(self) -> None:
        self.set_font("helvetica", "I", 8)
        self.set_text_color(130, 130, 130)
        self.cell(0, 6, _latin(self.title_text), align="R", new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def footer(self) -> None:
        self.set_y(-14)
        self.set_font("helvetica", "I", 8)
        self.set_text_color(130, 130, 130)
        self.cell(0, 8, f"Page {self.page_no()}/{{nb}}", align="C")


def pdf(paper: dict[str, Any]) -> bytes:
    analysis, metadata, gaps, grounding = _fields(paper)
    styles = citations.build(metadata)

    doc = _Report()
    doc.set_auto_page_break(auto=True, margin=18)
    doc.alias_nb_pages()
    doc.add_page()

    def write(height: float, text: str) -> None:
        """multi_cell leaves the cursor to the RIGHT by default, which starves
        the next call of horizontal space. Always return to the left margin."""
        doc.multi_cell(0, height, _latin(text), new_x="LMARGIN", new_y="NEXT")

    def heading(text: str, size: int = 12) -> None:
        doc.ln(3)
        doc.set_font("helvetica", "B", size)
        doc.set_text_color(20, 20, 20)
        write(6, text)
        doc.ln(1)

    def body(text: str, size: int = 10, italic: bool = False) -> None:
        doc.set_font("helvetica", "I" if italic else "", size)
        doc.set_text_color(45, 45, 45)
        write(5, text or "Not stated in this paper.")

    def bullets(items: list[str]) -> None:
        if not items:
            body("Not stated in this paper.")
            return
        for item in items:
            doc.set_font("helvetica", "", 10)
            doc.set_text_color(45, 45, 45)
            write(5, f"- {item}")

    doc.set_font("helvetica", "B", 16)
    doc.set_text_color(15, 15, 15)
    write(8, metadata.get("title") or paper.get("filename", "Paper"))
    doc.ln(1)

    doc.set_font("helvetica", "", 9)
    doc.set_text_color(110, 110, 110)
    meta_line = " | ".join(
        filter(
            None,
            [
                ", ".join(metadata.get("authors") or []) or None,
                str(metadata.get("year")) if metadata.get("year") else None,
                metadata.get("venue") or None,
                metadata.get("domain") or None,
                f"{paper.get('pages', '-')} pages",
            ],
        )
    )
    write(5, meta_line)

    heading("Summary")
    body(analysis.get("summary", {}).get("detailed", ""))
    heading("Objective")
    body(analysis.get("objective", ""))
    heading("Method")
    body(analysis.get("method", ""))
    heading("Datasets")
    bullets(analysis.get("datasets") or [])
    heading("Key findings")
    bullets(analysis.get("findings") or [])
    heading("Stated limitations")
    bullets(analysis.get("stated_limitations") or [])

    heading("Research gaps")
    if grounding:
        body(
            f"Checked against {grounding.get('corpus_size', 0)} papers retrieved from "
            f"OpenAlex, published from {grounding.get('paper_year', 'n.d.')} onward. "
            "A bounded corpus, not the whole literature.",
            size=9,
            italic=True,
        )

    for index, gap in enumerate(gaps, start=1):
        verdict = gap.get("verdict", "unverified")
        doc.ln(3)
        doc.set_font("helvetica", "B", 11)
        doc.set_text_color(20, 20, 20)
        write(5.5, f"Gap {index} - {gap.get('title', '')}")

        red, green, blue = VERDICT_RGB.get(verdict, (110, 110, 110))
        doc.set_font("helvetica", "B", 8)
        doc.set_text_color(red, green, blue)
        write(
            4.5,
            f"{VERDICT_LABEL.get(verdict, verdict)}  |  {gap.get('category', '-')}  |  "
            f"confidence: {gap.get('confidence', '-')}  |  {gap.get('origin', '-')}",
        )
        body(gap.get("description", ""))
        if gap.get("evidence_from_paper"):
            body(f"In the paper: {gap['evidence_from_paper']}", size=9, italic=True)
        if gap.get("reasoning"):
            body(f"Literature check: {gap['reasoning']}", size=9)
        for work in gap.get("evidence") or []:
            body(
                f"    * {work.get('title')} ({work.get('year', 'n.d.')}), "
                f"{work.get('citations', 0)} citations",
                size=8,
            )
        if gap.get("what_remains"):
            body(f"What remains: {gap['what_remains']}", size=9, italic=True)

    heading("Citation")
    for style in ("ieee", "apa", "mla", "chicago"):
        body(f"{style.upper()}: {styles[style]}", size=9)

    out = doc.output()
    return bytes(out)


# --------------------------------------------------------------------------
# DOCX
# --------------------------------------------------------------------------


def docx(paper: dict[str, Any]) -> bytes:
    analysis, metadata, gaps, grounding = _fields(paper)
    styles = citations.build(metadata)

    document = Document()
    document.add_heading(metadata.get("title") or paper.get("filename", "Paper"), level=0)

    meta_paragraph = document.add_paragraph()
    meta_run = meta_paragraph.add_run(
        " | ".join(
            filter(
                None,
                [
                    ", ".join(metadata.get("authors") or []) or None,
                    str(metadata.get("year")) if metadata.get("year") else None,
                    metadata.get("venue") or None,
                    metadata.get("domain") or None,
                    f"{paper.get('pages', '-')} pages",
                ],
            )
        )
    )
    meta_run.italic = True
    meta_run.font.size = Pt(9)
    meta_paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT

    def section(title: str, text: str) -> None:
        document.add_heading(title, level=1)
        document.add_paragraph(text or "Not stated in this paper.")

    def bullet_section(title: str, items: list[str]) -> None:
        document.add_heading(title, level=1)
        if not items:
            document.add_paragraph("Not stated in this paper.")
            return
        for item in items:
            document.add_paragraph(item, style="List Bullet")

    section("Summary", analysis.get("summary", {}).get("detailed", ""))
    section("Objective", analysis.get("objective", ""))
    section("Method", analysis.get("method", ""))
    bullet_section("Datasets", analysis.get("datasets") or [])
    bullet_section("Key findings", analysis.get("findings") or [])
    bullet_section("Stated limitations", analysis.get("stated_limitations") or [])

    document.add_heading("Research gaps", level=1)
    if grounding:
        note = document.add_paragraph()
        note_run = note.add_run(
            f"Checked against {grounding.get('corpus_size', 0)} papers retrieved from "
            f"OpenAlex, published from {grounding.get('paper_year', 'n.d.')} onward. "
            "A bounded corpus, not the whole literature."
        )
        note_run.italic = True
        note_run.font.size = Pt(9)

    for index, gap in enumerate(gaps, start=1):
        verdict = gap.get("verdict", "unverified")
        document.add_heading(f"Gap {index} - {gap.get('title', '')}", level=2)

        badge = document.add_paragraph()
        badge_run = badge.add_run(
            f"{VERDICT_LABEL.get(verdict, verdict)}  |  {gap.get('category', '-')}  |  "
            f"confidence: {gap.get('confidence', '-')}  |  {gap.get('origin', '-')}"
        )
        badge_run.bold = True
        badge_run.font.size = Pt(9)
        red, green, blue = VERDICT_RGB.get(verdict, (110, 110, 110))
        badge_run.font.color.rgb = RGBColor(red, green, blue)

        document.add_paragraph(gap.get("description", ""))
        if gap.get("evidence_from_paper"):
            quote = document.add_paragraph()
            quote_run = quote.add_run(f"In the paper: {gap['evidence_from_paper']}")
            quote_run.italic = True
        if gap.get("reasoning"):
            document.add_paragraph(f"Literature check: {gap['reasoning']}")
        for work in gap.get("evidence") or []:
            document.add_paragraph(
                f"{work.get('title')} ({work.get('year', 'n.d.')}), "
                f"{work.get('citations', 0)} citations",
                style="List Bullet",
            )
        if gap.get("what_remains"):
            remains = document.add_paragraph()
            remains_run = remains.add_run(f"What remains: {gap['what_remains']}")
            remains_run.italic = True

    document.add_heading("Citation", level=1)
    for style in ("ieee", "apa", "mla", "chicago"):
        document.add_paragraph(f"{style.upper()}: {styles[style]}")

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def safe_filename(paper: dict[str, Any], extension: str) -> str:
    _, metadata, _, _ = _fields(paper)
    stem = metadata.get("title") or paper.get("filename") or "report"
    stem = re.sub(r"[^A-Za-z0-9 \-_]", "", stem).strip().replace(" ", "-")[:60]
    return f"{stem or 'report'}.{extension}"
