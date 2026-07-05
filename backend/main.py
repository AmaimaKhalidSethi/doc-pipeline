"""
Intelligent Document Processing Pipeline -- FastAPI backend.

Written for: fastapi==0.139.0, python-multipart==0.0.32

Architecture (matches the project brief):
File Upload (FastAPI) -> Document Parser (PyMuPDF/python-docx) ->
Multi-Tool Extraction Agent -> Pydantic Validation -> SQLite -> REST API
-> Streamlit frontend (in ../frontend)
"""
from __future__ import annotations

import logging
import os
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

import db
from extraction import ExtractionAgent
from parsers import UnsupportedFileType, SUPPORTED_EXTENSIONS, extract_text, file_type_from_name
from schemas import DocumentDetail, DocumentExtraction, DocumentRecord

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("doc_pipeline")

_agent: ExtractionAgent | None = None

# Security/reliability limits. Enforced here (not just in the Streamlit
# frontend's config.toml maxUploadSize) because the API is reachable
# directly, independent of the frontend.
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", 25 * 1024 * 1024))  # 25 MB

# CORS: default to the Streamlit dev/compose origins; override via env var
# for other deployments. Avoids allow_origins=["*"], which is unnecessarily
# permissive for an API that has mutating endpoints (upload/delete) and no
# auth layer in front of it.
_default_origins = "http://localhost:8501,http://127.0.0.1:8501,http://frontend:8501"
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("ALLOWED_ORIGINS", _default_origins).split(",")
    if origin.strip()
]


def get_agent() -> ExtractionAgent:
    global _agent
    if _agent is None:
        _agent = ExtractionAgent()
    return _agent


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: on_event("startup") is deprecated as of current FastAPI;
    # lifespan is the documented replacement.
    db.init_db()
    logger.info("startup db_path=%s allowed_origins=%s", db.DB_PATH, ALLOWED_ORIGINS)
    yield
    # No shutdown cleanup needed (sqlite3 connections are opened/closed per-call).


app = FastAPI(
    title="Intelligent Document Processing Pipeline",
    description=(
        "Upload a PDF, DOCX, or TXT document and get back a validated "
        "extraction: summary, entities, key dates, key terms, and action items."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


# --- Minimal in-memory rate limiting -------------------------------------
# The extraction endpoint calls a paid, rate-limited upstream API (Groq),
# so an unauthenticated client hammering /documents/upload is both a cost
# risk and a DoS vector against the shared Groq rate limit. This is a
# simple fixed-window limiter per client IP -- adequate for a single-process
# deployment; a multi-worker/production deployment should move this to a
# shared store (e.g. Redis) instead, which is a bigger architectural change
# left for that point.
_UPLOAD_RATE_LIMIT = int(os.environ.get("UPLOAD_RATE_LIMIT_PER_MINUTE", 10))
_upload_timestamps: dict[str, deque] = defaultdict(deque)


def _client_is_rate_limited(client_ip: str) -> bool:
    now = time.monotonic()
    window = _upload_timestamps[client_ip]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= _UPLOAD_RATE_LIMIT:
        return True
    window.append(now)
    return False


@app.middleware("http")
async def rate_limit_uploads(request: Request, call_next):
    if request.url.path == "/documents/upload" and request.method == "POST":
        client_ip = request.client.host if request.client else "unknown"
        if _client_is_rate_limited(client_ip):
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many uploads. Please wait a minute and try again."},
            )
    return await call_next(request)


def _sanitize_filename(filename: str) -> str:
    """
    Defense-in-depth only: this filename is never used to construct a
    filesystem path (parsers work on in-memory bytes, and it's stored in
    SQLite via a parameterized query), so this isn't fixing a path-traversal
    bug. It strips characters that could otherwise render oddly in the
    Streamlit UI (which treats some labels as lightly markdown-aware) and
    caps length before it's stored/displayed.
    """
    name = os.path.basename(filename).strip()
    name = re.sub(r"[^\w.\-() ]", "_", name)
    return name[:255] or "unnamed_file"


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/documents", response_model=list[DocumentRecord])
def get_documents() -> list[DocumentRecord]:
    return db.list_documents()


@app.get("/documents/{document_id}", response_model=DocumentDetail)
def get_document(document_id: int) -> DocumentDetail:
    detail = db.get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
    return detail


@app.delete("/documents/{document_id}")
def remove_document(document_id: int) -> dict:
    deleted = db.delete_document(document_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
    return {"deleted": document_id}


@app.post("/documents/upload", response_model=DocumentDetail)
def upload_document(file: UploadFile = File(...)) -> DocumentDetail:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file has no filename.")

    if not any(file.filename.lower().endswith(ext) for ext in SUPPORTED_EXTENSIONS):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Supported: {sorted(SUPPORTED_EXTENSIONS)}",
        )

    # Enforce the size cap ourselves: UploadFile doesn't reject oversized
    # bodies on its own, and this endpoint is reachable directly (not just
    # through the Streamlit frontend's own maxUploadSize), so relying on
    # the frontend's limit alone isn't sufficient.
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB upload limit.",
        )
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    safe_filename = _sanitize_filename(file.filename)

    try:
        text = extract_text(safe_filename, content)
    except UnsupportedFileType as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:  # noqa: BLE001 - surface parser failures as 422s, not 500s
        # Log the real exception server-side; don't echo library/internal
        # error text back to the client (it can leak internals and isn't
        # actionable for the caller anyway).
        logger.exception("parse_failed filename=%s", safe_filename)
        raise HTTPException(
            status_code=422, detail=f"Could not parse '{safe_filename}'. Is the file corrupted or password-protected?"
        ) from None

    if not text.strip():
        raise HTTPException(
            status_code=422,
            detail=f"No extractable text found in '{safe_filename}' (is it a scanned/image-only PDF?).",
        )

    try:
        extraction: DocumentExtraction = get_agent().extract(text)
    except Exception:  # noqa: BLE001 - upstream LLM/API failure
        logger.exception("extraction_failed filename=%s", safe_filename)
        raise HTTPException(
            status_code=502, detail="Extraction failed due to an upstream error. Please try again."
        ) from None

    document_id = db.insert_document(
        filename=safe_filename,
        file_type=file_type_from_name(safe_filename),
        raw_text=text,
        extraction=extraction,
    )
    detail = db.get_document(document_id)
    assert detail is not None
    return detail
