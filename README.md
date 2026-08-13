# ResearchAdvisorLM

**AI-Powered Research Paper Summarization and Research Gap Identification with Literature Grounding**

Upload a research paper. Get a structured breakdown, then every research direction
it leaves open — each one **checked against the literature published since**, and
labelled *still open*, *partially addressed*, or *addressed*, with citations.

---

## Why this isn't just a summarizer

Most "research gap finders" read one paper and paraphrase its Limitations and
Future Work sections. Every gap they report is one the authors already wrote down,
and none of them can tell you whether somebody has since solved it.

ResearchAdvisorLM adds a grounding step:

```
Paper (2021)  ──►  extract stated + inferred gaps
                        │
                        ▼
        OpenAlex: papers citing it + later work on the topic
                        │
                        ▼
      "Has direction X been addressed by these N papers?"
                        │
    ┌───────────────────┼───────────────────┐
    ▼                   ▼                   ▼
 STILL OPEN         PARTIAL             ADDRESSED
 (nothing found)   (adjacent work)   (cite who did it)
```

A gap marked *still open* now carries evidence: the corpus that was searched and
came back empty. That is a checkable claim, not a vibe.

---

## Features

**Ingestion** — PDF upload (drag-drop or picker), column-aware text extraction,
section segmentation, scanned-PDF detection, size/type validation.

**Analysis** — structured JSON: title, authors, year, venue, domain, keywords,
objective, method, datasets, metrics, contributions, findings, conclusion, stated
limitations, stated future work. Four summary depths including *explain simply*.

**Gap finder** — stated gaps (from the authors) plus inferred gaps (what a reviewer
would raise), each categorised (dataset / evaluation / generalization / comparison /
scalability / methodological / reproducibility / application), each carrying the
evidence from the paper that shows it exists.

**Literature grounding** — OpenAlex retrieval of citing papers and later topical
work, verdict + confidence + reasoning + citations per gap, and an explicit
statement of how many papers were checked.

**Chat** — Q&A over the full paper text, answers name their section, refuses to
answer what the paper doesn't say. Retrieved related work is available to the model
for "what has other work done?" questions.

**Compare** — 2–5 papers side by side, plus the most useful output: gaps that
*none* of them address.

**Export** — Markdown report with summary, gaps, verdicts and a citation list.

---

## Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | FastAPI | async, one service, trivial to deploy |
| AI | Gemini Flash via REST | 1M context — a whole paper fits, no chunking needed |
| PDF | PyMuPDF | fast, column-aware block extraction |
| Literature | OpenAlex API | free, no key, ~250M works |
| Storage | SQLite | zero-config; swap for Postgres when you need persistence |
| Frontend | Vanilla JS + CSS | no build step, served by the same service |

**No FAISS, no sentence-transformers, no LangChain.** A 15-page paper is ~20k
tokens and fits in context whole — chunking it would lose cross-section context and
make answers worse. Retrieval happens where it earns its place: over the *external
literature corpus*, not over one document.

---

## Run locally

