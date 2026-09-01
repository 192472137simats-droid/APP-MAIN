"""Focused tests for Stage 7: discovery projects and runs.

The database is swapped for an in-memory SQLite connection and the discovery
pipeline is stubbed, so these run offline and never spend an LLM call.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.services import discovery


@pytest.fixture(autouse=True)
def memory_db_local():
    """Point the db module at a fresh in-memory database for each test."""
    previous = db._conn
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db._conn = conn
    db.init()
    yield
    conn.close()
    db._conn = previous


@pytest.fixture
def client(memory_db_local):
    """Signed in: every /api route except health and auth requires a session."""
    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/auth/register",
            json={"email": "tester@example.com", "password": "a-good-test-password"},
        )
        assert response.status_code == 200, response.text
        yield test_client


def _make_project(client, topic="LLM hallucination detection", goal="gaps"):
    response = client.post(
        "/api/discover/projects", json={"topic": topic, "goal": goal}
    )
    assert response.status_code == 200
    return response.json()["project"]


# --- db layer -------------------------------------------------------------


def test_project_round_trip():
    project_id = db.create_project("retrieval augmentation", "gaps")
    project = db.get_project(project_id)
    assert project["topic"] == "retrieval augmentation"
    assert project["goal"] == "gaps"


def test_unknown_project_is_none():
    assert db.get_project("nope") is None


def test_run_starts_as_running():
    project_id = db.create_project("topic", "gaps")
    run_id = db.create_run(project_id, "topic", "gaps", 6, 6)
    assert db.get_run(run_id)["status"] == "running"


def test_trace_accumulates_in_order():
    project_id = db.create_project("topic", "gaps")
    run_id = db.create_run(project_id, "topic", "gaps", 6, 6)

    db.append_run_step(run_id, "papers_discovered", {"count": 12})
    db.append_run_step(run_id, "papers_analysed", {"ok": 5})

    trace = db.get_run(run_id)["trace"]
    assert [step["step"] for step in trace] == ["papers_discovered", "papers_analysed"]
    assert trace[0]["data"] == {"count": 12}


def test_append_step_to_unknown_run_is_a_noop():
    db.append_run_step("missing", "step", {})  # must not raise


def test_finish_run_records_result_and_llm_calls():
    project_id = db.create_project("topic", "gaps")
    run_id = db.create_run(project_id, "topic", "gaps", 6, 6)

    db.finish_run(run_id, {"opportunities": [{"title": "x"}], "llm_calls": 9})

    run = db.get_run(run_id)
    assert run["status"] == "done"
    assert run["llm_calls"] == 9
    assert run["result"]["opportunities"] == [{"title": "x"}]


def test_soft_failure_result_is_recorded_as_error():
    """The pipeline reports 'no papers found' as a result key, not an exception."""
    project_id = db.create_project("topic", "gaps")
    run_id = db.create_run(project_id, "topic", "gaps", 6, 6)

    db.finish_run(run_id, {"error": "no papers found for this topic", "opportunities": []})

    run = db.get_run(run_id)
    assert run["status"] == "error"
    assert run["error"] == "no papers found for this topic"
    assert run["result"] is not None  # the evidence is still kept


def test_fail_run_sets_terminal_status():
    project_id = db.create_project("topic", "gaps")
    run_id = db.create_run(project_id, "topic", "gaps", 6, 6)

    db.fail_run(run_id, "LLMError: upstream refused")

    run = db.get_run(run_id)
    assert run["status"] == "error"
    assert "upstream refused" in run["error"]


def test_list_runs_omits_result_payload():
    project_id = db.create_project("topic", "gaps")
    run_id = db.create_run(project_id, "topic", "gaps", 6, 6)
    db.finish_run(run_id, {"opportunities": [], "llm_calls": 1})

    listed = db.list_runs(project_id)
    assert len(listed) == 1
    assert "result" not in listed[0]


def test_deleting_a_project_removes_its_runs():
    project_id = db.create_project("topic", "gaps")
    db.create_run(project_id, "topic", "gaps", 6, 6)

    db.delete_project(project_id)

    assert db.get_project(project_id) is None
    assert db.list_runs(project_id) == []


def test_list_projects_summarises_runs():
    project_id = db.create_project("topic", "gaps")
    run_id = db.create_run(project_id, "topic", "gaps", 6, 6)
    db.finish_run(run_id, {"opportunities": [], "llm_calls": 0})

    summary = db.list_projects()[0]
    assert summary["run_count"] == 1
    assert summary["last_status"] == "done"


# --- API ------------------------------------------------------------------


def test_create_and_fetch_project(client):
    project = _make_project(client)

    response = client.get(f"/api/discover/projects/{project['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["project"]["topic"] == "LLM hallucination detection"
    assert body["runs"] == []


def test_empty_topic_is_rejected(client):
    response = client.post("/api/discover/projects", json={"topic": "", "goal": "gaps"})
    assert response.status_code == 422


def test_overlong_topic_is_rejected(client):
    response = client.post(
        "/api/discover/projects", json={"topic": "x" * 301, "goal": "gaps"}
    )
    assert response.status_code == 422


def test_missing_project_returns_404(client):
    assert client.get("/api/discover/projects/missing").status_code == 404
    assert client.delete("/api/discover/projects/missing").status_code == 404
    assert client.post("/api/discover/projects/missing/runs", json={}).status_code == 404


def test_missing_run_returns_404(client):
    assert client.get("/api/discover/runs/missing").status_code == 404


def test_delete_project_endpoint(client):
    project = _make_project(client)

    assert client.delete(f"/api/discover/projects/{project['id']}").status_code == 200
    assert client.get(f"/api/discover/projects/{project['id']}").status_code == 404


def _finished_run(client, monkeypatch, papers=None, opportunities=None):
    """A completed run, with the pipeline stubbed so nothing is spent."""

    async def fake_run(query, *, goal, max_papers, max_validations, on_progress=None):
        return {
            "query": query,
            "goal": goal,
            "papers": papers or [],
            "papers_analysed": len(papers or []),
            "candidate_gaps": 2,
            "opportunities": opportunities or [],
            "llm_calls": 4,
        }

    monkeypatch.setattr(discovery, "run_discovery", fake_run)
    project = _make_project(client)
    return client.post(
        f"/api/discover/projects/{project['id']}/runs", json={}
    ).json()["run"]


def test_export_renders_evidence_and_the_caveat(client, monkeypatch):
    run = _finished_run(
        client, monkeypatch,
        opportunities=[{
            "id": "g1", "title": "Detectors are not evaluated under shift",
            "description": "d", "verdict": "open", "system_score": 80.2,
            "supporting_evidence": [
                {"title": "SelfCheckGPT", "authors": ["P. M."], "year": 2023,
                 "citations": 720, "url": "https://openalex.org/W1"}
            ],
        }],
    )

    response = client.get(f"/api/discover/runs/{run['id']}/export")
    body = response.text

    assert response.status_code == 200
    assert "Detectors are not evaluated under shift" in body
    assert "SelfCheckGPT" in body
    assert "STILL OPEN" in body
    assert "not a measure of scientific merit" in body


def test_export_flags_an_unevidenced_gap_as_unproven(client, monkeypatch):
    run = _finished_run(
        client, monkeypatch,
        opportunities=[{"id": "g1", "title": "No evidence gap", "description": "d",
                        "verdict": "open", "supporting_evidence": []}],
    )

    body = client.get(f"/api/discover/runs/{run['id']}/export").text
    assert "unproven rather than confirmed open" in body


def test_export_rejects_unsupported_formats(client, monkeypatch):
    run = _finished_run(client, monkeypatch)
    assert (
        client.get(f"/api/discover/runs/{run['id']}/export?format=pdf").status_code == 400
    )


def test_saving_a_discovered_paper_adds_it_to_the_library(client, monkeypatch):
    run = _finished_run(
        client, monkeypatch,
        papers=[{"id": "W1", "title": "Semantic Entropy", "abstract": "A" * 200,
                 "authors": ["S. Farquhar"], "year": 2024, "cited_by_count": 300}],
    )

    async def fake_analyse(_body):
        return {"metadata": {"title": "Semantic Entropy"}, "gaps": []}

    from app.services import analysis as analysis_service

    monkeypatch.setattr(analysis_service, "analyse_paper", fake_analyse)

    response = client.post(
        f"/api/discover/runs/{run['id']}/save-paper", json={"paper_index": 0}
    )
    assert response.status_code == 200
    assert response.json()["abstract_only"] is True

    library = client.get("/api/papers").json()
    assert [p["title"] for p in library] == ["Semantic Entropy"]


def test_saving_an_out_of_range_paper_is_404(client, monkeypatch):
    run = _finished_run(client, monkeypatch, papers=[])
    response = client.post(
        f"/api/discover/runs/{run['id']}/save-paper", json={"paper_index": 3}
    )
    assert response.status_code == 404


def test_start_run_executes_pipeline_and_stores_result(client, monkeypatch):
    """TestClient runs BackgroundTasks before returning, so the run completes."""

    async def fake_run_discovery(query, *, goal, max_papers, max_validations, on_progress=None):
        if on_progress:
            on_progress("papers_discovered", {"count": 4})
        return {"query": query, "goal": goal, "opportunities": [{"title": "gap"}], "llm_calls": 3}

    monkeypatch.setattr(discovery, "run_discovery", fake_run_discovery)

    project = _make_project(client)
    response = client.post(f"/api/discover/projects/{project['id']}/runs", json={})
    assert response.status_code == 200

    run_id = response.json()["run"]["id"]
    run = client.get(f"/api/discover/runs/{run_id}").json()["run"]

    assert run["status"] == "done"
    assert run["llm_calls"] == 3
    assert run["result"]["opportunities"] == [{"title": "gap"}]
    assert [step["step"] for step in run["trace"]] == ["papers_discovered"]


def test_pipeline_failure_is_recorded_not_raised(client, monkeypatch):
    async def boom(query, *, goal, max_papers, max_validations, on_progress=None):
        raise ValueError("query must be a non-empty string")

    monkeypatch.setattr(discovery, "run_discovery", boom)

    project = _make_project(client)
    response = client.post(f"/api/discover/projects/{project['id']}/runs", json={})
    assert response.status_code == 200

    run = client.get(f"/api/discover/runs/{response.json()['run']['id']}").json()["run"]
    assert run["status"] == "error"
    assert "non-empty" in run["error"]


def test_run_caps_are_enforced(client):
    project = _make_project(client)

    too_many = client.post(
        f"/api/discover/projects/{project['id']}/runs", json={"max_papers": 999}
    )
    assert too_many.status_code == 422


def test_run_appears_in_project_history(client, monkeypatch):
    async def fake_run_discovery(query, *, goal, max_papers, max_validations, on_progress=None):
        return {"opportunities": [], "llm_calls": 0}

    monkeypatch.setattr(discovery, "run_discovery", fake_run_discovery)

    project = _make_project(client)
    client.post(f"/api/discover/projects/{project['id']}/runs", json={})

    runs = client.get(f"/api/discover/projects/{project['id']}").json()["runs"]
    assert len(runs) == 1
    assert runs[0]["status"] == "done"
