"""Offline tests for Research Discovery Mode (Stages 2-6).

The LLM (analysis.analyse_paper, discovery.generate_json) and OpenAlex
(literature._get) are all mocked, so these run deterministically and never touch
the network or spend quota. They exercise real orchestration, clustering,
validation wiring, scoring, error isolation, and the bounded-budget behaviour.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import analysis, discovery, literature


# --- fixtures / fakes ------------------------------------------------------


def _work(oid, title, year=2022, cites=10, abstract="hallucination detection remains hard"):
    return {
        "openalex_id": f"https://openalex.org/{oid}", "title": title, "year": year,
        "url": f"https://doi.org/{oid}", "venue": "Venue", "citations": cites,
        "authors": ["A. Author"], "abstract": abstract, "source": "topic search",
    }


def _analysis(title="Paper"):
    return {
        "metadata": {"title": title},
        "stated_limitations": ["evaluated on a single dataset only"],
        "gaps": [
            {"id": "g1", "title": "single dataset evaluation",
             "description": "only one dataset was used for evaluation",
             "category": "dataset", "evidence_from_paper": "we evaluate on one dataset",
             "origin": "inferred"},
            {"id": "g2", "title": "no baseline comparison",
             "description": "missing comparison against strong baselines",
             "category": "comparison", "evidence_from_paper": "no baselines reported",
             "origin": "inferred"},
        ],
    }


async def _fake_analyse_paper(body):
    if "FAILING" in body:
        raise RuntimeError("simulated LLM failure")
    return _analysis()


def _raw_openalex(works):
    return {"results": [
        {
            "id": w["openalex_id"], "display_name": w["title"],
            "publication_year": w["year"], "doi": None,
            "authorships": [{"author": {"display_name": "A. Author"}}],
            "abstract_inverted_index": {"hallucination": [0], "detection": [1]},
            "cited_by_count": w["citations"],
            "primary_location": {"source": {"display_name": "Venue"}},
        }
        for w in works
    ]}


def _fake_get(works):
    async def _get(_client, _path, _params):
        return _raw_openalex(works)
    return _get


async def _fake_generate_json(prompt, schema, *, temperature=0.2, **kwargs):
    import re
    if "CLUSTERS:" in prompt:
        idxs = [int(m) for m in re.findall(r"\[cluster (\d+)\]", prompt)]
        return {"gaps": [{"cluster_index": i, "title": f"Merged gap {i}",
                          "description": "shared unresolved problem", "category": "dataset",
                          "research_area": "NLP", "affected_methods": [], "affected_datasets": [],
                          "confidence": "medium"} for i in idxs]}
    if "ADVERSARIALLY" in prompt:
        return {"verdict": "open", "confidence": "medium", "reasoning": "not solved in corpus",
                "supporting_refs": [1], "contradicting_refs": [], "what_remains": "a real study"}
    if "explain the opportunity" in prompt:
        idxs = [int(m) for m in re.findall(r"\[(\d+)\]", prompt)]
        return {"items": [{"index": i, "why_it_matters": "it matters",
                           "suggested_next_step": "run a benchmark",
                           "novelty_assessment": "not found addressed in the checked corpus",
                           "feasibility": "medium"} for i in idxs]}
    if "Rewrite this research topic" in prompt:
        return {"query": "broader hallucination detection query"}
    return {}


# --- Stage 2: multi-paper analysis ----------------------------------------


def test_multi_paper_analysis_isolates_failures(monkeypatch):
    monkeypatch.setattr(analysis, "analyse_paper", _fake_analyse_paper)
    papers = [_work("W1", "Good paper"), _work("W2", "FAILING paper"), _work("W3", "Another good")]
    records = asyncio.run(discovery.analyse_papers(papers, budget=None))
    assert len(records) == 3
    assert sum(r["ok"] for r in records) == 2          # one failure isolated
    bad = [r for r in records if not r["ok"]][0]
    assert "FAILING" in bad["paper"]["title"] and bad["error"]


def test_multi_paper_analysis_respects_budget(monkeypatch):
    monkeypatch.setattr(analysis, "analyse_paper", _fake_analyse_paper)
    papers = [_work(f"W{i}", f"Paper {i}") for i in range(5)]
    budget = discovery.Budget(max_calls=2, deadline_seconds=60)
    records = asyncio.run(discovery.analyse_papers(papers, budget=budget))
    assert len(records) == 5
    assert sum(r["ok"] for r in records) <= 2          # capped by budget
    assert budget.calls <= 2


# --- Stage 3: cross-paper synthesis ---------------------------------------


def test_synthesis_clusters_and_links_papers(monkeypatch):
    monkeypatch.setattr(discovery, "generate_json", _fake_generate_json)
    analyses = [
        {"ok": True, "paper": _work("W1", "P1"), "analysis": _analysis("P1")},
        {"ok": True, "paper": _work("W2", "P2"), "analysis": _analysis("P2")},
    ]
    gaps = asyncio.run(discovery.synthesize_gaps(analyses, budget=None))
    assert gaps, "expected candidate gaps"
    for gap in gaps:
        assert gap["id"] and "supporting_papers" in gap and "frequency" in gap
        assert gap["frequency"] == len(gap["supporting_papers"])   # traceable
    assert any(g["frequency"] >= 2 for g in gaps)       # a gap recurs across both papers


# --- Stage 4: adversarial validation --------------------------------------


def test_validation_produces_verdict_and_split_evidence(monkeypatch):
    monkeypatch.setattr(literature, "_get", _fake_get([_work("V1", "A solution paper", cites=99)]))
    monkeypatch.setattr(discovery, "generate_json", _fake_generate_json)
    gap = {"id": "cg1", "title": "single dataset evaluation", "description": "d",
           "affected_methods": ["transformer"], "frequency": 2,
           "supporting_papers": [{"title": "P1"}], "evidence": []}
    out = asyncio.run(discovery.validate_gap(gap, budget=None))
    assert out["verdict"] == "open"
    assert out["validation_corpus_size"] >= 1
    assert len(out["supporting_evidence"]) == 1          # ref 1 linked back
    assert out["contradicting_evidence"] == []


def test_validation_unverified_when_no_corpus(monkeypatch):
    monkeypatch.setattr(literature, "_get", _fake_get([]))   # OpenAlex returns nothing
    gap = {"id": "cg1", "title": "obscure gap", "frequency": 1, "supporting_papers": []}
    out = asyncio.run(discovery.validate_gap(gap, budget=None))
    assert out["verdict"] == "unverified" and out["validation_corpus_size"] == 0


# --- Stage 5: opportunity ranking -----------------------------------------


def test_ranking_orders_and_labels(monkeypatch):
    monkeypatch.setattr(discovery, "generate_json", _fake_generate_json)
    validated = [
        {"id": "a", "title": "addressed gap", "verdict": "addressed", "confidence": "high",
         "frequency": 1, "supporting_evidence": [], "contradicting_evidence": [{"title": "x"}]},
        {"id": "b", "title": "open recurring gap", "verdict": "open", "confidence": "high",
         "frequency": 3, "supporting_evidence": [{"title": "p"}], "contradicting_evidence": []},
    ]
    ranked = asyncio.run(discovery.rank_opportunities(validated, budget=discovery.Budget()))
    assert [g["id"] for g in ranked][0] == "b"           # open+recurring ranks above addressed
    assert all(g["score_is_system_generated"] for g in ranked)
    assert all("system_score" in g and "score_breakdown" in g for g in ranked)


# --- Stage 6: orchestration (bounded, end-to-end, mocked) ------------------


def _patch_all(monkeypatch, works):
    monkeypatch.setattr(literature, "_get", _fake_get(works))
    monkeypatch.setattr(analysis, "analyse_paper", _fake_analyse_paper)
    monkeypatch.setattr(discovery, "generate_json", _fake_generate_json)


def test_run_discovery_end_to_end(monkeypatch):
    _patch_all(monkeypatch, [_work(f"W{i}", f"Paper {i}") for i in range(6)])
    report = asyncio.run(discovery.run_discovery("LLM hallucination detection", max_papers=3))
    assert report["query"] == "LLM hallucination detection"
    assert report["papers_analysed"] >= 1
    assert report["opportunities"], "expected ranked opportunities"
    assert report["llm_calls"] <= discovery.MAX_LLM_CALLS
    steps = [t["step"] for t in report["trace"]]
    for expected in ("question_understood", "papers_discovered", "papers_analysed", "done"):
        assert expected in steps


def test_run_discovery_hard_llm_budget(monkeypatch):
    _patch_all(monkeypatch, [_work(f"W{i}", f"Paper {i}") for i in range(6)])
    budget = discovery.Budget(max_calls=1, deadline_seconds=60)
    report = asyncio.run(discovery.run_discovery("topic", max_papers=4, budget=budget))
    assert report["llm_calls"] <= 1                      # never exceeds the cap
    assert "opportunities" in report                     # still returns a structured report


def test_run_discovery_rejects_empty_query():
    with pytest.raises(ValueError):
        asyncio.run(discovery.run_discovery("   "))


# --- API endpoint ----------------------------------------------------------



def test_discover_run_endpoint(monkeypatch, auth_client):
    client = auth_client
    _patch_all(monkeypatch, [_work(f"W{i}", f"Paper {i}") for i in range(4)])
    resp = client.post("/api/discover/run", json={"query": "graph neural networks", "max_papers": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "graph neural networks"
    assert "opportunities" in data and "trace" in data


def test_discover_run_endpoint_empty_query(auth_client):
    client = auth_client
    # whitespace passes Pydantic min_length but run_discovery rejects it -> 400
    assert client.post("/api/discover/run", json={"query": "  "}).status_code == 400
