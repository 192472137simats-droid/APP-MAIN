"""ResearchAdvisorLM - FastAPI backend + web client in one deployable service."""

import ipaddress
import re
import socket
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, File, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .config import LLM_API_KEY, LLM_MODEL, MAX_UPLOAD_MB, OCR_MAX_PAGES
from .services import citations as citation_service
from .services import export as export_service
from .services import analysis as analysis_service
from .services import literature
from .services import llm_service
from .services import pdf_service
from .services.llm_service import LLMError
from .services.pdf_service import PdfError

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="ResearchAdvisorLM API",
    description="AI research paper summarisation with literature-grounded gap analysis",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    db.init()


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


class ChatRequest(BaseModel):
    paper_id: str
    question: str = Field(min_length=1, max_length=2000)


class LibraryChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class CompareRequest(BaseModel):
    paper_ids: list[str] = Field(min_length=2, max_length=5)


class ReviewRequest(BaseModel):
    paper_ids: list[str] = Field(min_length=2, max_length=10)


class TopicRequest(BaseModel):
    gap_id: str


class ImportRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


# --------------------------------------------------------------------------
# URL import
# --------------------------------------------------------------------------

_ARXIV_ABS = re.compile(r"arxiv\.org/abs/([^\s?#]+)", re.IGNORECASE)
_ARXIV_ID = re.compile(r"^(?:arxiv:)?(\d{4}\.\d{4,5}(?:v\d+)?)$", re.IGNORECASE)
_DOI = re.compile(r"^(?:doi:)?(10\.\d{4,9}/\S+)$", re.IGNORECASE)


def _normalise_paper_url(raw: str) -> str:
    """Accept arXiv IDs/links and DOIs, and refuse anything that isn't public http(s).

    The server fetches whatever URL it is handed, so this also has to stop
    requests being aimed at private network addresses.
    """
    if match := _ARXIV_ID.match(raw):
        return f"https://arxiv.org/pdf/{match.group(1)}"
    if match := _DOI.match(raw):
        return f"https://doi.org/{match.group(1)}"
    if match := _ARXIV_ABS.search(raw):
        return f"https://arxiv.org/pdf/{match.group(1)}"

    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(400, "Only http and https links are supported.")
    if not parsed.hostname:
        raise HTTPException(400, "That does not look like a valid URL.")

    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise HTTPException(400, f"Could not resolve that host: {exc}") from exc

    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
        ):
            raise HTTPException(400, "That host is not publicly reachable.")

    return parsed.geturl()


# --------------------------------------------------------------------------
# Ingestion shared by file upload and URL import
# --------------------------------------------------------------------------


async def _ingest(data: bytes, filename: str) -> dict[str, Any]:
    if not data:
        raise HTTPException(400, "The uploaded file is empty.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(422, "That file is not a valid PDF.")

    try:
        extracted = pdf_service.extract(data, filename)
    except PdfError as exc:
        raise HTTPException(422, str(exc)) from exc

    # No text layer - fall back to reading rendered pages with the vision model.
    if extracted["needs_ocr"]:
        try:
            images = pdf_service.render_pages(data, max_pages=OCR_MAX_PAGES)
            text = await analysis_service.transcribe_pages(images)
        except (PdfError, LLMError) as exc:
            raise HTTPException(
                422,
                "This looks like a scanned PDF and it could not be transcribed: "
                f"{exc}",
            ) from exc

        if len(text.strip()) < 200:
            raise HTTPException(
                422, "This PDF appears to contain no readable text, even after OCR."
            )

        warnings = [
            f"No text layer found - pages were transcribed with OCR"
            + (f" (first {OCR_MAX_PAGES} pages only)" if extracted["pages"] > OCR_MAX_PAGES else "")
            + ". Accuracy may be lower than for a digital PDF."
        ]
        extracted = pdf_service.assemble(
            text,
            extracted["pages"],
            filename,
            pdf_title=extracted["pdf_title"],
            warnings=warnings,
            source="ocr",
        )

    try:
        result = await analysis_service.analyse_paper(extracted["body"])
    except LLMError as exc:
        raise HTTPException(502, f"AI analysis failed: {exc}") from exc

    paper_id = uuid.uuid4().hex[:12]
    metadata = result.get("metadata") or {}
    title = metadata.get("title") or extracted["pdf_title"] or filename

    db.save_paper(
        paper_id=paper_id,
        filename=filename,
        pages=extracted["pages"],
        characters=extracted["characters"],
        title=title,
        full_text=extracted["text"],
        sections=extracted["sections"],
        analysis=result,
    )

    return {
        "paper_id": paper_id,
        "filename": filename,
        "pages": extracted["pages"],
        "characters": extracted["characters"],
        "sections_detected": extracted["section_names"],
        "warnings": extracted["warnings"],
        "source": extracted["source"],
        "analysis": result,
    }


# --------------------------------------------------------------------------
# Meta
# --------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model": LLM_MODEL,
        "resolved_model": llm_service.resolved_model_name(),
        "key_configured": bool(LLM_API_KEY),
        "gemini_key_configured": bool(LLM_API_KEY),  # deprecated alias
        "papers": len(db.list_papers()),
    }


