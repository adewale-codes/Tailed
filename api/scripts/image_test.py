"""Build and run the production API image the way Heroku does, then run the samples end to end.

    python api/scripts/image_test.py                 # from the repository root; needs Docker and git
    python api/scripts/image_test.py --samples genuine
    python api/scripts/image_test.py --extra-ca ca.pem   # only if antivirus intercepts HTTPS (see below)

Why: tests that run from a checkout can pass while production fails, because production differs in
ways a checkout hides. This catches the two that broke samples once:
- Build context is api/ only (heroku.yml), so nothing outside api/ is in the image.
- The sample PDFs are uploaded exactly as git will deploy them (the index version, which Vercel
  serves), and the run stops if this checkout's copies differ from those bytes (for example line
  endings converted by core.autocrlf).

Steps: docker build api/ -> Postgres 16 container -> release phase (alembic upgrade head) as a
random uid -> web dyno as a random uid with ENVIRONMENT=production, a random WEB_PROXY_SECRET,
$PORT and 512 MB -> for each sample, the web app's flow (session, pieces, start, poll, report) ->
a tampered sample is refused with 400 and the log has a reason code but no file names.

Standard library only, so it runs without the project's dependencies. Uses only the synthetic
sample documents. Prints no document contents. Exit code 0 when everything passed.

--extra-ca: on a machine whose antivirus re-signs HTTPS, `uv sync` inside the build can't download
packages. This builds from a temporary copy of the Dockerfile whose `uv sync` line alone trusts the
given certificate (passed as a build secret, never stored in the image). The build context is still
api/ only. Leave it out everywhere else, including CI.
"""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
API = REPO / "api"
SAMPLES_DIR = REPO / "web" / "public" / "samples"
SAMPLES = ["genuine", "edited_balance", "multiple_problems"]
IMAGE = "tallied-api:image-test"
RUN = uuid.uuid4().hex[:8]
NET, DB, WEB = f"tallied-it-{RUN}", f"tallied-it-db-{RUN}", f"tallied-it-web-{RUN}"
UID = "54321:54321"  # no such user in the image, like Heroku's random dyno user

failures: list[str] = []


def ok(name: str, passed: bool, detail: str = "") -> bool:
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)
    if not passed:
        failures.append(name)
    return passed


def sh(*args: str, check: bool = True, quiet: bool = False) -> subprocess.CompletedProcess:
    res = subprocess.run(args, capture_output=True)
    if check and res.returncode != 0:
        err = res.stderr.decode(errors="replace")[-2000:]
        raise RuntimeError(f"{' '.join(args[:3])}... failed:\n{err}")
    if not quiet and res.returncode != 0:
        print(res.stderr.decode(errors="replace")[-500:])
    return res


# ---------------------------------------------------------------------------
# The sample files, exactly as git will deploy them
# ---------------------------------------------------------------------------


def deployed_bytes(path: Path) -> bytes:
    """The file as git stores it (the index): what a push deploys and Vercel serves."""
    rel = path.relative_to(REPO).as_posix()
    return sh("git", "-C", str(REPO), "show", f":{rel}").stdout


def sample_files(sample: str) -> list[tuple[str, bytes]]:
    files = []
    for path in sorted((SAMPLES_DIR / sample).glob("*.pdf")):
        data = deployed_bytes(path)
        ok(f"{sample}/{path.name}: checkout matches what git deploys", path.read_bytes() == data,
           "" if path.read_bytes() == data else
           f"checkout {path.stat().st_size} bytes, git {len(data)} bytes: "
           "mark *.pdf binary in .gitattributes")
        files.append((path.name, data))
    return files


# ---------------------------------------------------------------------------
# HTTP (urllib, multipart by hand)
# ---------------------------------------------------------------------------


def request(base: str, method: str, path: str, secret: str, fields: dict | None = None,
            file: tuple[str, bytes] | None = None) -> tuple[int, dict]:
    headers = {"X-Tallied-Proxy-Secret": secret, "X-Tallied-Client-IP": "198.51.100.77"}
    body = None
    if fields is not None or file is not None:
        boundary = uuid.uuid4().hex
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                 for k, v in (fields or {}).items()]
        if file:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="chunk"; filename="piece"\r\n'
                         f"Content-Type: application/octet-stream\r\n\r\n".encode() + file[1] + b"\r\n")
        body = b"".join(parts) + f"--{boundary}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    req = urllib.request.Request(base + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def run_sample(base: str, secret: str, sample: str, files: list[tuple[str, bytes]]) -> tuple[int, dict]:
    """The web app's flow (web/src/lib/upload.ts): session, one piece per file, start."""
    status, body = request(base, "POST", f"/uploads?sample={sample}", secret)
    if status != 201:
        return status, body
    upload_id = body["upload_id"]
    for index, (name, data) in enumerate(files, start=1):
        status, body = request(base, "POST", f"/uploads/{upload_id}/chunks", secret,
                               {"index": index, "offset": 0, "name": name}, (name, data))
        if status != 200:
            return status, body
    return request(base, "POST", "/checks", secret, {"upload_id": upload_id, "sample": sample})


# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------


