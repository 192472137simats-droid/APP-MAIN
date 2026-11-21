# Project Code — ResearchAdvisorLM

Use this as a map to the real source code during your review. The actual C++ implementation is in `cpp/src/`; this guide does not replace those source files.

## Project in one sentence

ResearchAdvisorLM extracts a research paper, asks a configured language model to summarize and identify possible research gaps, and checks those gaps against later research found through OpenAlex.

## Technologies

- **Python + FastAPI:** main web backend, authentication, PDF workflows, discovery, chat, comparison, literature search, and exports.
- **HTML, CSS, JavaScript:** browser interface in `app/static/`.
- **C++17:** a separate analysis service and a standalone C++ demo.
- **OpenRouter-compatible API:** language model requests; the app sends paper text and receives structured JSON. The project does not train a model.
- **OpenAlex:** retrieves scholarly works for literature grounding.
- **SQLite:** stores papers and analysis data. The deployed Python app uses the configured data directory/volume.
- **PyMuPDF:** PDF text extraction in the main Python app.

## Main processing flow

```text
PDF upload
   ↓
FastAPI extracts text and saves the paper
   ↓
C++ analysis service when available; otherwise Python fallback
   ↓
Structured summary, methods, findings, limitations, and possible gaps
   ↓
OpenAlex search grounds gaps against retrieved later work
   ↓
Library, compare, chat, and export features
```

## Where to show the code

| File | What to point out |
|---|---|
| `app/main.py` | Main API routes and background analysis dispatch; the C++-to-Python fallback is in `_analyse_in_background`. |
| `app/services/analysis.py` | Python structured analysis prompt and model call. |
| `app/services/cpp_core.py` | Python client's HTTP request to the local C++ service. |
| `app/services/pdf_service.py` | PDF extraction for the main app. |
| `app/services/literature.py` and `app/services/discovery.py` | Literature search and discovery workflows. |
| `cpp/src/server.cpp` | C++ HTTP server; `/api/core/analyze` accepts paper text and returns analysis JSON. |
| `cpp/src/analysis.cpp` | Builds the model request with libcurl, parses/validates JSON with JsonCpp, and implements the background worker. |
| `cpp/src/analysis.hpp` | `AnalysisWorker` class and its thread, queue, mutex, and condition variable. |
| `cpp/src/pdf_extract.cpp` | Poppler-based text extraction for the standalone C++ app. |
| `cpp/src/paper_store.cpp` | SQLite storage for the standalone C++ app. |
| `cpp/CMakeLists.txt` | C++17 build configuration and linked libraries. |

## C++ features used

- C++17 standard and standard library containers/types such as `std::string`, `std::vector`, `std::queue`, `std::optional`, and `std::filesystem`.
- Classes and structs, including `AnalysisWorker`, `PaperStore`, and `PaperRecord`.
- Background processing with `std::thread`; `std::mutex` and `std::condition_variable` coordinate the job queue.
- Resource management with RAII and smart pointers, including Poppler document/page objects and SQLite statement cleanup.
- HTTP server networking with sockets; the C++ service binds to loopback for the integrated app.
- libcurl for outbound model requests; JsonCpp for JSON parsing and validation; SQLite and Poppler for the standalone C++ mode.
- Exceptions for reporting and handling request, PDF, and model errors.

## Explain the C++/Python split accurately

The Python app remains the main application. It extracts PDFs with PyMuPDF and owns the full web and research workflow. The integrated C++ service receives already-extracted text for structured analysis. The C++ source also includes its own Poppler PDF extraction and SQLite library for standalone mode.

In the current live demo, the C++ analysis service was unavailable, so the Python fallback completed the BERT analysis. The fallback keeps the user-facing analysis available; do not say that C++ handled that particular live request.

## Short review script

> “ResearchAdvisorLM helps a researcher inspect a paper and explore its possible research gaps. FastAPI handles the app and paper workflows. A configured language model generates a structured analysis, and OpenAlex provides later papers to check the gaps against. I also implemented a C++17 analysis service. It uses a worker thread and queue, sockets for its internal HTTP endpoint, libcurl for model requests, and JsonCpp for the response. In the live demo the C++ service was unavailable, so the Python fallback produced the BERT analysis.”
