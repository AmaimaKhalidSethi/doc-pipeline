# Intelligent Document Processing Pipeline

Project 2-P-A from CalderR's Agentic AI Engineering Internship, Week 2
(Prompt Engineering & Tool Calling), built with a Streamlit frontend
instead of a plain "simple frontend."

Upload a PDF, DOCX, or TXT document and get back a validated extraction:
summary, entities, key dates, key terms, and action items -- stored in
SQLite and served over a REST API, with a Streamlit UI on top.

```
File Upload (Streamlit) -> FastAPI -> Document Parser (PyMuPDF/python-docx)
   -> Multi-Tool Extraction Agent (Groq, strict structured outputs)
   -> Pydantic Validation -> SQLite -> REST API -> Streamlit UI
```

## Security Audit (auto-audit-fix pass, 2026-07-05)

This project went through a full audit-and-fix pass after the initial build.
Every fix below is already applied in this codebase; see the changelog
delivered alongside this README for the itemized list. Highlights:

- CORS was wildcard (`allow_origins=["*"]`) with no auth in front of
  mutating endpoints -- restricted to configurable origins.
- No upload size limit was enforced at the API layer (only in the
  Streamlit frontend's own config, which a direct API caller bypasses) --
  added a `MAX_UPLOAD_BYTES` cap, returning `413`.
- No rate limiting on the endpoint that calls the paid Groq API -- added a
  simple per-IP fixed-window limiter (`UPLOAD_RATE_LIMIT_PER_MINUTE`).
- Parse/extraction failures echoed raw exception text back to callers --
  now logged server-side only, generic message to the client.
- Both Docker images ran as root -- both now run as a non-root `appuser`.
- `groq` bumped 1.1.1 -> 1.5.0 (1.1.2 sanitized endpoint path params);
  `starlette` explicitly pinned `>=1.0.1` (fixes CVE-2026-48710 "BadHost",
  a Host-header validation bypass -- already satisfied transitively via
  `fastapi[standard]`, pinned explicitly given the severity).
- `pymupdf`, `python-multipart`, `pydantic`, and `streamlit` versions were
  checked against known CVEs (CVE-2026-3029, CVE-2026-53539/40347/24486,
  CVE-2026-33682) -- all already past the fixed versions, no change needed.
- Added `.gitignore` and `.env.example` (neither existed).

**Not fixed -- needs your decision:** there is still no authentication on
any endpoint. Anyone who can reach the API can upload, view, and delete
documents. Adding auth (API key header, JWT, or similar) is a real
architecture decision -- left for you to choose rather than picked
unilaterally.

## Research Findings (per research-before-code workflow, run 2026-07-05)

**Versions used:**
- `fastapi` 0.139.0 (with `python-multipart` 0.0.32 for file uploads)
- `pymupdf` 1.27.2 -- import as `pymupdf`, not the legacy `fitz` alias
- `python-docx` 1.2.0
- `pydantic` 2.13.4
- `groq` 1.1.1
- `streamlit` 1.58.0

**Breaking changes / deprecations to note:**
- **`@app.on_event("startup")` is deprecated** in current FastAPI in favor
  of the `lifespan` context-manager pattern. `backend/main.py` uses
  `lifespan=` instead -- an easy thing to get wrong if you write FastAPI
  from memory, since a huge amount of existing tutorial content (and
  training data) still shows `on_event`.
- **Streamlit's `use_container_width` param is deprecated** (removed after
  2025-12-31) in favor of `width="stretch"` / `width="content"`.
  `frontend/app.py` uses the new `width=` parameter.
- Groq deprecated `llama-3.3-70b-versatile` / `llama-3.1-8b-instant`; this
  project uses `openai/gpt-oss-120b`.

