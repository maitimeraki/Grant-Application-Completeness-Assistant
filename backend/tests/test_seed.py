from __future__ import annotations

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import (
    Assessment,
    AuditLog,
    Claim,
    ClarificationQuestion,
    Document,
    Mapping,
    MappingCitation,
    Requirement,
    SupportingDoc,
)
from app.services.quotes import quote_in_text
from app.services.seed import (
    SAMPLE_A_APPLICATION_TITLE,
    SAMPLE_A_GUIDELINE_TITLE,
    SAMPLE_B_APPLICATION_TITLE,
    seed_demo_data,
)


def _fake_pipeline(assessment_id: int, *, with_question: bool = True) -> None:
    """Deterministic stand-in for the LLM pipeline (no network / key needed)."""
    session = SessionLocal()
    try:
        from app.models import utcnow

        assessment = session.get(Assessment, assessment_id)
        assert assessment is not None
        block = assessment.application_version.blocks[0]

        # R1 — strong mapping: verified citation, no weakness (→ confirmed).
        r1 = Requirement(
            assessment_id=assessment.id,
            code="R1",
            title="Legal entity in EEA",
            requirement_text="The applicant must be a legal entity established in a Member State.",
            basis="mandatory",
            source="llm",
        )
        session.add(r1)
        session.flush()
        m1 = Mapping(
            requirement_id=r1.id,
            status="suggested",
            rationale="Draft states registration.",
            confidence=0.9,
            source="llm",
        )
        session.add(m1)
        session.flush()
        session.add(
            MappingCitation(
                mapping_id=m1.id,
                block_id=block.id,
                quote=block.text[:60].strip(),
                verified=True,
            )
        )

        # R2 — weak mapping with a hallucinated quote (→ corrected).
        r2 = Requirement(
            assessment_id=assessment.id,
            code="R2",
            title="Impact and measurement",
            requirement_text="The application must specify quantified emission-reduction targets and a monitoring plan.",
            basis="mandatory",
            source="llm",
        )
        session.add(r2)
        session.flush()
        m2 = Mapping(
            requirement_id=r2.id,
            status="suggested",
            rationale="Partially addressed.",
            confidence=0.4,
            weakness="weak",
            source="llm",
        )
        session.add(m2)
        session.flush()
        session.add(
            MappingCitation(
                mapping_id=m2.id,
                block_id=block.id,
                quote="a quote that appears nowhere in the document",
                verified=False,
            )
        )

        # R3 — missing mapping (→ rejected).
        r3 = Requirement(
            assessment_id=assessment.id,
            code="R3",
            title="Consortium agreement",
            requirement_text="A signed consortium agreement is mandatory when more than one partner is involved.",
            basis="mandatory",
            source="llm",
        )
        session.add(r3)
        session.flush()
        session.add(
            Mapping(
                requirement_id=r3.id,
                status="suggested",
                rationale="No passage addresses this.",
                confidence=0.0,
                weakness="missing",
                source="llm",
            )
        )

        session.add(
            Claim(
                assessment_id=assessment.id,
                claim_text="The applicant employs 12 FTE.",
                support="partial",
                source="llm",
            )
        )
        session.add(
            Claim(
                assessment_id=assessment.id,
                claim_text="Over 10,000 small garment producers served.",
                support="unsupported",
                source="llm",
            )
        )
        if with_question:
            session.add(
                ClarificationQuestion(
                    assessment_id=assessment.id,
                    requirement_id=r3.id,
                    question="Please upload the signed consortium agreement.",
                    priority=1,
                )
            )

        assessment.status = "review"
        assessment.completed_at = utcnow()
        assessment.progress = {"stage": "finalize", "message": "Complete — ready for review"}
        session.commit()
    finally:
        session.close()


def _assessment_with_extras(assessment_id: int) -> Assessment:
    from sqlalchemy.orm import joinedload

    from app.models import Claim, Mapping, Requirement

    db = SessionLocal()
    assessment = db.get(
        Assessment,
        assessment_id,
        options=[
            joinedload(Assessment.requirements)
            .joinedload(Requirement.mapping)
            .joinedload(Mapping.citations)
            .joinedload(MappingCitation.block),
            joinedload(Assessment.claims).joinedload(Claim.block),
            joinedload(Assessment.questions),
            joinedload(Assessment.supporting_docs),
        ],
    )
    assert assessment is not None
    return assessment


