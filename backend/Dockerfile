# MediLens AI — backend image (FastAPI + SQLAlchemy + PyMuPDF + Tesseract)
#
# Build context is the repository root, because the backend imports the
# top-level packages (database, ai, rag, ocr, document_processing, prompts).
#
#   docker build -f backend/Dockerfile -t medilens-backend .

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Tesseract is optional at runtime (text-layer PDFs never need it), but the
# default OCR_PROVIDER=tesseract expects the binary to be present.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first so code changes reuse the cached layer.
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY . .

# Runtime directories (uploads, exports, sqlite fallback) live inside /app.
RUN mkdir -p /app/backend/var/uploads /app/backend/var/exports

EXPOSE 8000

# Apply migrations, then serve. DATABASE_URL comes from the environment.
CMD ["sh", "-c", "alembic upgrade head && uvicorn backend.main:app --host 0.0.0.0 --port 8000"]
