from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.models import (
    Assessment,
    AuditLog,
    ClarificationQuestion,
    Claim,
    Document,
    DocumentVersion,
    LLMCallLog,
    Mapping,
    MappingCitation,
    Requirement,
    SupportingDoc,
    TextBlock,
    utcnow,
)
from app.schemas import (
    AssessmentCreate,
    AssessmentOut,
    AssessmentSummaryOut,
    ClaimOut,
    ClaimPatch,
    LLMCallLogOut,
    MappingCreate,
    MappingOut,
    MappingPatch,
    QuestionOut,
    QuestionPatch,
    RequirementCreate,
    RequirementOut,
    RequirementPatch,
    SupportingDocOut,
    SupportingDocPatch,
)
from app.services.llm import llm_available
from app.services.pipeline import PIPELINE_VERSION, run_llm_pipeline
from app.services.quotes import quote_in_text
from app.services.staleness import compute_staleness
from app.services.summary import DISCLAIMER, build_completion, generate_summary_markdown

router = APIRouter(prefix="/api", tags=["assessments"])


# ---------- serialization ----------

def _version_out(version: DocumentVersion) -> dict:
    return {
        "id": version.id,
        "version_no": version.version_no,
        "content_hash": version.content_hash,
        "source_type": version.source_type,
        "original_filename": version.original_filename,
        "extraction_warnings": version.extraction_warnings or [],
        "created_at": version.created_at,
        "block_count": len(version.blocks),
    }


def _mapping_out(mapping: Mapping) -> MappingOut:
    return MappingOut(
        id=mapping.id,
        status=mapping.status,
        rationale=mapping.rationale,
        confidence=mapping.confidence,
        weakness=mapping.weakness,
        user_note=mapping.user_note,
        source=mapping.source,
        reviewed_at=mapping.reviewed_at,
        citations=[
            {
                "id": c.id,
                "block_id": c.block_id,
                "quote": c.quote,
                "verified": c.verified,
                "block": c.block,
            }
            for c in sorted(mapping.citations, key=lambda x: (not x.verified, x.id))
        ],
    )


def _requirement_out(req: Requirement) -> RequirementOut:
    return RequirementOut(
        id=req.id,
        code=req.code,
        title=req.title,
        requirement_text=req.requirement_text,
        basis=req.basis,
        basis_quote=req.basis_quote,
        basis_block_id=req.basis_block_id,
        basis_verified=req.basis_verified,
        basis_block=req.basis_block,
        category=req.category,
        required_supporting_doc_name=req.required_supporting_doc_name,
        not_applicable=req.not_applicable,
        source=req.source,
        mapping=_mapping_out(req.mapping) if req.mapping else None,
    )


def _assessment_out(assessment: Assessment) -> AssessmentOut:
    completion = build_completion(assessment)
    staleness = compute_staleness(
        assessment,
        assessment.guideline_version.document,
        assessment.application_version.document,
    )
    return AssessmentOut(
        id=assessment.id,
        status=assessment.status,
        progress=assessment.progress or {},
        pipeline_version=assessment.pipeline_version,
        llm_model=assessment.llm_model,
        created_at=assessment.created_at,
        completed_at=assessment.completed_at,
        guideline_version=_version_out(assessment.guideline_version),
        application_version=_version_out(assessment.application_version),
        guideline_document_title=assessment.guideline_version.document.title,
        application_document_title=assessment.application_version.document.title,
        requirements=[_requirement_out(r) for r in assessment.requirements],
        supporting_docs=[SupportingDocOut.model_validate(d) for d in assessment.supporting_docs],
        claims=[ClaimOut.model_validate(c) for c in assessment.claims],
        questions=[QuestionOut.model_validate(q) for q in assessment.questions],
        completion=completion,
        staleness=staleness,
    )


ASSESSMENT_LOADS = [
    joinedload(Assessment.guideline_version).joinedload(DocumentVersion.document),
    joinedload(Assessment.guideline_version).joinedload(DocumentVersion.blocks),
    joinedload(Assessment.application_version).joinedload(DocumentVersion.document),
    joinedload(Assessment.application_version).joinedload(DocumentVersion.blocks),
    joinedload(Assessment.requirements)
    .joinedload(Requirement.mapping)
    .joinedload(Mapping.citations)
    .joinedload(MappingCitation.block),
    joinedload(Assessment.requirements).joinedload(Requirement.basis_block),
    joinedload(Assessment.supporting_docs),
    joinedload(Assessment.claims).joinedload(Claim.block),
    joinedload(Assessment.questions),
]


