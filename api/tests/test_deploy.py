"""Heroku deployment files (see DEPLOY.md). No database needed."""

from __future__ import annotations

import re
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
REPO = API_DIR.parent


def test_heroku_manifest_builds_the_api_dockerfile_and_migrates_on_release() -> None:
    manifest = (REPO / "heroku.yml").read_text(encoding="utf-8")
    assert re.search(r"^\s+web: api/Dockerfile\s*$", manifest, re.M)
    assert re.search(r"^release:\s*$", manifest, re.M) and "- alembic upgrade head" in manifest
    # No `run` section: the web dyno must use the Dockerfile CMD (start.sh, with --no-proxy-headers).
    assert not re.search(r"^run:", manifest, re.M)


def test_web_dyno_listens_on_heroku_port() -> None:
    start = (API_DIR / "scripts" / "start.sh").read_text(encoding="utf-8")
    line = next(x for x in start.splitlines() if x.startswith("exec uvicorn"))
    assert '--port "${PORT:-8000}"' in line and "--host 0.0.0.0" in line
    assert 'CMD ["sh", "scripts/start.sh"]' in (API_DIR / "Dockerfile").read_text(encoding="utf-8")


def test_image_does_not_depend_on_its_own_uid_owning_the_upload_folder() -> None:
    """Heroku ignores USER and runs as a random uid: a folder chowned at build time isn't writable."""
    dockerfile = (API_DIR / "Dockerfile").read_text(encoding="utf-8")
    instructions = [x for x in dockerfile.splitlines() if not x.lstrip().startswith("#")]
    assert not any("chown" in x or "mkdir" in x for x in instructions)
    assert "UPLOAD_TEMP_DIR=/tmp/tallied-uploads" in dockerfile