def build(extra_ca: Path | None) -> None:
    if extra_ca is None:
        sh("docker", "build", "-q", "-t", IMAGE, str(API))  # exactly what heroku.yml builds
        return
    dockerfile = (API / "Dockerfile").read_text(encoding="utf-8")
    line = "RUN uv sync --frozen --no-dev --no-install-project"
    if line not in dockerfile:
        raise RuntimeError("--extra-ca: the uv sync line in api/Dockerfile changed; update image_test.py")
    with tempfile.TemporaryDirectory() as tmp:
        patched = Path(tmp) / "Dockerfile"
        patched.write_text(dockerfile.replace(
            line, "RUN --mount=type=secret,id=ca,target=/run/ca.pem SSL_CERT_FILE=/run/ca.pem "
                  "uv sync --frozen --no-dev --no-install-project"), encoding="utf-8")
        sh("docker", "build", "-q", "-t", IMAGE, "-f", str(patched), "--secret", f"id=ca,src={extra_ca}",
           str(API))
        print(f"note: built with --extra-ca (uv sync trusts {extra_ca.name}); context is still api/ only")


def start(secret: str) -> str:
    db_url = f"postgres://tallied:tallied@{DB}:5432/tallied"  # the form Heroku's add-on sets
    sh("docker", "network", "create", NET)
    sh("docker", "run", "-d", "--name", DB, "--network", NET, "-e", "POSTGRES_USER=tallied",
       "-e", "POSTGRES_PASSWORD=tallied", "-e", "POSTGRES_DB=tallied", "postgres:16.6")
    for _ in range(60):
        if sh("docker", "exec", DB, "pg_isready", "-U", "tallied", "-h", "127.0.0.1",
              check=False, quiet=True).returncode == 0:
            break
        time.sleep(1)
    time.sleep(2)
    release = sh("docker", "run", "--rm", "--network", NET, "--user", UID, "-e", f"DATABASE_URL={db_url}",
                 IMAGE, "alembic", "upgrade", "head", check=False, quiet=True)
    ok("release phase: alembic upgrade head", release.returncode == 0)
    sh("docker", "run", "-d", "--name", WEB, "--network", NET, "--user", UID, "--memory=512m",
       "--memory-swap=512m", "-p", "127.0.0.1::5123", "-e", "PORT=5123", "-e", f"DATABASE_URL={db_url}",
       "-e", "ENVIRONMENT=production", "-e", f"WEB_PROXY_SECRET={secret}",
       "-e", f"RATE_LIMIT_SALT={secrets.token_urlsafe(32)}", IMAGE)
    port = sh("docker", "port", WEB, "5123/tcp").stdout.decode().strip().splitlines()[0].rsplit(":", 1)[1]
    base = f"http://127.0.0.1:{port}"
    for _ in range(90):
        try:
            with urllib.request.urlopen(base + "/health", timeout=2) as res:
                if res.status == 200:
                    break
        except OSError:
            time.sleep(1)
    else:
        raise RuntimeError("the web dyno never answered /health:\n" + logs())
    return base


def logs() -> str:
    res = sh("docker", "logs", WEB, check=False, quiet=True)
    return (res.stdout + res.stderr).decode(errors="replace")


def cleanup() -> None:
    for name in (WEB, DB):
        sh("docker", "rm", "-f", name, check=False, quiet=True)
    sh("docker", "network", "rm", NET, check=False, quiet=True)


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--samples", nargs="*", default=SAMPLES, choices=SAMPLES)
    parser.add_argument("--extra-ca", type=Path, help="see the module docstring; never in CI")
    args = parser.parse_args()

    files = {s: sample_files(s) for s in args.samples}
    expected = {s: json.loads((REPO / "fixtures" / s / "expected.json").read_text("utf-8"))
                ["expected_verdict"]
                for s in args.samples}
    print("building api/Dockerfile with api/ as the build context...", flush=True)
    build(args.extra_ca)
    secret = secrets.token_urlsafe(48)
    try:
        base = start(secret)
        ok("web dyno is up in production mode", True, "GET /health 200")
        status, _ = request(base, "GET", "/checks/" + "x" * 43, "")
        ok("direct calls without the secret are refused", status == 403, f"HTTP {status}")

        for sample in args.samples:
            status, body = run_sample(base, secret, sample, files[sample])
            if not ok(f"{sample}: sample accepted", status == 202, f"HTTP {status} {body.get('error', '')}"):
                continue
            check_id = body["id"]
            for _ in range(120):
                _, progress = request(base, "GET", f"/checks/{check_id}/progress", secret)
                if progress.get("status") in ("done", "failed"):
                    break
                time.sleep(0.5)
            status, report = request(base, "GET", f"/checks/{check_id}", secret)
            verdict = report.get("report", {}).get("verdict")
            ok(f"{sample}: report verdict", verdict == expected[sample],
               f"got {verdict}, want {expected[sample]}")
            ok(f"{sample}: report is tagged as a sample", (report.get("sample") or {}).get("id") == sample)

        # A tampered sample: refused, with a reason code in the log and no file names.
        name, data = files[args.samples[0]][0]
        tampered = [(name, data[:-1] + bytes([data[-1] ^ 1])), *files[args.samples[0]][1:]]
        status, body = run_sample(base, secret, args.samples[0], tampered)
        ok("tampered sample refused", status == 400 and body.get("error", {}).get("code") == "not_a_sample",
           f"HTTP {status}")
        time.sleep(0.5)
        log = logs()
        ok("rejection logged with a reason code and position",
           "sample.rejected" in log and "reason=sample_hash_mismatch position=1" in log)
        ok("no file names in the log", ".pdf" not in log)
    finally:
        if failures:
            rejected = [line for line in logs().splitlines() if "sample.rejected" in line]
            if rejected:
                print("API log, sample rejections:\n  " + "\n  ".join(rejected[:10]))
        cleanup()

    print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
