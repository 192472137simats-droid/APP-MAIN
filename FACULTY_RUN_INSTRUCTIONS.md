# ResearchAdvisorLM — Faculty Testing & Evaluation Guide

This guide enables faculty and examiners to run and verify the complete **ResearchAdvisorLM** project implementation on any laptop in under 2 minutes.

---

## ⚡ Quick Start (Run on Faculty Laptop)

### Step 1: Clone the Repository
```bash
git clone <YOUR-GITHUB-REPO-URL>
cd app1-main
```

### Step 2: Install Requirements
Ensure Python 3.10+ is installed, then run:
```bash
pip install -r requirements.txt
```

### Step 3: Run the Application (1 Command)
```bash
python run_project.py
```
* **Auto-Launch:** This script automatically checks dependencies, initializes the local SQLite database, starts the server at `http://127.0.0.1:8000`, and opens your default web browser automatically.

---

## 📦 Alternative: 100% Self-Contained Single-File Execution
If you prefer testing with a single consolidated file without folder navigation:
```bash
python standalone_app.py
```
* **Zero-Setup:** Embeds the complete backend API, PDF extractor, SQLite database, grounded literature search, and Web UI directly into a single script. Works even without external API keys via intelligent rule-based analysis.

---

## 🧪 Key Features to Test

1. **PDF Upload & Text Extraction:**
   - Upload any research paper in PDF format (e.g., Transformer, BERT, or Computer Vision paper).
   - System extracts text structure, pages, and metadata via PyMuPDF.

2. **Automated Structured Analysis:**
   - Generates objective, methodology, key findings, and stated limitations.
   - Provides an **ELI5 (Explain Like I'm 5)** simplified conceptual analogy.

3. **Grounded Research Gap Identification:**
   - Detects concrete research gaps across scalability, evaluation, generalization, and methodology.
   - Integrates live scholarly queries against **OpenAlex** to ground identified gaps against subsequent peer-reviewed work.

4. **Interactive Chat with Paper:**
   - Type questions regarding paper methodology, dataset choices, and benchmark results in the built-in conversational panel.

---

## 🛠️ Architecture Summary
- **Backend:** FastAPI (Python 3.10+) with asynchronous endpoints.
- **Frontend:** Responsive client (HTML5, Modern CSS, Vanilla JS) communicating over REST.
- **Data Persistence:** Local SQLite with WAL mode.
- **Scholarly Grounding:** OpenAlex REST API for verifying research gaps against later literature.
- **C++ Core (Optional):** C++17 analysis service in `cpp/src/` with CMake configuration for native benchmarking.
