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
    assert [c["done"] for c in progress["checks"]] == [True] * 6
    assert [c["number"] for c in progress["checks"]] == [1, 2, 3, 4, 5, 6]

    body = client.get(f"/checks/{check_id}").json()
    assert body["report"]["verdict"] == "looks_good"
    assert [c["status"] for c in body["report"]["checks"]] == ["pass"] * 6
    assert body["report"]["affordability"]["rent_pct_of_payslip_net"] == "41.5"
    keys = {f["key"] for f in body["files"]}
    assert keys == {"1", "2", "3", "4", "5"}
    assert all(f["images"] for f in body["files"])

    png = client.get(f"/checks/{check_id}/pages/3/1.png")
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    assert png.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert png.headers["cache-control"] == "no-store"
    assert client.get(f"/checks/{check_id}/pages/3/9.png").json()["error"]["code"] == "page_not_found"


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
    assert progress["preview"] == {"file": "1", "page": 1}
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
                 f"/checks/{check_id}/pages/1/1.png"):
        res = client.get(path)
        assert res.status_code == 410, path
        assert res.json()["error"]["code"] == "check_expired"
        assert "24 hours" in res.json()["error"]["message"]
    assert client.post(f"/checks/{check_id}/feedback", json={"vote": "up"}).status_code == 410


def test_cleanup_deletes_report_and_page_images(client, settings, db) -> None:
    keep = upload(client).json()["id"]
    old = upload(client).json()["id"]
    _age(db, old, 25)
    assert sql(db, "SELECT count(*) FROM page_images WHERE check_id = :id", id=old)[0][0] == 8

    result = run_cleanup()
    assert result.wiped == 1
    assert sql(db, "SELECT count(*) FROM page_images WHERE check_id = :id", id=old)[0][0] == 0
    row = sql(db, "SELECT report, progress, monthly_rent, status FROM checks WHERE id = :id", id=old)[0]
    assert row == (None, None, None, "expired")
    assert client.get(f"/checks/{old}").status_code == 410
    # the newer check is untouched
    assert client.get(f"/checks/{keep}").status_code == 200
    assert sql(db, "SELECT count(*) FROM page_images WHERE check_id = :id", id=keep)[0][0] == 8
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

    funnel = {r["step"]: r["checks"] for r in analytics.funnel()}
    assert funnel == {"check_started": 3, "check_finished": 2, "report_viewed": 2,
                      "evidence_opened": 1, "feedback_sent": 1, "email_left": 1}
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
