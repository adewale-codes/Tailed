"""Run a private Postgres for local development and tests, using installed Postgres binaries.

It lives in api/.pgdata (git-ignored), listens only on 127.0.0.1:55432, and never touches any
other Postgres server on the machine. Local trust auth, so no password is needed.

    uv run python scripts/local_db.py start    # init on first run, create tallied + tallied_test
    uv run python scripts/local_db.py stop
    uv run python scripts/local_db.py status

Then: DATABASE_URL=postgresql+psycopg://tallied@127.0.0.1:55432/tallied
      TEST_DATABASE_URL=postgresql+psycopg://tallied@127.0.0.1:55432/tallied_test
(If you have Docker, the root docker-compose.yml is an alternative.)
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / ".pgdata"
PORT = "55432"
USER = "tallied"
DATABASES = ["tallied", "tallied_test"]


def _bin(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    candidates = sorted(glob.glob(rf"C:\Program Files\PostgreSQL\*\bin\{name}.exe"), reverse=True)
    if candidates:
        return candidates[0]
    sys.exit(f"Couldn't find {name}. Install PostgreSQL or use docker-compose.yml instead.")


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=check, capture_output=True, text=True)


def start() -> None:
    if not (DATA / "PG_VERSION").exists():
        DATA.mkdir(parents=True, exist_ok=True)
        _run(_bin("initdb"), "-D", str(DATA), "-U", USER, "--auth=trust", "-E", "UTF8")
        print(f"initialised {DATA}")
    if _run(_bin("pg_ctl"), "-D", str(DATA), "status", check=False).returncode != 0:
        # Don't capture output here: the server inherits the pipes and would keep them open.
        subprocess.run([_bin("pg_ctl"), "-D", str(DATA), "-l", str(DATA / "server.log"), "-w",
                        "-o", f"-p {PORT} -h 127.0.0.1", "start"],
                       check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        print(f"started on 127.0.0.1:{PORT}")
    for db in DATABASES:
        exists = _run(_bin("psql"), "-h", "127.0.0.1", "-p", PORT, "-U", USER, "-d", "postgres",
                      "-tAc", f"SELECT 1 FROM pg_database WHERE datname='{db}'").stdout.strip()
        if not exists:
            _run(_bin("createdb"), "-h", "127.0.0.1", "-p", PORT, "-U", USER, db)
            print(f"created database {db}")
    print(f"DATABASE_URL=postgresql+psycopg://{USER}@127.0.0.1:{PORT}/tallied")


def stop() -> None:
    _run(_bin("pg_ctl"), "-D", str(DATA), "-w", "stop", check=False)
    print("stopped")


def status() -> None:
    print(_run(_bin("pg_ctl"), "-D", str(DATA), "status", check=False).stdout.strip() or "not running")


if __name__ == "__main__":
    os.environ.setdefault("PGCLIENTENCODING", "UTF8")
    {"start": start, "stop": stop, "status": status}[sys.argv[1] if len(sys.argv) > 1 else "status"]()