```bash
python -m venv venv
venv\Scripts\activate          # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env           # then put your Gemini key in it
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000

Get a free Gemini API key at <https://aistudio.google.com/apikey>.

---

## Deploy to Railway

1. Push this repo to GitHub.
2. Railway → **New Project → Deploy from GitHub repo** → pick the repo.
3. **Variables** → add `GEMINI_API_KEY`. Optionally `OPENALEX_MAILTO` (your email —
   gets you into OpenAlex's faster polite pool).
4. **Settings → Networking → Generate Domain**.

`railway.json` and `Procfile` set the start command and a `/api/health` healthcheck.
`.python-version` pins Python 3.12.

> **Note:** Railway's filesystem is ephemeral — the SQLite library resets on each
> redeploy. Fine for a demo. For persistence, attach a Railway volume mounted at
> `/data` and set `DATA_DIR=/data`, or move to Postgres.

---

## Android app

A native Kotlin + Jetpack Compose client lives in [`android/`](android). It talks to the
same FastAPI backend, so the phone never sees the Gemini key.

**Build it:**

```bash
cd android && ./gradlew assembleDebug
```

The APK lands in `android/app/build/outputs/apk/debug/`. Every push to `main` also
builds one in CI — grab it from the Actions run's artifacts.

Requires JDK 17 and an Android SDK with platform 34. Point the app at a different
backend by editing `API_BASE_URL` in `android/app/build.gradle.kts`.

| | |
|---|---|
| Min SDK | 26 (Android 8.0) |
| Compile / target SDK | 34 |
| AGP / Kotlin / Compose BOM | 8.7.3 / 2.0.21 / 2024.10.01 |
| Networking | OkHttp + kotlinx.serialization |

**Two things that are easy to get wrong here**, both handled:

- *Timeouts.* Analysis takes ~60s and grounding ~90s. OkHttp defaults to a 10s read
  timeout, which would fail every meaningful call. This client uses 240s read / 300s call.
- *Nullability.* `grounding` is null until validation runs, and gaps only grow
  `verdict`/`confidence`/`evidence` afterwards. Every DTO field has a default and
  optional ones are nullable, with `coerceInputValues = true` — kotlinx's defaults
  cover *missing* keys but not explicit nulls.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | status, model, whether the key is configured |
| `POST` | `/api/upload` | multipart PDF → extraction + analysis |
| `POST` | `/api/papers/{id}/ground` | run literature grounding on the gaps |
| `GET` | `/api/papers` | library list |
| `GET` | `/api/papers/{id}` | full paper + analysis + grounding + chat history |
| `DELETE` | `/api/papers/{id}` | delete paper and messages |
| `POST` | `/api/chat` | `{paper_id, question}` → grounded answer |
| `POST` | `/api/compare` | `{paper_ids: [...]}` → comparison table + shared gaps |
| `GET` | `/api/papers/{id}/export` | Markdown report download |

Interactive docs at `/docs`.

---

## Environment

| Variable | Default | Notes |
|---|---|---|
| `GEMINI_API_KEY` | — | **required** |
| `GEMINI_MODEL` | `gemini-3.6-flash` | falls back automatically if retired; `GET /api/models` lists what your key can call |
| `OPENALEX_MAILTO` | — | your email; faster OpenAlex pool |
| `GROUNDING_CORPUS_SIZE` | `40` | papers retrieved per grounding run |
| `MAX_UPLOAD_MB` | `25` | |
| `MAX_PAPER_CHARS` | `180000` | analysis truncation limit |
| `DATA_DIR` | `./data` | SQLite location |

---

## Known limitations

State these plainly — they are questions you will be asked, and having answers is
worth more than pretending they don't exist.

- **Bounded corpus.** Verdicts are judged against ~40 retrieved papers, not all of
  science. The UI always shows the corpus size. *Still open* means "not found in
  what we checked", never "provably novel".
- **Text only.** Equations, figures and numbers inside result tables may be missed.
- **OCR is vision-based, not exact.** PDFs with no text layer are detected and
  transcribed by the vision model (no Tesseract needed), but accuracy is lower than
  for a digital PDF and only the first 20 pages are read (configurable via
  `OCR_MAX_PAGES`).
- **OpenAlex coverage.** Very new preprints and some venues may be missing or
  abstract-less, which lowers verdict confidence.
- **LLM error.** The model can misread a paper. Every gap ships with the paper
  evidence it was derived from so a human can check it in seconds.

---

## Roadmap

- [x] PDF/DOCX export — Markdown, PDF and DOCX, all downloadable
- [x] OCR fallback for scanned papers — vision-model page transcription
- [x] Evaluation harness — `eval/run.py`, runnable end to end, measuring extraction
      accuracy, summary faithfulness, gap precision and grounded-vs-ungrounded win rate
      (currently benchmarked on 2 papers; dataset defines 8 — expand toward 15–20)
- [ ] Per-claim page anchors in the summary (findings/contributions already anchored)
- [ ] Postgres + user accounts
