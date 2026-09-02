"""Tiny SQLite layer. No ORM - the schema is four tables wide."""

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from .config import DB_PATH

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
    return _conn


def init() -> None:
    conn = connect()
    with _lock:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS papers (
                id           TEXT PRIMARY KEY,
                filename     TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                pages        INTEGER,
                characters   INTEGER,
                title        TEXT,
                full_text    TEXT,
                sections     TEXT,
                analysis     TEXT,
                grounding    TEXT
            );

            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_id   TEXT NOT NULL,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_messages_paper ON messages(paper_id);

            -- Research Discovery Mode (Stage 7). A project is a saved research
            -- question; a run is one execution of the discovery pipeline for it.
            -- Gaps and opportunities stay inside the run's result JSON: nothing
            -- queries them column-wise yet, so normalising them would be schema
            -- we cannot justify.
            CREATE TABLE IF NOT EXISTS discovery_projects (
                id         TEXT PRIMARY KEY,
                topic      TEXT NOT NULL,
                goal       TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS discovery_runs (
                id              TEXT PRIMARY KEY,
                project_id      TEXT NOT NULL,
                status          TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                query           TEXT NOT NULL,
                goal            TEXT NOT NULL,
                max_papers      INTEGER,
                max_validations INTEGER,
                trace           TEXT,
                result          TEXT,
                error           TEXT,
                llm_calls       INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (project_id) REFERENCES discovery_projects(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_runs_project ON discovery_runs(project_id);

            -- Accounts. Passwords are stored as a scrypt hash plus a per-user
            -- salt; the plaintext never reaches the database.
            CREATE TABLE IF NOT EXISTS users (
                id            TEXT PRIMARY KEY,
                email         TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                salt          TEXT NOT NULL,
                created_at    TEXT NOT NULL
            );

            -- Opaque session tokens, kept server-side so logging out actually
            -- revokes access - a self-contained signed cookie cannot.
            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

            -- Discovery analyses the same paper whenever two topics overlap,
            -- and every re-run repeats the whole cost. Abstracts do not change,
            -- so the structured analysis is cached by OpenAlex work id. Shared
            -- across accounts on purpose: this is public bibliographic data,
            -- never a user's own document.
            CREATE TABLE IF NOT EXISTS paper_analysis_cache (
                work_id    TEXT PRIMARY KEY,
                analysis   TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
    _migrate_owner_columns()


def _migrate_owner_columns() -> None:
    """Add owner_id to the pre-auth tables, idempotently.

    These tables existed before accounts did, so the column arrives by ALTER
    TABLE rather than in CREATE TABLE. Existing rows keep owner_id NULL until
    claim_unowned_rows() hands them to the first account.
    """
    conn = connect()
    with _lock:
        for table in ("papers", "discovery_projects"):
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "owner_id" not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN owner_id TEXT")

        # Analysis now runs after the upload response is sent, so a paper has to
        # record that it is still being worked on, or that the work failed.
        paper_columns = {row["name"] for row in conn.execute("PRAGMA table_info(papers)")}
        if "analysis_status" not in paper_columns:
            conn.execute("ALTER TABLE papers ADD COLUMN analysis_status TEXT")
            # Existing rows were analysed synchronously, so they are complete.
            conn.execute("UPDATE papers SET analysis_status = 'done'")
        if "analysis_error" not in paper_columns:
            conn.execute("ALTER TABLE papers ADD COLUMN analysis_error TEXT")
        conn.commit()


# --- analysis cache -------------------------------------------------------


def get_cached_analysis(work_id: str) -> dict[str, Any] | None:
    if not work_id:
        return None
    conn = connect()
    row = conn.execute(
        "SELECT analysis FROM paper_analysis_cache WHERE work_id = ?", (work_id,)
    ).fetchone()
    return json.loads(row["analysis"]) if row else None


def cache_analysis(work_id: str, analysis: dict) -> None:
    """Store a successful analysis. Failures are never cached, so a transient
    provider error cannot become a permanent wrong answer."""
    if not work_id or not analysis:
        return
    conn = connect()
    with _lock:
        conn.execute(
            """INSERT OR REPLACE INTO paper_analysis_cache (work_id, analysis, created_at)
               VALUES (?,?,?)""",
            (work_id, json.dumps(analysis), _now()),
        )
        conn.commit()


# --- users and sessions ---------------------------------------------------


def create_user(email: str, password_hash: str, salt: str) -> str:
    user_id = uuid.uuid4().hex[:12]
    conn = connect()
    with _lock:
        conn.execute(
            "INSERT INTO users (id, email, password_hash, salt, created_at) VALUES (?,?,?,?,?)",
            (user_id, email.strip().lower(), password_hash, salt, _now()),
        )
        conn.commit()
    return user_id


def get_user_by_email(email: str) -> dict[str, Any] | None:
    conn = connect()
    row = conn.execute(
        "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
    ).fetchone()
    return dict(row) if row else None


def get_user(user_id: str) -> dict[str, Any] | None:
    conn = connect()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def count_users() -> int:
    conn = connect()
    return conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


def create_session(user_id: str, token: str, expires_at: str) -> None:
    conn = connect()
    with _lock:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (token, user_id, _now(), expires_at),
        )
        conn.commit()


def get_session_user(token: str) -> dict[str, Any] | None:
    """Resolve a session token to its user, refusing expired tokens."""
    conn = connect()
    row = conn.execute(
        """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token = ? AND s.expires_at > ?""",
        (token, _now()),
    ).fetchone()
    return dict(row) if row else None


def delete_session(token: str) -> None:
    conn = connect()
    with _lock:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()


def purge_expired_sessions() -> int:
    conn = connect()
    with _lock:
        cursor = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (_now(),))
        conn.commit()
    return cursor.rowcount


def claim_unowned_rows(user_id: str) -> dict[str, int]:
    """Hand pre-auth data to its first owner.

    Papers and projects created before accounts existed have no owner, so they
    would be invisible to everyone. The first account to register inherits
    them; later accounts start empty.
    """
    conn = connect()
    with _lock:
        papers = conn.execute(
            "UPDATE papers SET owner_id = ? WHERE owner_id IS NULL", (user_id,)
        ).rowcount
        projects = conn.execute(
            "UPDATE discovery_projects SET owner_id = ? WHERE owner_id IS NULL", (user_id,)
        ).rowcount
        conn.commit()
    return {"papers": papers, "projects": projects}


# --- papers ---------------------------------------------------------------


def save_paper(
    paper_id: str,
    filename: str,
    pages: int,
    characters: int,
    title: str,
    full_text: str,
    sections: dict,
    analysis: dict,
    owner_id: str | None = None,
    analysis_status: str = "done",
) -> None:
    conn = connect()
    with _lock:
        conn.execute(
            """INSERT OR REPLACE INTO papers
               (id, filename, created_at, pages, characters, title, full_text,
                sections, analysis, grounding, owner_id, analysis_status)
               VALUES (?,?,?,?,?,?,?,?,?,
                       COALESCE((SELECT grounding FROM papers WHERE id = ?), NULL), ?, ?)""",
            (
                paper_id,
                filename,
                _now(),
                pages,
                characters,
                title,
                full_text,
                json.dumps(sections),
                json.dumps(analysis),
                paper_id,
                owner_id,
                analysis_status,
            ),
        )
        conn.commit()


def set_analysis(paper_id: str, analysis: dict | None, error: str | None = None) -> None:
    """Attach the finished analysis to a paper, or record why it failed.

    Analysis happens after the upload response has already been sent, so the
    row exists first and is completed here.
    """
    conn = connect()
    with _lock:
        conn.execute(
            """UPDATE papers
               SET analysis = ?, analysis_status = ?, analysis_error = ?
               WHERE id = ?""",
            (
                json.dumps(analysis) if analysis is not None else None,
                "error" if error else "done",
                error,
                paper_id,
            ),
        )
        conn.commit()


def mark_analysis_pending(paper_id: str) -> None:
    """Reset a paper so it can be analysed again, clearing the previous error."""
    conn = connect()
    with _lock:
        conn.execute(
            """UPDATE papers
               SET analysis_status = 'pending', analysis_error = NULL
               WHERE id = ?""",
            (paper_id,),
        )
        conn.commit()


def set_title(paper_id: str, title: str) -> None:
    """The real title only becomes known once analysis has read the paper."""
    conn = connect()
    with _lock:
        conn.execute("UPDATE papers SET title = ? WHERE id = ?", (title, paper_id))
        conn.commit()


def reap_stalled_analyses() -> int:
    """Fail papers still marked `pending` at startup.

    Analysis runs in this process, so anything still pending when the app boots
    died with the previous one and would otherwise be polled for ever.
    """
    conn = connect()
    with _lock:
        cursor = conn.execute(
            """UPDATE papers
               SET analysis_status = 'error',
                   analysis_error = 'Analysis was interrupted when the server restarted.'
               WHERE analysis_status = 'pending'"""
        )
        conn.commit()
    return cursor.rowcount


def save_grounding(paper_id: str, grounding: dict) -> None:
    conn = connect()
    with _lock:
        conn.execute(
            "UPDATE papers SET grounding = ? WHERE id = ?",
            (json.dumps(grounding), paper_id),
        )
        conn.commit()


def _row_to_paper(row: sqlite3.Row, include_text: bool = False) -> dict[str, Any]:
    paper = {
        "id": row["id"],
        "filename": row["filename"],
        "created_at": row["created_at"],
        "pages": row["pages"],
        "characters": row["characters"],
        "title": row["title"],
        "analysis": json.loads(row["analysis"]) if row["analysis"] else None,
        "grounding": json.loads(row["grounding"]) if row["grounding"] else None,
        "analysis_status": row["analysis_status"] or "done",
        "analysis_error": row["analysis_error"],
    }
    if include_text:
        paper["full_text"] = row["full_text"]
        paper["sections"] = json.loads(row["sections"]) if row["sections"] else {}
    return paper


def get_paper(
    paper_id: str, include_text: bool = False, owner_id: str | None = None
) -> dict[str, Any] | None:
    """Fetch one paper. Passing owner_id scopes the lookup to that user, so a
    guessed id from another account reads as missing rather than forbidden."""
    conn = connect()
    if owner_id is None:
        row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM papers WHERE id = ? AND owner_id = ?", (paper_id, owner_id)
        ).fetchone()
    return _row_to_paper(row, include_text) if row else None


def list_papers(owner_id: str | None = None) -> list[dict[str, Any]]:
    conn = connect()
    if owner_id is None:
        rows = conn.execute(
            """SELECT id, filename, created_at, pages, characters, title, analysis,
                  grounding, analysis_status, analysis_error
               FROM papers ORDER BY created_at DESC"""
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, filename, created_at, pages, characters, title, analysis,
                      grounding, analysis_status, analysis_error
               FROM papers WHERE owner_id = ? ORDER BY created_at DESC""",
            (owner_id,),
        ).fetchall()
    out = []
    for row in rows:
        analysis = json.loads(row["analysis"]) if row["analysis"] else {}
        grounding = json.loads(row["grounding"]) if row["grounding"] else None
        meta = (analysis or {}).get("metadata", {})
        gaps = (grounding or {}).get("gaps", [])
        out.append(
            {
                "id": row["id"],
                "filename": row["filename"],
                "created_at": row["created_at"],
                "pages": row["pages"],
                "title": row["title"],
                "year": meta.get("year"),
                "authors": meta.get("authors", []),
                "domain": meta.get("domain"),
                "analysis_status": row["analysis_status"] or "done",
                "analysis_error": row["analysis_error"],
                "grounded": grounding is not None,
                "open_gaps": sum(1 for g in gaps if g.get("verdict") == "open"),
                "total_gaps": len(gaps) or len((analysis or {}).get("gaps", [])),
            }
        )
    return out


def delete_paper(paper_id: str, owner_id: str | None = None) -> int:
    """Delete a paper and its chat. Returns rows removed, so a caller can tell
    "deleted" from "was never yours"."""
    conn = connect()
    with _lock:
        conn.execute("DELETE FROM messages WHERE paper_id = ?", (paper_id,))
        if owner_id is None:
            cursor = conn.execute("DELETE FROM papers WHERE id = ?", (paper_id,))
        else:
            cursor = conn.execute(
                "DELETE FROM papers WHERE id = ? AND owner_id = ?", (paper_id, owner_id)
            )
        conn.commit()
    return cursor.rowcount


# --- chat -----------------------------------------------------------------


def add_message(paper_id: str, role: str, content: str) -> None:
    conn = connect()
    with _lock:
        conn.execute(
            "INSERT INTO messages (paper_id, role, content, created_at) VALUES (?,?,?,?)",
            (paper_id, role, content, _now()),
        )
        conn.commit()


def get_messages(paper_id: str, limit: int = 40) -> list[dict[str, str]]:
    conn = connect()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE paper_id = ? ORDER BY id DESC LIMIT ?",
        (paper_id, limit),
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


# --- discovery projects ---------------------------------------------------


def create_project(topic: str, goal: str = "gaps", owner_id: str | None = None) -> str:
    project_id = uuid.uuid4().hex[:12]
    now = _now()
    conn = connect()
    with _lock:
        conn.execute(
            """INSERT INTO discovery_projects
               (id, topic, goal, created_at, updated_at, owner_id)
               VALUES (?,?,?,?,?,?)""",
            (project_id, topic, goal, now, now, owner_id),
        )
        conn.commit()
    return project_id


def _row_to_project(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "topic": row["topic"],
        "goal": row["goal"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_project(project_id: str, owner_id: str | None = None) -> dict[str, Any] | None:
    """Passing owner_id scopes the lookup, so another account's id reads as
    missing rather than forbidden."""
    conn = connect()
    if owner_id is None:
        row = conn.execute(
            "SELECT * FROM discovery_projects WHERE id = ?", (project_id,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM discovery_projects WHERE id = ? AND owner_id = ?",
            (project_id, owner_id),
        ).fetchone()
    return _row_to_project(row) if row else None


def list_projects(owner_id: str | None = None) -> list[dict[str, Any]]:
    """Projects newest first, each with a lightweight run summary."""
    conn = connect()
    rows = conn.execute(
        """SELECT p.*,
                  (SELECT COUNT(*) FROM discovery_runs r WHERE r.project_id = p.id) AS run_count,
                  (SELECT r.status FROM discovery_runs r WHERE r.project_id = p.id
                   ORDER BY r.created_at DESC LIMIT 1) AS last_status
           FROM discovery_projects p
           WHERE (? IS NULL OR p.owner_id = ?)
           ORDER BY p.updated_at DESC""",
        (owner_id, owner_id),
    ).fetchall()
    out = []
    for row in rows:
        project = _row_to_project(row)
        project["run_count"] = row["run_count"]
        project["last_status"] = row["last_status"]
        out.append(project)
    return out


def delete_project(project_id: str, owner_id: str | None = None) -> int:
    """Returns rows removed, so a caller can tell "deleted" from "not yours"."""
    conn = connect()
    with _lock:
        if owner_id is not None:
            owned = conn.execute(
                "SELECT 1 FROM discovery_projects WHERE id = ? AND owner_id = ?",
                (project_id, owner_id),
            ).fetchone()
            if owned is None:
                return 0
        # Explicit, because SQLite only enforces ON DELETE CASCADE when the
        # foreign_keys pragma is on, and this connection does not enable it.
        conn.execute("DELETE FROM discovery_runs WHERE project_id = ?", (project_id,))
        cursor = conn.execute(
            "DELETE FROM discovery_projects WHERE id = ?", (project_id,)
        )
        conn.commit()
    return cursor.rowcount


# --- discovery runs -------------------------------------------------------


def reap_orphaned_runs() -> int:
    """Mark runs still flagged `running` at startup as interrupted.

    A run only executes inside a live process, so anything still `running` when
    the app boots died with the previous process. Without this it would poll as
    in-progress for ever.
    """
    conn = connect()
    with _lock:
        cursor = conn.execute(
            """UPDATE discovery_runs
               SET status = 'error',
                   error = 'Run was interrupted when the server restarted.',
                   updated_at = ?
               WHERE status = 'running'""",
            (_now(),),
        )
        conn.commit()
    return cursor.rowcount


def create_run(
    project_id: str,
    query: str,
    goal: str,
    max_papers: int,
    max_validations: int,
) -> str:
    """Record a run as `running` before the pipeline starts, so a crashed or
    still-executing run is always visible rather than silently missing."""
    run_id = uuid.uuid4().hex[:12]
    now = _now()
    conn = connect()
    with _lock:
        conn.execute(
            """INSERT INTO discovery_runs
               (id, project_id, status, created_at, updated_at, query, goal,
                max_papers, max_validations, trace, result, error, llm_calls)
               VALUES (?,?,'running',?,?,?,?,?,?,'[]',NULL,NULL,0)""",
            (run_id, project_id, now, now, query, goal, max_papers, max_validations),
        )
        conn.execute(
            "UPDATE discovery_projects SET updated_at = ? WHERE id = ?", (now, project_id)
        )
        conn.commit()
    return run_id


def append_run_step(run_id: str, step: str, data: dict | None = None) -> None:
    """Append one progress step to a run's trace, so the UI can poll a live run."""
    conn = connect()
    with _lock:
        row = conn.execute(
            "SELECT trace FROM discovery_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return
        trace = json.loads(row["trace"]) if row["trace"] else []
        trace.append({"step": step, "data": data or {}, "at": _now()})
        conn.execute(
            "UPDATE discovery_runs SET trace = ?, updated_at = ? WHERE id = ?",
            (json.dumps(trace), _now(), run_id),
        )
        conn.commit()


def finish_run(run_id: str, result: dict) -> None:
    """Store a completed run's result.

    The pipeline reports soft failures ("no papers found", "no papers could be
    analysed") as an `error` key on an otherwise well-formed result, so a run
    that came back empty is recorded as `error` rather than a successful one
    that happens to have no opportunities.
    """
    error = (result.get("error") or "").strip() or None
    conn = connect()
    with _lock:
        conn.execute(
            """UPDATE discovery_runs
               SET status = ?, result = ?, error = ?, llm_calls = ?, updated_at = ?
               WHERE id = ?""",
            (
                "error" if error else "done",
                json.dumps(result),
                error,
                int(result.get("llm_calls") or 0),
                _now(),
                run_id,
            ),
        )
        conn.commit()


def fail_run(run_id: str, error: str) -> None:
    conn = connect()
    with _lock:
        conn.execute(
            "UPDATE discovery_runs SET status = 'error', error = ?, updated_at = ? WHERE id = ?",
            (error, _now(), run_id),
        )
        conn.commit()


def _row_to_run(row: sqlite3.Row, include_result: bool = True) -> dict[str, Any]:
    run = {
        "id": row["id"],
        "project_id": row["project_id"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "query": row["query"],
        "goal": row["goal"],
        "max_papers": row["max_papers"],
        "max_validations": row["max_validations"],
        "llm_calls": row["llm_calls"],
        "error": row["error"],
        "trace": json.loads(row["trace"]) if row["trace"] else [],
    }
    if include_result:
        run["result"] = json.loads(row["result"]) if row["result"] else None
    return run


def get_run(run_id: str, include_result: bool = True) -> dict[str, Any] | None:
    conn = connect()
    row = conn.execute("SELECT * FROM discovery_runs WHERE id = ?", (run_id,)).fetchone()
    return _row_to_run(row, include_result) if row else None


def list_runs(project_id: str) -> list[dict[str, Any]]:
    """Runs for a project, newest first. Result JSON is omitted - it is large
    and the list view only needs status and progress."""
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM discovery_runs WHERE project_id = ? ORDER BY created_at DESC",
        (project_id,),
    ).fetchall()
    return [_row_to_run(row, include_result=False) for row in rows]