@app.get("/api/models")
async def models() -> dict[str, Any]:
    """Which models this API key can actually call.

    Google retires model aliases without notice, so a name that worked last
    month can start returning 404. This reports the live list and the model
    the app has settled on.
    """
    try:
        available = await llm_service.available_models()
        selected = await llm_service.resolve_model()
    except LLMError as exc:
        raise HTTPException(502, str(exc)) from exc

    return {
        "configured": LLM_MODEL,
        "selected": selected,
        "configured_is_available": any(m["name"] == LLM_MODEL for m in available),
        "available": available,
    }


# --------------------------------------------------------------------------
# Papers
# --------------------------------------------------------------------------


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> dict[str, Any]:
    """Stage 1: extract the PDF and run the structured analysis."""
    filename = file.filename or "paper.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported.")

    # Read in chunks and stop at the limit - reading first and checking after
    # lets an oversized upload exhaust memory before we can reject it.
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(413, f"File exceeds the {MAX_UPLOAD_MB} MB limit.")
        chunks.append(chunk)

    return await _ingest(b"".join(chunks), filename)


@app.post("/api/import")
async def import_from_url(request: ImportRequest) -> dict[str, Any]:
    """Ingest a paper straight from an arXiv or DOI link."""
    url = _normalise_paper_url(request.url.strip())

    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "ResearchAdvisorLM/1.0"})
    except httpx.RequestError as exc:
        raise HTTPException(502, f"Could not fetch that URL: {exc}") from exc

    if response.status_code != 200:
        raise HTTPException(502, f"That URL returned HTTP {response.status_code}.")

    data = response.content
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"That PDF exceeds the {MAX_UPLOAD_MB} MB limit.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(
            422,
            "That link did not return a PDF. Use a direct PDF link, an arXiv "
            "abstract/PDF URL, or a DOI.",
        )

    name = url.rstrip("/").split("/")[-1] or "paper"
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    return await _ingest(data, name)


@app.post("/api/papers/{paper_id}/ground")
async def ground(paper_id: str) -> dict[str, Any]:
    """Stage 2: check each gap against later literature."""
    paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(404, "Paper not found.")
    if not paper.get("analysis"):
        raise HTTPException(409, "This paper has not been analysed yet.")

    try:
        result = await analysis_service.ground_gaps(paper["analysis"])
    except LLMError as exc:
        raise HTTPException(502, f"Gap validation failed: {exc}") from exc

    db.save_grounding(paper_id, result)
    return result


@app.get("/api/papers")
def papers() -> list[dict[str, Any]]:
    return db.list_papers()


@app.get("/api/papers/{paper_id}")
def paper_detail(paper_id: str, include_text: bool = False) -> dict[str, Any]:
    """include_text adds the full extracted text - large, so opt-in only."""
    paper = db.get_paper(paper_id, include_text=include_text)
    if not paper:
        raise HTTPException(404, "Paper not found.")
    paper["messages"] = db.get_messages(paper_id)
    return paper


@app.delete("/api/papers/{paper_id}")
def paper_delete(paper_id: str) -> dict[str, str]:
    if not db.get_paper(paper_id):
        raise HTTPException(404, "Paper not found.")
    db.delete_paper(paper_id)
    return {"status": "deleted"}


# --------------------------------------------------------------------------
# Chat
# --------------------------------------------------------------------------


@app.post("/api/chat")
async def chat(request: ChatRequest) -> dict[str, str]:
    paper = db.get_paper(request.paper_id, include_text=True)
    if not paper:
        raise HTTPException(404, "Paper not found.")

    history = db.get_messages(request.paper_id, limit=12)
    try:
        answer = await analysis_service.answer_question(request.question, paper, history)
    except LLMError as exc:
        raise HTTPException(502, f"Chat failed: {exc}") from exc

    db.add_message(request.paper_id, "user", request.question)
    db.add_message(request.paper_id, "assistant", answer)
    return {"answer": answer}


# --------------------------------------------------------------------------
# Compare
# --------------------------------------------------------------------------


