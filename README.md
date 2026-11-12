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
cp .env.example .env        # fill in keys; never commit .env
uv sync                     # creates .venv with app + dev dependencies
uv run uvicorn app.main:app --reload --port 8000
```

Check it's up at http://localhost:8000/health (returns `{"status":"ok"}`). Interactive docs are at
http://localhost:8000/docs.

Tests and lint:

```bash
cd api
uv run pytest
uv run ruff check . ../fixtures
```

## Web

```bash
cd web
cp .env.example .env.local
npm install
npm run dev
```

Open http://localhost:3000. The home page shows whether it can reach the API.

## Fixtures

```bash
cd api
uv run python ../fixtures/generate.py
```

## Privacy rules

- Uploaded documents are only ever written to a temp folder and are deleted when processing ends,
  including on errors.
- Logs contain check IDs, timings and error types only. They never contain document contents,
  transactions, names or amounts.
- Secrets live in `.env` files (git-ignored). `.env.example` files hold placeholders.
# Tailed
