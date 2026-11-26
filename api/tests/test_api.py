"""API, privacy and rate-limit tests. Need TEST_DATABASE_URL (see conftest.py)."""

from __future__ import annotations

import json
import string
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.checks.companies_house import CompanyRecord, StaticCompanies
from app.config import get_settings
from app.main import app
from app.services import analytics, processing
from app.services.cleanup import run_cleanup
from app.services.limits import new_check_id, reserve_ai_call

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
PHOTO = FIXTURES / "photo_statement" / "statement_2026-08_page1_photo.jpg"
REGISTRY = StaticCompanies([
    CompanyRecord(**r)
    for r in json.loads((FIXTURES / "companies_house.json").read_text(encoding="utf-8"))["records"]
])


@pytest.fixture
def settings(db, tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "upload_temp_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(s, "anthropic_api_key", "")         # no real Claude calls
    monkeypatch.setattr(s, "companies_house_api_key", "")   # employer_real -> not_checked
    monkeypatch.setattr(s, "checks_per_ip_per_day", 3)
    monkeypatch.setattr(s, "trust_proxy_headers", False)
    monkeypatch.setattr(s, "web_proxy_secret", "")
    monkeypatch.setattr(s, "max_files", 12)
    monkeypatch.setattr(s, "max_file_mb", 10)
    monkeypatch.setattr(s, "ai_daily_call_cap", 200)
    return s


@pytest.fixture
def client(settings):
    with TestClient(app) as c:
        yield c


def sql(db_url: str, query: str, **params):
    engine = create_engine(db_url)
    with engine.begin() as conn:
        result = conn.execute(text(query), params)
        rows = result.fetchall() if result.returns_rows else None
    engine.dispose()
    return rows


def fixture_files(case: str = "genuine") -> list[tuple]:
    return [("files", (p.name, p.read_bytes(), "application/pdf"))
            for p in sorted((FIXTURES / case).glob("*.pdf"))]


def upload(client: TestClient, files=None, rent="1100", consent="true", headers=None):
    data = {"monthly_rent": rent}
    if consent is not None:
        data["consent"] = consent
    return client.post("/checks", files=files if files is not None else fixture_files(),
                       data=data, headers=headers or {})


def temp_contents(settings) -> list[Path]:
    root = Path(settings.upload_temp_dir)
    return list(root.rglob("*")) if root.exists() else []


# ---------------------------------------------------------------------------
# Full run: upload -> progress -> report -> page image
# ---------------------------------------------------------------------------


def test_full_run(client, settings, monkeypatch) -> None:
    monkeypatch.setattr(processing, "CompaniesHouseClient", lambda key: REGISTRY)
    res = upload(client)
    assert res.status_code == 202, res.text
    check_id = res.json()["id"]

    progress = client.get(f"/checks/{check_id}/progress").json()
    assert progress["status"] == "done" and progress["stage"] == "done"
    assert progress["documents"] == {"read": 5, "total": 5}
    assert [c["done"] for c in progress["checks"]] == [True] * 7
    assert [c["number"] for c in progress["checks"]] == [1, 2, 3, 4, 5, 6, 7]

    body = client.get(f"/checks/{check_id}").json()
    assert body["report"]["verdict"] == "looks_good"
    assert [c["status"] for c in body["report"]["checks"]] == ["pass"] * 7
    assert body["report"]["affordability"]["rent_pct_of_payslip_net"] == "41.5"
    keys = {f["key"] for f in body["files"]}
    assert keys == {"1", "2", "3", "4", "5"}
    # Nothing flagged, so no page images at all: only pages with a finding are stored.
    assert all(f["images"] == [] for f in body["files"])
    assert progress["preview"] is None
    assert client.get(f"/checks/{check_id}/pages/3/1").json()["error"]["code"] == "page_not_found"


def evidence_view_pages(body: dict) -> set[tuple[str, int]]:
    """Pages the web evidence view opens: same rule as findingsOf() in web/src/lib/report.ts."""
    return {(e["file"], e.get("page") or 1)
            for c in body["report"]["checks"] if c["status"] == "flag"
            for e in c["evidence"] if e["flag"] and e.get("file")}


@pytest.mark.parametrize("case", ["edited_balance", "salary_mismatch", "bad_payslip", "name_mismatch",
                                  "edited_metadata", "multiple_problems", "genuine"])
def test_every_evidence_page_is_stored_as_webp_and_nothing_else(client, settings, db, case) -> None:
    from io import BytesIO

    from PIL import Image

    check_id = upload(client, fixture_files(case)).json()["id"]
    body = client.get(f"/checks/{check_id}").json()
    want = evidence_view_pages(body)
    assert bool(want) == (case != "genuine")
    stored = {(k, p) for k, p in sql(db, "SELECT file_key, page FROM page_images WHERE check_id = :id",
                                     id=check_id)}
    assert stored == want  # every page the evidence view needs, and no other page
    listed = {(f["key"], i["page"]) for f in body["files"] for i in f.get("images", [])}
    assert listed == want
    for file_key, page in want:
        res = client.get(f"/checks/{check_id}/pages/{file_key}/{page}")
        assert res.status_code == 200 and res.headers["content-type"] == "image/webp"
        assert res.headers["cache-control"] == "no-store"
        with Image.open(BytesIO(res.content)) as img:
            assert img.format == "WEBP"
            pages = next(f for f in body["files"] if f["key"] == file_key)["pages"]
            pdf_page = next(p for p in pages if p["page"] == page)
            assert round(img.width / pdf_page["width"] * 72) == processing.PAGE_DPI  # readable size
    progress = client.get(f"/checks/{check_id}/progress").json()
    if want:  # the checking screen previews the first finding page
        assert (progress["preview"]["file"], progress["preview"]["page"]) in want
    else:
        assert progress["preview"] is None


def test_report_shows_evidence_with_file_keys(client, settings) -> None:
    check_id = upload(client, fixture_files("edited_balance")).json()["id"]
    body = client.get(f"/checks/{check_id}").json()
    assert body["report"]["verdict"] == "needs_a_closer_look"
    balances = body["report"]["checks"][0]
    mismatch = next(e for e in balances["evidence"] if e["kind"] == "balance_mismatch")
    assert mismatch["numbers"]["difference"] == "900.00"
    names = {f["key"]: f["name"] for f in body["files"]}
    assert names[mismatch["file"]] == "statement_2026-10.pdf"
    employer = next(c for c in body["report"]["checks"] if c["id"] == "employer_real")
    assert employer["status"] == "not_checked"  # no Companies House key: never a pass


def test_progress_reports_each_check(client, settings, monkeypatch) -> None:
    seen: list[dict] = []
    real_update = processing._update

    def spy(check_id, **values):
        real_update(check_id, **values)
        if "progress" in values:
            seen.append(json.loads(json.dumps(values["progress"])))

    monkeypatch.setattr(processing, "_update", spy)
    upload(client)
    done_counts = [sum(v is not None for v in p["checks"].values()) for p in seen]
    assert done_counts == sorted(done_counts) and set(range(7)) <= set(done_counts)
    assert [p["documents_read"] for p in seen if p["stage"] == "reading"][-1] == 5


def test_one_unreadable_file_is_reported_not_fatal(client, settings) -> None:
    files = fixture_files() + [("files", ("broken.pdf", b"%PDF-1.7 not really", "application/pdf"))]
    check_id = upload(client, files).json()["id"]
    progress = client.get(f"/checks/{check_id}/progress").json()
    assert [f["status"] for f in progress["files"]] == ["read"] * 5 + ["unreadable"]
    assert progress["files"][5] == {"key": "6", "status": "unreadable", "code": "unreadable_file",
                                    "message": progress["files"][5]["message"]}
    assert progress["preview"] is None  # nothing flagged, so no page image to preview
    assert "broken.pdf" not in json.dumps(progress)
    body = client.get(f"/checks/{check_id}").json()
    broken = next(f for f in body["files"] if f["name"] == "broken.pdf")
    assert broken["error"]["code"] == "unreadable_file"
    assert body["report"]["verdict"] == "looks_good"


def test_only_unreadable_files_fails_with_friendly_error(client, settings) -> None:
    files = [("files", ("broken.pdf", b"%PDF-1.7 not really", "application/pdf"))]
    check_id = upload(client, files).json()["id"]
    res = client.get(f"/checks/{check_id}")
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "unreadable_file"
    assert client.get(f"/checks/{check_id}/progress").json()["error"]["code"] == "unreadable_file"


# ---------------------------------------------------------------------------
# Upload validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("consent", [None, "", "false", "no"])
def test_consent_required(client, settings, consent) -> None:
    res = upload(client, consent=consent)
    assert res.status_code == 400 and res.json()["error"]["code"] == "consent_required"
    assert sql(settings.database_url or get_settings().database_url,
               "SELECT count(*) FROM rate_limits")[0][0] == 0  # refused uploads don't count


@pytest.mark.parametrize("rent", ["", "abc", "0", "-5", "NaN", "200000"])
def test_rent_required(client, settings, rent) -> None:
    res = upload(client, rent=rent)
    assert res.status_code == 400 and res.json()["error"]["code"] == "invalid_rent"


def test_too_many_files(client, settings) -> None:
    pdf = (FIXTURES / "genuine" / "payslip_2026-09.pdf").read_bytes()
    files = [("files", (f"p{i}.pdf", pdf, "application/pdf")) for i in range(13)]
    res = upload(client, files)
    assert res.status_code == 400 and res.json()["error"]["code"] == "too_many_files"
    assert temp_contents(settings) == []


def test_no_files(client, settings) -> None:
    res = upload(client, files=[])
    assert res.status_code == 400 and res.json()["error"]["code"] == "no_files"


def test_file_too_large(client, settings, monkeypatch) -> None:
    monkeypatch.setattr(settings, "max_file_mb", 1)
    big = b"%PDF-1.7\n" + b"0" * (1024 * 1024 + 10)
    res = upload(client, [("files", ("big.pdf", big, "application/pdf"))])
    assert res.status_code == 413 and res.json()["error"]["code"] == "file_too_large"
    assert temp_contents(settings) == []


def test_whole_request_too_large(client, settings, monkeypatch) -> None:
    monkeypatch.setattr(settings, "max_files", 1)
    monkeypatch.setattr(settings, "max_file_mb", 1)
    res = upload(client, [("files", ("a.pdf", b"%PDF-1.7\n" + b"0" * (3 * 1024 * 1024),
                                     "application/pdf"))])
    assert res.status_code == 413 and res.json()["error"]["code"] == "upload_too_large"


@pytest.mark.parametrize("name, content", [
    ("statement.pdf", b"PK\x03\x04 a zip file pretending to be a pdf"),
    ("statement.pdf", b"<html>not a pdf</html>"),
    ("photo.jpg", b"GIF89a not a jpeg"),
    ("photo.webp", b"RIFF\x00\x00\x00\x00WEBPVP8 "),
])
def test_real_file_type_is_checked(client, settings, name, content) -> None:
    res = upload(client, [("files", (name, content, "application/pdf"))])
    assert res.status_code == 415 and res.json()["error"]["code"] == "unsupported_file_type"
    assert temp_contents(settings) == []


def test_real_type_wins_over_extension(client, settings) -> None:
    pdf = (FIXTURES / "genuine" / "payslip_2026-09.pdf").read_bytes()
    res = upload(client, [("files", ("payslip.txt", pdf, "text/plain"))])
    assert res.status_code == 202


# ---------------------------------------------------------------------------
# Privacy rule 1: uploads live only in a temp folder, deleted when processing ends or fails
# ---------------------------------------------------------------------------


def test_temp_folder_used_then_emptied(client, settings, monkeypatch) -> None:
    seen_during_processing: list[Path] = []
    real_read = processing.read_document

    def spy(data: bytes):
        seen_during_processing.extend(p for p in temp_contents(settings) if p.is_file())
        return real_read(data)

    monkeypatch.setattr(processing, "read_document", spy)
    res = upload(client)
    assert res.status_code == 202
    root = Path(settings.upload_temp_dir).resolve()
    assert seen_during_processing, "files should be in the temp folder while processing"
    assert all(root in p.resolve().parents for p in seen_during_processing)
    assert all(p.suffix == ".upload" for p in seen_during_processing)  # no original filenames
    assert temp_contents(settings) == []  # empty after processing


def test_temp_folder_emptied_when_processing_fails(client, settings, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(processing, "run_checks", boom)
    check_id = upload(client).json()["id"]
    assert temp_contents(settings) == []
    res = client.get(f"/checks/{check_id}")
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "internal_error"
    assert "simulated crash" not in res.text and "Traceback" not in res.text


def test_stale_upload_folders_are_removed_by_cleanup(client, settings) -> None:
    stale = Path(settings.upload_temp_dir) / "check-left-behind"
    stale.mkdir(parents=True)
    (stale / "1.upload").write_bytes(b"%PDF-1.7")
    later = datetime.now(UTC) + timedelta(hours=2)
    assert run_cleanup(now=later).temp_folders_removed == 1
    assert not stale.exists()


# ---------------------------------------------------------------------------
# Privacy rule 2: reports and page images deleted after 24 hours; GET returns 410
# ---------------------------------------------------------------------------


def _age(db_url: str, check_id: str, hours: int) -> None:
    sql(db_url, "UPDATE checks SET created_at = now() - make_interval(hours => :h), "
                "expires_at = now() - make_interval(hours => :h) + interval '24 hours' "
                "WHERE id = :id", h=hours, id=check_id)


def test_report_older_than_24_hours_is_410(client, settings, db) -> None:
    check_id = upload(client).json()["id"]
    assert client.get(f"/checks/{check_id}").status_code == 200
    _age(db, check_id, 25)
    # Even before the cleanup job runs, nothing is served
    for path in (f"/checks/{check_id}", f"/checks/{check_id}/progress",
                 f"/checks/{check_id}/pages/1/1"):
        res = client.get(path)
        assert res.status_code == 410, path
        assert res.json()["error"]["code"] == "check_expired"
        assert "24 hours" in res.json()["error"]["message"]
    assert client.post(f"/checks/{check_id}/feedback", json={"vote": "up"}).status_code == 410


def test_cleanup_deletes_report_and_page_images(client, settings, db) -> None:
    keep = upload(client, fixture_files("multiple_problems")).json()["id"]
    old = upload(client, fixture_files("multiple_problems")).json()["id"]
    _age(db, old, 25)
    stored = sql(db, "SELECT count(*) FROM page_images WHERE check_id = :id", id=old)[0][0]
    assert stored > 0

    result = run_cleanup()
    assert result.wiped == 1
    assert sql(db, "SELECT count(*) FROM page_images WHERE check_id = :id", id=old)[0][0] == 0
    row = sql(db, "SELECT report, progress, monthly_rent, status FROM checks WHERE id = :id", id=old)[0]
    assert row == (None, None, None, "expired")
    assert client.get(f"/checks/{old}").status_code == 410
    # the newer check is untouched
    assert client.get(f"/checks/{keep}").status_code == 200
    assert sql(db, "SELECT count(*) FROM page_images WHERE check_id = :id", id=keep)[0][0] == stored
    assert run_cleanup().wiped == 0  # idempotent


def test_cleanup_runs_on_a_schedule(settings, db, monkeypatch) -> None:
    with TestClient(app) as c:
        check_id = upload(c).json()["id"]
    _age(db, check_id, 25)
    monkeypatch.setattr(settings, "cleanup_interval_minutes", 60)
    with TestClient(app):  # startup launches the cleanup loop, which runs straight away
        for _ in range(50):
            if sql(db, "SELECT status FROM checks WHERE id = :id", id=check_id)[0][0] == "expired":
                break
            time.sleep(0.1)
    assert sql(db, "SELECT status FROM checks WHERE id = :id", id=check_id)[0][0] == "expired"


def test_unknown_check_is_404(client, settings) -> None:
    for path in ("/checks/" + new_check_id(), "/checks/1", "/checks/../etc"):
        res = client.get(path)
        assert res.status_code == 404
        assert res.json()["error"]["code"] in {"check_not_found", "not_found"}


# ---------------------------------------------------------------------------
# Privacy rule 3: check ids are long random tokens
# ---------------------------------------------------------------------------


def test_check_ids_are_long_random_tokens(client, settings) -> None:
    ids = [new_check_id() for _ in range(1000)]
    assert len(set(ids)) == 1000
    allowed = set(string.ascii_letters + string.digits + "-_")
    for i in ids:
        assert len(i) == 43 and set(i) <= allowed
        assert not i.isdigit()
    issued = [upload(client).json()["id"] for _ in range(2)]
    assert all(len(i) == 43 for i in issued) and issued[0] != issued[1]
    assert not any(a.startswith(b[:8]) for a, b in [issued, issued[::-1]])  # not sequential


# ---------------------------------------------------------------------------
# Privacy rule 4: feedback emails are stored apart from reports
# ---------------------------------------------------------------------------


def test_feedback_emails_survive_report_deletion_and_are_never_exposed(client, settings, db) -> None:
    check_id = upload(client).json()["id"]
    res = client.post(f"/checks/{check_id}/feedback",
                      json={"vote": "down", "comment": "Missed a payslip", "email": "agent@example.com"})
    assert res.status_code == 201

    report = client.get(f"/checks/{check_id}")
    assert "agent@example.com" not in report.text
    assert sql(db, "SELECT count(*) FROM checks WHERE report::text LIKE '%agent@example.com%'")[0][0] == 0

    _age(db, check_id, 25)
    run_cleanup()
    sql(db, "DELETE FROM checks WHERE id = :id", id=check_id)  # even a hard delete
    assert sql(db, "SELECT email FROM feedback_contacts") == [("agent@example.com",)]
    fb = sql(db, "SELECT vote, comment, verdict, contact_id IS NOT NULL FROM feedback")
    assert fb == [("down", "Missed a payslip", "looks_good", True)]
    assert client.get(f"/checks/{check_id}").status_code == 404
    assert "agent@example.com" not in client.get(f"/checks/{check_id}").text


@pytest.mark.parametrize("body, code", [
    ({"vote": "sideways"}, "invalid_feedback"),
    ({"vote": "up", "follow_up": "wrong_result"}, "invalid_feedback"),  # a "down" answer
    ({"vote": "down", "follow_up": "anything at all"}, "invalid_feedback"),
    ({}, "invalid_feedback"),
    ({"vote": "up", "email": "not-an-email"}, "invalid_email"),
])
def test_feedback_validation(client, settings, body, code) -> None:
    check_id = upload(client).json()["id"]
    res = client.post(f"/checks/{check_id}/feedback", json=body)
    assert res.status_code == 400 and res.json()["error"]["code"] == code


def test_feedback_without_email(client, settings, db) -> None:
    check_id = upload(client).json()["id"]
    assert client.post(f"/checks/{check_id}/feedback", json={"vote": "up"}).status_code == 201
    assert sql(db, "SELECT count(*) FROM feedback_contacts")[0][0] == 0


def test_feedback_follow_up_is_stored(client, settings, db) -> None:
    check_id = upload(client).json()["id"]
    res = client.post(f"/checks/{check_id}/feedback",
                      json={"vote": "down", "follow_up": "hard_to_understand", "comment": "Too long"})
    assert res.status_code == 201
    assert sql(db, "SELECT vote, follow_up, comment FROM feedback") == [
        ("down", "hard_to_understand", "Too long")]


# ---------------------------------------------------------------------------
# Analytics (cookie-free events table)
# ---------------------------------------------------------------------------


def test_funnel_events_recorded_and_anonymous(client, settings, db) -> None:
    first = upload(client).json()["id"]
    second = upload(client).json()["id"]
    upload(client, [("files", ("broken.pdf", b"%PDF-1.7 not really", "application/pdf"))])
    for name in ["report_viewed", "report_viewed", "evidence_opened"]:
        assert client.post(f"/checks/{first}/events", json={"name": name}).status_code == 204
    client.post(f"/checks/{second}/events", json={"name": "report_viewed"})
    client.post(f"/checks/{first}/feedback", json={"vote": "up", "email": "a@example.com"})

    client.post(f"/checks/{second}/feedback", json={"vote": "down"})
    assert analytics.votes() == {"up": 1, "down": 1}
    funnel = {r["step"]: r["checks"] for r in analytics.funnel()}
    assert funnel == {"check_started": 3, "check_finished": 2, "report_viewed": 2,
                      "evidence_opened": 1, "feedback_sent": 2, "email_left": 1}
    assert sql(db, "SELECT detail FROM events WHERE name = 'check_failed'") == [("unreadable_file",)]
    assert sql(db, "SELECT detail FROM events WHERE name = 'check_finished'") == [
        ("looks_good",), ("looks_good",)]
    # Nothing in the table can identify a check, a person or a document.
    dump = str(sql(db, "SELECT * FROM events"))
    for secret in [first, second, "a@example.com", "testclient", "127.0.0.1"]:
        assert secret not in dump
    assert sql(db, "SELECT column_name FROM information_schema.columns WHERE table_name = 'events' "
                   "ORDER BY ordinal_position") == [("id",), ("name",), ("check_ref",), ("detail",),
                                                     ("created_at",)]


def test_client_events_are_limited_to_known_names(client, settings) -> None:
    check_id = upload(client).json()["id"]
    for name in ["check_finished", "feedback_sent", "anything"]:
        res = client.post(f"/checks/{check_id}/events", json={"name": name})
        assert res.status_code == 400 and res.json()["error"]["code"] == "invalid_request"
    assert client.post(f"/checks/{'x' * 43}/events", json={"name": "report_viewed"}).status_code == 404


def test_old_events_are_deleted_by_cleanup(client, settings, db) -> None:
    upload(client)
    sql(db, "UPDATE events SET created_at = created_at - interval '401 days'")
    run_cleanup()
    assert sql(db, "SELECT count(*) FROM events")[0][0] == 0


# ---------------------------------------------------------------------------
# Rate limits
# ---------------------------------------------------------------------------


def test_fourth_check_from_same_ip_is_429(client, settings, db) -> None:
    for _ in range(3):
        assert upload(client).status_code == 202
    res = upload(client)
    assert res.status_code == 429
    assert res.json()["error"] == {
        "code": "rate_limited",
        "message": "You've reached today's limit of 3 free checks. Please try again tomorrow."}
    assert temp_contents(settings) == []
    # Stored in Postgres as a salted hash, never the IP itself
    rows = sql(db, "SELECT ip_hash, count FROM rate_limits")
    assert len(rows) == 1 and rows[0][1] == 3
    assert "testclient" not in rows[0][0] and len(rows[0][0]) == 64


def test_spoofed_forwarded_for_is_ignored_by_default(client, settings) -> None:
    for i in range(3):
        upload(client, headers={"X-Forwarded-For": f"10.0.0.{i}"})
    assert upload(client, headers={"X-Forwarded-For": "10.0.0.99"}).status_code == 429


def test_forwarded_for_used_behind_trusted_proxy(client, settings, monkeypatch) -> None:
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    for _ in range(3):
        assert upload(client, headers={"X-Forwarded-For": "203.0.113.5"}).status_code == 202
    assert upload(client, headers={"X-Forwarded-For": "203.0.113.5"}).status_code == 429
    assert upload(client, headers={"X-Forwarded-For": "203.0.113.6, 10.0.0.1"}).status_code == 202


def test_web_proxy_client_ip_trusted_only_with_secret(client, settings, monkeypatch) -> None:
    monkeypatch.setattr(settings, "web_proxy_secret", "s3cret-value")
    good = {"X-Tallied-Proxy-Secret": "s3cret-value"}
    for _ in range(3):
        assert upload(client, headers={**good, "X-Tallied-Client-IP": "198.51.100.7"}).status_code == 202
    assert upload(client, headers={**good, "X-Tallied-Client-IP": "198.51.100.7"}).status_code == 429
    # A different user behind the same web server gets their own allowance.
    assert upload(client, headers={**good, "X-Tallied-Client-IP": "198.51.100.8"}).status_code == 202
    # Without the right secret the header is ignored, so callers can't spoof a fresh IP.
    bad = {"X-Tallied-Proxy-Secret": "wrong", "X-Tallied-Client-IP": "198.51.100.9"}
    assert upload(client, headers=bad).status_code == 202  # counts against the socket IP
    for _ in range(2):
        upload(client, headers=bad)
    assert upload(client, headers={"X-Tallied-Client-IP": "198.51.100.10"}).status_code == 429


def test_forwarded_headers_from_a_reverse_proxy_are_ignored(client, settings, monkeypatch) -> None:
    """Behind Heroku's router: whatever forwarded headers arrive, only the web app's secret
    header can name the visitor. Everything else counts against the connecting address."""
    monkeypatch.setattr(settings, "web_proxy_secret", "s3cret-value")
    spoofed = lambda n: {  # noqa: E731
        "X-Forwarded-For": f"203.0.113.{n}", "X-Real-IP": f"203.0.113.{n}",
        "Forwarded": f"for=203.0.113.{n};proto=https", "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "api.example", "X-Tallied-Client-IP": f"203.0.113.{n}"}
    assert [upload(client, headers=spoofed(n)).status_code for n in range(4)] == [202, 202, 202, 429]
    web = {"X-Tallied-Proxy-Secret": "s3cret-value", "X-Tallied-Client-IP": "198.51.100.20"}
    assert upload(client, headers={**spoofed(9), **web}).status_code == 202


def test_start_script_never_trusts_forwarded_headers() -> None:
    """uvicorn would otherwise take the client address from X-Forwarded-For on 127.0.0.1."""
    start = (Path(__file__).resolve().parents[1] / "scripts" / "start.sh").read_text(encoding="utf-8")
    line = next(x for x in start.splitlines() if x.startswith("exec uvicorn"))
    assert "--no-proxy-headers" in line and "--forwarded-allow-ips" not in line


def test_rate_limit_resets_next_day(client, settings, db) -> None:
    for _ in range(3):
        upload(client)
    sql(db, "UPDATE rate_limits SET day = day - 1")
    assert upload(client).status_code == 202


# ---------------------------------------------------------------------------
# Global AI cap
# ---------------------------------------------------------------------------


def test_ai_daily_cap_counts_in_postgres(settings, db, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_daily_call_cap", 2)
    assert [reserve_ai_call() for _ in range(3)] == [True, True, False]
    assert sql(db, "SELECT calls FROM ai_usage")[0][0] == 2


def test_ai_cap_reached_gives_friendly_error_not_a_claude_call(client, settings, monkeypatch) -> None:
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test-not-real")
    monkeypatch.setattr(settings, "ai_daily_call_cap", 0)

    def no_network(*args, **kwargs):
        raise AssertionError("Claude must not be called once the cap is reached")

    monkeypatch.setattr("anthropic.Anthropic", no_network)
    res = upload(client, [("files", ("photo.jpg", PHOTO.read_bytes(), "image/jpeg"))])
    check_id = res.json()["id"]
    err = client.get(f"/checks/{check_id}").json()["error"]
    assert err["code"] == "ai_daily_limit"
    assert "today's limit" in err["message"]


# ---------------------------------------------------------------------------
# Errors are JSON with a code and message, never a stack trace
# ---------------------------------------------------------------------------


def test_errors_are_json_without_stack_traces(settings, monkeypatch) -> None:
    from app.api import checks as checks_api

    def explode(check_id: str):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(checks_api, "_load", explode)
    with TestClient(app, raise_server_exceptions=False) as c:
        res = c.get("/checks/" + new_check_id())
        assert res.status_code == 500
        assert res.json() == {"error": {"code": "internal_error",
                                        "message": "Something went wrong on our side. Please try again."}}
        assert "secret" not in res.text and "Traceback" not in res.text
        missing = c.get("/nope")
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
        wrong = c.delete("/checks")
        assert wrong.status_code == 405 and wrong.json()["error"]["code"] == "method_not_allowed"


# ---------------------------------------------------------------------------
# Chunked upload sessions (the web app's path; each request stays under Vercel's 4.5 MB cap)
# ---------------------------------------------------------------------------


OCTET = "application/octet-stream"


def chunked_upload(client, files, piece=700_000, rent="1100") -> str:
    res = client.post("/uploads")
    assert res.status_code == 201
    upload_id = res.json()["upload_id"]
    for index, (name, data) in enumerate(files, start=1):
        for offset in range(0, len(data), piece):
            r = client.post(f"/uploads/{upload_id}/chunks",
                            data={"index": str(index), "offset": str(offset), "name": name},
                            files={"chunk": ("blob", data[offset:offset + piece], OCTET)})
            assert r.status_code == 200, r.text
    res = client.post("/checks", data={"upload_id": upload_id, "monthly_rent": rent, "consent": "true"})
    assert res.status_code == 202, res.text
    return res.json()["id"]


def raw_fixture(case: str = "genuine") -> list[tuple[str, bytes]]:
    return [(p.name, p.read_bytes()) for p in sorted((FIXTURES / case).glob("*.pdf"))]


def test_chunked_upload_runs_the_same_check(client, settings) -> None:
    files = raw_fixture()
    check_id = chunked_upload(client, files, piece=2_000)  # many small pieces per file
    body = client.get(f"/checks/{check_id}").json()
    assert body["report"]["verdict"] == "looks_good"
    assert [f["name"] for f in body["files"]] == [n for n, _ in files]
    assert temp_contents(settings) == []  # session folder claimed and deleted after processing


def test_chunk_out_of_order_is_refused(client, settings) -> None:
    upload_id = client.post("/uploads").json()["upload_id"]
    data = raw_fixture()[0][1]
    res = client.post(f"/uploads/{upload_id}/chunks", data={"index": "1", "offset": "500"},
                      files={"chunk": ("blob", data[500:900], "application/octet-stream")})
    assert res.status_code == 409 and res.json()["error"]["code"] == "upload_out_of_order"


def test_chunked_upload_checks_real_type_and_size(client, settings, monkeypatch) -> None:
    upload_id = client.post("/uploads").json()["upload_id"]
    res = client.post(f"/uploads/{upload_id}/chunks", data={"index": "1", "offset": "0", "name": "x.pdf"},
                      files={"chunk": ("blob", b"MZ\x90\x00 not a pdf", "application/pdf")})
    assert res.status_code == 415 and res.json()["error"]["code"] == "unsupported_file_type"

    monkeypatch.setattr(settings, "max_file_mb", 1)
    data = raw_fixture()[0][1] + b"\0" * 1_100_000
    res = client.post(f"/uploads/{upload_id}/chunks", data={"index": "2", "offset": "0"},
                      files={"chunk": ("blob", data[:600_000], "application/octet-stream")})
    assert res.status_code == 200
    res = client.post(f"/uploads/{upload_id}/chunks", data={"index": "2", "offset": "600000"},
                      files={"chunk": ("blob", data[600_000:1_200_000], "application/octet-stream")})
    assert res.status_code == 413 and res.json()["error"]["code"] == "file_too_large"
    assert not (Path(settings.upload_temp_dir) / f"session-{upload_id}" / "2.upload").exists()


def test_oversized_chunk_request_is_refused_before_reading(client, settings) -> None:
    upload_id = client.post("/uploads").json()["upload_id"]
    res = client.post(f"/uploads/{upload_id}/chunks", data={"index": "1", "offset": "0"},
                      files={"chunk": ("blob", b"%PDF" + b"\0" * (4 * 1024 * 1024 + 70_000), OCTET)})
    assert res.status_code == 413 and res.json()["error"]["code"] == "chunk_too_large"


def test_upload_session_can_only_start_one_check(client, settings) -> None:
    res = client.post("/uploads")
    upload_id = res.json()["upload_id"]
    name, data = raw_fixture()[0]
    client.post(f"/uploads/{upload_id}/chunks", data={"index": "1", "offset": "0", "name": name},
                files={"chunk": ("blob", data, "application/octet-stream")})
    form = {"upload_id": upload_id, "monthly_rent": "1100", "consent": "true"}
    assert client.post("/checks", data=form).status_code == 202
    again = client.post("/checks", data=form)
    assert again.status_code == 404 and again.json()["error"]["code"] == "upload_not_found"
    bogus = client.post("/checks", data={**form, "upload_id": "../../etc"})
    assert bogus.status_code == 404


def test_abandoned_upload_sessions_are_removed(client, settings) -> None:
    upload_id = client.post("/uploads").json()["upload_id"]
    name, data = raw_fixture()[0]
    client.post(f"/uploads/{upload_id}/chunks", data={"index": "1", "offset": "0", "name": name},
                files={"chunk": ("blob", data, "application/octet-stream")})
    assert run_cleanup(now=datetime.now(UTC) + timedelta(minutes=30)).temp_folders_removed == 0
    assert run_cleanup(now=datetime.now(UTC) + timedelta(hours=2)).temp_folders_removed == 1
    assert temp_contents(settings) == []


def test_upload_session_refused_when_daily_limit_used(client, settings) -> None:
    for _ in range(3):
        upload(client)
    res = client.post("/uploads")
    assert res.status_code == 429 and res.json()["error"]["code"] == "rate_limited"
    assert temp_contents(settings) == []


# ---------------------------------------------------------------------------
# Memory bound: one check processed at a time (Heroku Basic dyno, 512 MB)
# ---------------------------------------------------------------------------


def test_checks_are_processed_one_at_a_time(monkeypatch) -> None:
    import asyncio
    import threading

    running, most = 0, 0
    lock = threading.Lock()

    def fake_process(*_args) -> None:
        nonlocal running, most
        with lock:
            running += 1
            most = max(most, running)
        time.sleep(0.2)
        with lock:
            running -= 1

    monkeypatch.setattr(processing, "process_check", fake_process)

    async def three_at_once() -> None:
        await asyncio.gather(*(processing.run_check(f"id{n}", Path("."), [], None) for n in range(3)))

    asyncio.run(three_at_once())
    assert most == 1


def test_check_failed_while_queued_is_not_restarted(settings, db, tmp_path) -> None:
    """If the cleanup gives up on a check that waited too long, a worker must not revive it."""
    with TestClient(app) as c:
        check_id = upload(c).json()["id"]
    sql(db, "UPDATE checks SET status = 'failed', error_code = 'internal_error', progress = NULL "
            "WHERE id = :id", id=check_id)
    folder = tmp_path / "queued-upload"
    folder.mkdir()
    (folder / "1.upload").write_bytes(b"%PDF-")
    processing.process_check(check_id, folder, [], None)
    assert sql(db, "SELECT status, progress FROM checks WHERE id = :id", id=check_id)[0] == ("failed", None)
    assert not folder.exists()


# ---------------------------------------------------------------------------
# Restarts: checks the previous process was running are failed at startup, and given back
# ---------------------------------------------------------------------------


def test_startup_fails_interrupted_checks_and_gives_the_check_back(settings, db) -> None:
    with TestClient(app) as c:
        finished = upload(c).json()["id"]
        interrupted = upload(c).json()["id"]
        queued = upload(c).json()["id"]
        assert upload(c).status_code == 429  # 3 used today
    # The link to the visitor's hash is gone once a check ends...
    assert sql(db, "SELECT count(*) FROM checks WHERE rate_limit_key IS NOT NULL")[0][0] == 0
    # ...so simulate two checks a restart killed: still processing, with their key.
    hashed = sql(db, "SELECT ip_hash FROM rate_limits")[0][0]
    for check_id, status in ((interrupted, "checking"), (queued, "queued")):
        sql(db, "UPDATE checks SET status = :s, report = NULL, rate_limit_key = :h WHERE id = :id",
            s=status, h=hashed, id=check_id)

    with TestClient(app) as c:  # the API starts again
        for check_id in (interrupted, queued):
            progress = c.get(f"/checks/{check_id}/progress").json()
            assert progress["status"] == "failed" and progress["stage"] == "failed"
            assert progress["error"] == {
                "code": "check_interrupted",
                "message": "Something interrupted this check. Please try again. "
                           "This one hasn't counted towards today's limit."}
            assert c.get(f"/checks/{check_id}").json()["error"]["code"] == "check_interrupted"
        assert c.get(f"/checks/{finished}").status_code == 200  # finished checks are untouched
        assert sql(db, "SELECT count FROM rate_limits")[0][0] == 1  # both checks given back
        assert sql(db, "SELECT count(*) FROM checks WHERE rate_limit_key IS NOT NULL")[0][0] == 0
        assert [upload(c).status_code for _ in range(3)] == [202, 202, 429]
    assert sql(db, "SELECT count(*) FROM events WHERE name = 'check_failed' AND detail = 'interrupted'"
               )[0][0] == 2


def test_startup_refund_never_goes_below_zero_or_touches_other_days(settings, db) -> None:
    with TestClient(app) as c:
        check_id = upload(c).json()["id"]
    hashed = sql(db, "SELECT ip_hash FROM rate_limits")[0][0]
    sql(db, "UPDATE rate_limits SET day = day - 1")  # the count is from yesterday, not the check's day
    sql(db, "UPDATE checks SET status = 'reading', rate_limit_key = :h WHERE id = :id", h=hashed, id=check_id)
    with TestClient(app):
        pass
    assert sql(db, "SELECT count FROM rate_limits")[0][0] == 1
    assert sql(db, "SELECT status FROM checks WHERE id = :id", id=check_id)[0][0] == "failed"


def test_check_records_the_rate_limit_key_only_while_processing(client, settings, db, monkeypatch) -> None:
    seen: list = []
    real = processing.process_check

    def spy(check_id, *args, **kwargs):
        seen.append(sql(db, "SELECT rate_limit_key FROM checks WHERE id = :id", id=check_id)[0][0])
        real(check_id, *args, **kwargs)

    monkeypatch.setattr(processing, "process_check", spy)
    check_id = upload(client).json()["id"]
    assert seen == [sql(db, "SELECT ip_hash FROM rate_limits")[0][0]]
    assert sql(db, "SELECT rate_limit_key FROM checks WHERE id = :id", id=check_id)[0][0] is None


def test_cleanup_fails_stuck_checks_and_gives_the_check_back(settings, db) -> None:
    from app.services.cleanup import STUCK_MESSAGE

    with TestClient(app) as c:
        stuck, recent, _ = (upload(c).json()["id"] for _ in range(3))
        assert upload(c).status_code == 429  # 3 used today
    hashed = sql(db, "SELECT ip_hash FROM rate_limits")[0][0]
    for check_id in (stuck, recent):  # both still processing, as if a worker hung
        sql(db, "UPDATE checks SET status = 'checking', report = NULL, rate_limit_key = :h WHERE id = :id",
            h=hashed, id=check_id)
    # Run the cleanup 20 minutes from now: `stuck` is then over 15 minutes old, `recent` isn't.
    sql(db, "UPDATE checks SET created_at = now() + interval '10 minutes' WHERE id = :id", id=recent)
    later = datetime.now(UTC) + timedelta(minutes=20)

    result = run_cleanup(now=later)
    assert (result.stuck_failed, result.stuck_refunded) == (1, 1)
    row = sql(db, "SELECT status, error_code, error_message, rate_limit_key, progress->>'stage' "
                  "FROM checks WHERE id = :id", id=stuck)[0]
    assert row == ("failed", "internal_error", STUCK_MESSAGE, None, "failed")
    untouched = sql(db, "SELECT status, rate_limit_key FROM checks WHERE id = :id", id=recent)[0]
    assert untouched == ("checking", hashed)
    assert sql(db, "SELECT count FROM rate_limits")[0][0] == 2  # one check given back

    again = run_cleanup(now=later)  # never given back twice
    assert (again.stuck_failed, again.stuck_refunded) == (0, 0)
    assert sql(db, "SELECT count FROM rate_limits")[0][0] == 2
    with TestClient(app) as c:
        progress = c.get(f"/checks/{stuck}/progress").json()
        assert progress["status"] == "failed" and progress["error"]["message"] == STUCK_MESSAGE


# ---------------------------------------------------------------------------
# Production: only the web app (with WEB_PROXY_SECRET) may call the API, except GET /health
# ---------------------------------------------------------------------------

SECRET = "web-proxy-secret-for-tests-0123456789abcdef"


@pytest.fixture
def production(settings, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "web_proxy_secret", SECRET)
    return settings


def via_web_app(ip: str | None = None) -> dict[str, str]:
    """Headers the web app's server-side routes send (web/src/lib/api-server.ts)."""
    return {"X-Tallied-Proxy-Secret": SECRET, **({"X-Tallied-Client-IP": ip} if ip else {})}


def every_endpoint(check_id: str) -> list[tuple[str, str, dict]]:
    form = {"data": {"monthly_rent": "1100", "consent": "true"}, "files": fixture_files()}
    return [
        ("POST", "/checks", form),
        ("POST", "/uploads", {}),
        ("POST", f"/uploads/{'u' * 43}/chunks", {"data": {"index": "1", "offset": "0"},
                                                 "files": {"chunk": ("b", b"%PDF-", "application/pdf")}}),
        ("GET", f"/checks/{check_id}", {}),
        ("GET", f"/checks/{check_id}/progress", {}),
        ("GET", f"/checks/{check_id}/pages/1/1", {}),
        ("POST", f"/checks/{check_id}/feedback", {"json": {"vote": "up"}}),
        ("POST", f"/checks/{check_id}/events", {"json": {"name": "report_viewed"}}),
        ("GET", "/docs", {}),
        ("GET", "/no-such-path", {}),
        ("POST", "/health", {}),
    ]


def test_direct_requests_without_the_secret_are_refused_in_production(client, production, db) -> None:
    check_id = client.post("/checks", headers=via_web_app("198.51.100.1"),
                           data={"monthly_rent": "1100", "consent": "true"},
                           files=fixture_files("edited_balance")).json()["id"]
    events_before = sql(db, "SELECT count(*) FROM events")[0][0]
    for method, path, kwargs in every_endpoint(check_id):
        res = client.request(method, path, **kwargs)
        assert res.status_code == 403, (method, path)
        assert res.json() == {"error": {"code": "forbidden", "message":
                                        "This service only answers requests from the Tallied website."}}
    # Refused before anything ran: no check, upload, feedback or event was recorded.
    assert sql(db, "SELECT count(*) FROM checks")[0][0] == 1
    assert sql(db, "SELECT count(*) FROM feedback")[0][0] == 0
    assert sql(db, "SELECT count(*) FROM events")[0][0] == events_before
    assert sql(db, "SELECT sum(count) FROM rate_limits")[0][0] == 1


@pytest.mark.parametrize("wrong", ["", "wrong", SECRET[:-1], SECRET + "x", SECRET.upper()])
def test_a_wrong_secret_is_refused_in_production(client, production, wrong) -> None:
    for method, path, kwargs in every_endpoint("x" * 43):
        res = client.request(method, path, headers={"X-Tallied-Proxy-Secret": wrong}, **kwargs)
        assert res.status_code == 403, (method, path)


def test_health_needs_no_secret_in_production(client, production) -> None:
    res = client.get("/health")
    assert res.status_code == 200 and res.json() == {"status": "ok"}


def test_requests_through_the_web_app_work_and_count_per_visitor(client, production, db) -> None:
    alice, bob = via_web_app("198.51.100.1"), via_web_app("198.51.100.2")
    # The whole web-app flow: upload session, a piece, start, progress, report, page image, feedback.
    upload_id = client.post("/uploads", headers=alice).json()["upload_id"]
    name, data = raw_fixture()[0]
    piece = client.post(f"/uploads/{upload_id}/chunks", headers=alice,
                        data={"index": "1", "offset": "0", "name": name},
                        files={"chunk": ("blob", data, "application/octet-stream")})
    assert piece.status_code == 200
    started = client.post("/checks", headers=alice,
                          data={"upload_id": upload_id, "monthly_rent": "1100", "consent": "true"})
    assert started.status_code == 202
    check_id = started.json()["id"]
    assert client.get(f"/checks/{check_id}/progress", headers=alice).json()["status"] == "done"
    assert client.get(f"/checks/{check_id}", headers=alice).status_code == 200
    flagged = upload(client, fixture_files("edited_balance"), headers=alice).json()["id"]
    report = client.get(f"/checks/{flagged}", headers=alice).json()
    file_key, page = next(iter(evidence_view_pages(report)))
    assert client.get(f"/checks/{flagged}/pages/{file_key}/{page}", headers=alice).status_code == 200
    assert client.post(f"/checks/{flagged}/feedback", headers=alice, json={"vote": "up"}).status_code == 201
    assert client.post(f"/checks/{flagged}/events", headers=alice,
                       json={"name": "report_viewed"}).status_code == 204

    # Per visitor: Alice has used 2, Bob none.
    assert upload(client, headers=alice).status_code == 202
    assert upload(client, headers=alice).status_code == 429
    assert client.post("/uploads", headers=alice).status_code == 429
    assert [upload(client, headers=bob).status_code for _ in range(4)] == [202, 202, 202, 429]
    # The secret without a visitor IP counts against the connecting address, like any other.
    assert upload(client, headers=via_web_app()).status_code == 202


def test_secret_is_optional_in_development(client, settings) -> None:
    assert not settings.is_production
    assert upload(client).status_code == 202
    assert client.post("/uploads").status_code == 201


@pytest.mark.parametrize("secret", ["", "   ", "too-short", "x" * 31])
def test_production_refuses_to_start_without_a_strong_secret(settings, monkeypatch, secret) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "web_proxy_secret", secret)
    with pytest.raises(RuntimeError, match="WEB_PROXY_SECRET"), TestClient(app):
        pass


def test_production_starts_with_a_strong_secret(production) -> None:
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
