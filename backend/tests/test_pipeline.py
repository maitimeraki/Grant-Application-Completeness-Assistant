from __future__ import annotations

from typing import Any

import pytest

from app.services.pipeline import (
    ClaimsResult,
    ExtractedClaim,
    ExtractedQuestion,
    ExtractedRequirement,
    ExtractionResult,
    MappingResult,
    QuestionsResult,
)

from tests.conftest import make_application, make_guideline

REAL_GUIDELINE_QUOTE = "Applicants must be registered non-profit organizations operating in the region."
FAKE_GUIDELINE_QUOTE = "The budget must be approved by the regional board."  # hallucinated
REAL_APP_QUOTE = "Our organization is a registered non-profit organization"


def fake_complete_json(system: str, user: str, schema: type, **_: Any):
    if schema is ExtractionResult:
        return ExtractionResult(
            requirements=[
                ExtractedRequirement(
                    code="R1",
                    title="Registered non-profit status",
                    requirement_text="Applicants must be registered non-profit organizations.",
                    basis="mandatory",
                    basis_quote=REAL_GUIDELINE_QUOTE,
                    category="eligibility",
                ),
                ExtractedRequirement(
                    code="R2",
                    title="Board-approved budget",
                    requirement_text="The budget must be board-approved.",
                    basis="mandatory",
                    basis_quote=FAKE_GUIDELINE_QUOTE,
                    category="budget",
                ),
                ExtractedRequirement(
                    code="R3",
                    title="Logic model",
                    requirement_text="Applicants should include a logic model.",
                    basis="recommended",
                    basis_quote="Applicants should include a logic model (recommended).",
                ),
            ]
        )
    if schema is MappingResult:
        if "R2" in user.split("REQUIREMENT")[1][:10]:
            return MappingResult(mapped=False, quotes=[], rationale="No budget-approval passage found.", confidence=0.9, weakness="none")
        if "R3" in user.split("REQUIREMENT")[1][:10]:
            return MappingResult(mapped=False, quotes=[], rationale="Logic model not mentioned.", confidence=0.8, weakness="none")
        return MappingResult(
            mapped=True,
            quotes=[REAL_APP_QUOTE],
            rationale="Application states non-profit registration.",
            confidence=0.92,
            weakness="none",
        )
    if schema is ClaimsResult:
        return ClaimsResult(
            claims=[
                ExtractedClaim(
                    claim_text="Ten years of experience delivering community programs",
                    quote="ten years of experience",
                    support="unsupported",
                    supporting_refs=[],
                )
            ]
        )
    if schema is QuestionsResult:
        return QuestionsResult(
            questions=[
                ExtractedQuestion(requirement_code="R2", question="Who approves the budget?", priority=1)
            ]
        )
    raise AssertionError(f"unexpected schema {schema}")


@pytest.fixture()
def llm_mocked(monkeypatch):
    import app.routers.assessments as assessments_router
    import app.services.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "complete_json", fake_complete_json)
    monkeypatch.setattr(pipeline_module, "llm_available", lambda: True)
    monkeypatch.setattr(assessments_router, "llm_available", lambda: True)


