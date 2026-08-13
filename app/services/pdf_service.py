"""PDF text extraction and section segmentation.

Reading order matters: two-column IEEE/ACM layouts interleave badly with naive
extraction, so we sort blocks by column then by vertical position.
"""

import re
from typing import Any

import fitz  # PyMuPDF

from ..config import MAX_PAPER_CHARS

# Canonical section names we try to recognise, in the order they usually appear.
SECTION_PATTERNS: list[tuple[str, str]] = [
    ("abstract", r"abstract"),
    ("introduction", r"(introduction|background)"),
    ("related_work", r"(related\s+works?|literature\s+(review|survey)|prior\s+work)"),
    ("method", r"(method(s|ology)?|approach|proposed\s+(method|model|system|approach|framework)|architecture|system\s+design)"),
    ("experiments", r"(experiments?|experimental\s+(setup|design)|implementation|dataset|data\s+collection|evaluation\s+setup)"),
    ("results", r"(results?|findings|evaluation|performance|analysis)"),
    ("discussion", r"(discussion|ablation)"),
    ("limitations", r"(limitations?|threats\s+to\s+validity|shortcomings)"),
    ("conclusion", r"(conclusions?|concluding\s+remarks?|summary)"),
    ("future_work", r"(future\s+(work|research|directions?|scope)|further\s+work)"),
    ("references", r"(references?|bibliography|works\s+cited)"),
]

# A heading line: optional numbering, a short title, nothing else.
# Roman numerals must carry a separator ("IV. Results") - otherwise the pattern
# eats the leading letter of words like "Introduction" or "Limitations".
_HEADING_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*[\.\)]?\s+|[IVXLC]{1,5}[\.\)]\s+)?"
    r"([A-Za-z][A-Za-z \-&/]{2,60})\s*:?\s*$"
)

# Compound headings are common ("Conclusion and Future Work", "Results and
# Discussion"), so match most-specific first rather than in document order.
_MATCH_ORDER = [
    "future_work",
    "limitations",
    "related_work",
    "abstract",
    "introduction",
    "method",
    "experiments",
    "results",
    "discussion",
    "conclusion",
    "references",
]


class PdfError(Exception):
    """Raised when a PDF cannot be turned into usable text."""


def _page_text(page: "fitz.Page") -> str:
    """Extract a page in reading order, column-aware."""
    blocks = page.get_text("blocks")  # (x0, y0, x1, y1, text, block_no, block_type)
    text_blocks = [b for b in blocks if len(b) >= 5 and b[4] and b[4].strip()]
    if not text_blocks:
        return ""

    width = page.rect.width
    mid = width / 2
    slack = width * 0.05

    # Blocks that cross the page centre (title, author line, wide figures)
    # belong to neither column. They must be kept separately - dropping them
    # loses the entire paper header on every two-column paper.
    spanning = [b for b in text_blocks if b[0] < mid - slack and b[2] > mid + slack]
    spanning_ids = {id(b) for b in spanning}
    left = [b for b in text_blocks if id(b) not in spanning_ids and b[2] <= mid + slack]
    right = [b for b in text_blocks if id(b) not in spanning_ids and b[0] >= mid - slack]

    two_column = len(left) >= 3 and len(right) >= 3

    if two_column:
        # Anything above the columns is the header and reads first; anything
        # below (wide figures, footnotes) reads last.
        column_top = min(b[1] for b in left + right)
        header = sorted((b for b in spanning if b[1] < column_top), key=lambda b: b[1])
        footer = sorted((b for b in spanning if b[1] >= column_top), key=lambda b: b[1])
        ordered = (
            header
            + sorted(left, key=lambda b: b[1])
            + sorted(right, key=lambda b: b[1])
            + footer
        )
    else:
        ordered = sorted(text_blocks, key=lambda b: (round(b[1], 1), b[0]))

    return "\n".join(b[4].strip() for b in ordered)


