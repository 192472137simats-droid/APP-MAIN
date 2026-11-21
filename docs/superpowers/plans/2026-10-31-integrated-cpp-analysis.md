# Integrated C++ Analysis Implementation Plan

> **For agentic workers:** Implement inline in this session. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the complete original ResearchAdvisorLM app and delegate structured paper analysis to the C++ service in integrated mode.

**Architecture:** FastAPI remains the public application and continues to serve `app/static`, authentication, storage, grounding, chat, discovery, compare, and exports. When `CPP_CORE_URL` is set, its existing background analysis workflow posts paper text to a loopback C++ endpoint and stores the returned analysis in the existing Python database. Without that setting, the Python analysis path remains unchanged.

**Tech Stack:** FastAPI, Python `httpx`, C++17, libcurl, JsonCpp, existing unittest/pytest suites.

**Spec:** This plan's acceptance criteria and global constraints below.

## Global Constraints

- Keep `app/static` and the existing Python API contracts intact.
- Do not migrate or rewrite the existing Python SQLite database.
- Keep the C++ core listener bound to `127.0.0.1`.
- Reuse `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL`; never log or expose key values.
- Preserve standalone C++ demo routes and Python-only mode.
- When integrated analysis fails, persist the existing retryable paper-analysis error state.

## Review Focus

- C++ core unavailable after configuration: analysis must fail visibly and leave a retryable error, not hang or silently route to another model.
- Python-only installation: analysis must continue using the original Python service when `CPP_CORE_URL` is unset.
- Untrusted PDF prompt instructions and UTF-8 text: existing C++ prompt fencing and truncation must still apply.
- Original browser behavior: Python remains the server for the original multi-section static interface and its API.
- Secret handling: neither health responses nor errors/logs may contain provider key values.

---

### Task 1: C++ analysis endpoint

**Files:**
- Modify: `cpp/src/analysis.hpp`, `cpp/src/analysis.cpp`, `cpp/src/server.cpp`
- Test: `cpp/tests/test_server.py`

**Interfaces:**
- `AnalysisWorker::analyze_text(const std::string&) const -> std::string` returns validated analysis JSON or throws a sanitized runtime error.
- `POST /api/core/analyze` accepts UTF-8 `text/plain` paper text and returns the existing structured analysis JSON.

- [x] Add an HTTP integration test that sends text to `/api/core/analyze`, checks the structured analysis response, and confirms the mock provider received the configured model.
- [x] Run the focused C++ HTTP test and observe the endpoint fail before implementation (HTTP 405 from the old binary).
- [x] Implement the synchronous worker method and endpoint; return 503 when the provider is not configured.
- [x] Rebuild and run the C++ HTTP suite.

### Task 2: Python bridge and dispatch

**Files:**
- Create: `app/services/cpp_core.py`
- Modify: `app/config.py`, `app/main.py`, `.env.example`
- Test: `tests/test_cpp_core.py`

**Interfaces:**
- `CPP_CORE_URL` defaults to empty; a non-empty value enables the bridge.
- `cpp_core.analyse_paper(body: str) -> dict[str, Any]` uses `httpx`, validates a JSON object response, and raises `LLMError` on network, status, or response failures.
- `_analyse_in_background` calls C++ only when `CPP_CORE_URL` is configured; otherwise it calls the existing Python analysis service.

- [x] Add tests for successful bridge response, malformed/non-2xx response handling, and the configured/unconfigured dispatch choice.
- [x] Run those tests and observe the missing adapter failure during collection.
- [x] Implement the bridge, timeout, and dispatch while preserving existing database status/error handling.
- [x] Run focused Python tests and the existing Python suite.

### Task 3: Integrated launcher and run instructions

**Files:**
- Create: `scripts/run-integrated.ps1`
- Modify: `README.md`, `cpp/README.md`

- [x] Add a PowerShell launcher that builds the C++ server, waits for its loopback health endpoint, sets `CPP_CORE_URL`, runs the original FastAPI app, and only stops a C++ process it started itself.
- [x] Document the integrated startup command, separate standalone modes, and the Python fallback when `CPP_CORE_URL` is empty.
- [x] Verify the launcher syntax, C++ build, C++ HTTP tests, Python test suite, and original static UI/API smoke path.

## Execution Results

- C++ build: `cmake --build cpp/build/integrated --config Release` passed.
- Test suite: `python -m pytest -q` passed (125 tests, 6 subtests); the suite includes the C++ HTTP and Python-to-C++ integration path.
- Launcher: PowerShell parser passed; integrated run started both services on loopback.
- Runtime smoke: original UI returned HTTP 200 and retained Discover/Compare navigation; Python health and auth endpoints returned successfully; C++ health reported analysis configured.
- Review: confirmed the bridge preserves the Python API/database as source of truth, validates the C++ response, keeps C++ bound to loopback, and stores C++ failures through the existing retryable analysis error path.

