from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from app.config import get_settings
from app.db import SessionLocal
from app.models import LLMCallLog
from app.services.llm import LLMOutputError, complete_json

from tests.conftest import make_application, make_guideline


class Tiny(BaseModel):
    value: int


class _Usage:
    def __init__(self, prompt: int, completion: int) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Message(content)


class _Response:
    def __init__(self, content: str, prompt_tokens: int = 100, completion_tokens: int = 50) -> None:
        self.choices = [_Choice(content)]
        self.usage = _Usage(prompt_tokens, completion_tokens)


@pytest.fixture()
def llm_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_api_key", "sk-test-not-real")


def _fake_completion(payloads: list[str], calls: list):
    def fake(**kwargs):
        calls.append(kwargs)
        return _Response(payloads[len(calls) - 1])

    return fake


def test_successful_call_is_logged(llm_key, monkeypatch):
    calls: list = []
    monkeypatch.setattr("litellm.completion", _fake_completion([json.dumps({"value": 7})], calls))

    result = complete_json(
        system="s", user="u", schema=Tiny, stage="map_requirements", assessment_id=None
    )
    assert result.value == 7

    with SessionLocal() as session:
        rows = session.query(LLMCallLog).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.success is True
        assert row.stage == "map_requirements"
        assert row.model == get_settings().llm_model
        assert row.attempt == 1
        assert row.error is None
        assert row.tokens_prompt == 100
        assert row.tokens_completion == 50
        assert row.prompt_chars and row.prompt_chars > 0
        assert row.completion_chars and row.completion_chars > 0
        assert row.latency_ms is not None and row.latency_ms >= 0


def test_validation_failure_then_repair_logs_both_attempts(llm_key, monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        "litellm.completion",
        _fake_completion(["this is not json at all", json.dumps({"value": 3})], calls),
    )

    result = complete_json(system="s", user="u", schema=Tiny, stage="claims", assessment_id=None)
    assert result.value == 3
    assert len(calls) == 2  # repair prompt sent on second attempt

    with SessionLocal() as session:
        rows = session.query(LLMCallLog).order_by(LLMCallLog.attempt).all()
        assert len(rows) == 2
        assert rows[0].success is False and rows[0].attempt == 1
        assert rows[0].error  # validation failure recorded
        assert rows[1].success is True and rows[1].attempt == 2


def test_repeated_failure_raises_and_logs(llm_key, monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        "litellm.completion", _fake_completion(["garbage one", "garbage two"], calls)
    )

    with pytest.raises(LLMOutputError):
        complete_json(system="s", user="u", schema=Tiny, stage="questions", assessment_id=None)

    with SessionLocal() as session:
        rows = session.query(LLMCallLog).all()
        assert len(rows) == 2
        assert all(r.success is False for r in rows)


def test_transport_error_is_logged_then_raised(llm_key, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr("litellm.completion", boom)

    with pytest.raises(RuntimeError, match="network down"):
        complete_json(system="s", user="u", schema=Tiny, stage="extract_requirements")

    with SessionLocal() as session:
        rows = session.query(LLMCallLog).all()
        assert len(rows) == 1
        assert rows[0].success is False
        assert "transport error" in (rows[0].error or "")


def test_llm_logs_endpoint_returns_ordered_rows(client):
    import datetime as dt

    guideline = make_guideline(client)
    application = make_application(client)
    assessment_id = client.post(
        "/api/assessments",
        json={
            "guideline_version_id": guideline["versions"][-1]["id"],
            "application_version_id": application["versions"][-1]["id"],
            "run_llm": False,
        },
    ).json()["id"]

    base = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.timezone.utc)
    with SessionLocal() as session:
        session.add(
            LLMCallLog(
                assessment_id=assessment_id, stage="extract_requirements",
                model="gpt-4o-mini", attempt=2, success=False, error="bad json",
                created_at=base + dt.timedelta(minutes=1),
            )
        )
        session.add(
            LLMCallLog(
                assessment_id=assessment_id, stage="extract_requirements",
                model="gpt-4o-mini", attempt=1, success=True, latency_ms=120,
                created_at=base,
            )
        )
        session.commit()

    resp = client.get(f"/api/assessments/{assessment_id}/llm-logs")
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["attempt"] for r in rows] == [1, 2]
    assert rows[0]["success"] is True
    assert rows[1]["success"] is False
    assert rows[1]["error"] == "bad json"


def test_llm_logs_endpoint_404(client):
    resp = client.get("/api/assessments/999999/llm-logs")
    assert resp.status_code == 404
