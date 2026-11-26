"""Sample checks: made-up documents anyone can run (app/samples.py). Need TEST_DATABASE_URL."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_api import fixture_files, sql, temp_contents

from app.main import app
from app.samples import SAMPLES, sha256_file
from app.services import analytics

REPO = Path(__file__).resolve().parents[2]
WEB_SAMPLES = REPO / "web" / "public" / "samples"
EXPECTED = {"genuine": "looks_good", "edited_balance": "needs_a_closer_look",
            "multiple_problems": "high_risk"}


def sample_files(sample_id: str) -> list[tuple[str, bytes]]:
    """The files exactly as the web app serves them (web/public/samples/<id>/)."""
    return [(name, (WEB_SAMPLES / sample_id / name).read_bytes()) for name, _ in SAMPLES[sample_id].files]


def run_sample(c: TestClient, sample_id: str, files: list[tuple[str, bytes]] | None = None,
               headers: dict | None = None, **form):
    """The web app's flow: open a session for the sample, send each file in a piece, start it."""
    headers = headers or {}
    session = c.post(f"/uploads?sample={sample_id}", headers=headers)
    if session.status_code != 201:
        return session
    upload_id = session.json()["upload_id"]
    for index, (name, data) in enumerate(files if files is not None else sample_files(sample_id), start=1):
        piece = c.post(f"/uploads/{upload_id}/chunks", headers=headers,
                       data={"index": str(index), "offset": "0", "name": name},
                       files={"chunk": ("piece", data, "application/octet-stream")})
        assert piece.status_code == 200, piece.text
    return c.post("/checks", headers=headers, data={"upload_id": upload_id, "sample": sample_id, **form})


def real_check(c: TestClient, headers: dict | None = None):
    return c.post("/checks", headers=headers or {}, data={"monthly_rent": "1100", "consent": "true"},
                  files=[("files", ("p.pdf", (REPO / "fixtures/genuine/payslip_2026-09.pdf").read_bytes(),
                                    "application/pdf"))])


# ---------------------------------------------------------------------------
# The sample documents
# ---------------------------------------------------------------------------


def test_sample_hashes_match_the_fixtures_and_the_web_copies() -> None:
    assert set(SAMPLES) == set(EXPECTED)
    for sample in SAMPLES.values():
        web_dir = WEB_SAMPLES / sample.id
        assert sorted(p.name for p in web_dir.iterdir()) == sorted(n for n, _ in sample.files)
        for name, digest in sample.files:
            assert sha256_file(REPO / "fixtures" / sample.fixture / name) == digest, (sample.id, name)
            assert sha256_file(web_dir / name) == digest, (sample.id, name)
        expected = json.loads((REPO / "fixtures" / sample.fixture / "expected.json").read_text("utf-8"))
        assert expected["expected_verdict"] == EXPECTED[sample.id]


def test_gitattributes_keeps_pdfs_byte_for_byte() -> None:
    """Without `*.pdf binary`, a Windows checkout with core.autocrlf=true rewrites small PDFs
    (\\n -> \\r\\n), so hashes taken there don't match what Vercel serves (this broke production)."""
    lines = (REPO / ".gitattributes").read_text("utf-8").splitlines()
    rules = {line.split()[0]: line.split()[1:] for line in lines if line.strip() and not line.startswith("#")}
    assert "binary" in rules.get("*.pdf", [])


def test_no_pdf_in_this_checkout_has_converted_line_endings() -> None:
    """reportlab writes "\\n" line ends. A "\\r\\n" in the first two lines means git converted the file."""
    pdfs = sorted(WEB_SAMPLES.glob("*/*.pdf")) + sorted((REPO / "fixtures").glob("*/*.pdf"))
    assert len(pdfs) == 50
    converted = [p.relative_to(REPO).as_posix() for p in pdfs
                 if any(line.endswith(b"\r") for line in p.read_bytes().split(b"\n", 2)[:2])]
    assert converted == [], "line endings converted; see .gitattributes and DEPLOY.md"