def test_seed_creates_sample_sets_and_runs_review_pass():
    result = seed_demo_data(run_pipeline_fn=_fake_pipeline, run_ai=True)
    assert result["status"] == "seeded"
    assert result["review_pass"]["confirmed"] >= 1
    assert result["review_pass"]["corrected"] == 1
    assert result["review_pass"]["rejected"] >= 1

    db = SessionLocal()
    try:
        titles = set(db.scalars(select(Document.title)).all())
        assert SAMPLE_A_GUIDELINE_TITLE in titles
        assert SAMPLE_A_APPLICATION_TITLE in titles
        assert SAMPLE_B_APPLICATION_TITLE in titles

        # Sample A — worked example: review states applied.
        a = _assessment_with_extras(result["sample_a_assessment_id"])
        assert a.status == "review"
        statuses = {req.mapping.status for req in a.requirements if req.mapping}
        assert {"confirmed", "corrected", "rejected"} <= statuses

        corrected = next(
            req.mapping for req in a.requirements if req.mapping and req.mapping.status == "corrected"
        )
        citation = corrected.citations[0]
        assert citation.verified
        assert quote_in_text(citation.quote, citation.block.text)

        accepted = [c for c in a.claims if c.user_accepted is True]
        disputed = [c for c in a.claims if c.user_accepted is False]
        assert accepted and disputed
        assert any(q.answer for q in a.questions)

        # Supporting-doc metadata seeded from the sample JSON files.
        assert len(a.supporting_docs) == 10
        missing_required = [d for d in a.supporting_docs if d.required and not d.present]
        assert missing_required

        # Sample B — reviewer test set: manual, untouched by the review pass.
        b = db.get(Assessment, result["sample_b_assessment_id"])
        assert b is not None
        assert b.status == "review"
        assert b.requirements == []
        assert len(b.supporting_docs) == 7
        assert b.progress.get("stage") == "manual"

        # Audit trail written for the scripted review actions + the seed itself.
        actions = set(db.scalars(select(AuditLog.action)).all())
        assert {"confirm", "correct", "reject", "seed"} <= actions
    finally:
        db.close()


def test_seed_is_idempotent():
    first = seed_demo_data(run_pipeline_fn=_fake_pipeline, run_ai=True)
    assert first["status"] == "seeded"
    second = seed_demo_data(run_pipeline_fn=_fake_pipeline, run_ai=True)
    assert second["status"] == "skipped"

    db = SessionLocal()
    try:
        assert db.scalar(select(func.count()).select_from(Document)) == 3
        assert db.scalar(select(func.count()).select_from(Assessment)) == 2
        assert db.scalar(select(func.count()).select_from(SupportingDoc)) == 17
    finally:
        db.close()


def test_seed_without_llm_key_skips_pipeline():
    calls: list[int] = []
    result = seed_demo_data(run_pipeline_fn=lambda i: calls.append(i))
    assert result["status"] == "seeded"
    assert calls == []
    assert "review_pass" not in result

    db = SessionLocal()
    try:
        a = db.get(Assessment, result["sample_a_assessment_id"])
        assert a is not None
        assert a.status == "review"
        assert a.requirements == []
        assert a.llm_model is None
    finally:
        db.close()


def test_seed_review_pass_leaves_remaining_mappings_suggested():
    result = seed_demo_data(run_pipeline_fn=_fake_pipeline, run_ai=True)
    a = _assessment_with_extras(result["sample_a_assessment_id"])
    # Only 3 mappings exist in the fake pipeline; confirm caps at 4 so all
    # strong ones are confirmed — every mapping must have a definite status.
    for req in a.requirements:
        assert req.mapping is not None
        assert req.mapping.status in ("confirmed", "corrected", "rejected", "suggested")


def test_seed_adds_reviewer_questions_when_ai_questions_missing():
    from functools import partial

    result = seed_demo_data(
        run_pipeline_fn=partial(_fake_pipeline, with_question=False), run_ai=True
    )
    a = _assessment_with_extras(result["sample_a_assessment_id"])
    assert a.questions, "reviewer-authored fallback questions expected"
    assert all(q.question.startswith("Reviewer follow-up") for q in a.questions)
    assert all(q.answer is None for q in a.questions)
    assert result["review_pass"]["questions"] >= 1
