"""End-to-end workflow test driven by the production-format sample inputs
in samples/ (grant-guideline.md, draft-application.md, supporting-documents.json).

The LLM is mocked (quotes are taken verbatim from the real sample files), so
this runs deterministically in CI while exercising ingestion, extraction,
mapping, gap analysis, claims, questions, human review, deterministic
completion, supporting-doc tracking, summary and staleness on real documents.
"""
from __future__ import annotations

import json
from pathlib import Path
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

SAMPLES = Path(__file__).resolve().parent.parent.parent / "samples"
GUIDELINE_MD = (SAMPLES / "grant-guideline.md").read_text(encoding="utf-8")
APPLICATION_MD = (SAMPLES / "draft-application.md").read_text(encoding="utf-8")
SUPPORTING_DOCS = json.loads((SAMPLES / "supporting-documents.json").read_text(encoding="utf-8"))

# Verbatim quotes from the real sample files
G_PORTAL = "The application must be submitted through the NCIA portal."
G_AUDITED = "audited financial statements must be uploaded"
G_LOGIC = "A logic model or theory of change for the project (recommended)."
G_FAKE = "A cash-flow forecast covering 24 months must be attached."  # not in the guideline
A_AUDITED = "two complete audited financial statements (2024, 2025)"
A_LOGIC = "A logic model covering inputs, activities, outputs and outcomes is included"
A_CLAIM = "more than 10,000 small garment producers"


def fake_complete_json(system: str, user: str, schema: type, **_: Any):
    if schema is ExtractionResult:
        return ExtractionResult(
            requirements=[
                ExtractedRequirement(
                    code="R1",
                    title="Portal submission before deadline",
                    requirement_text="The application must be submitted through the NCIA portal before the deadline.",
                    basis="mandatory",
                    basis_quote=G_PORTAL,
                    category="submission",
                ),
                ExtractedRequirement(
                    code="R2",
                    title="Two audited financial statements",
                    requirement_text="Two complete audited financial statements must be uploaded.",
                    basis="mandatory",
                    basis_quote=G_AUDITED,
                    category="eligibility",
                    required_supporting_doc_name="Audited financial statements",
                ),
                ExtractedRequirement(
                    code="R3",
                    title="Logic model (recommended)",
                    requirement_text="Applicants should include a logic model or theory of change.",
                    basis="recommended",
                    basis_quote=G_LOGIC,
                ),
                ExtractedRequirement(
                    code="R4",
                    title="Cash-flow forecast",
                    requirement_text="A cash-flow forecast must be attached.",
                    basis="mandatory",
                    basis_quote=G_FAKE,  # hallucinated -> demoted to ambiguous
                ),
            ]
        )
    if schema is MappingResult:
        code = user.split("REQUIREMENT")[1].split(":")[0].strip()
        if code == "R2":
            return MappingResult(mapped=True, quotes=[A_AUDITED],
                                  rationale="Draft states audited statements exist.", confidence=0.9, weakness="none")
        if code == "R3":
            return MappingResult(mapped=True, quotes=[A_LOGIC],
                                 rationale="Logic model referenced in annex list.", confidence=0.85, weakness="none")
        return MappingResult(mapped=False, quotes=[],
                             rationale="No passage addresses this requirement.", confidence=0.9, weakness="none")
    if schema is ClaimsResult:
        return ClaimsResult(claims=[ExtractedClaim(
            claim_text="Served more than 10,000 small garment producers in the past year",
            quote=A_CLAIM, support="unsupported", supporting_refs=[],
        )])
    if schema is QuestionsResult:
        return QuestionsResult(questions=[ExtractedQuestion(
            requirement_code="R1", question="Has the application been uploaded to the NCIA portal before 31 March 2026?",
            priority=1,
        )])
    raise AssertionError(f"unexpected schema {schema}")


@pytest.fixture()
def llm_mocked(monkeypatch):
    import app.routers.assessments as assessments_router
    import app.services.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "complete_json", fake_complete_json)
    monkeypatch.setattr(pipeline_module, "llm_available", lambda: True)
    monkeypatch.setattr(assessments_router, "llm_available", lambda: True)


@pytest.fixture()
def sample_assessment(client, llm_mocked):
    g = client.post("/api/documents/paste",
                    json={"kind": "guideline", "title": "Green Horizon Call GH-2026-02", "text": GUIDELINE_MD})
    a = client.post("/api/documents/paste",
                    json={"kind": "application", "title": "ClosedLoop-Dye draft", "text": APPLICATION_MD})
    assert g.status_code == 201 and a.status_code == 201, (g.text, a.text)
    resp = client.post("/api/assessments", json={
        "guideline_version_id": g.json()["versions"][-1]["id"],
        "application_version_id": a.json()["versions"][-1]["id"],
        "run_llm": True,
        "supporting_docs": SUPPORTING_DOCS,
    })
    assert resp.status_code == 201, resp.text
    return {"guideline": g.json(), "application": a.json(), "assessment_id": resp.json()["id"]}


