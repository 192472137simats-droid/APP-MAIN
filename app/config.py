"""Runtime configuration. Everything sensitive comes from the environment."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "researchadvisor.db"

# --- Gemini ---------------------------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
# Pinned for reproducibility (you should be able to say which model produced
# your results). If it is ever retired, the service falls back automatically -
# see resolve_model() in services/gemini_service.py.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"

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