**Gotchas found:**
- Groq's strict structured-outputs mode requires `additionalProperties:
  false` and every property in `required` at **every** object level,
  including nested models. `DocumentExtraction` has a nested
  `list[ActionItem]`, so `backend/extraction.py`'s `build_strict_schema()`
  walks the Pydantic-generated `$defs` recursively, not just the top-level
  schema (the Smart Form Filler project's flat-schema version wouldn't
  have caught this).
- **Streamlit theming, and the reason for this project's `config.toml`:**
  current Streamlit supports separate `[theme.light]` and `[theme.dark]`
  tables in `.streamlit/config.toml`, which the user can switch between
  from the built-in Settings menu. That's what `frontend/.streamlit/config.toml`
  uses -- two explicit, high-contrast palettes -- instead of relying on
  Streamlit's default light theme, which is low-contrast enough that UI
  elements can be hard to distinguish. No custom CSS injection was needed
  once the config.toml tables were set correctly.
- `python:3.12-slim` doesn't include `curl`, which the frontend
  Dockerfile's `HEALTHCHECK` needs -- installed explicitly via `apt-get`.

## Architecture

**Backend (`backend/`)**
- `schemas.py` -- `DocumentExtraction` (with nested `ActionItem`) and the
  API response models.
- `parsers.py` -- dispatches to PyMuPDF (PDF), python-docx (DOCX), or plain
  decoding (TXT) based on file extension.
- `extraction.py` -- `ExtractionAgent`: builds the strict JSON Schema
  (recursively, for nested models), calls Groq with retry/exponential
  backoff, validates the response against `DocumentExtraction`.
- `db.py` -- SQLite persistence (`documents` + `extractions` tables), using
  stdlib `sqlite3` -- FastAPI runs sync `def` path functions in a
  threadpool automatically, so this doesn't block the event loop.
- `main.py` -- FastAPI app: `POST /documents/upload`, `GET /documents`,
  `GET /documents/{id}`, `DELETE /documents/{id}`, `GET /health`.

**Frontend (`frontend/`)**
- `app.py` -- Streamlit UI: an Upload tab and a Document Library tab, both
  talking to the backend over HTTP via `BACKEND_URL`.
- `.streamlit/config.toml` -- explicit light/dark theme tables (see
  "Gotchas" above).

## Usage

### Local (two terminals)

```bash
# Terminal 1 -- backend
cd backend
pip install -r requirements.txt
export GROQ_API_KEY=your_key_here
fastapi dev main.py   # http://localhost:8000/docs for interactive API docs

# Terminal 2 -- frontend
cd frontend
pip install -r requirements.txt
export BACKEND_URL=http://localhost:8000
streamlit run app.py   # http://localhost:8501
```

### Docker Compose (recommended)

```bash
export GROQ_API_KEY=your_key_here
docker compose up --build
```

- Frontend: http://localhost:8501
- Backend API docs: http://localhost:8000/docs
- SQLite data persists in the `doc-pipeline-data` named volume across restarts.

To switch between the light and dark themes, use Streamlit's own
**⋮ menu → Settings → Choose app theme** in the running app.

## Testing & Validation

```bash
pip install -r backend/requirements.txt pytest
pytest tests/test_pipeline.py -v      # parsers, strict schema, full API lifecycle
python tests/run_demo.py              # offline plumbing check on all 5 sample docs
python tests/run_demo.py --live       # same, but with the real Groq extraction agent
```

`tests/test_pipeline.py` covers PDF/DOCX/TXT parsing (against real
generated files, not fixtures), the recursive strict-schema builder, and
the full upload -> list -> detail -> delete API lifecycle -- all offline,
with the extraction agent mocked, so no `GROQ_API_KEY` or network access is
needed. This sandbox's network allowlist doesn't include `api.groq.com`,
so live-mode extraction accuracy hasn't been verified here; run
`python tests/run_demo.py --live` on a machine with API access for that.

## Sample Documents

`sample_documents/` has 5 real files spanning all 3 supported formats:
- `project_kickoff.txt`, `vendor_contract_summary.txt`,
  `incident_postmortem.txt` (TXT)
- `board_update_q2.docx` (DOCX, includes a table)
- `product_launch_review.pdf` (PDF)

Each contains realistic entities, dates, and action items so extraction
quality is easy to eyeball once you're running in `--live` mode.

## CI

`.github/workflows/ci.yml` runs the offline pytest suite and the mock demo
on every push/PR, plus a separate job that builds both Docker images to
catch Dockerfile breakage early.
