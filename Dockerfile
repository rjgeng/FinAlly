# syntax=docker/dockerfile:1

# ── Stage 1: Build Next.js frontend ──────────────────────────────────────
FROM node:20-slim AS frontend-builder
WORKDIR /build/frontend

# Install dependencies first for better layer caching
COPY frontend/package*.json ./
RUN npm ci

# Copy the rest of the frontend source and build static export
COPY frontend/ ./
RUN npm run build
# Static export output is in /build/frontend/out/

# ── Stage 2: Python backend ──────────────────────────────────────────────
FROM python:3.12-slim AS final
WORKDIR /app

# Install uv (fast Python project manager)
RUN pip install --no-cache-dir uv

# Copy backend project files and install dependencies (production only)
COPY backend/pyproject.toml backend/uv.lock ./backend/
COPY backend/ ./backend/
RUN cd backend && uv sync --no-dev --frozen

# Copy static frontend build from stage 1
COPY --from=frontend-builder /build/frontend/out/ ./static/

# Runtime: ensure the db directory exists for the SQLite volume mount
RUN mkdir -p /app/db

EXPOSE 8000

CMD ["backend/.venv/bin/uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
