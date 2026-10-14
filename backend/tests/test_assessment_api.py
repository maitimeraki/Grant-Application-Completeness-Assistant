from __future__ import annotations

from tests.conftest import (
    APPLICATION_MD,
    GUIDELINE_MD,
    make_application,
    make_assessment,
    make_guideline,
)


def _add_requirement_and_mapping(
    client, assessment_id: int, *, code="R1", quote: str, block_text_hint: str,
    required_supporting_doc_name: str | None = None,
):
    """Helper: create a manual requirement + mapping against a block containing the hint."""
    assessment = client.get(f"/api/assessments/{assessment_id}").json()
    app_blocks = client.get(
        f"/api/versions/{assessment['application_version']['id']}/blocks"
    ).json()
    block = next(
        b for b in app_blocks
        if " ".join(b["text"].split()).find(" ".join(block_text_hint.split())) >= 0
    )

    req_resp = client.post(
        f"/api/assessments/{assessment_id}/requirements",
        json={
            "code": code,
            "title": "Registered non-profit status",
            "requirement_text": "Applicants must be registered non-profit organizations.",
            "basis": "mandatory",
            "required_supporting_doc_name": required_supporting_doc_name,
        },
    )
    assert req_resp.status_code == 201, req_resp.text
    req_id = req_resp.json()["id"]

    map_resp = client.post(
        f"/api/requirements/{req_id}/mappings",
        json={"quote": quote, "block_id": block["id"], "status": "confirmed"},
    )
    assert map_resp.status_code == 201, map_resp.text
    return req_id, map_resp.json()


def test_manual_assessment_full_flow(client):
    guideline = make_guideline(client)
    application = make_application(client)
    created = make_assessment(
        client,
        guideline,
        application,
        supporting_docs=[{"expected_name": "Cover Letter", "required": True, "present": False}],
    )
    assessment_id = created["id"]
    assert created["status"] == "review"

    data = client.get(f"/api/assessments/{assessment_id}").json()
    assert data["staleness"]["stale"] is False
    assert data["completion"]["mandatory"]["total"] == 0
    assert data["supporting_docs"][0]["present"] is False

    _, mapping = _add_requirement_and_mapping(
        client, assessment_id,
        quote="registered non-profit organization",
        block_text_hint="ten years of experience",
        required_supporting_doc_name="Cover Letter",
    )
    assert mapping["verified"] is True

    data = client.get(f"/api/assessments/{assessment_id}").json()
    req = data["requirements"][0]
    assert req["mapping"]["status"] == "confirmed"
    assert req["mapping"]["citations"][0]["verified"] is True
    assert data["completion"]["mandatory"]["satisfied"] == 0  # supporting doc still missing

    # mark supporting doc present -> satisfied
    doc_id = data["supporting_docs"][0]["id"]
    assert client.patch(f"/api/supporting-docs/{doc_id}", json={"present": True}).status_code == 200
    data = client.get(f"/api/assessments/{assessment_id}").json()
    assert data["completion"]["mandatory"]["satisfied"] == 1
    assert data["completion"]["mandatory"]["percentage"] == 100.0


