# Tallied API (FastAPI). Deployed on Coolify as a Dockerfile application; the 24-hour cleanup runs
# inside this same container as a Coolify scheduled task (see DEPLOY.md).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Same uv version the lockfile was made with.
COPY --from=ghcr.io/astral-sh/uv:0.9.28 /uv /uvx /bin/

WORKDIR /app

# Dependencies first (cached between builds), runtime only: no test or lint tools.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY scripts/start.sh ./scripts/start.sh

# Run as an unprivileged user. Uploads go to /tmp/tallied-uploads and are deleted after each check.
RUN useradd --create-home --uid 10001 tallied && mkdir -p /tmp/tallied-uploads \
    && chown tallied /tmp/tallied-uploads
USER tallied

ENV PATH="/app/.venv/bin:$PATH" \
    UPLOAD_TEMP_DIR=/tmp/tallied-uploads \
    ENVIRONMENT=production

EXPOSE 8000
# Migrations, then uvicorn on $PORT (8000), never trusting forwarded headers (see scripts/start.sh).
CMD ["sh", "scripts/start.sh"]
