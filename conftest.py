"""Shared pytest setup.

Puts the repo root on sys.path so `import app...` resolves from anywhere, and
provides the two fixtures every API test now needs: an isolated database, and
a client that is signed in. Every /api route except health and auth requires a
session, so an unauthenticated TestClient would only ever see 401s.
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Session cookies are marked Secure by default; TestClient speaks plain http,
# so a secure cookie would never come back and every test would look logged out.
os.environ.setdefault("ALLOW_INSECURE_COOKIE", "1")


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Rate-limit counters live in module state and would leak between tests.

    Every test signs in from the same client address, so without this the
    register limit trips partway through the suite and later tests see 429s.
    """
    from app import main

    main._rate_state.clear()
    yield
    main._rate_state.clear()


@pytest.fixture
def memory_db():
    """Point the db module at a fresh in-memory database for one test."""
    from app import db

    previous = db._conn
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db._conn = conn
    db.init()
    yield db
    conn.close()
    db._conn = previous


@pytest.fixture
def client(memory_db):
    """An unauthenticated client against the isolated database."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_client(client):
    """A client signed in as a freshly registered account.

    Registering also claims any rows created earlier in the test, mirroring how
    the first real account inherits pre-auth data.
    """
    response = client.post(
        "/api/auth/register",
        json={"email": "tester@example.com", "password": "a-good-test-password"},
    )
    assert response.status_code == 200, response.text
    client.user = response.json()["user"]
    return client
