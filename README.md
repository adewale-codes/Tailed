# Tallied

Free MVP: upload a UK tenant's bank statements and payslips (no account needed) and get a plain-English
report on whether the story adds up, with evidence and a feedback form.

```
api/       Python 3.12 FastAPI app (uv, pytest, ruff)
web/       Next.js (App Router, TypeScript), Tailwind, Framer Motion
fixtures/  Synthetic test documents + generator (see fixtures/README.md)
```

Deploying (API and Postgres on Hetzner with Coolify, web app on Vercel), every environment variable, and the pre-launch checklist: see
[DEPLOY.md](DEPLOY.md).

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
| POST | `/uploads` | Opens a chunked upload session (the web app uses this to stay under Vercel's 4.5 MB request cap) |
| POST | `/uploads/{id}/chunks` | Multipart: `index`, `offset`, `name`, `chunk` (up to 4 MB) |
| POST | `/checks` | Multipart: `files` (1-12 PDF/JPG/PNG, 10 MB each) or `upload_id`, plus `monthly_rent`, `consent=true`. Returns an id; processing runs in the background |
| GET | `/checks/{id}/progress` | Which of the seven checks are done (poll this) |
| GET | `/checks/{id}` | The finished report (202 while processing, 410 after 24 hours) |
| GET | `/checks/{id}/pages/{file}/{page}.png` | Rendered page image for the evidence view |
| POST | `/checks/{id}/feedback` | `{"vote": "up"\|"down", "follow_up"?, "comment"?, "email"?}` |
| POST | `/checks/{id}/events` | `{"name": "report_viewed"\|"evidence_opened"}` (analytics, no cookies) |

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
(in production, a Coolify scheduled task): `uv run python -m app.services.cleanup`.

## Web

```bash
cd web
cp .env.example .env.local
npm install
npm run dev
```

Open http://localhost:3000 (the API must be running too).

- `/` is the upload screen. `/check/[id]` shows the live checks. `/report/[id]` is the report, and
  `/report/[id]/evidence/[findingId]` shows each finding highlighted on the page image.
- The browser only ever calls this app's own `/api/checks...` routes. They forward to the API from the
  server, so `API_URL` and `WEB_PROXY_SECRET` (in `web/.env.local`) never reach the browser.
- Set the same `WEB_PROXY_SECRET` in `api/.env` so the API rate-limits each visitor rather than the web
  server. On Vercel the visitor's IP comes from Vercel's `x-real-ip`. Elsewhere, only set
  `TRUST_FORWARDED_FOR=true` behind a proxy that overwrites `X-Forwarded-For`. Locally, every check
  counts against one shared limit.
- Design tokens (colours and fonts) live in `web/tailwind.config.mts`. Use the names (`bg-canvas`,
  `text-ink`, `bg-accent`, `text-closer`...), never hex values.

## End-to-end tests

Browser tests in `web/e2e/` drive the real app with the synthetic fixtures: upload, live checks,
report, evidence highlights, feedback, the 410 page, keyboard use, and a recorder that fails the run
if any element animates while reduced motion is on.

**They need everything running locally:** Postgres, the API and the web app. The tests talk to the
real API and reset its daily check limit between tests (via `api/scripts/e2e_db.py`, which refuses
any database that isn't on this machine). They also write a few test feedback rows to the local
database. A check before the run stops it with a clear message if anything isn't up.

```bash
# 1. Postgres and the API (leave CHECKS_PER_IP_PER_DAY at its default of 3: one test checks the 4th is refused)
cd api
uv run python scripts/local_db.py start
uv run alembic upgrade head
uv run uvicorn app.main:app --port 8000

# 2. The web app, as a production build, in another terminal
cd web
npm run build
npm run start            # serves http://localhost:3000 using API_URL from web/.env.local

# 3. The tests, in a third terminal
cd web
npx playwright install chromium     # first time only: downloads Playwright's browser
npm run test:e2e
```

Options (environment variables): `E2E_BASE_URL` (default `http://127.0.0.1:3000`), `E2E_API_URL`
(default `http://127.0.0.1:8000`), and `E2E_BROWSER_CHANNEL=msedge` (or `chrome`) to use an installed
browser instead of downloading Chromium. A full run takes about 5 minutes; failures keep a screenshot
and trace in `web/test-results/` (`npx playwright show-trace <file>`).

## Fixtures

```bash
cd api
uv run python ../fixtures/generate.py
```

## Analytics

Cookie-free and self-hosted: an `events` table holding an event name, a time and a salted hash of
the check id. There's no IP address, user agent, cookie or document data. Events: check started,
finished (or failed), report viewed, evidence opened, feedback sent and email left. To see the funnel:

```bash
cd api
uv run python scripts/funnel.py --days 30
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