def test_mapping_reject_and_correct(client):
    guideline = make_guideline(client)
    application = make_application(client)
    assessment_id = make_assessment(client, guideline, application)["id"]

    req_id, mapping = _add_requirement_and_mapping(
        client, assessment_id,
        quote="registered non-profit organization",
        block_text_hint="ten years of experience",
    )
    mapping_id = mapping["id"]

    # reject
    resp = client.patch(f"/api/mappings/{mapping_id}", json={"action": "reject", "note": "wrong section"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    data = client.get(f"/api/assessments/{assessment_id}").json()
    assert data["completion"]["mandatory"]["unsatisfied"] == 1

    # correct with a verified replacement citation
    assessment = client.get(f"/api/assessments/{assessment_id}").json()
    blocks = client.get(f"/api/versions/{assessment['application_version']['id']}/blocks").json()
    block = next(b for b in blocks if "42,000 EUR" in b["text"])
    resp = client.patch(
        f"/api/mappings/{mapping_id}",
        json={"action": "correct", "quote": "42,000 EUR", "block_id": block["id"], "note": "cite budget"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "corrected"

    data = client.get(f"/api/assessments/{assessment_id}").json()
    citation = data["requirements"][0]["mapping"]["citations"][0]
    assert citation["verified"] is True
    assert "42,000 EUR" in citation["quote"]


def test_correct_rejects_quote_not_in_block(client):
    guideline = make_guideline(client)
    application = make_application(client)
    assessment_id = make_assessment(client, guideline, application)["id"]
    _, mapping = _add_requirement_and_mapping(
        client, assessment_id,
        quote="registered non-profit organization",
        block_text_hint="ten years of experience",
    )
    resp = client.patch(
        f"/api/mappings/{mapping['id']}",
        json={"action": "confirm", "quote": "this text appears nowhere", "block_id": 1},
    )
    assert resp.status_code == 400
    assert "does not appear" in resp.json()["detail"]


def test_requirement_basis_override_and_na(client):
    guideline = make_guideline(client)
    application = make_application(client)
    assessment_id = make_assessment(client, guideline, application)["id"]
    req_id, _ = _add_requirement_and_mapping(
        client, assessment_id,
        quote="registered non-profit organization",
        block_text_hint="ten years of experience",
    )
    resp = client.patch(f"/api/requirements/{req_id}", json={"not_applicable": True})
    assert resp.status_code == 200
    data = client.get(f"/api/assessments/{assessment_id}").json()
    assert data["requirements"][0]["not_applicable"] is True
    assert data["completion"]["mandatory"]["total"] == 0
    assert data["completion"]["mandatory"]["not_applicable"] == 1

    resp = client.patch(f"/api/requirements/{req_id}", json={"basis": "recommended"})
    assert resp.json()["basis"] == "recommended"


def test_staleness_after_new_version(client):
    guideline = make_guideline(client)
    application = make_application(client)
    assessment_id = make_assessment(client, guideline, application)["id"]

    # new application version
    client.post(
        f"/api/documents/{application['id']}/versions/paste",
        json={"text": APPLICATION_MD + "\n\nWe also partner with three schools.\n"},
    )
    data = client.get(f"/api/assessments/{assessment_id}").json()
    assert data["staleness"]["stale"] is True
    assert data["staleness"]["application_stale"] is True
    assert any("newer version" in r for r in data["staleness"]["reasons"])


def test_summary_markdown(client):
    guideline = make_guideline(client)
    application = make_application(client)
    assessment_id = make_assessment(
        client, guideline, application,
        supporting_docs=[{"expected_name": "Cover Letter", "required": True}],
    )["id"]
    _add_requirement_and_mapping(
        client, assessment_id,
        quote="registered non-profit organization",
        block_text_hint="ten years of experience",
    )

    summary = client.get(f"/api/assessments/{assessment_id}/summary").json()
    md = summary["markdown"]
    assert "# Completeness Review Summary" in md
    assert "not an authoritative funding-eligibility" in md
    assert "Community Grant" in md
    assert "Cover Letter" in md  # missing required supporting document listed
    assert "v1" in md


def test_duplicate_requirement_code_conflict(client):
    guideline = make_guideline(client)
    application = make_application(client)
    assessment_id = make_assessment(client, guideline, application)["id"]
    payload = {"code": "R1", "title": "T", "requirement_text": "Text", "basis": "mandatory"}
    assert client.post(f"/api/assessments/{assessment_id}/requirements", json=payload).status_code == 201
    assert client.post(f"/api/assessments/{assessment_id}/requirements", json=payload).status_code == 409


def test_assessment_rejects_wrong_document_kinds(client):
    guideline = make_guideline(client)
    application = make_application(client)
    # swap: use application version as "guideline"
    resp = client.post(
        "/api/assessments",
        json={
            "guideline_version_id": application["versions"][-1]["id"],
            "application_version_id": guideline["versions"][-1]["id"],
            "run_llm": False,
        },
    )
    assert resp.status_code == 400


def test_claim_and_question_review(client):
    guideline = make_guideline(client)
    application = make_application(client)
    assessment_id = make_assessment(client, guideline, application)["id"]

    from app.db import SessionLocal
    from app.models import Assessment, Claim, ClarificationQuestion

    with SessionLocal() as session:
        assessment = session.get(Assessment, assessment_id)
        session.add(Claim(assessment_id=assessment.id, claim_text="Delivered to 5,000 people", support="unsupported"))
        session.add(ClarificationQuestion(assessment_id=assessment.id, question="How was reach measured?"))
        session.commit()

    data = client.get(f"/api/assessments/{assessment_id}").json()
    claim_id = data["claims"][0]["id"]
    question_id = data["questions"][0]["id"]

    resp = client.patch(f"/api/claims/{claim_id}", json={"user_accepted": False, "note": "disputed"})
    assert resp.json()["user_accepted"] is False

    resp = client.patch(f"/api/questions/{question_id}", json={"answer": "Survey data"})
    assert resp.json()["answer"] == "Survey data"

    summary = client.get(f"/api/assessments/{assessment_id}/summary").json()["markdown"]
    assert "Answered clarification questions" in summary
    assert "Survey data" in summary
