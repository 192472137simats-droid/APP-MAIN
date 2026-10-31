"""ResearchAdvisorLM - FastAPI backend + web client in one deployable service."""

import ipaddress
import logging
import os
import re
import socket
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .config import CPP_CORE_URL, LLM_API_KEY, LLM_MODEL, MAX_UPLOAD_MB, OCR_MAX_PAGES
from .services import citations as citation_service
from .services import export as export_service
from .services import analysis as analysis_service
from .services import auth
from .services import cpp_core
from .services import discovery
from .services import literature
from .services import llm_service
from .services import pdf_service
from .services.llm_service import LLMError
from .services.pdf_service import PdfError

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="ResearchAdvisorLM API",
    description="AI research paper summarisation with literature-grounded gap analysis",
    version="1.0.0",
)

# Credentials now ride on a cookie, so "*" is no longer allowed: browsers
# reject wildcard origins on credentialed requests, and it would let any site
# drive this API as the signed-in user. Set ALLOWED_ORIGINS for a separate
# frontend; same-origin (the bundled client) needs no entry.
_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# Authentication and rate limiting
#
# Everything under /api is private except health and the auth routes. This app
# holds a user's library and spends real model credits, so an open endpoint is
# both a data leak and someone else's bill.
# --------------------------------------------------------------------------

_PUBLIC_PATHS = {"/api/health", "/api/auth/login", "/api/auth/register", "/api/auth/me"}

# Per-IP ceilings on the routes that cost money or CPU. Fixed windows held in
# memory: adequate for one instance, and honest about it - a multi-replica
# deployment would need shared state.
_RATE_LIMITS = {
    "/api/discover/run": (5, 3600),
    "/api/discover/projects": (30, 3600),
    "/api/upload": (20, 3600),
    "/api/import": (20, 3600),
    "/api/auth/login": (10, 900),
    "/api/auth/register": (5, 3600),
}
_rate_state: dict[str, list[float]] = {}
_rate_lock = threading.Lock()


def _rate_rule(path: str) -> tuple[str, int, int] | None:
    for prefix, (limit, window) in _RATE_LIMITS.items():
        if path == prefix or path.startswith(prefix + "/"):
            return prefix, limit, window
    return None


def _rate_limited(client: str, path: str) -> bool:
    rule = _rate_rule(path)
    if rule is None:
        return False
    prefix, limit, window = rule
    now = time.time()
    key = f"{client}|{prefix}"
    with _rate_lock:
        hits = [t for t in _rate_state.get(key, []) if now - t < window]
        if len(hits) >= limit:
            _rate_state[key] = hits
            return True
        hits.append(now)
        _rate_state[key] = hits
    return False


def current_user(request: Request) -> dict[str, Any] | None:
    """The signed-in user, or None. Accepts a cookie or a bearer token, so a
    native client can authenticate without cookie handling."""
    token = request.cookies.get(auth.SESSION_COOKIE)
    if not token:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            token = header[7:].strip()
    return auth.user_for_token(token)


def require_user(request: Request) -> dict[str, Any]:
    user = current_user(request)
    if user is None:
        raise HTTPException(401, "Sign in to continue.")
    return user


@app.middleware("http")
async def _guard(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/"):
        client = request.client.host if request.client else "unknown"
        if _rate_limited(client, path):
            return JSONResponse(
                {"detail": "Too many requests. Wait a while and try again."},
                status_code=429,
            )
        if path not in _PUBLIC_PATHS and current_user(request) is None:
            return JSONResponse({"detail": "Sign in to continue."}, status_code=401)
    return await call_next(request)


@app.on_event("startup")
def _startup() -> None:
    db.init()
    # Discovery runs live in this process; any left "running" died with the
    # previous one and would otherwise poll for ever.
    db.reap_orphaned_runs()
    db.reap_stalled_analyses()
    db.purge_expired_sessions()


# --------------------------------------------------------------------------
# Auth endpoints
# --------------------------------------------------------------------------


class CredentialsRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


def _issue_session(user_id: str, response: Response) -> str:
    token = auth.start_session(user_id)
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        max_age=auth.SESSION_DAYS * 24 * 3600,
        httponly=True,          # unreadable from JavaScript, so XSS cannot lift it
        samesite="lax",         # blocks the cookie on cross-site form posts
        secure=not os.getenv("ALLOW_INSECURE_COOKIE"),
    )
    return token


