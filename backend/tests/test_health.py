from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root() -> None:
    response = client.get("/")
    assert response.status_code == 200
    dist_index = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist" / "index.html"
    if dist_index.is_file():
        # built frontend present: "/" serves the SPA shell
        assert "text/html" in response.headers["content-type"]
        assert 'id="root"' in response.text
    else:
        body = response.json()
        assert body["name"] == "Grant Application Completeness Assistant"
        assert "not a funding-eligibility" in body["note"]


def test_health() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"
