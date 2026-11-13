"""Shared test setup.

API tests need a real Postgres: set TEST_DATABASE_URL (the local cluster from
scripts/local_db.py uses postgresql+psycopg://tallied@127.0.0.1:55432/tallied_test).
The schema is rebuilt with Alembic once per run and tables are emptied before each test.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

# Must happen before the app reads its settings.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["CLEANUP_INTERVAL_MINUTES"] = "0"   # tests run cleanup explicitly
os.environ["RATE_LIMIT_SALT"] = "test-salt"

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

API_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def migrated_db() -> str:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set (see tests/conftest.py)")
    from alembic import command
    from alembic.config import Config

    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    engine.dispose()
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(cfg, "head")
    return TEST_DATABASE_URL


@pytest.fixture
def db(migrated_db: str) -> Iterator[str]:
    engine = create_engine(migrated_db)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE checks, page_images, rate_limits, ai_usage, feedback, "
                          "feedback_contacts, events RESTART IDENTITY CASCADE"))
    engine.dispose()
    yield migrated_db