def test_sample_documents_are_clearly_fictional() -> None:
    """Every page says it's a synthetic test document, from the fictional Northgate Bank."""
    import pdfplumber

    for sample in SAMPLES.values():
        for name, _ in sample.files:
            with pdfplumber.open(WEB_SAMPLES / sample.id / name) as pdf:
                text = " ".join((page.extract_text() or "") for page in pdf.pages)
            assert "synthetic" in text.lower(), (sample.id, name)
            if name.startswith("statement"):
                assert "Northgate Bank" in text


# ---------------------------------------------------------------------------
# Running a sample: the normal pipeline, tagged as a sample
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sample_id", list(EXPECTED))
def test_a_sample_runs_through_the_normal_pipeline(client, settings, db, sample_id) -> None:
    res = run_sample(client, sample_id)
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["sample"] == {"id": sample_id, "title": SAMPLES[sample_id].title}
    check_id = body["id"]

    progress = client.get(f"/checks/{check_id}/progress").json()
    assert progress["status"] == "done" and progress["sample"] == body["sample"]
    report = client.get(f"/checks/{check_id}").json()
    assert report["sample"] == body["sample"]
    assert report["report"]["verdict"] == EXPECTED[sample_id]
    assert [f["name"] for f in report["files"]] == [n for n, _ in SAMPLES[sample_id].files]
    if sample_id == "edited_balance":
        flags = [c["id"] for c in report["report"]["checks"] if c["status"] == "flag"]
        assert flags == ["balances_add_up"]
    assert sql(db, "SELECT sample_id FROM checks WHERE id = :id", id=check_id)[0][0] == sample_id
    assert temp_contents(settings) == []  # uploads deleted, as for any check


def test_samples_need_no_consent_and_use_their_own_rent(client, settings) -> None:
    res = run_sample(client, "genuine", monthly_rent="5000")  # no consent field at all
    assert res.status_code == 202
    report = client.get(f"/checks/{res.json()['id']}").json()
    assert report["report"]["affordability"]["monthly_rent"] == "1100.00"


def test_real_checks_still_need_consent_and_are_not_samples(client, settings, db) -> None:
    res = client.post("/checks", data={"monthly_rent": "1100"}, files=fixture_files())
    assert res.status_code == 400 and res.json()["error"]["code"] == "consent_required"
    res = real_check(client)
    assert res.status_code == 202 and res.json()["sample"] is None
    assert client.get(f"/checks/{res.json()['id']}/progress").json()["sample"] is None
    assert sql(db, "SELECT sample_id FROM checks")[0][0] is None


# ---------------------------------------------------------------------------
# A real upload can't pass as a sample
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tamper", ["real_document", "one_missing", "one_extra", "other_sample",
                                    "edited_byte"])
def test_files_that_are_not_exactly_the_sample_are_refused(client, settings, db, tamper) -> None:
    files = sample_files("genuine")
    if tamper == "real_document":
        files[0] = ("statement_2026-08.pdf", b"%PDF-1.7 someone's real statement")
    elif tamper == "one_missing":
        files = files[:-1]
    elif tamper == "one_extra":
        files = [*files, files[0]]
    elif tamper == "other_sample":
        files = sample_files("multiple_problems")
    else:
        name, data = files[2]
        files[2] = (name, data[:-1] + bytes([data[-1] ^ 1]))
    res = run_sample(client, "genuine", files=files)
    assert res.status_code == 400
    assert res.json()["error"] == {"code": "not_a_sample", "message":
                                   "These aren't the sample documents. Please choose the sample again."}
    assert sql(db, "SELECT count(*) FROM checks")[0][0] == 0
    assert sql(db, "SELECT count(*) FROM rate_limits")[0][0] == 0  # nothing counted
    assert temp_contents(settings) == []  # and the uploads are deleted


def test_unknown_sample_is_refused(client, settings) -> None:
    assert client.post("/uploads?sample=nope").json()["error"]["code"] == "sample_not_found"
    res = client.post("/checks", data={"sample": "nope"}, files=fixture_files())
    assert res.status_code == 404 and res.json()["error"]["code"] == "sample_not_found"


# ---------------------------------------------------------------------------
# Limits: samples have their own, and don't use the 3 real checks
# ---------------------------------------------------------------------------


