"""ResearchAdvisorLM - One-Click Project Runner for Faculty & Reviewers.

Run this script directly from the project root:
    python run_project.py

It verifies dependencies, initializes the local SQLite database, starts the
FastAPI application server, and automatically opens the user interface in your browser.
"""

import os
import sys
import time
import webbrowser
import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

def check_dependencies():
    """Verify required packages are installed."""
    required = ["fastapi", "uvicorn", "pymupdf", "httpx", "dotenv", "pydantic"]
    missing = []
    for pkg in required:
        try:
            if pkg == "pymupdf":
                import fitz
            elif pkg == "dotenv":
                import dotenv
            else:
                __import__(pkg)
        except ImportError:
            missing.append(pkg)
    
    if missing:
        print(f"[*] Missing dependencies detected: {', '.join(missing)}")
        print("[*] Installing required packages from requirements.txt...")
        req_file = ROOT_DIR / "requirements.txt"
        if req_file.exists():
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(req_file)])
        else:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "fastapi", "uvicorn[standard]", "pymupdf", "httpx", "python-dotenv", "pydantic"])
        print("[+] All dependencies installed successfully.\n")

def open_browser(url: str, delay_seconds: float = 1.5):
    """Open default browser after server initializes."""
    import threading
    def _open():
        time.sleep(delay_seconds)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=_open, daemon=True).start()

def main():
    print("=" * 70)
    print("      ResearchAdvisorLM - AI Research Paper & Gap Analysis System     ")
    print("=" * 70)
    print(f"[*] Project root directory: {ROOT_DIR}")
    
    # 1. Check requirements
    check_dependencies()

    # 2. Set default environment if .env doesn't exist
    env_file = ROOT_DIR / ".env"
    if not env_file.exists():
        example_env = ROOT_DIR / ".env.example"
        if example_env.exists():
            import shutil
            shutil.copy(example_env, env_file)
            print("[+] Initialized .env from .env.example")
    
    # 3. Create data directory
    data_dir = ROOT_DIR / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # 4. Initialize database
    try:
        from app import db
        db.init()
        print("[+] SQLite database initialized at data/researchadvisor.db")
    except Exception as e:
        print(f"[!] Warning during DB initialization: {e}")

    # 5. Launch uvicorn
    host = "127.0.0.1"
    port = 8000
    app_url = f"http://{host}:{port}"
    print(f"[+] Server starting at: {app_url}")
    print("[*] Opening application in your web browser...")
    open_browser(app_url)
    print("[*] Press Ctrl+C in this terminal window to stop the application.\n")
    
    import uvicorn
    uvicorn.run("app.main:app", host=host, port=port, reload=False)

if __name__ == "__main__":
    main()
