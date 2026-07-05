# Written for fastapi==0.139.0 / python 3.12
FROM python:3.12-slim

WORKDIR /app

# PyMuPDF ships manylinux wheels for this platform, no build toolchain needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV DOC_PIPELINE_DB=/data/doc_pipeline.db
RUN mkdir -p /data

# Run as a non-root user rather than the container default (root).
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# --reload is for local dev only; production mode (no reload) is the
# current FastAPI CLI recommendation for containers.
CMD ["fastapi", "run", "main.py", "--host", "0.0.0.0", "--port", "8000"]
