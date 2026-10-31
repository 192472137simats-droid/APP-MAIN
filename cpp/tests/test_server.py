"""HTTP tests for the C++ PDF ingestion server."""

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import fitz


ROOT = Path(__file__).resolve().parents[2]
INTEGRATED_SERVER = ROOT / "cpp" / "build" / "integrated" / "research-advisor-server.exe"
STANDALONE_SERVER = ROOT / "cpp" / "build" / "research-advisor-server.exe"
DEFAULT_SERVER = INTEGRATED_SERVER if INTEGRATED_SERVER.is_file() else STANDALONE_SERVER
SERVER = Path(os.environ.get("RESEARCH_ADVISOR_SERVER", DEFAULT_SERVER))


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert SERVER.is_file(), f"C++ server binary is missing: {SERVER}"
        cls.data_dir = tempfile.TemporaryDirectory()

        class MockModel(BaseHTTPRequestHandler):
            calls = []

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                request = json.loads(body)
                self.calls.append(request)
                analysis = {
                    "metadata": {"title": "Capstone test paper", "authors": [], "year": 2026,
                                 "venue": "", "domain": "C++", "keywords": ["testing"]},
                    "summary": {"one_line": "A test paper.", "short": "A small capstone test.",
                                "detailed": "This paper tests the C++ analysis flow.",
                                "eli5": "It checks that analysis works."},
                    "objective": "Verify the analysis flow.", "method": "Mock model response.",
                    "datasets": [], "metrics": [], "contributions": [], "findings": [],
                    "conclusion": "The flow works.", "stated_limitations": [],
                    "stated_future_work": [], "gaps": [],
                }
                content = ("not valid JSON" if "MODEL_FAILURE" in request["messages"][-1]["content"]
                           else json.dumps(analysis))
                response = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, *_args):
                pass

        cls.mock_model = MockModel
        cls.provider = ThreadingHTTPServer(("127.0.0.1", 0), MockModel)
        cls.provider_thread = threading.Thread(target=cls.provider.serve_forever, daemon=True)
        cls.provider_thread.start()
        database = Path(cls.data_dir.name) / "researchadvisor-cpp.db"
        with sqlite3.connect(database) as connection:
            connection.execute(
                "CREATE TABLE papers (id TEXT PRIMARY KEY, filename TEXT NOT NULL, "
                "title TEXT NOT NULL, pages INTEGER NOT NULL, characters INTEGER NOT NULL, "
                "full_text TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')))"
            )
            connection.execute(
                "INSERT INTO papers (id, filename, title, pages, characters, full_text) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("legacy-paper", "legacy.pdf", "Legacy paper", 1, 20, "[page 1]\nLegacy text"),
            )
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        environment = os.environ.copy()
        environment["RESEARCH_ADVISOR_DATA_DIR"] = cls.data_dir.name
        environment["LLM_API_KEY"] = "test-only-key"
        environment["LLM_MODEL"] = "test-model"
        environment["LLM_BASE_URL"] = f"http://127.0.0.1:{cls.provider.server_port}/v1"
        cls.process = subprocess.Popen(
            [str(SERVER), str(cls.port), str(ROOT / "cpp" / "web")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=environment,
        )
        for _ in range(50):
            try:
                with urlopen(f"http://127.0.0.1:{cls.port}/api/health", timeout=0.2):
                    return
            except OSError:
                if cls.process.poll() is not None:
                    break
                time.sleep(0.1)
        error = cls.process.stderr.read().decode(errors="replace") if cls.process.poll() is not None else ""
        cls.process.terminate()
        raise AssertionError(f"server did not start: {error}")

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.process.stderr.close()
        cls.provider.shutdown()
        cls.provider.server_close()
        cls.provider_thread.join(timeout=5)
        cls.data_dir.cleanup()

    def get(self, path):
        with urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=2) as response:
            return response.status, response.headers, response.read()

    def test_health_is_json(self):
        status, headers, body = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers["Content-Type"])
        self.assertEqual(json.loads(body), {
            "status": "ok", "service": "research-advisor-cpp",
            "analysis_configured": True, "model": "test-model",
        })

    def test_core_analysis_endpoint_returns_structured_analysis(self):
        request = Request(
            f"http://127.0.0.1:{self.port}/api/core/analyze",
            data=b"A short paper body for the integrated analysis engine.",
            headers={"Content-Type": "text/plain; charset=utf-8"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            analysis = json.load(response)

        self.assertEqual(analysis["metadata"]["title"], "Capstone test paper")
        self.assertEqual(analysis["summary"]["short"], "A small capstone test.")
        self.assertEqual(self.mock_model.calls[-1]["model"], "test-model")

    def test_python_background_analysis_integrates_with_cpp_core(self):
        from app import main
        from app.services import cpp_core

        service_url = f"http://127.0.0.1:{self.port}"
        old_main_url = main.CPP_CORE_URL
        old_core_url = cpp_core.CPP_CORE_URL
        old_set_analysis = main.db.set_analysis
        old_set_title = main.db.set_title
        persisted = []
        main.CPP_CORE_URL = service_url
        cpp_core.CPP_CORE_URL = service_url
        main.db.set_analysis = lambda paper_id, analysis, error=None: persisted.append(
            (paper_id, analysis, error)
        )
        main.db.set_title = lambda *_args: None
        try:
            asyncio.run(
                main._analyse_in_background(
                    "integrated-paper", "A sample paper passed from FastAPI to C++."
                )
            )
        finally:
            main.CPP_CORE_URL = old_main_url
            cpp_core.CPP_CORE_URL = old_core_url
            main.db.set_analysis = old_set_analysis
            main.db.set_title = old_set_title

        self.assertEqual(persisted[0][0], "integrated-paper")
        self.assertEqual(persisted[0][1]["metadata"]["title"], "Capstone test paper")
        self.assertEqual(persisted[0][1]["summary"]["short"], "A small capstone test.")
        self.assertIsNone(persisted[0][2])

    def test_existing_database_is_upgraded_without_losing_papers(self):
        _, _, body = self.get("/api/papers/legacy-paper?include_text=true")
        paper = json.loads(body)
        self.assertEqual(paper["analysis_status"], "extracted")
        self.assertEqual(paper["analysis"], None)
        self.assertIn("Legacy text", paper["full_text"])

    def test_existing_web_assets_are_served(self):
        for path, expected_type, marker in (
            ("/", "text/html", b"Research Advisor C++"),
            ("/styles.css", "text/css", b"body"),
            ("/app.js", "javascript", b"async function loadPapers"),
        ):
            with self.subTest(path=path):
                status, headers, body = self.get(path)
                self.assertEqual(status, 200)
                self.assertIn(expected_type, headers["Content-Type"])
                self.assertIn(marker, body)

    def test_unknown_and_private_paths_return_404(self):
        for path in ("/missing", "/.env", "/%2e%2e/.env"):
            with self.subTest(path=path):
                with self.assertRaises(HTTPError) as error:
                    self.get(path)
                self.assertEqual(error.exception.code, 404)

    def test_pdf_upload_extracts_text_and_persists_it(self):
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text((72, 72), "ResearchAdvisor C++ sample paper")
        page.insert_text((72, 100), "Abstract: A small text-layer PDF for upload testing.")
        data = pdf.tobytes()
        pdf.close()

        boundary = "test-boundary-42"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="sample.pdf"\r\n'
            "Content-Type: application/pdf\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        request = Request(
            f"http://127.0.0.1:{self.port}/api/upload",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            uploaded = json.load(response)

        self.assertEqual(uploaded["filename"], "sample.pdf")
        self.assertEqual(uploaded["pages"], 1)
        self.assertGreater(uploaded["characters"], 20)
        paper_id = uploaded["paper_id"]
        self.assertTrue(paper_id)

        _, _, detail_body = self.get(f"/api/papers/{paper_id}?include_text=true")
        detail = json.loads(detail_body)
        self.assertEqual(detail["id"], paper_id)
        self.assertIn("ResearchAdvisor C++ sample paper", detail["full_text"])

        _, _, list_body = self.get("/api/papers")
        self.assertIn(paper_id, [paper["id"] for paper in json.loads(list_body)])

        database = Path(self.data_dir.name) / "researchadvisor-cpp.db"
        with sqlite3.connect(database) as connection:
            row = connection.execute("SELECT full_text FROM papers WHERE id = ?", (paper_id,)).fetchone()
        self.assertIsNotNone(row)
        self.assertIn("ResearchAdvisor C++ sample paper", row[0])

    def test_invalid_pdf_is_rejected(self):
        boundary = "invalid-boundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="bad.pdf"\r\n'
            "Content-Type: application/pdf\r\n\r\n"
            "not actually a pdf"
            f"\r\n--{boundary}--\r\n"
        ).encode()
        request = Request(
            f"http://127.0.0.1:{self.port}/api/upload",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=5)
        self.assertEqual(error.exception.code, 422)

    def test_pdf_without_text_is_rejected(self):
        pdf = fitz.open()
        pdf.new_page()
        data = pdf.tobytes()
        pdf.close()

        boundary = "empty-page-boundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="empty.pdf"\r\n'
            "Content-Type: application/pdf\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        request = Request(
            f"http://127.0.0.1:{self.port}/api/upload",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=5)
        self.assertEqual(error.exception.code, 422)
        self.assertIn("no extractable text", error.exception.read().decode())

    def test_paper_analysis_runs_in_background_and_is_saved(self):
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text((72, 72), "Capstone analysis test paper")
        data = pdf.tobytes()
        pdf.close()

        boundary = "analysis-boundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="analysis.pdf"\r\n'
            "Content-Type: application/pdf\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        upload = Request(
            f"http://127.0.0.1:{self.port}/api/upload", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urlopen(upload, timeout=5) as response:
            paper_id = json.load(response)["paper_id"]

        analyze = Request(
            f"http://127.0.0.1:{self.port}/api/papers/{paper_id}/analyze",
            data=b"", method="POST",
        )
        with urlopen(analyze, timeout=5) as response:
            self.assertEqual(response.status, 202)
            self.assertEqual(json.load(response)["analysis_status"], "pending")

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            _, _, detail_body = self.get(f"/api/papers/{paper_id}")
            paper = json.loads(detail_body)
            if paper["analysis_status"] != "pending":
                break
            time.sleep(0.1)
        self.assertEqual(paper["analysis_status"], "done", paper)
        self.assertEqual(paper["analysis"]["summary"]["short"], "A small capstone test.")
        self.assertEqual(self.mock_model.calls[-1]["model"], "test-model")
        self.assertEqual(self.mock_model.calls[-1]["response_format"], {"type": "json_object"})

        database = Path(self.data_dir.name) / "researchadvisor-cpp.db"
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT analysis_status, analysis_json FROM papers WHERE id = ?", (paper_id,)
            ).fetchone()
        self.assertEqual(row[0], "done")
        self.assertEqual(json.loads(row[1])["objective"], "Verify the analysis flow.")

    def test_invalid_model_output_marks_analysis_error(self):
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text((72, 72), "MODEL_FAILURE")
        data = pdf.tobytes()
        pdf.close()
        boundary = "model-failure-boundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="failure.pdf"\r\n'
            "Content-Type: application/pdf\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        upload = Request(
            f"http://127.0.0.1:{self.port}/api/upload", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urlopen(upload, timeout=5) as response:
            paper_id = json.load(response)["paper_id"]
        analyze = Request(
            f"http://127.0.0.1:{self.port}/api/papers/{paper_id}/analyze",
            data=b"", method="POST",
        )
        with urlopen(analyze, timeout=5) as response:
            self.assertEqual(response.status, 202)

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            _, _, detail_body = self.get(f"/api/papers/{paper_id}")
            paper = json.loads(detail_body)
            if paper["analysis_status"] != "pending":
                break
            time.sleep(0.1)
        self.assertEqual(paper["analysis_status"], "error", paper)
        self.assertIn("JSON", paper["analysis_error"])
        self.assertIsNone(paper["analysis"])


if __name__ == "__main__":
    unittest.main()
