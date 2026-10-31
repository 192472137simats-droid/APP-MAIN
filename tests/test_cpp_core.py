"""Contracts for the optional C++ analysis bridge."""

import asyncio

import httpx
import pytest

from app.services import cpp_core
from app.services.llm_service import LLMError


ANALYSIS = {
    "metadata": {"title": "Bridge test"},
    "summary": {"short": "The analysis reached the Python app."},
    "gaps": [],
}


class StubClient:
    def __init__(self, response):
        self.response = response
        self.request = None

    async def post(self, url, *, content, headers):
        self.request = httpx.Request("POST", url, content=content, headers=headers)
        self.response.request = self.request
        return self.response


def test_analysis_posts_text_and_returns_structured_result(monkeypatch):
    client = StubClient(httpx.Response(200, json=ANALYSIS))
    monkeypatch.setattr(cpp_core, "CPP_CORE_URL", "http://127.0.0.1:8081")
    result = asyncio.run(
        cpp_core.analyse_paper("[page 1]\nPaper text", client=client)
    )

    assert result == ANALYSIS
    assert str(client.request.url) == "http://127.0.0.1:8081/api/core/analyze"
    assert client.request.headers["content-type"].startswith("text/plain")
    assert client.request.content == b"[page 1]\nPaper text"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(502, json={"detail": "provider unavailable"}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"summary": {"short": "missing metadata and gaps"}}),
    ],
    ids=["http-error", "invalid-json", "invalid-analysis"],
)
def test_analysis_rejects_failed_or_malformed_core_response(monkeypatch, response):
    client = StubClient(response)
    monkeypatch.setattr(cpp_core, "CPP_CORE_URL", "http://127.0.0.1:8081")
    with pytest.raises(LLMError):
        asyncio.run(cpp_core.analyse_paper("Paper text", client=client))


def test_analysis_requires_a_configured_core(monkeypatch):
    monkeypatch.setattr(cpp_core, "CPP_CORE_URL", "")

    with pytest.raises(LLMError, match="not configured"):
        asyncio.run(cpp_core.analyse_paper("Paper text"))


def test_analysis_reports_a_stopped_core_as_unavailable(monkeypatch):
    class OfflineClient:
        async def post(self, url, *, content, headers):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(cpp_core, "CPP_CORE_URL", "http://127.0.0.1:8081")
    with pytest.raises(LLMError, match="unavailable"):
        asyncio.run(cpp_core.analyse_paper("Paper text", client=OfflineClient()))


def test_background_analysis_uses_python_unless_core_is_configured(monkeypatch):
    from app import main

    calls = []
    analysis = {"metadata": {"title": "Dispatch test"}}

    async def python_analysis(_body):
        calls.append("python")
        return analysis

    async def cpp_analysis(_body):
        calls.append("cpp")
        return analysis

    monkeypatch.setattr(main.analysis_service, "analyse_paper", python_analysis)
    monkeypatch.setattr(main.cpp_core, "analyse_paper", cpp_analysis)
    monkeypatch.setattr(main.db, "set_analysis", lambda *_args: None)
    monkeypatch.setattr(main.db, "set_title", lambda *_args: None)

    monkeypatch.setattr(main, "CPP_CORE_URL", "")
    asyncio.run(main._analyse_in_background("paper-id", "paper body"))
    monkeypatch.setattr(main, "CPP_CORE_URL", "http://127.0.0.1:8081")
    asyncio.run(main._analyse_in_background("paper-id", "paper body"))

    assert calls == ["python", "cpp"]


def test_background_analysis_saves_core_failure_as_retryable_error(monkeypatch):
    from app import main

    persisted = []

    async def fail(_body):
        raise LLMError("The C++ analysis service returned HTTP 502.")

    monkeypatch.setattr(main, "CPP_CORE_URL", "http://127.0.0.1:8081")
    monkeypatch.setattr(main.cpp_core, "analyse_paper", fail)
    monkeypatch.setattr(
        main.db,
        "set_analysis",
        lambda paper_id, analysis, error=None: persisted.append(
            (paper_id, analysis, error)
        ),
    )

    asyncio.run(main._analyse_in_background("paper-id", "paper body"))

    assert persisted == [
        ("paper-id", None, "AI analysis failed: The C++ analysis service returned HTTP 502.")
    ]