def test_samples_dont_count_towards_the_real_daily_limit(client, settings) -> None:
    assert [real_check(client).status_code for _ in range(4)] == [202, 202, 202, 429]
    assert client.post("/uploads").status_code == 429
    assert [run_sample(client, s).status_code for s in EXPECTED] == [202, 202, 202]


def test_samples_have_their_own_configurable_daily_limit(client, settings, monkeypatch) -> None:
    assert settings.sample_checks_per_ip_per_day == 10  # the default
    monkeypatch.setattr(settings, "sample_checks_per_ip_per_day", 2)
    assert [run_sample(client, "genuine").status_code for _ in range(2)] == [202, 202]
    refused = run_sample(client, "genuine")  # refused when opening the upload session
    assert refused.status_code == 429
    assert refused.json()["error"] == {"code": "sample_rate_limited", "message":
                                       "You've run today's 2 sample checks. Please come back tomorrow, "
                                       "or check real documents."}
    # Starting directly (no session) is refused too, and real checks are untouched.
    direct = client.post("/checks", data={"sample": "genuine"},
                         files=[("files", (n, d, "application/pdf")) for n, d in sample_files("genuine")])
    assert direct.status_code == 429 and direct.json()["error"]["code"] == "sample_rate_limited"
    assert [real_check(client).status_code for _ in range(3)] == [202, 202, 202]


def test_sample_limit_is_per_visitor_through_the_web_app(client, settings, monkeypatch) -> None:
    secret = "web-proxy-secret-for-tests-0123456789abcdef"
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "web_proxy_secret", secret)
    monkeypatch.setattr(settings, "sample_checks_per_ip_per_day", 1)
    alice = {"X-Tallied-Proxy-Secret": secret, "X-Tallied-Client-IP": "198.51.100.1"}
    bob = {"X-Tallied-Proxy-Secret": secret, "X-Tallied-Client-IP": "198.51.100.2"}
    assert run_sample(client, "genuine", headers=alice).status_code == 202
    assert run_sample(client, "genuine", headers=alice).status_code == 429
    assert run_sample(client, "genuine", headers=bob).status_code == 202
    assert client.post("/uploads?sample=genuine").status_code == 403  # still needs the secret


def test_samples_still_count_towards_the_ai_daily_cap(client, settings, db, monkeypatch) -> None:
    """The wording step calls Claude for samples too, and each call reserves from AI_DAILY_CALL_CAP."""
    import anthropic

    built: list[int] = []

    class Offline:
        def __init__(self, **_):
            built.append(1)
            self.beta = self
            self.messages = self

        def create(self, **_):
            raise anthropic.APIConnectionError(request=None)  # the engine's wording is used instead

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test-not-real")
    monkeypatch.setattr(settings, "ai_daily_call_cap", 1)
    monkeypatch.setattr("anthropic.Anthropic", Offline)
    first = run_sample(client, "genuine").json()["id"]
    assert sql(db, "SELECT calls FROM ai_usage")[0][0] == 1 and built == [1]
    second = run_sample(client, "edited_balance").json()["id"]  # cap reached: Claude not called
    assert sql(db, "SELECT calls FROM ai_usage")[0][0] == 1 and built == [1]
    for check_id in (first, second):  # both still finish, with the engine's wording
        assert client.get(f"/checks/{check_id}").json()["report"]["wording_source"] == "engine"


def test_restart_gives_an_interrupted_sample_back_to_the_sample_limit(settings, db, monkeypatch) -> None:
    monkeypatch.setattr(settings, "sample_checks_per_ip_per_day", 1)
    with TestClient(app) as c:
        check_id = run_sample(c, "genuine").json()["id"]
        assert run_sample(c, "genuine").status_code == 429
        assert real_check(c).status_code == 202
    from app.services.limits import ip_hash, sample_ip_hash

    counts = dict(sql(db, "SELECT ip_hash, count FROM rate_limits"))
    assert counts == {sample_ip_hash("testclient"): 1, ip_hash("testclient"): 1}  # two separate counters
    # As if a restart killed the sample mid-check (rate_limit_key is set while it processes).
    sql(db, "UPDATE checks SET status = 'checking', rate_limit_key = :k WHERE id = :id",
        k=sample_ip_hash("testclient"), id=check_id)
    with TestClient(app) as c:  # restart: the sample is failed and given back to the sample counter
        assert c.get(f"/checks/{check_id}/progress").json()["error"]["code"] == "check_interrupted"
        counts = dict(sql(db, "SELECT ip_hash, count FROM rate_limits"))
        assert counts == {sample_ip_hash("testclient"): 0, ip_hash("testclient"): 1}
        assert run_sample(c, "genuine").status_code == 202


