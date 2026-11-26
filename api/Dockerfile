# Tallied API (FastAPI). Deployed to Heroku's container stack via ../heroku.yml (see DEPLOY.md).
# Heroku builds this from the api/ folder, runs migrations in the release phase, then starts the
# CMD below on a single Basic web dyno. The 24-hour cleanup runs from Heroku Scheduler in a one-off
# dyno using this same image.
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

# Run as an unprivileged user locally. Heroku ignores USER and runs the container as its own random
# non-root user, so nothing here may depend on this uid owning a folder. In particular the upload
# folder is NOT created at build time: the app creates /tmp/tallied-uploads (mode 700) on first use,
# as whichever user it runs as. /tmp is on the dyno's ephemeral disk, wiped on every restart.
RUN useradd --create-home --uid 10001 tallied
USER tallied

ENV PATH="/app/.venv/bin:$PATH" \
    UPLOAD_TEMP_DIR=/tmp/tallied-uploads \
    ENVIRONMENT=production

# Heroku ignores EXPOSE and sets $PORT; this is only for `docker run -p 8000:8000` locally.
EXPOSE 8000
# Migrations, then uvicorn on $PORT (8000 if unset), never trusting forwarded headers (scripts/start.sh).
CMD ["sh", "scripts/start.sh"]
