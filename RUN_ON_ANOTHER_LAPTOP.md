# Run ResearchAdvisorLM on another Windows laptop

1. Install Python 3.12 and extract this project ZIP.
2. Open the extracted `ResearchAdvisorLM` folder.
3. Right-click `run-on-windows.ps1` and choose **Run with PowerShell**. If Windows blocks the script, open PowerShell in that folder and run `powershell -ExecutionPolicy Bypass -File .\run-on-windows.ps1`.
4. Enter an OpenRouter API key when prompted. The script does not save the key in a file.
5. Open <http://127.0.0.1:8000> in a browser.

The first launch downloads the Python packages and needs an internet connection. The app runs its Python analysis path by default. The C++ source is included under `cpp/`; running the integrated C++ mode separately requires the native libraries and CMake setup described in `cpp/README.md`.

Local paper data is created in `data/` on the new laptop. The ZIP intentionally excludes `.env`, database files, and existing papers; configure a new provider key on that laptop.
