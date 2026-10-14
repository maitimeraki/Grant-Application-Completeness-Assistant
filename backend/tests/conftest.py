from __future__ import annotations

import os
import tempfile
from pathlib import Path

_tmpdir = tempfile.mkdtemp(prefix="gaca-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmpdir) / 'test.db'}"
os.environ["DATA_DIR"] = str(Path(_tmpdir) / "data")
os.environ["STORAGE_DIR"] = str(Path(_tmpdir) / "storage")
os.environ["LLM_API_KEY"] = ""
os.environ["SEED_ON_STARTUP"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def db_tables():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


GUIDELINE_MD = """# Community Grant Program

## Eligibility

Applicants must be registered non-profit organizations operating in the region.

## Submission

The budget must not exceed 50,000 EUR.

Applicants should include a logic model (recommended).

A signed cover letter is a required supporting document.
"""

APPLICATION_MD = """# Project Abstract

Our organization is a registered non-profit organization with ten years of
experience delivering community programs.

# Budget

The total budget is 42,000 EUR, well below the 50,000 EUR ceiling.
"""


def make_guideline(client: TestClient, text: str = GUIDELINE_MD) -> dict:
    resp = client.post(
        "/api/documents/paste",
        json={"kind": "guideline", "title": "Community Grant", "text": text},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def make_application(client: TestClient, text: str = APPLICATION_MD) -> dict:
    resp = client.post(
        "/api/documents/paste",
        json={"kind": "application", "title": "Draft App", "text": text},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def make_assessment(client: TestClient, guideline: dict, application: dict, **extra) -> dict:
    resp = client.post(
        "/api/assessments",
        json={
            "guideline_version_id": guideline["versions"][-1]["id"],
            "application_version_id": application["versions"][-1]["id"],
            "run_llm": False,
            **extra,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()
