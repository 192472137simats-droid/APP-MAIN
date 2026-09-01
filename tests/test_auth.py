"""Accounts, sessions, and tenant isolation.

The isolation tests are the point: multi-user is only worth anything if one
account genuinely cannot reach another's papers, projects or runs.
"""

import pytest

from app import db
from app.services import auth, discovery

GOOD_PASSWORD = "a-good-test-password"


def _register(client, email, password=GOOD_PASSWORD):
    return client.post("/api/auth/register", json={"email": email, "password": password})


def _login(client, email, password=GOOD_PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


# --- registration and login -----------------------------------------------


def test_register_then_use_the_api(client):
    assert _register(client, "a@example.com").status_code == 200
    assert client.get("/api/papers").status_code == 200


def test_setup_needed_flips_after_first_account(client):
    assert client.get("/api/auth/me").json()["setup_needed"] is True
    _register(client, "a@example.com")
    assert client.get("/api/auth/me").json()["setup_needed"] is False


def test_duplicate_email_is_refused(client):
    _register(client, "a@example.com")
    response = _register(client, "A@Example.com ")  # case and space insensitive
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


@pytest.mark.parametrize(
    "email,password",
    [("not-an-email", GOOD_PASSWORD), ("a@example.com", "short"), ("", GOOD_PASSWORD)],
)
def test_bad_credentials_are_refused(client, email, password):
    assert _register(client, email, password).status_code in (400, 422)


def test_wrong_password_does_not_reveal_whether_the_account_exists(client):
    _register(client, "a@example.com")
    known = _login(client, "a@example.com", "wrong-password-entirely")
    unknown = _login(client, "nobody@example.com", "wrong-password-entirely")

    assert known.status_code == unknown.status_code == 401
    assert known.json()["detail"] == unknown.json()["detail"]


def test_logout_revokes_the_session(client):
    _register(client, "a@example.com")
    assert client.get("/api/papers").status_code == 200

    client.post("/api/auth/logout")
    assert client.get("/api/papers").status_code == 401


def test_password_is_never_stored_in_the_clear(client):
    _register(client, "a@example.com")
    user = db.get_user_by_email("a@example.com")

    assert GOOD_PASSWORD not in user["password_hash"]
    assert user["password_hash"] != GOOD_PASSWORD
    assert len(user["salt"]) == 32


def test_expired_session_is_rejected(client):
    _register(client, "a@example.com")
    user = db.get_user_by_email("a@example.com")
    db.create_session(user["id"], "stale-token", "2000-01-01T00:00:00+00:00")

    assert auth.user_for_token("stale-token") is None


# --- the guard ------------------------------------------------------------


def test_api_is_closed_without_a_session(client):
    for path in ("/api/papers", "/api/discover/projects", "/api/models"):
        assert client.get(path).status_code == 401, path


def test_health_stays_public(client):
    assert client.get("/api/health").status_code == 200


def test_bearer_token_works_for_clients_without_cookies(client):
    token = _register(client, "a@example.com").json()["token"]
    client.cookies.clear()

    assert client.get("/api/papers").status_code == 401
    assert (
        client.get("/api/papers", headers={"Authorization": f"Bearer {token}"}).status_code
        == 200
    )


# --- isolation between accounts -------------------------------------------


def test_first_account_inherits_pre_auth_data(client, memory_db):
    """Rows created before accounts existed must not become invisible."""
    memory_db.save_paper("legacy", "old.pdf", 1, 10, "Legacy Paper", "text", {}, {})

    claimed = _register(client, "first@example.com").json()["claimed"]

    assert claimed["papers"] == 1
    assert len(client.get("/api/papers").json()) == 1


def test_second_account_starts_empty(client, memory_db):
    memory_db.save_paper("legacy", "old.pdf", 1, 10, "Legacy Paper", "text", {}, {})
    _register(client, "first@example.com")
    client.post("/api/auth/logout")

    _register(client, "second@example.com")

    assert client.get("/api/papers").json() == []


def test_one_account_cannot_read_or_delete_another_paper(client, memory_db):
    _register(client, "first@example.com")
    memory_db.save_paper(
        "p1", "mine.pdf", 1, 10, "Mine", "text", {}, {},
        owner_id=db.get_user_by_email("first@example.com")["id"],
    )
    client.post("/api/auth/logout")
    _register(client, "second@example.com")

    assert client.get("/api/papers/p1").status_code == 404
    assert client.delete("/api/papers/p1").status_code == 404
    # And it is still there for its real owner.
    assert db.get_paper("p1") is not None


def test_one_account_cannot_reach_another_project_or_run(client, monkeypatch):
    # Stub the pipeline: starting a run otherwise calls OpenAlex and the LLM
    # for real, which makes this test take minutes and cost money.
    async def fake_run(query, *, goal, max_papers, max_validations, on_progress=None):
        return {"opportunities": [], "llm_calls": 0}

    monkeypatch.setattr(discovery, "run_discovery", fake_run)

    _register(client, "first@example.com")
    project = client.post(
        "/api/discover/projects", json={"topic": "mine", "goal": "gaps"}
    ).json()["project"]
    run = client.post(
        f"/api/discover/projects/{project['id']}/runs", json={}
    ).json()["run"]

    client.post("/api/auth/logout")
    _register(client, "second@example.com")

    assert client.get(f"/api/discover/projects/{project['id']}").status_code == 404
    assert client.delete(f"/api/discover/projects/{project['id']}").status_code == 404
    assert client.get("/api/discover/projects").json()["projects"] == []
    # Runs carry no owner of their own - they inherit it from the project.
    assert client.get(f"/api/discover/runs/{run['id']}").status_code == 404
    assert (
        client.post(
            f"/api/discover/runs/{run['id']}/challenge", json={"gap_id": "g1"}
        ).status_code
        == 404
    )


# --- rate limiting --------------------------------------------------------


def test_repeated_registration_is_rate_limited(client):
    codes = [_register(client, f"user{i}@example.com").status_code for i in range(8)]
    assert 429 in codes, codes


def test_rate_limit_does_not_block_unlisted_routes(client):
    _register(client, "a@example.com")
    assert all(client.get("/api/papers").status_code == 200 for _ in range(25))