def test_sample_documents_ingest_as_real_sized_documents(client):
    g = client.post("/api/documents/paste",
                    json={"kind": "guideline", "title": "Call", "text": GUIDELINE_MD}).json()
    a = client.post("/api/documents/paste",
                    json={"kind": "application", "title": "Draft", "text": APPLICATION_MD}).json()
    assert g["versions"][-1]["block_count"] >= 30  # production-format guideline, not a stub
    assert a["versions"][-1]["block_count"] >= 15
    assert not g["versions"][-1]["extraction_warnings"], g["versions"][-1]["extraction_warnings"]


def test_sample_pipeline_extract_map_and_verify(client, sample_assessment):
    data = client.get(f"/api/assessments/{sample_assessment['assessment_id']}").json()
    assert data["status"] == "review"

    by_code = {r["code"]: r for r in data["requirements"]}
    assert set(by_code) == {"R1", "R2", "R3", "R4"}

    # verbatim guideline quotes verified against the real guideline
    assert by_code["R1"]["basis"] == "mandatory" and by_code["R1"]["basis_verified"] is True
    assert by_code["R2"]["basis"] == "mandatory" and by_code["R2"]["basis_verified"] is True
    assert by_code["R3"]["basis"] == "recommended"
    # hallucinated quote demoted
    assert by_code["R4"]["basis"] == "ambiguous" and by_code["R4"]["basis_verified"] is False

    # application citations verified against the real application
    assert by_code["R2"]["mapping"]["citations"][0]["verified"] is True
    assert by_code["R3"]["mapping"]["citations"][0]["verified"] is True
    # unmapped requirement flagged as missing
    assert by_code["R1"]["mapping"]["weakness"] == "missing"

    # claims + clarification questions from the real documents
    assert data["claims"][0]["support"] == "unsupported"
    assert A_CLAIM in data["claims"][0]["claim_text"]
    assert len(data["questions"]) == 1

    # sample supporting-doc metadata ingested
    assert len(data["supporting_docs"]) == len(SUPPORTING_DOCS)
    missing_required = [d for d in data["supporting_docs"] if d["required"] and not d["present"]]
    assert len(missing_required) == 5  # audited statements, headcount, consortium, A3, 2nd letter


def test_sample_completion_and_human_review(client, sample_assessment):
    aid = sample_assessment["assessment_id"]
    data = client.get(f"/api/assessments/{aid}").json()
    by_code = {r["code"]: r for r in data["requirements"]}

    # deterministic completion: both mandatory requirements are open suggestions
    assert data["completion"]["mandatory"]["total"] == 2
    assert data["completion"]["mandatory"]["open"] == 2
    assert "not an authoritative funding-eligibility" in data["completion"]["disclaimer"]

    # reviewer confirms R2, but its supporting doc is missing -> not satisfied yet
    resp = client.patch(f"/api/mappings/{by_code['R2']['mapping']['id']}", json={"action": "confirm"})
    assert resp.status_code == 200, resp.text
    data = client.get(f"/api/assessments/{aid}").json()
    assert data["completion"]["mandatory"]["satisfied"] == 0
    assert data["completion"]["mandatory"]["unsatisfied"] == 1

    # mark the audited statements as present -> requirement satisfied
    doc = next(d for d in data["supporting_docs"] if d["expected_name"] == "Audited financial statements")
    assert client.patch(f"/api/supporting-docs/{doc['id']}", json={"present": True}).status_code == 200
    data = client.get(f"/api/assessments/{aid}").json()
    assert data["completion"]["mandatory"]["satisfied"] == 1

    # reviewer rejects the requirement with no evidence (R1)
    resp = client.patch(f"/api/mappings/{by_code['R1']['mapping']['id']}",
                        json={"action": "reject", "note": "Not submitted yet"})
    assert resp.status_code == 200, resp.text
    data = client.get(f"/api/assessments/{aid}").json()
    assert data["completion"]["mandatory"]["unsatisfied"] == 1
    assert data["completion"]["mandatory"]["open"] == 0

    # hallucinated basis (R4) is surfaced as an ambiguity for the reviewer
    assert data["completion"]["ambiguous_basis_count"] == 1


def test_sample_reviewed_summary(client, sample_assessment):
    aid = sample_assessment["assessment_id"]
    summary = client.get(f"/api/assessments/{aid}/summary").json()
    md = summary["markdown"]
    assert "Completeness Review Summary" in md
    assert "not an authoritative funding-eligibility" in md
    assert "Green Horizon" in md  # documents the source guideline
    assert summary["disclaimer"]


def test_sample_staleness_on_guideline_change(client, sample_assessment):
    aid = sample_assessment["assessment_id"]
    gid = sample_assessment["guideline"]["id"]
    resp = client.post(f"/api/documents/{gid}/versions/paste",
                       json={"text": GUIDELINE_MD + "\n\n## 8. Amendment 1\n\nNew annex requirement.\n"})
    assert resp.status_code == 201, resp.text
    data = client.get(f"/api/assessments/{aid}").json()
    assert data["staleness"]["stale"] is True
    assert data["staleness"]["reasons"]
