from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_hosted_database_urls_get_the_psycopg_driver() -> None:
    from app.config import Settings

    for given in ["postgresql://u:p@host:5432/db", "postgres://u:p@host:5432/db"]:
        assert Settings(database_url=given).database_url == "postgresql+psycopg://u:p@host:5432/db"
    already = "postgresql+psycopg://u@127.0.0.1:55432/tallied"
    assert Settings(database_url=already).database_url == already


def test_cors_only_allows_the_web_app_origin() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    allowed = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:3000"
    other = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers
    preflight = client.options("/checks", headers={"Origin": "https://evil.example",
                                                   "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in preflight.headers


def test_interactive_docs_are_off_in_production() -> None:
    import subprocess
    import sys

    code = ("import os; os.environ['ENVIRONMENT']='production'; "
            "from fastapi.testclient import TestClient; from app.main import app; c=TestClient(app); "
            "print(*(c.get(p).status_code for p in ['/docs', '/openapi.json', '/health']))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["404", "404", "200"]