def _classify_heading(line: str) -> str | None:
    match = _HEADING_RE.match(line)
    if not match:
        return None
    title = re.sub(r"\s+", " ", match.group(1)).strip().lower()
    if len(title) > 60:
        return None

    patterns = dict(SECTION_PATTERNS)
    for name in _MATCH_ORDER:
        if re.search(rf"\b(?:{patterns[name]})\b", title, flags=re.IGNORECASE):
            return name
    return None


def segment_sections(text: str) -> dict[str, str]:
    """Split flat text into named sections. Unmatched preamble lands in 'header'."""
    sections: dict[str, list[str]] = {"header": []}
    current = "header"
    for line in text.split("\n"):
        heading = _classify_heading(line)
        if heading:
            current = heading
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items() if "".join(v).strip()}


def assemble(
    text: str,
    page_count: int,
    filename: str,
    *,
    pdf_title: str = "",
    warnings: list[str] | None = None,
    source: str = "text-layer",
    needs_ocr: bool = False,
) -> dict[str, Any]:
    """Shared post-processing for both the text layer and OCR output."""
    warnings = list(warnings or [])

    sections = segment_sections(text)
    if "references" not in sections:
        warnings.append("No reference section detected; citation parsing may be limited.")

    # Body text sent to the model: drop references, they burn tokens for nothing.
    body = "\n\n".join(
        f"## {name.replace('_', ' ').title()}\n{content}"
        for name, content in sections.items()
        if name not in {"references"}
    ) or text

    truncated = False
    if len(body) > MAX_PAPER_CHARS:
        body = body[:MAX_PAPER_CHARS]
        truncated = True
        warnings.append(
            f"Paper is very long; analysis used the first {MAX_PAPER_CHARS:,} characters."
        )

    return {
        "filename": filename,
        "pages": page_count,
        "characters": len(text),
        "text": text,
        "body": body,
        "sections": sections,
        "section_names": list(sections.keys()),
        "pdf_title": pdf_title,
        "warnings": warnings,
        "truncated": truncated,
        "source": source,
        "needs_ocr": needs_ocr,
    }


def extract(data: bytes, filename: str = "paper.pdf") -> dict[str, Any]:
    """Turn PDF bytes into text + sections + warnings.

    If the PDF has no usable text layer this returns needs_ocr=True rather than
    failing, so the caller can fall back to vision transcription.
    """
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - surface any parser failure the same way
        raise PdfError(f"Could not open the PDF: {exc}") from exc

    if doc.needs_pass:
        raise PdfError("This PDF is password protected.")

    # Page markers let the model cite where a claim came from.
    pages = []
    for number, page in enumerate(doc, start=1):
        page_text = _page_text(page)
        if page_text:
            pages.append(f"[page {number}]\n{page_text}")

    page_count = doc.page_count
    doc_meta = doc.metadata or {}
    doc.close()

    text = "\n\n".join(pages).strip()
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    pdf_title = (doc_meta.get("title") or "").strip()

    # Roughly the marker text alone - nothing real was extracted.
    if len(re.sub(r"\[page \d+\]", "", text).strip()) < 200:
        return assemble(
            "", page_count, filename, pdf_title=pdf_title, source="none", needs_ocr=True
        )

    warnings: list[str] = []
    if len(text) / max(page_count, 1) < 400:
        warnings.append(
            "Low text density - parts of this PDF may be images and were not read."
        )

    return assemble(
        text, page_count, filename, pdf_title=pdf_title, warnings=warnings
    )


def render_pages(data: bytes, max_pages: int = 20, dpi: int = 140) -> list[bytes]:
    """Rasterise pages to JPEG for vision transcription of scanned PDFs."""
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise PdfError(f"Could not open the PDF: {exc}") from exc

    images: list[bytes] = []
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    for index, page in enumerate(doc):
        if index >= max_pages:
            break
        pixmap = page.get_pixmap(matrix=matrix)
        images.append(pixmap.tobytes("jpeg", jpg_quality=75))
    doc.close()
    return images
