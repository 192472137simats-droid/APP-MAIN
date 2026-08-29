"""Focused tests for Step 2: topic -> ranked candidate-paper retrieval.

Network is mocked at literature._get, so these run offline and deterministically.
They exercise the real dedup + ranking logic and the read-only endpoint.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import literature


# --- helpers ---------------------------------------------------------------


def _raw_work(oid: str, title: str, year: int, citations: int) -> dict:
    """A minimal OpenAlex /works record, the shape _work_to_dict expects."""
    return {
        "id": f"https://openalex.org/{oid}",
        "display_name": title,
        "publication_year": year,
        "doi": None,
        "authorships": [{"author": {"display_name": "A. Author"}}],
        "abstract_inverted_index": {"Example": [0], "abstract": [1]},
        "cited_by_count": citations,
        "primary_location": {"source": {"display_name": "Some Venue"}},
    }


def _fake_get(results: list[dict]):
    """Replacement for literature._get that returns canned OpenAlex results."""

    async def _get(_client, _path, _params):  # signature matches the real _get
        return {"results": results}

    return _get


# --- search_topic: validation ---------------------------------------------


def test_empty_query_raises():
    with pytest.raises(ValueError):
        asyncio.run(literature.search_topic("   "))


def test_non_integer_limit_raises(monkeypatch):
    monkeypatch.setattr(literature, "_get", _fake_get([_raw_work("W1", "t", 2022, 1)]))
    with pytest.raises(ValueError):
        asyncio.run(literature.search_topic("graph neural networks", limit="lots"))


# --- search_topic: dedup, shape, ranking ----------------------------------


def test_dedup_and_shape(monkeypatch):
    raw = [
        _raw_work("W1", "Graph neural networks", 2022, 500),
        _raw_work("W1", "Graph neural networks (duplicate id)", 2022, 500),
        _raw_work("W2", "GNN survey", 2023, 50),
        _raw_work("W3", "Older GNN work", 2016, 900),
    ]
    monkeypatch.setattr(literature, "_get", _fake_get(raw))
    results = asyncio.run(literature.search_topic("graph neural networks", limit=10))

    ids = [r["openalex_id"] for r in results]
    assert len(ids) == len(set(ids))          # no duplicates
    assert len(results) == 3                   # the duplicate W1 was removed
    first = results[0]
    for key in ("openalex_id", "title", "year", "citations", "authors", "source", "rank_score"):
        assert key in first                    # normalised dict + additive rank_score
    assert first["source"] == "topic search"


def test_relevance_prioritised_over_citations(monkeypatch):
    # OpenAlex returns in relevance order: W1, W2, W3. W3 has huge citations but
    # is least relevant, so relevance must keep W1 on top.
    raw = [
        _raw_work("W1", "Exact topic match", 2021, 10),
        _raw_work("W2", "Also relevant", 2021, 10),
        _raw_work("W3", "Barely relevant but cited", 2021, 5000),
    ]
    monkeypatch.setattr(literature, "_get", _fake_get(raw))
    results = asyncio.run(literature.search_topic("q", limit=3))
    assert results[0]["openalex_id"].endswith("W1")
    scores = [r["rank_score"] for r in results]
    assert scores == sorted(scores, reverse=True)   # descending by rank_score


def test_limit_is_respected_and_clamped(monkeypatch):
    raw = [_raw_work(f"W{i}", f"title {i}", 2022, i) for i in range(30)]
    monkeypatch.setattr(literature, "_get", _fake_get(raw))

    assert len(asyncio.run(literature.search_topic("q", limit=5))) == 5
    # Over-large limit is clamped to <= the number of available results.
    big = asyncio.run(literature.search_topic("q", limit=999))
    assert len(big) == 30


# --- endpoint --------------------------------------------------------------

client = TestClient(app)  # no context manager -> app lifespan/startup not run -> no DB


def test_endpoint_ok(monkeypatch):
    monkeypatch.setattr(literature, "_get", _fake_get([_raw_work("W1", "Topic paper", 2022, 100)]))
    resp = client.get("/api/discover/search", params={"q": "graph neural networks", "limit": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "graph neural networks"
    assert data["count"] == len(data["results"]) == 1
    assert data["results"][0]["title"] == "Topic paper"


def test_endpoint_rejects_empty_query():
    assert client.get("/api/discover/search", params={"q": "   "}).status_code == 400
    assert client.get("/api/discover/search").status_code == 400