# ---------------------------------------------------------------------------
# Analytics: samples are tagged and kept out of the launch metrics
# ---------------------------------------------------------------------------


def test_sample_events_and_feedback_are_tagged_and_kept_out_of_funnel(client, settings, db, capsys) -> None:
    sample_id = run_sample(client, "edited_balance").json()["id"]
    real_id = real_check(client).json()["id"]
    for check_id, vote in ((sample_id, "up"), (real_id, "down")):
        client.post(f"/checks/{check_id}/events", json={"name": "report_viewed"})
        assert client.post(f"/checks/{check_id}/feedback", json={"vote": vote}).status_code == 201

    tagged = dict(sql(db, "SELECT sample, count(*) FROM events GROUP BY sample"))
    assert tagged == {True: 4, False: 4}  # started, finished, report viewed, feedback sent
    assert sql(db, "SELECT sample, vote FROM feedback ORDER BY sample") == [(False, "down"), (True, "up")]

    real = {r["step"]: r["checks"] for r in analytics.funnel(30)}
    samples = {r["step"]: r["checks"] for r in analytics.funnel(30, sample=True)}
    assert real["check_started"] == 1 and real["check_finished"] == 1 and real["feedback_sent"] == 1
    assert samples["check_started"] == 1 and samples["report_viewed"] == 1
    assert analytics.votes(30) == {"up": 0, "down": 1}
    assert analytics.votes(30, sample=True) == {"up": 1, "down": 0}

    import runpy
    import sys

    sys.argv = ["funnel.py", "--days", "30"]
    with pytest.raises(SystemExit):
        runpy.run_path(str(REPO / "api" / "scripts" / "funnel.py"), run_name="__main__")
    out = capsys.readouterr().out
    real_part, sample_part = out.split("Sample checks (made-up documents")
    assert "REAL CHECKS (launch metrics)" in real_part
    assert "Yes 0, Not really 1" in real_part and "Yes 1, Not really 0" in sample_part


# ---------------------------------------------------------------------------
# Rejections are logged with a reason code only (never file names or contents)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("files", "reason"), [
    ("one_missing", "reason=sample_file_count_mismatch expected=5 got=4"),
    ("third_changed", "reason=sample_hash_mismatch position=3"),
])
def test_sample_rejection_logs_a_reason_code(client, settings, caplog, files, reason) -> None:
    sent = sample_files("genuine")
    if files == "one_missing":
        sent = sent[:-1]
    else:
        name, data = sent[2]
        sent[2] = (name, data + b"x")
    with caplog.at_level("WARNING", logger="app.samples"):
        assert run_sample(client, "genuine", files=sent).status_code == 400
    lines = [r.getMessage() for r in caplog.records if r.name == "app.samples"]
    assert lines == [f"sample.rejected sample=genuine {reason}"]
    assert ".pdf" not in " ".join(lines) and "%PDF" not in " ".join(lines)


def test_missing_sample_hashes_are_a_server_error_with_a_reason_code(client, settings, caplog,
                                                                      monkeypatch) -> None:
    import dataclasses

    from app import samples

    files = sample_files("genuine")  # the real documents, read before the hashes go missing
    monkeypatch.setitem(samples.SAMPLES, "genuine", dataclasses.replace(samples.SAMPLES["genuine"], files=()))
    with caplog.at_level("ERROR", logger="app.samples"):
        res = run_sample(client, "genuine", files=files)
    assert res.status_code == 500 and res.json()["error"]["code"] == "internal_error"
    assert [r.getMessage() for r in caplog.records if r.name == "app.samples"] == [
        "sample.rejected sample=genuine reason=sample_hashes_unavailable"]