@app.post("/api/chat/library")
async def chat_library(request: LibraryChatRequest) -> dict[str, Any]:
    """Ask a question across every paper in the library."""
    papers = [db.get_paper(item["id"]) for item in db.list_papers()]
    papers = [p for p in papers if p]

    try:
        answer = await analysis_service.answer_across_library(request.question, papers)
    except LLMError as exc:
        raise HTTPException(502, f"Library chat failed: {exc}") from exc

    return {"answer": answer, "papers_searched": len(papers)}


@app.post("/api/compare")
async def compare(request: CompareRequest) -> dict[str, Any]:
    papers_data = []
    for paper_id in request.paper_ids:
        paper = db.get_paper(paper_id)
        if not paper:
            raise HTTPException(404, f"Paper {paper_id} not found.")
        papers_data.append(paper)

    try:
        result = await analysis_service.compare_papers(papers_data)
    except LLMError as exc:
        raise HTTPException(502, f"Comparison failed: {exc}") from exc

    result["papers"] = [
        {
            "id": p["id"],
            "title": (p.get("analysis") or {}).get("metadata", {}).get("title", p["filename"]),
            "year": (p.get("analysis") or {}).get("metadata", {}).get("year"),
        }
        for p in papers_data
    ]
    return result


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------


@app.get("/api/papers/{paper_id}/citation")
def citation(paper_id: str) -> dict[str, Any]:
    """IEEE / APA / MLA / Chicago / BibTeX for one paper."""
    paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(404, "Paper not found.")
    metadata = (paper.get("analysis") or {}).get("metadata") or {}
    return citation_service.build(metadata)


@app.get("/api/papers/{paper_id}/export")
def export_report(paper_id: str, format: str = "md") -> Response:
    """Download the report as markdown, pdf or docx."""
    paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(404, "Paper not found.")

    fmt = format.lower().lstrip(".")
    if fmt in ("md", "markdown"):
        content: bytes = export_service.markdown(paper).encode("utf-8")
        media = "text/markdown; charset=utf-8"
        extension = "md"
    elif fmt == "pdf":
        content = export_service.pdf(paper)
        media = "application/pdf"
        extension = "pdf"
    elif fmt == "docx":
        content = export_service.docx(paper)
        media = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        extension = "docx"
    else:
        raise HTTPException(400, "format must be one of: md, pdf, docx")

    filename = export_service.safe_filename(paper, extension)
    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --------------------------------------------------------------------------
# Research topic generator
# --------------------------------------------------------------------------


@app.post("/api/papers/{paper_id}/topic")
async def topic(paper_id: str, request: TopicRequest) -> dict[str, Any]:
    """Turn one open gap into a concrete project proposal."""
    paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(404, "Paper not found.")

    analysis = paper.get("analysis") or {}
    gaps = (paper.get("grounding") or {}).get("gaps") or analysis.get("gaps") or []
    gap = next((g for g in gaps if g.get("id") == request.gap_id), None)
    if not gap:
        raise HTTPException(
            404, f"No gap {request.gap_id!r} on this paper. Known: {[g.get('id') for g in gaps]}"
        )

    try:
        result = await analysis_service.generate_topic(analysis, gap)
    except LLMError as exc:
        raise HTTPException(502, f"Topic generation failed: {exc}") from exc

    result["gap"] = {"id": gap.get("id"), "title": gap.get("title"), "verdict": gap.get("verdict")}
    return result


# --------------------------------------------------------------------------
# Literature review
# --------------------------------------------------------------------------


@app.post("/api/review")
async def review(request: ReviewRequest) -> dict[str, Any]:
    """Generate a Related Work chapter across several papers."""
    papers_data = []
    for paper_id in request.paper_ids:
        paper = db.get_paper(paper_id)
        if not paper:
            raise HTTPException(404, f"Paper {paper_id} not found.")
        papers_data.append(paper)

    try:
        return await analysis_service.generate_review(papers_data)
    except LLMError as exc:
        raise HTTPException(502, f"Literature review failed: {exc}") from exc


# --------------------------------------------------------------------------
# Discover (Step 2) - topic -> ranked candidate papers.
# Read-only: no SQLite writes, no LLM, no analysis, no grounding.
# --------------------------------------------------------------------------


@app.get("/api/discover/search")
async def discover_search(q: str = "", limit: int = 20) -> dict[str, Any]:
    """Rank candidate papers for a topic query, straight from OpenAlex."""
    query = (q or "").strip()
    if not query:
        raise HTTPException(400, "Query 'q' must not be empty.")
    if len(query) > 300:
        raise HTTPException(400, "Query 'q' is too long (max 300 characters).")

    try:
        results = await literature.search_topic(query, limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    return {"query": query, "count": len(results), "results": results}


# --------------------------------------------------------------------------
# Static client (mounted last so /api/* wins)
# --------------------------------------------------------------------------

if STATIC_DIR.exists():

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
