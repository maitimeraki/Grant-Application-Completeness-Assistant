# syntax=docker/dockerfile:1

# ---------- stage 1: build the frontend ----------
FROM node:20-bookworm-slim AS frontend-build
WORKDIR /build

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# ---------- stage 2: backend runtime + built SPA ----------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    DATABASE_URL=sqlite:////data/app.db \
    DATA_DIR=/data/uploads \
    STORAGE_DIR=/data/storage \
    LOG_LEVEL=INFO

WORKDIR /app/backend

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# backend/ contents land in /app/backend (app/, alembic/, alembic.ini, ...)
COPY backend/ ./
# reviewer sample inputs (seeded into the DB on first boot — see app/services/seed.py)
COPY samples/ /app/samples/
RUN test -f /app/samples/grant-guideline.md \
    && test -f /app/samples/second/draft-application-weak.md
# built SPA — served by FastAPI (see app/main.py) at the same origin as the API
COPY --from=frontend-build /build/dist /app/frontend/dist

COPY docker/entrypoint.sh /entrypoint.sh

# non-root user; /data is the volume mount point for persistence
RUN useradd --create-home --uid 10001 appuser \
    && chmod +x /entrypoint.sh \
    && mkdir -p /data/uploads /data/storage \
    && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
