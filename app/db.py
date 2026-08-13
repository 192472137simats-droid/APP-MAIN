"""Tiny SQLite layer. No ORM - the schema is four tables wide."""

import json
import sqlite3
import threading
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
            """
        )
        conn.commit()


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
) -> None:
    conn = connect()
    with _lock:
        conn.execute(
            """INSERT OR REPLACE INTO papers
               (id, filename, created_at, pages, characters, title, full_text, sections, analysis, grounding)
               VALUES (?,?,?,?,?,?,?,?,?, COALESCE((SELECT grounding FROM papers WHERE id = ?), NULL))""",
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
            ),
        )
        conn.commit()


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
    }
    if include_text:
        paper["full_text"] = row["full_text"]
        paper["sections"] = json.loads(row["sections"]) if row["sections"] else {}
    return paper


def get_paper(paper_id: str, include_text: bool = False) -> dict[str, Any] | None:
    conn = connect()
    row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
    return _row_to_paper(row, include_text) if row else None


def list_papers() -> list[dict[str, Any]]:
    conn = connect()
    rows = conn.execute(
        """SELECT id, filename, created_at, pages, characters, title, analysis, grounding
           FROM papers ORDER BY created_at DESC"""
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
                "grounded": grounding is not None,
                "open_gaps": sum(1 for g in gaps if g.get("verdict") == "open"),
                "total_gaps": len(gaps) or len((analysis or {}).get("gaps", [])),
            }
        )
    return out


def delete_paper(paper_id: str) -> None:
    conn = connect()
    with _lock:
        conn.execute("DELETE FROM messages WHERE paper_id = ?", (paper_id,))
        conn.execute("DELETE FROM papers WHERE id = ?", (paper_id,))
        conn.commit()


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
