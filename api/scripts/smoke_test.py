"""End-to-end smoke test of a running Tallied, through the web app's public routes.

    cd api
    uv run python scripts/smoke_test.py --web https://YOUR-APP.vercel.app --api https://api.YOUR-DOMAIN

Uploads every synthetic fixture exactly as the browser does (chunked, through /api/...), waits
for each report, compares the verdict with the fixture's expected.json, fetches every evidence
page image, and checks the privacy page, security headers and API hardening. Uses only the
synthetic documents in /fixtures. Prints no document contents.

Each fixture is one check, so 7 checks from your IP: raise CHECKS_PER_IP_PER_DAY on the API for
the run (see DEPLOY.md), or use --cases to run a few at a time.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
CASES = ["genuine", "edited_balance", "salary_mismatch", "bad_payslip", "name_mismatch",
         "edited_metadata", "multiple_problems"]
PIECE = int(3.5 * 1024 * 1024)

results: list[tuple[str, bool, str]] = []


def ok(name: str, passed: bool, detail: str = "") -> bool:
    results.append((name, passed, detail))
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)
    return passed


def run_case(web: httpx.Client, case: str, rent: str) -> dict | None:
    files = sorted((FIXTURES / case).glob("*.pdf"))
    res = web.post("/api/uploads")
    if res.status_code == 429:
        ok(f"{case}: start upload", False,
           "429 daily limit reached; raise CHECKS_PER_IP_PER_DAY for the test")
        return None
    if not ok(f"{case}: start upload", res.status_code == 201, f"HTTP {res.status_code}"):
        return None
    upload_id = res.json()["upload_id"]
    for index, path in enumerate(files, start=1):
        data = path.read_bytes()
        for offset in range(0, len(data), PIECE):
            r = web.post(f"/api/uploads/{upload_id}/chunks",
                         data={"index": str(index), "offset": str(offset), "name": path.name},
                         files={"chunk": ("piece", data[offset:offset + PIECE], "application/octet-stream")})
            if r.status_code != 200:
                ok(f"{case}: upload {path.name}", False, f"HTTP {r.status_code} {r.text[:120]}")
                return None
    res = web.post("/api/checks", data={"upload_id": upload_id, "monthly_rent": rent, "consent": "true"})
    if not ok(f"{case}: check accepted", res.status_code == 202, f"HTTP {res.status_code}"):
        return None
    check_id = res.json()["id"]

    started = time.monotonic()
    while time.monotonic() - started < 180:
        p = web.get(f"/api/checks/{check_id}/progress").json()
        if p["status"] in ("done", "failed"):
            break
        time.sleep(1)
    elapsed = time.monotonic() - started
    if not ok(f"{case}: processing finished", p["status"] == "done", f"{p['status']} in {elapsed:.1f}s"):
        return None
    report = web.get(f"/api/checks/{check_id}").json()
    for page in (f"/check/{check_id}", f"/report/{check_id}"):
        r = web.get(page)
        ok(f"{case}: {page.split('/')[1]} page loads and is noindex",
           r.status_code == 200 and "noindex" in r.headers.get("x-robots-tag", ""), f"HTTP {r.status_code}")
    return report


def check_report(web: httpx.Client, case: str, report: dict) -> dict:
    want = json.loads((FIXTURES / case / "expected.json").read_text(encoding="utf-8"))
    r = report["report"]
    flags = [c["id"] for c in r["checks"] if c["status"] == "flag"]
    not_checked = [c["id"] for c in r["checks"] if c["status"] == "not_checked"]
    ok(f"{case}: verdict", r["verdict"] == want["expected_verdict"],
       f"got {r['verdict']}, expected {want['expected_verdict']}")
    expected_flags = sorted(k for k, v in want["expected_checks"].items() if v == "flag")
    ok(f"{case}: flagged checks", sorted(flags) == expected_flags, ", ".join(flags) or "none")
    # Every page the evidence view shows (same rule as findingsOf in web/src/lib/report.ts).
    images = 0
    for c in r["checks"]:
        if c["status"] != "flag":
            continue
        for e in c["evidence"]:
            if e["flag"] and e.get("file"):
                page = e.get("page") or 1
                img = web.get(f"/api/checks/{report['id']}/pages/{e['file']}/{page}")
                good = img.status_code == 200 and img.headers.get("content-type") == "image/webp"
                images += 1
                if not good:
                    ok(f"{case}: evidence image {e['file']}/{page}", False, f"HTTP {img.status_code}")
    if images:
        ok(f"{case}: all {images} evidence page images load", True)
    return {"case": case, "verdict": r["verdict"], "expected": want["expected_verdict"],
            "flags": ", ".join(flags) or "none", "not_checked": ", ".join(not_checked) or "none",
            "wording": r.get("wording_source", "?")}


def check_site(web: httpx.Client, api: httpx.Client | None) -> None:
    home = web.get("/")
    ok("home page loads", home.status_code == 200, f"HTTP {home.status_code}")
    for header, want in [("referrer-policy", "no-referrer"), ("x-frame-options", "DENY"),
                         ("x-content-type-options", "nosniff")]:
        ok(f"web header {header}", home.headers.get(header) == want, home.headers.get(header) or "missing")
    ok("no X-Powered-By header", "x-powered-by" not in home.headers)
    privacy = web.get("/privacy")
    ok("privacy page loads", privacy.status_code == 200 and "24 hours" in privacy.text,
       f"HTTP {privacy.status_code}")
    placeholders = ["[privacy@your-domain.co.uk]", "[Your name or company name]", "[number]", "[date]"]
    leaked = any(p in privacy.text for p in placeholders)
    ok("privacy placeholders filled in", not leaked, "still has [placeholders]" if leaked else "")
    robots = web.get("/robots.txt")
    ok("robots.txt hides /check and /report", "Disallow: /report/" in robots.text)
    ok("browser JS never sees the API URL", api is None or str(api.base_url).rstrip("/") not in home.text)
    expired = web.get("/api/checks/" + "x" * 43)
    ok("unknown check is a JSON 404", expired.status_code == 404 and "error" in expired.json())

    if api is None:
        return
    health = api.get("/health")
    ok("API health", health.status_code == 200, f"HTTP {health.status_code}")
    ok("API docs are off in production", api.get("/docs").status_code == 404)
    cors = api.options("/checks", headers={"Origin": "https://evil.example",
                                           "Access-Control-Request-Method": "POST"})
    ok("API CORS refuses other origins", "access-control-allow-origin" not in cors.headers)
    boom = api.get("/checks/" + "x" * 43 + "/pages/1/999999999999999999999")
    ok("API errors are JSON with no stack trace",
       "error" in boom.json() and "Traceback" not in boom.text and 'File "' not in boom.text,
       f"HTTP {boom.status_code}")
    bad = api.post("/checks", data={"monthly_rent": "1100", "consent": "true"},
                   files={"files": ("x.pdf", b"MZ\x90\x00 not a pdf", "application/pdf")})
    ok("API rejects a fake PDF by its real type", bad.status_code in (415, 429), f"HTTP {bad.status_code}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--web", required=True, help="web app URL, e.g. https://tallied.vercel.app")
    parser.add_argument("--api", help="API URL (optional), e.g. https://api.your-domain.co.uk")
    parser.add_argument("--cases", nargs="*", default=CASES)
    parser.add_argument("--rent", default="1100")
    args = parser.parse_args()

    web = httpx.Client(base_url=args.web.rstrip("/"), timeout=60, follow_redirects=True)
    api = httpx.Client(base_url=args.api.rstrip("/"), timeout=30) if args.api else None
    try:
        check_site(web, api)
    except httpx.HTTPError as exc:
        ok("site reachable", False, f"{type(exc).__name__}: {exc}")
        return 1
    rows = []
    for case in args.cases:
        try:
            report = run_case(web, case, args.rent)
        except httpx.HTTPError as exc:
            ok(f"{case}: reachable", False, type(exc).__name__)
            continue
        if report is None:
            if results[-1][2].startswith("429"):
                break
            continue
        rows.append(check_report(web, case, report))

    print("\n| fixture | expected | actual | flags raised | not checked | wording |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['case']} | {r['expected']} | {r['verdict']} | {r['flags']} | {r['not_checked']} "
              f"| {r['wording']} |")
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
