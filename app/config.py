"""Runtime configuration. Everything sensitive comes from the environment."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "researchadvisor.db"

# --- LLM (OpenAI-compatible) ----------------------------------------------
# The app talks to any OpenAI-compatible /chat/completions API: OpenRouter
# (default), Groq, Mistral, Cerebras, a local server, etc. Switch providers by
# changing three env vars - no code change:
#   LLM_BASE_URL   the provider's OpenAI-compatible base (…/v1)
#   LLM_API_KEY    that provider's key
#   LLM_MODEL      a model id the key can call
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
LLM_API_KEY = (os.getenv("LLM_API_KEY") or os.getenv("OPENROUTER_API_KEY") or "").strip()
# Free model ids are retired without notice, and a model can also lose its free
# tier while keeping the id - nemotron-nano-9b-v2 began returning 404, then
# minimax-m2.7:free started answering "unavailable for free, use the paid slug".
# Always re-check GET {LLM_BASE_URL}/models before trusting this default.
#
# Latency decides it as much as availability: full-paper analysis has to finish
# well inside the platform's 300s proxy timeout. Measured 2026-09-07 on one
# analysis - laguna-s-2.1 24s, nemotron-3-super-120b 33s, nex-n2.5-pro >95s.
LLM_MODEL = os.getenv("LLM_MODEL", "poolside/laguna-s-2.1:free").strip()
# Optional separate vision model for OCR of scanned PDFs (needs image input).
# Empty -> OCR falls back to LLM_MODEL and errors clearly if it can't see.
LLM_VISION_MODEL = os.getenv(
    "LLM_VISION_MODEL", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
).strip()
# OpenRouter reads these for attribution; other providers ignore them.
LLM_APP_URL = os.getenv("LLM_APP_URL", "https://github.com/sai2610teja-dev/research-advisor-lm").strip()
LLM_APP_TITLE = os.getenv("LLM_APP_TITLE", "ResearchAdvisorLM").strip()

# --- Literature grounding -------------------------------------------------
# OpenAlex asks for a contact address to get into the polite (faster) pool.
OPENALEX_MAILTO = os.getenv("OPENALEX_MAILTO", "").strip()
OPENALEX_BASE = "https://api.openalex.org"
GROUNDING_CORPUS_SIZE = int(os.getenv("GROUNDING_CORPUS_SIZE", "40"))

# --- Limits ---------------------------------------------------------------
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "25"))
# Vision OCR is billed per page image, so cap what a scanned PDF can cost.
OCR_MAX_PAGES = int(os.getenv("OCR_MAX_PAGES", "20"))
MAX_PAPER_CHARS = int(os.getenv("MAX_PAPER_CHARS", "180000"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "180"))
