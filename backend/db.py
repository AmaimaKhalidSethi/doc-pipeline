"""
SQLite persistence for the document pipeline.

Plain stdlib sqlite3 rather than an ORM: two tables, simple queries, and the
project brief only asks for "Database (SQLite)" -- adding SQLAlchemy would
be pure overhead here. FastAPI runs sync `def` path functions in a
threadpool automatically, so these blocking sqlite3 calls don't block the
event loop as long as the endpoints that use them stay `def`, not
`async def` (see main.py).
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from schemas import ActionItem, DocumentDetail, DocumentExtraction, DocumentRecord

DB_PATH = Path(os.environ.get("DOC_PIPELINE_DB", str(Path(__file__).parent / "doc_pipeline.db")))

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    raw_text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extractions (
    document_id INTEGER PRIMARY KEY REFERENCES documents(id),
    summary TEXT NOT NULL,
    entities_json TEXT NOT NULL,
    key_dates_json TEXT NOT NULL,
    key_terms_json TEXT NOT NULL,
    action_items_json TEXT NOT NULL
);
"""


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA)


def insert_document(filename: str, file_type: str, raw_text: str, extraction: DocumentExtraction) -> int:
    uploaded_at = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO documents (filename, file_type, uploaded_at, raw_text) VALUES (?, ?, ?, ?)",
            (filename, file_type, uploaded_at, raw_text),
        )
        document_id = cursor.lastrowid
        conn.execute(
            """INSERT INTO extractions
               (document_id, summary, entities_json, key_dates_json, key_terms_json, action_items_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                document_id,
                extraction.summary,
                json.dumps(extraction.entities),
                json.dumps(extraction.key_dates),
                json.dumps(extraction.key_terms),
                json.dumps([item.model_dump() for item in extraction.action_items]),
            ),
        )
        return document_id


def list_documents() -> list[DocumentRecord]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, filename, file_type, uploaded_at, LENGTH(raw_text) AS char_count "
            "FROM documents ORDER BY id DESC"
        ).fetchall()
    return [DocumentRecord(**dict(row)) for row in rows]


def get_document(document_id: int) -> DocumentDetail | None:
    with get_connection() as conn:
        doc_row = conn.execute(
            "SELECT id, filename, file_type, uploaded_at, raw_text FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        if doc_row is None:
            return None
        ext_row = conn.execute(
            "SELECT * FROM extractions WHERE document_id = ?", (document_id,)
        ).fetchone()

    extraction = DocumentExtraction(
        summary=ext_row["summary"],
        entities=json.loads(ext_row["entities_json"]),
        key_dates=json.loads(ext_row["key_dates_json"]),
        key_terms=json.loads(ext_row["key_terms_json"]),
        action_items=[ActionItem(**item) for item in json.loads(ext_row["action_items_json"])],
    )
    raw_text = doc_row["raw_text"]
    return DocumentDetail(
        id=doc_row["id"],
        filename=doc_row["filename"],
        file_type=doc_row["file_type"],
        uploaded_at=doc_row["uploaded_at"],
        char_count=len(raw_text),
        text_preview=raw_text[:1000],
        extraction=extraction,
    )


def delete_document(document_id: int) -> bool:
    with get_connection() as conn:
        conn.execute("DELETE FROM extractions WHERE document_id = ?", (document_id,))
        cursor = conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        return cursor.rowcount > 0