def test_pipeline_end_to_end_with_mocked_llm(client, llm_mocked):
    guideline = make_guideline(client)
    application = make_application(client)
    resp = client.post(
        "/api/assessments",
        json={
            "guideline_version_id": guideline["versions"][-1]["id"],
            "application_version_id": application["versions"][-1]["id"],
            "run_llm": True,
            "supporting_docs": [{"expected_name": "Cover Letter", "required": True}],
        },
    )
    assert resp.status_code == 201, resp.text
    assessment_id = resp.json()["id"]

    data = client.get(f"/api/assessments/{assessment_id}").json()
    assert data["status"] == "review"

    by_code = {r["code"]: r for r in data["requirements"]}
    assert set(by_code) == {"R1", "R2", "R3"}

    # verified quote -> mandatory kept
    assert by_code["R1"]["basis"] == "mandatory"
    assert by_code["R1"]["basis_verified"] is True
    assert by_code["R1"]["mapping"]["citations"][0]["verified"] is True
    assert by_code["R1"]["mapping"]["status"] == "suggested"

    # hallucinated guideline quote -> basis demoted to ambiguous, citation unverified
    assert by_code["R2"]["basis"] == "ambiguous"
    assert by_code["R2"]["basis_verified"] is False

    assert by_code["R3"]["basis"] == "recommended"

    # gap analysis: no verified citation -> weakness missing
    assert by_code["R2"]["mapping"]["weakness"] == "missing"

    # claims + questions produced
    assert data["claims"][0]["support"] == "unsupported"
    assert data["claims"][0]["block_id"] is not None
    assert len(data["questions"]) == 1

    # deterministic completion reflects it
    completion = data["completion"]
    assert completion["mandatory"]["total"] == 1  # R2 is ambiguous basis
    assert completion["mandatory"]["open"] == 1   # R1 unreviewed suggestion
    assert completion["ambiguous_basis_count"] == 1

    # confirming R1 with its citation -> mandatory satisfied (Cover Letter not required by R1)
    mapping_id = by_code["R1"]["mapping"]["id"]
    resp = client.patch(f"/api/mappings/{mapping_id}", json={"action": "confirm"})
    assert resp.status_code == 200
    data = client.get(f"/api/assessments/{assessment_id}").json()
    assert data["completion"]["mandatory"]["satisfied"] == 1


def test_pipeline_unconfigured_llm_creates_error(client, monkeypatch):
    import app.routers.assessments as assessments_router

    monkeypatch.setattr(assessments_router, "llm_available", lambda: True)
    guideline = make_guideline(client)
    application = make_application(client)
    resp = client.post(
        "/api/assessments",
        json={
            "guideline_version_id": guideline["versions"][-1]["id"],
            "application_version_id": application["versions"][-1]["id"],
            "run_llm": True,
        },
    )
    assert resp.status_code == 201
    data = client.get(f"/api/assessments/{resp.json()['id']}").json()
    assert data["status"] == "error"
    assert "not configured" in data["progress"]["message"]


def test_run_llm_requires_key(client):
    guideline = make_guideline(client)
    application = make_application(client)
    resp = client.post(
        "/api/assessments",
        json={
            "guideline_version_id": guideline["versions"][-1]["id"],
            "application_version_id": application["versions"][-1]["id"],
            "run_llm": True,
        },
    )
    assert resp.status_code == 400
    assert "LLM not configured" in resp.json()["detail"]


def test_rerun_carry_forward_confirmed_mappings(client, llm_mocked):
    guideline = make_guideline(client)
    application = make_application(client)
    assessment_id = client.post(
        "/api/assessments",
        json={
            "guideline_version_id": guideline["versions"][-1]["id"],
            "application_version_id": application["versions"][-1]["id"],
            "run_llm": True,
        },
    ).json()["id"]

    data = client.get(f"/api/assessments/{assessment_id}").json()
    r1 = next(r for r in data["requirements"] if r["code"] == "R1")
    client.patch(f"/api/mappings/{r1['mapping']['id']}", json={"action": "confirm"})

    # new guideline version -> old assessment goes stale
    client.post(
        f"/api/documents/{guideline['id']}/versions/paste",
        json={"text": "# Community Grant Program\n\nUpdated deadline: 1 March 2027.\n\nApplicants must be registered non-profit organizations operating in the region.\n"},
    )
    old = client.get(f"/api/assessments/{assessment_id}").json()
    assert old["staleness"]["stale"] is True

    # re-run against current versions
    rerun = client.post(f"/api/assessments/{assessment_id}/rerun").json()
    assert rerun["carry_forward_from"] == assessment_id
    new_data = client.get(f"/api/assessments/{rerun['id']}").json()
    assert new_data["status"] == "review"
    assert new_data["staleness"]["stale"] is False

    # guideline changed -> requirements re-extracted (not copied)
    assert {r["code"] for r in new_data["requirements"]} == {"R1", "R2", "R3"}
    # application unchanged -> confirmed R1 mapping carried forward
    new_r1 = next(r for r in new_data["requirements"] if r["code"] == "R1")
    assert new_r1["mapping"]["status"] == "confirmed"
    new_r2 = next(r for r in new_data["requirements"] if r["code"] == "R2")
    assert new_r2["mapping"]["status"] == "suggested"  # unconfirmed mapping not carried
