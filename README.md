# Tallied

Free MVP: upload a UK tenant's bank statements and payslips (no account needed) and get a plain-English
report on whether the story adds up, with evidence and a feedback form.

```
api/       Python 3.12 FastAPI app (uv, pytest, ruff)
web/       Next.js (App Router, TypeScript), Tailwind, Framer Motion
fixtures/  Synthetic test documents + generator (see fixtures/README.md)
```

## Prerequisites

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Node.js 20+ and npm

## API

```bash
cd api
cp .env.example .env                     # fill in keys; never commit .env
uv sync                                  # creates .venv with app + dev dependencies
uv run python scripts/local_db.py start  # private Postgres on 127.0.0.1:55432 (or: docker compose up -d)
uv run alembic upgrade head              # create tables
uv run uvicorn app.main:app --reload --port 8000
```

Check it's up at http://localhost:8000/health (returns `{"status":"ok"}`). Interactive docs are at
http://localhost:8000/docs.

### Endpoints

| Method | Path | What it does |
|---|---|---|
| POST | `/checks` | Multipart: `files` (1-12 PDF/JPG/PNG, 10 MB each), `monthly_rent`, `consent=true`. Returns an id; processing runs in the background |
| GET | `/checks/{id}/progress` | Which of the six checks are done (poll this) |
| GET | `/checks/{id}` | The finished report (202 while processing, 410 after 24 hours) |
| GET | `/checks/{id}/pages/{file}/{page}.png` | Rendered page image for the evidence view |
| POST | `/checks/{id}/feedback` | `{"vote": "up"\|"down", "comment"?, "email"?}` |

```bash
curl -F files=@fixtures/genuine/statement_2026-08.pdf -F files=@fixtures/genuine/payslip_2026-09.pdf \
     -F monthly_rent=1100 -F consent=true http://localhost:8000/checks
```

Every error is JSON: `{"error": {"code": "...", "message": "plain English"}}`.

Tests and lint (API tests need the local Postgres running):

```bash
cd api
TEST_DATABASE_URL=postgresql+psycopg://tallied@127.0.0.1:55432/tallied_test uv run pytest
uv run ruff check . ../fixtures
```

The cleanup job runs inside the API every `CLEANUP_INTERVAL_MINUTES`. It can also run on its own
(e.g. as a Railway cron): `uv run python -m app.services.cleanup`.

## Web

```bash
cd web
cp .env.example .env.local
npm install
npm run dev
```

Open http://localhost:3000 (the API must be running too).

- `/` is the upload screen. `/check/[id]` shows the live checks. `/report/[id]` is a placeholder until
  the report screen is built.
- The browser only ever calls this app's own `/api/checks...` routes. They forward to the API from the
  server, so `API_URL` and `WEB_PROXY_SECRET` (in `web/.env.local`) never reach the browser.
- Set the same `WEB_PROXY_SECRET` in `api/.env` so the API rate-limits each visitor rather than the web
  server. On Vercel the visitor's IP comes from Vercel's `x-real-ip`. Elsewhere, only set
  `TRUST_FORWARDED_FOR=true` behind a proxy that overwrites `X-Forwarded-For`. Locally, every check
  counts against one shared limit.
- Design tokens (colours and fonts) live in `web/tailwind.config.mts`. Use the names (`bg-canvas`,
  `text-ink`, `bg-accent`, `text-closer`...), never hex values.

## Fixtures

```bash
cd api
uv run python ../fixtures/generate.py
```

## Privacy rules

- Uploaded documents are only ever written to a temp folder and are deleted when processing ends,
  including on errors. Only rendered page images and the report are stored, and both are deleted
  24 hours after the check.
- Check ids are 256-bit random tokens. IP addresses are never stored, only salted hashes for rate
  limiting. Feedback emails are kept apart from reports.
- Logs contain check IDs, timings and error types only. They never contain document contents,
  transactions, names or amounts.
- Secrets live in `.env` files (git-ignored). `.env.example` files hold placeholders.
# Tailed