@app.post("/api/auth/register")
async def register(request: CredentialsRequest, response: Response) -> dict[str, Any]:
    try:
        user = auth.register(request.email, request.password)
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc)) from exc

    token = _issue_session(user["id"], response)
    return {
        "user": {"id": user["id"], "email": user["email"]},
        "claimed": user["claimed"],
        "token": token,
    }


@app.post("/api/auth/login")
async def login(request: CredentialsRequest, response: Response) -> dict[str, Any]:
    try:
        user = auth.authenticate(request.email, request.password)
    except auth.AuthError as exc:
        raise HTTPException(401, str(exc)) from exc

    token = _issue_session(user["id"], response)
    return {"user": user, "token": token}


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response) -> dict[str, Any]:
    auth.end_session(request.cookies.get(auth.SESSION_COOKIE) or "")
    response.delete_cookie(auth.SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/auth/me")
async def me(request: Request) -> dict[str, Any]:
    """Who am I, and does anyone exist yet?

    `setup_needed` lets the client show "create the first account" instead of
    a login form on a fresh deployment.
    """
    return {"user": current_user(request), "setup_needed": db.count_users() == 0}


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


# DOI links legitimately chain (doi.org -> publisher -> CDN), so allow a few.
_MAX_REDIRECTS = 5


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


async def _ingest(
    data: bytes, filename: str, owner_id: str, background: BackgroundTasks
) -> dict[str, Any]:
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

    paper_id = uuid.uuid4().hex[:12]

    # The paper is stored before it is analysed. Analysis needs many minutes on
    # a slow model and a platform proxy cuts the request long before that -
    # which is exactly how upload failed with a 502 after 300s. The row exists
    # immediately, the client polls, and _analyse_in_background completes it.
    db.save_paper(
        paper_id=paper_id,
        filename=filename,
        pages=extracted["pages"],
        characters=extracted["characters"],
        title=extracted["pdf_title"] or filename,
        full_text=extracted["text"],
        sections=extracted["sections"],
        analysis={},
        owner_id=owner_id,
        analysis_status="pending",
    )
    background.add_task(_analyse_in_background, paper_id, extracted["body"])

    return {
        "paper_id": paper_id,
        "filename": filename,
        "pages": extracted["pages"],
        "characters": extracted["characters"],
        "sections_detected": extracted["section_names"],
        "warnings": extracted["warnings"],
        "source": extracted["source"],
        "analysis": None,
        "analysis_status": "pending",
    }


@app.post("/api/papers/{paper_id}/reanalyse")
async def reanalyse(
    paper_id: str,
    background: BackgroundTasks,
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    """Re-run analysis for a paper whose first attempt failed or was cut short.

    A deploy or crash mid-analysis leaves a paper unusable, and re-uploading the
    PDF was the only way back. The extracted text is already stored, so a retry
    costs one model call and no upload.
    """
    paper = db.get_paper(paper_id, include_text=True, owner_id=user["id"])
    if paper is None:
        raise HTTPException(404, "Paper not found.")
    if paper.get("analysis_status") == "pending":
        raise HTTPException(409, "This paper is already being analysed.")

    body = paper.get("full_text") or ""
    if not body.strip():
        raise HTTPException(422, "No extracted text is stored for this paper.")

    db.mark_analysis_pending(paper_id)
    background.add_task(_analyse_in_background, paper_id, body)
    return {"paper_id": paper_id, "analysis_status": "pending"}


async def _analyse_in_background(paper_id: str, body: str) -> None:
    """Analyse an already-stored paper, then complete its row.

    Every exit path writes a terminal status; otherwise the client would poll a
    paper that says "pending" for ever.
    """
    try:
        if CPP_CORE_URL:
            result = await cpp_core.analyse_paper(body)
        else:
            result = await analysis_service.analyse_paper(body)
    except LLMError as exc:
        logger.warning("Analysis failed for paper %s: %s", paper_id, exc)
        db.set_analysis(paper_id, None, f"AI analysis failed: {exc}")
        return
    except Exception as exc:  # noqa: BLE001 - a crash must not strand the paper
        logger.exception("Unexpected analysis failure for paper %s", paper_id)
        db.set_analysis(paper_id, None, f"Unexpected error: {type(exc).__name__}")
        return

    db.set_analysis(paper_id, result)
    title = (result.get("metadata") or {}).get("title")
    if title:
        db.set_title(paper_id, title)


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
async def upload(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    user: dict = Depends(require_user),
) -> dict[str, Any]:
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

    return await _ingest(b"".join(chunks), filename, user["id"], background)


@app.post("/api/import")
async def import_from_url(
    request: ImportRequest,
    background: BackgroundTasks,
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    """Ingest a paper straight from an arXiv or DOI link."""
    url = _normalise_paper_url(request.url.strip())
    limit = MAX_UPLOAD_MB * 1024 * 1024

    # Redirects are followed by hand: httpx's follow_redirects would leave only
    # the first URL vetted, so a public link could bounce the fetch to
    # 169.254.169.254 or a private host and defeat _normalise_paper_url.
    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
            for _ in range(_MAX_REDIRECTS + 1):
                async with client.stream(
                    "GET", url, headers={"User-Agent": "ResearchAdvisorLM/1.0"}
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise HTTPException(502, "That URL redirected without a target.")
                        url = _normalise_paper_url(str(response.url.join(location)))
                        continue

                    if response.status_code != 200:
                        raise HTTPException(
                            502, f"That URL returned HTTP {response.status_code}."
                        )

                    # Read incrementally: trusting Content-Length would let a
                    # hostile server stream unbounded data into memory.
                    chunks, total = [], 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > limit:
                            raise HTTPException(
                                413, f"That PDF exceeds the {MAX_UPLOAD_MB} MB limit."
                            )
                        chunks.append(chunk)
                    data = b"".join(chunks)
                    break
            else:
                raise HTTPException(502, "That URL redirected too many times.")
    except httpx.RequestError as exc:
        raise HTTPException(502, f"Could not fetch that URL: {exc}") from exc
    if not data.startswith(b"%PDF"):
        raise HTTPException(
            422,
            "That link did not return a PDF. Use a direct PDF link, an arXiv "
            "abstract/PDF URL, or a DOI.",
        )

    name = url.rstrip("/").split("/")[-1] or "paper"
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    return await _ingest(data, name, user["id"], background)


@app.post("/api/papers/{paper_id}/ground")
async def ground(paper_id: str, user: dict = Depends(require_user)) -> dict[str, Any]:
    """Stage 2: check each gap against later literature."""
    paper = db.get_paper(paper_id, owner_id=user["id"])
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
def papers(user: dict = Depends(require_user)) -> list[dict[str, Any]]:
    return db.list_papers(owner_id=user["id"])


@app.get("/api/papers/{paper_id}")
def paper_detail(
    paper_id: str, include_text: bool = False, user: dict = Depends(require_user)
) -> dict[str, Any]:
    """include_text adds the full extracted text - large, so opt-in only."""
    paper = db.get_paper(paper_id, include_text=include_text, owner_id=user["id"])
    if not paper:
        raise HTTPException(404, "Paper not found.")
    paper["messages"] = db.get_messages(paper_id)
    return paper


@app.delete("/api/papers/{paper_id}")
def paper_delete(paper_id: str, user: dict = Depends(require_user)) -> dict[str, str]:
    if not db.get_paper(paper_id, owner_id=user["id"]):
        raise HTTPException(404, "Paper not found.")
    db.delete_paper(paper_id, owner_id=user["id"])
    return {"status": "deleted"}


# --------------------------------------------------------------------------
# Chat
# --------------------------------------------------------------------------


@app.post("/api/chat")
async def chat(
    request: ChatRequest, user: dict = Depends(require_user)
) -> dict[str, str]:
    paper = db.get_paper(request.paper_id, include_text=True, owner_id=user["id"])
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
async def chat_library(
    request: LibraryChatRequest, user: dict = Depends(require_user)
) -> dict[str, Any]:
    """Ask a question across every paper in the library."""
    papers = [
        db.get_paper(item["id"], owner_id=user["id"])
        for item in db.list_papers(owner_id=user["id"])
    ]
    papers = [p for p in papers if p]

    try:
        answer = await analysis_service.answer_across_library(request.question, papers)
    except LLMError as exc:
        raise HTTPException(502, f"Library chat failed: {exc}") from exc

    return {"answer": answer, "papers_searched": len(papers)}


@app.post("/api/compare")
async def compare(
    request: CompareRequest, user: dict = Depends(require_user)
) -> dict[str, Any]:
    papers_data = []
    for paper_id in request.paper_ids:
        paper = db.get_paper(paper_id, owner_id=user["id"])
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
def citation(paper_id: str, user: dict = Depends(require_user)) -> dict[str, Any]:
    """IEEE / APA / MLA / Chicago / BibTeX for one paper."""
    paper = db.get_paper(paper_id, owner_id=user["id"])
    if not paper:
        raise HTTPException(404, "Paper not found.")
    metadata = (paper.get("analysis") or {}).get("metadata") or {}
    return citation_service.build(metadata)


@app.get("/api/papers/{paper_id}/export")
def export_report(
    paper_id: str, format: str = "md", user: dict = Depends(require_user)
) -> Response:
    """Download the report as markdown, pdf or docx."""
    paper = db.get_paper(paper_id, owner_id=user["id"])
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
async def topic(
    paper_id: str, request: TopicRequest, user: dict = Depends(require_user)
) -> dict[str, Any]:
    """Turn one open gap into a concrete project proposal."""
    paper = db.get_paper(paper_id, owner_id=user["id"])
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
async def review(
    request: ReviewRequest, user: dict = Depends(require_user)
) -> dict[str, Any]:
    """Generate a Related Work chapter across several papers."""
    papers_data = []
    for paper_id in request.paper_ids:
        paper = db.get_paper(paper_id, owner_id=user["id"])
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


class DiscoverRunRequest(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    goal: str = Field(default="gaps", max_length=40)
    max_papers: int = Field(default=6, ge=1, le=50)
    max_validations: int = Field(default=6, ge=1, le=20)


@app.post("/api/discover/run")
async def discover_run(request: DiscoverRunRequest) -> dict[str, Any]:
    """Stage 6: run the bounded agentic discovery workflow for a topic.

    Reuses paper analysis, OpenAlex grounding and the LLM service. Bounded by
    hard caps (papers, LLM calls, iterations, wall-clock) so it can never run
    away. Does not modify any existing paper/library data.
    """
    try:
        return await discovery.run_discovery(
            request.query,
            goal=request.goal,
            max_papers=request.max_papers,
            max_validations=request.max_validations,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(502, f"Discovery failed: {exc}") from exc


# --------------------------------------------------------------------------
# Discovery projects (Stage 7) - save a research question, run discovery
# against it in the background, leave, come back and read the result.
# --------------------------------------------------------------------------


class ProjectRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=300)
    goal: str = Field(default="gaps", max_length=40)


class ProjectRunRequest(BaseModel):
    max_papers: int = Field(default=6, ge=1, le=50)
    max_validations: int = Field(default=6, ge=1, le=20)


@app.post("/api/discover/projects")
async def create_project(
    request: ProjectRequest, user: dict = Depends(require_user)
) -> dict[str, Any]:
    project_id = db.create_project(
        request.topic.strip(), request.goal.strip() or "gaps", owner_id=user["id"]
    )
    return {"project": db.get_project(project_id, owner_id=user["id"])}


@app.get("/api/discover/projects")
async def list_projects(user: dict = Depends(require_user)) -> dict[str, Any]:
    projects = db.list_projects(owner_id=user["id"])
    return {"count": len(projects), "projects": projects}


@app.get("/api/discover/projects/{project_id}")
async def get_project(
    project_id: str, user: dict = Depends(require_user)
) -> dict[str, Any]:
    project = db.get_project(project_id, owner_id=user["id"])
    if project is None:
        raise HTTPException(404, "Project not found.")
    return {"project": project, "runs": db.list_runs(project_id)}


@app.delete("/api/discover/projects/{project_id}")
async def delete_project(
    project_id: str, user: dict = Depends(require_user)
) -> dict[str, Any]:
    if db.get_project(project_id, owner_id=user["id"]) is None:
        raise HTTPException(404, "Project not found.")
    db.delete_project(project_id, owner_id=user["id"])
    return {"deleted": project_id}


async def _execute_run(
    run_id: str, project: dict[str, Any], max_papers: int, max_validations: int
) -> None:
    """Drive one discovery run, streaming progress into the run's trace.

    Runs detached from the request, so a full pipeline (many LLM calls) never
    holds an HTTP connection open. Every exit path writes a terminal status,
    otherwise a run would sit at `running` for ever.
    """
    try:
        result = await discovery.run_discovery(
            project["topic"],
            goal=project["goal"],
            max_papers=max_papers,
            max_validations=max_validations,
            on_progress=lambda step, data: db.append_run_step(run_id, step, data),
        )
        db.finish_run(run_id, result)
    except (LLMError, ValueError, httpx.HTTPError) as exc:
        db.fail_run(run_id, f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001 - a crash must not strand the run
        db.fail_run(run_id, f"Unexpected error: {type(exc).__name__}: {exc}")


@app.post("/api/discover/projects/{project_id}/runs")
async def start_project_run(
    project_id: str,
    request: ProjectRunRequest,
    background: BackgroundTasks,
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    project = db.get_project(project_id, owner_id=user["id"])
    if project is None:
        raise HTTPException(404, "Project not found.")

    run_id = db.create_run(
        project_id,
        project["topic"],
        project["goal"],
        request.max_papers,
        request.max_validations,
    )
    background.add_task(
        _execute_run, run_id, project, request.max_papers, request.max_validations
    )
    return {"run": db.get_run(run_id)}


@app.get("/api/discover/runs/{run_id}")
async def get_run(run_id: str, user: dict = Depends(require_user)) -> dict[str, Any]:
    run = _owned_run(run_id, user)
    return {"run": run}


def _owned_run(run_id: str, user: dict) -> dict[str, Any]:
    """Fetch a run, or 404 unless it belongs to this user.

    Runs carry no owner of their own; ownership comes from the parent project.
    Without this check a signed-in user could read or re-challenge someone
    else's research by guessing a run id.
    """
    run = db.get_run(run_id)
    if run is None or db.get_project(run["project_id"], owner_id=user["id"]) is None:
        raise HTTPException(404, "Run not found.")
    return run


@app.get("/api/discover/runs/{run_id}/export")
def export_discovery(
    run_id: str, format: str = "md", user: dict = Depends(require_user)
) -> Response:
    """Download a discovery run as a report."""
    run = _owned_run(run_id, user)

    fmt = format.lower().lstrip(".")
    if fmt not in ("md", "markdown"):
        # The PDF and DOCX builders are shaped around a single analysed paper;
        # a run has a different structure, so claiming support would be a lie.
        raise HTTPException(400, "Discovery reports are available as md.")

    content = export_service.discovery_markdown(run).encode("utf-8")
    filename = export_service.safe_discovery_filename(run, "md")
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class SavePaperRequest(BaseModel):
    paper_index: int = Field(ge=0, le=99)


@app.post("/api/discover/runs/{run_id}/save-paper")
async def save_discovered_paper(
    run_id: str, request: SavePaperRequest, user: dict = Depends(require_user)
) -> dict[str, Any]:
    """Move a paper found by discovery into the library.

    Discovery analyses abstracts and then discards the papers, so a promising
    result could not be chatted with, grounded or exported. This carries it
    across instead of making the user hunt down the PDF again.
    """
    run = _owned_run(run_id, user)
    papers = (run.get("result") or {}).get("papers") or []
    if request.paper_index >= len(papers):
        raise HTTPException(404, "That paper is not in this run.")

    work = papers[request.paper_index]
    title = work.get("title") or "Untitled"
    abstract = work.get("abstract") or ""
    body = "\n".join(
        [
            f"Title: {title}",
            f"Authors: {', '.join(work.get('authors') or [])}",
            f"Year: {work.get('year', 'n.d.')}",
            f"Venue: {work.get('venue', '')}",
            "",
            "Abstract:",
            abstract or "(no abstract available)",
        ]
    )

    try:
        analysis = await analysis_service.analyse_paper(body)
    except LLMError as exc:
        raise HTTPException(502, f"Could not analyse that paper: {exc}") from exc

    paper_id = uuid.uuid4().hex[:12]
    db.save_paper(
        paper_id=paper_id,
        filename=work.get("openalex_url") or work.get("id") or title,
        pages=0,                      # abstract-only: there is no PDF behind this
        characters=len(abstract),
        title=title,
        full_text=body,
        sections={},
        analysis=analysis,
        owner_id=user["id"],
    )
    return {"paper_id": paper_id, "title": title, "abstract_only": True}


class ChallengeRequest(BaseModel):
    gap_id: str = Field(min_length=1, max_length=120)


@app.post("/api/discover/runs/{run_id}/challenge")
async def challenge_gap(
    run_id: str, request: ChallengeRequest, user: dict = Depends(require_user)
) -> dict[str, Any]:
    """Re-run adversarial validation for one opportunity in a finished run.

    The point of the feature is that a verdict is a claim, not a fact: the user
    can force the system to search again and try to disprove it. The verdict can
    move in either direction, including from `open` to `addressed`.
    """
    run = _owned_run(run_id, user)

    result = run.get("result") or {}
    opportunities = result.get("opportunities") or []
    index = next(
        (i for i, o in enumerate(opportunities) if str(o.get("id")) == request.gap_id), None
    )
    if index is None:
        raise HTTPException(404, "Gap not found in this run.")

    try:
        revalidated = await discovery.validate_gap(
            opportunities[index], budget=discovery.Budget()
        )
        # Re-score through the public ranker; no budget means no LLM prose call,
        # so a challenge costs only the validation search.
        rescored = await discovery.rank_opportunities([revalidated], budget=None)
    except LLMError as exc:
        raise HTTPException(502, f"Challenge failed: {exc}") from exc

    # Keep the prose the original ranking produced; validation does not regenerate it.
    updated = {**opportunities[index], **rescored[0]}
    opportunities[index] = updated
    opportunities.sort(key=lambda o: o.get("system_score") or 0, reverse=True)
    result["opportunities"] = opportunities
    db.finish_run(run_id, result)

    return {"gap": updated, "run": db.get_run(run_id)}


# --------------------------------------------------------------------------
# Static client (mounted last so /api/* wins)
# --------------------------------------------------------------------------

if STATIC_DIR.exists():

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