def _get_assessment_or_404(db: Session, assessment_id: int) -> Assessment:
    assessment = db.get(
        Assessment, assessment_id, options=ASSESSMENT_LOADS
    )
    if assessment is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return assessment


def _get_version_or_404(db: Session, version_id: int) -> DocumentVersion:
    version = db.get(
        DocumentVersion, version_id, options=[joinedload(DocumentVersion.document)]
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Document version not found")
    return version


def _audit(db: Session, entity: str, entity_id: int, action: str, before: dict, after: dict) -> None:
    db.add(AuditLog(entity=entity, entity_id=entity_id, action=action, before=before, after=after))


# ---------- assessment lifecycle ----------

@router.get("/assessments")
def list_assessments(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(Assessment)
        .options(
            joinedload(Assessment.guideline_version).joinedload(DocumentVersion.document),
            joinedload(Assessment.application_version).joinedload(DocumentVersion.document),
        )
        .order_by(Assessment.created_at.desc())
    ).unique().all()
    return [
        {
            "id": a.id,
            "status": a.status,
            "created_at": a.created_at,
            "guideline_title": a.guideline_version.document.title,
            "guideline_version_no": a.guideline_version.version_no,
            "application_title": a.application_version.document.title,
            "application_version_no": a.application_version.version_no,
        }
        for a in rows
    ]


def _copy_forward(previous: Assessment, assessment: Assessment, db: Session) -> None:
    """Reuse requirements from a previous assessment when the guideline is unchanged.

    Mapping carry-forward happens inside the pipeline (after extraction), because
    requirement codes only exist once extraction has run.
    """
    if previous.guideline_version_id == assessment.guideline_version_id:
        for req in previous.requirements:
            db.add(
                Requirement(
                    assessment_id=assessment.id,
                    code=req.code,
                    title=req.title,
                    requirement_text=req.requirement_text,
                    basis=req.basis,
                    basis_quote=req.basis_quote,
                    basis_block_id=req.basis_block_id,
                    basis_verified=req.basis_verified,
                    category=req.category,
                    required_supporting_doc_name=req.required_supporting_doc_name,
                    not_applicable=req.not_applicable,
                    source=req.source,
                )
            )
    db.commit()


@router.post("/assessments", status_code=201)
def create_assessment(
    body: AssessmentCreate, background: BackgroundTasks, db: Session = Depends(get_db)
) -> dict:
    g_version = _get_version_or_404(db, body.guideline_version_id)
    a_version = _get_version_or_404(db, body.application_version_id)
    if g_version.document.kind != "guideline":
        raise HTTPException(status_code=400, detail="Selected version is not a guideline document")
    if a_version.document.kind != "application":
        raise HTTPException(status_code=400, detail="Selected version is not an application document")
    if body.run_llm and not llm_available():
        raise HTTPException(
            status_code=400,
            detail="LLM not configured (set LLM_API_KEY in backend/.env) or create the assessment with run_llm=false.",
        )

    assessment = Assessment(
        guideline_version_id=g_version.id,
        application_version_id=a_version.id,
        guideline_hash=g_version.content_hash,
        application_hash=a_version.content_hash,
        pipeline_version=PIPELINE_VERSION,
        llm_model=None if not body.run_llm else _current_model(),
        status="running" if body.run_llm else "review",
        progress={"stage": "queued", "message": "Queued"} if body.run_llm else {"stage": "manual", "message": "Manual review"},
    )
    db.add(assessment)
    for doc_in in body.supporting_docs:
        db.add(
            SupportingDoc(
                assessment=assessment,
                expected_name=doc_in.expected_name.strip(),
                required=doc_in.required,
                present=doc_in.present,
                metadata_json=doc_in.metadata,
                source="user",
            )
        )
    db.commit()

    if body.run_llm:
        background.add_task(run_llm_pipeline, assessment.id)
    return {"id": assessment.id, "status": assessment.status}


def _current_model() -> str:
    from app.config import get_settings

    return get_settings().llm_model


@router.get("/assessments/{assessment_id}")
def get_assessment(assessment_id: int, db: Session = Depends(get_db)) -> dict:
    assessment = _get_assessment_or_404(db, assessment_id)
    return _assessment_out(assessment).model_dump(mode="json")


@router.post("/assessments/{assessment_id}/rerun", status_code=201)
def rerun_assessment(
    assessment_id: int, background: BackgroundTasks, db: Session = Depends(get_db)
) -> dict:
    previous = _get_assessment_or_404(db, assessment_id)
    g_version = previous.guideline_version
    a_version = previous.application_version
    if previous.guideline_version.document.active_version:
        g_version = previous.guideline_version.document.active_version
    if previous.application_version.document.active_version:
        a_version = previous.application_version.document.active_version

    if not llm_available():
        raise HTTPException(
            status_code=400,
            detail="LLM not configured (set LLM_API_KEY in backend/.env) — re-run requires the LLM pipeline.",
        )

    assessment = Assessment(
        guideline_version_id=g_version.id,
        application_version_id=a_version.id,
        guideline_hash=g_version.content_hash,
        application_hash=a_version.content_hash,
        pipeline_version=PIPELINE_VERSION,
        llm_model=_current_model(),
        status="running",
        progress={"stage": "queued", "message": "Queued", "carry_forward_from": previous.id},
    )
    db.add(assessment)
    for doc in previous.supporting_docs:
        db.add(
            SupportingDoc(
                assessment=assessment,
                expected_name=doc.expected_name,
                required=doc.required,
                present=doc.present,
                metadata_json=doc.metadata_json,
                source=doc.source,
            )
        )
    db.commit()
    _copy_forward(previous, assessment, db)
    background.add_task(run_llm_pipeline, assessment.id)
    return {"id": assessment.id, "status": assessment.status, "carry_forward_from": previous.id}


# ---------- manual requirement/mapping creation ----------

@router.post("/assessments/{assessment_id}/requirements", status_code=201)
def create_requirement(
    assessment_id: int, body: RequirementCreate, db: Session = Depends(get_db)
) -> dict:
    _get_assessment_or_404(db, assessment_id)
    existing = db.scalar(
        select(Requirement).where(
            Requirement.assessment_id == assessment_id, Requirement.code == body.code
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail=f"Requirement code '{body.code}' already exists")
    basis = body.basis
    basis_verified = False
    basis_block_id = None
    if body.basis_quote and body.basis_block_id:
        block = db.get(TextBlock, body.basis_block_id)
        if block is None:
            raise HTTPException(status_code=400, detail="basis_block_id not found")
        if quote_in_text(body.basis_quote, block.text):
            basis_block_id = block.id
            basis_verified = True
        elif basis != "ambiguous":
            basis = "ambiguous"
    req = Requirement(
        assessment_id=assessment_id,
        code=body.code.strip()[:50],
        title=body.title.strip(),
        requirement_text=body.requirement_text.strip(),
        basis=basis,
        basis_quote=body.basis_quote,
        basis_block_id=basis_block_id,
        basis_verified=basis_verified,
        category=body.category,
        required_supporting_doc_name=body.required_supporting_doc_name,
        source="manual",
    )
    db.add(req)
    db.commit()
    return {"id": req.id, "code": req.code}


@router.patch("/requirements/{requirement_id}")
def patch_requirement(
    requirement_id: int, body: RequirementPatch, db: Session = Depends(get_db)
) -> dict:
    req = db.get(Requirement, requirement_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Requirement not found")
    before = {"basis": req.basis, "not_applicable": req.not_applicable}
    if body.basis is not None:
        req.basis = body.basis
    if body.not_applicable is not None:
        req.not_applicable = body.not_applicable
    after = {"basis": req.basis, "not_applicable": req.not_applicable}
    _audit(db, "requirement", req.id, "review", before, after)
    db.commit()
    return {"id": req.id, **after}


@router.post("/requirements/{requirement_id}/mappings", status_code=201)
def create_manual_mapping(
    requirement_id: int, body: MappingCreate, db: Session = Depends(get_db)
) -> dict:
    req = db.get(Requirement, requirement_id, options=[joinedload(Requirement.mapping)])
    if req is None:
        raise HTTPException(status_code=404, detail="Requirement not found")
    if req.mapping is not None:
        raise HTTPException(status_code=409, detail="Mapping already exists — patch it instead")
    block = db.get(TextBlock, body.block_id)
    if block is None:
        raise HTTPException(status_code=400, detail="block_id not found")
    verified = quote_in_text(body.quote, block.text)
    mapping = Mapping(
        requirement_id=req.id,
        status=body.status,
        rationale=body.rationale,
        confidence=1.0 if verified else 0.5,
        weakness=None if verified else "weak",
        source="manual",
    )
    db.add(mapping)
    db.flush()
    db.add(MappingCitation(mapping_id=mapping.id, block_id=block.id, quote=body.quote.strip(), verified=verified))
    db.commit()
    return {"id": mapping.id, "verified": verified}


# ---------- review actions ----------

@router.patch("/mappings/{mapping_id}")
def patch_mapping(mapping_id: int, body: MappingPatch, db: Session = Depends(get_db)) -> dict:
    mapping = db.get(
        Mapping,
        mapping_id,
        options=[
            joinedload(Mapping.citations).joinedload(MappingCitation.block),
            joinedload(Mapping.requirement).joinedload(Requirement.assessment),
        ],
    )
    if mapping is None:
        raise HTTPException(status_code=404, detail="Mapping not found")
    assessment = mapping.requirement.assessment
    if assessment.status == "running":
        raise HTTPException(status_code=409, detail="Assessment pipeline is still running")

    before = {"status": mapping.status, "user_note": mapping.user_note, "citations": len(mapping.citations)}
    status_map = {"confirm": "confirmed", "correct": "corrected", "reject": "rejected"}
    mapping.status = status_map[body.action]
    mapping.user_note = body.note
    mapping.reviewed_at = utcnow()

    if body.action in ("confirm", "correct") and body.quote and body.block_id is not None:
        block = db.get(TextBlock, body.block_id)
        if block is None:
            raise HTTPException(status_code=400, detail="block_id not found")
        if not quote_in_text(body.quote, block.text):
            raise HTTPException(
                status_code=400,
                detail="The supplied quote does not appear in the selected text block",
            )
        for old in list(mapping.citations):
            db.delete(old)
        db.flush()
        db.add(
            MappingCitation(
                mapping_id=mapping.id, block_id=block.id, quote=body.quote.strip(), verified=True
            )
        )
    elif body.action == "confirm":
        # trust falls back to the deterministic quote check
        for citation in mapping.citations:
            citation.verified = quote_in_text(citation.quote, citation.block.text)

    after = {"status": mapping.status, "user_note": mapping.user_note}
    _audit(db, "mapping", mapping.id, body.action, before, after)
    db.commit()
    return {"id": mapping.id, **after}


@router.patch("/claims/{claim_id}")
def patch_claim(claim_id: int, body: ClaimPatch, db: Session = Depends(get_db)) -> dict:
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    before = {"support": claim.support, "note": claim.note, "user_accepted": claim.user_accepted}
    if body.support is not None:
        claim.support = body.support
    if body.note is not None:
        claim.note = body.note
    if body.user_accepted is not None:
        claim.user_accepted = body.user_accepted
    _audit(db, "claim", claim.id, "review", before, {"support": claim.support, "note": claim.note})
    db.commit()
    return {"id": claim.id, "support": claim.support, "user_accepted": claim.user_accepted}


@router.patch("/supporting-docs/{doc_id}")
def patch_supporting_doc(doc_id: int, body: SupportingDocPatch, db: Session = Depends(get_db)) -> dict:
    doc = db.get(SupportingDoc, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Supporting document not found")
    if body.present is not None:
        doc.present = body.present
    if body.required is not None:
        doc.required = body.required
    db.commit()
    return {"id": doc.id, "present": doc.present, "required": doc.required}


@router.patch("/questions/{question_id}")
def patch_question(question_id: int, body: QuestionPatch, db: Session = Depends(get_db)) -> dict:
    question = db.get(ClarificationQuestion, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")
    question.answer = body.answer.strip() if body.answer and body.answer.strip() else None
    question.answered_at = utcnow() if question.answer else None
    db.commit()
    return {"id": question.id, "answer": question.answer}


# ---------- summary ----------

@router.get("/assessments/{assessment_id}/llm-logs", response_model=list[LLMCallLogOut])
def get_llm_logs(assessment_id: int, db: Session = Depends(get_db)) -> list[LLMCallLogOut]:
    """Structured record of every LLM call made while running this assessment."""
    _get_assessment_or_404(db, assessment_id)
    rows = db.scalars(
        select(LLMCallLog)
        .where(LLMCallLog.assessment_id == assessment_id)
        .order_by(LLMCallLog.created_at.asc(), LLMCallLog.id.asc())
    ).all()
    return [LLMCallLogOut.model_validate(r) for r in rows]


@router.get("/assessments/{assessment_id}/summary", response_model=AssessmentSummaryOut)
def get_summary(assessment_id: int, db: Session = Depends(get_db)) -> AssessmentSummaryOut:
    assessment = _get_assessment_or_404(db, assessment_id)
    completion = build_completion(assessment)
    markdown = generate_summary_markdown(assessment, completion)
    return AssessmentSummaryOut(
        assessment_id=assessment.id, markdown=markdown, disclaimer=DISCLAIMER
    )
