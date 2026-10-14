from __future__ import annotations

import datetime as dt
import logging
from typing import Literal, Optional

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    Assessment,
    ClarificationQuestion,
    Claim,
    Mapping,
    MappingCitation,
    Requirement,
    SupportingDoc,
    TextBlock,
    utcnow,
)
from app.services import retrieval
from app.services.llm import (
    LLMNotConfiguredError,
    LLMOutputError,
    complete_json,
    llm_available,
)
from app.services.quotes import find_block_for_quote

logger = logging.getLogger(__name__)

PIPELINE_VERSION = "1"

STAGES = [
    "extract_requirements",
    "map_requirements",
    "gap_analysis",
    "claims",
    "questions",
    "finalize",
]

CHUNK_CHAR_LIMIT = 14000


# ---------- LLM output schemas ----------

class ExtractedRequirement(BaseModel):
    code: str
    title: str
    requirement_text: str
    basis: Literal["mandatory", "recommended", "ambiguous"]
    basis_quote: str = ""
    category: Optional[str] = None
    required_supporting_doc_name: Optional[str] = None


class ExtractionResult(BaseModel):
    requirements: list[ExtractedRequirement] = Field(default_factory=list)


class MappingResult(BaseModel):
    mapped: bool
    quotes: list[str] = Field(default_factory=list)
    rationale: str = ""
    confidence: float = 0.5
    weakness: Literal["none", "weak", "ambiguous"] = "none"


class ExtractedClaim(BaseModel):
    claim_text: str
    quote: str
    support: Literal["supported", "partial", "unsupported"] = "unsupported"
    supporting_refs: list[str] = Field(default_factory=list)


class ClaimsResult(BaseModel):
    claims: list[ExtractedClaim] = Field(default_factory=list)


class ExtractedQuestion(BaseModel):
    requirement_code: Optional[str] = None
    question: str
    priority: int = 2


class QuestionsResult(BaseModel):
    questions: list[ExtractedQuestion] = Field(default_factory=list)


# ---------- helpers ----------

def _chunk_blocks(blocks: list[TextBlock], limit: int = CHUNK_CHAR_LIMIT) -> list[list[TextBlock]]:
    chunks: list[list[TextBlock]] = []
    current: list[TextBlock] = []
    size = 0
    for block in blocks:
        block_len = len(block.text) + 1
        if current and size + block_len > limit:
            chunks.append(current)
            current, size = [], 0
        current.append(block)
        size += block_len
    if current:
        chunks.append(current)
    return chunks


def _format_blocks(blocks: list[TextBlock]) -> str:
    lines = []
    for b in blocks:
        page = f" p.{b.page}" if b.page else ""
        section = f" [{b.section_path}]" if b.section_path else ""
        lines.append(f"<block id={b.id}{page}{section}>\n{b.text}\n</block>")
    return "\n\n".join(lines)


def _set_progress(assessment: Assessment, stage: str, message: str, **extra) -> None:
    progress = dict(assessment.progress or {})
    progress.update({"stage": stage, "message": message, "updated_at": utcnow().isoformat(), **extra})
    assessment.progress = progress


def _doc_meta_line(doc: SupportingDoc) -> str:
    meta = doc.metadata_json or {}
    return f"- {doc.expected_name} (required={doc.required}, present={doc.present}, metadata={meta})"


# ---------- stages ----------

def _stage_extract_requirements(session, assessment: Assessment) -> None:
    version = assessment.guideline_version
    blocks = list(version.blocks)
    if not blocks:
        return
    _set_progress(assessment, "extract_requirements", "Extracting requirements from guideline")
    session.commit()

    existing_codes = {r.code for r in assessment.requirements}
    seen = set(existing_codes)
    failed_chunks = 0
    for chunk in _chunk_blocks(blocks):
        user = (
            "Extract every submission requirement from this grant guideline "
            "excerpt (blocks are tagged with ids).\n\n"
            "Rules:\n"
            "- Extract AT MOST 12 requirements per excerpt; prioritize the most "
            "significant ones.\n"
            "- Distinguish MANDATORY requirements (must, is required, mandatory, "
            "applicants shall, deadline, limit) from RECOMMENDED ones (should, "
            "encouraged, optional, preference). Use \"ambiguous\" when the "
            "guideline does not clearly signal either.\n"
            "- basis_quote must be copied VERBATIM from the guideline text, "
            "max 200 characters (a verbatim prefix is fine).\n"
            "- Use stable short codes like R1, R2, ... (globally unique).\n"
            "- If a requirement names a specific attachment or supporting "
            "document, set required_supporting_doc_name to its exact name.\n\n"
            f"{_format_blocks(chunk)}"
        )
        try:
            result = complete_json(
                system="You extract grant submission requirements as structured JSON.",
                user=user,
                schema=ExtractionResult,
                max_tokens=16384,
                stage="extract_requirements",
                assessment_id=assessment.id,
            )
        except (LLMOutputError, LLMNotConfiguredError) as exc:
            logger.warning("requirement extraction chunk failed: %s", exc)
            failed_chunks += 1
            continue

        for item in result.requirements:
            code = item.code.strip()
            if not code or code in seen:
                continue
            seen.add(code)
            basis = item.basis
            basis_block = None
            basis_quote = item.basis_quote.strip() or None
            basis_verified = False
            if basis_quote:
                basis_block = find_block_for_quote(basis_quote, blocks)
                if basis_block is not None:
                    basis_verified = True
                else:
                    # hallucinated quote: do not trust the mandatory/recommended call
                    basis = "ambiguous"
            elif basis != "ambiguous":
                basis = "ambiguous"
            session.add(
                Requirement(
                    assessment_id=assessment.id,
                    code=code[:50],
                    title=item.title.strip()[:300],
                    requirement_text=item.requirement_text.strip(),
                    basis=basis,
                    basis_quote=basis_quote,
                    basis_block_id=basis_block.id if basis_block else None,
                    basis_verified=basis_verified,
                    category=(item.category or None),
                    required_supporting_doc_name=item.required_supporting_doc_name or None,
                    source="llm",
                )
            )
    if failed_chunks:
        _set_progress(
            assessment, "extract_requirements",
            f"{failed_chunks} extraction chunk(s) failed", failed_chunks=failed_chunks,
        )
    session.commit()
    # the requirements collection was (possibly) loaded before extraction ran;
    # expire it so later stages see the newly added rows.
    session.expire(assessment, ["requirements"])


def _stage_map_requirements(session, assessment: Assessment) -> None:
    app_blocks = list(assessment.application_version.blocks)
    if not app_blocks:
        return
    requirements = list(
        session.scalars(
            select(Requirement).where(Requirement.assessment_id == assessment.id)
        ).all()
    )
    already_mapped = set(
        session.scalars(
            select(Mapping.requirement_id).where(
                Mapping.requirement_id.in_([r.id for r in requirements])
            )
        ).all()
    ) if requirements else set()
    todo = [r for r in requirements if r.id not in already_mapped]
    total = len(todo)
    for i, req in enumerate(todo, start=1):
        _set_progress(
            assessment, "map_requirements",
            f"Mapping requirement {req.code} ({i}/{total})", done=i, total=total,
        )
        session.commit()

        candidates = retrieval.score_blocks(req.requirement_text, app_blocks, top_k=8)
        cand_text = _format_blocks([b for _, b in candidates]) or "(no candidate passages found)"
        user = (
            "Determine whether the draft application below satisfies this "
            "requirement. Quote VERBATIM supporting passages from the "
            "application blocks.\n\n"
            f"REQUIREMENT {req.code}: {req.title}\n{req.requirement_text}\n\n"
            f"CANDIDATE PASSAGES:\n{cand_text}\n\n"
            "Set mapped=false and quotes=[] when no passage addresses the "
            "requirement at all. Use weakness=\"weak\" when a passage only "
            "vaguely addresses it, and weakness=\"ambiguous\" when it is "
            "unclear whether the passage satisfies the requirement. "
            "confidence is 0..1."
        )
        mapping = Mapping(
            requirement_id=req.id, status="suggested", source="llm", confidence=0.0,
        )
        try:
            result = complete_json(
                system="You map grant requirements to application evidence as JSON.",
                user=user,
                schema=MappingResult,
                stage="map_requirements",
                assessment_id=assessment.id,
            )
        except (LLMOutputError, LLMNotConfiguredError) as exc:
            mapping.rationale = f"Mapping failed: {exc}"
            mapping.weakness = "missing"
            mapping.confidence = 0.0
            session.add(mapping)
            continue

        mapping.rationale = result.rationale
        mapping.confidence = max(0.0, min(1.0, result.confidence))
        mapping.weakness = None if result.weakness == "none" else result.weakness
        session.add(mapping)
        session.flush()

        verified_any = False
        for quote in result.quotes[:5]:
            block = find_block_for_quote(quote, app_blocks)
            verified = block is not None
            if verified:
                verified_any = True
            else:
                # hallucinated quote: keep it visible but unverified
                block = candidates[0][1] if candidates else app_blocks[0]
            session.add(
                MappingCitation(
                    mapping_id=mapping.id,
                    block_id=block.id,
                    quote=quote,
                    verified=verified,
                )
            )
        if result.mapped and not result.quotes:
            mapping.weakness = "missing"
        elif not result.mapped:
            mapping.weakness = "missing"
        elif not verified_any:
            mapping.weakness = mapping.weakness or "weak"
    session.commit()


def _stage_gap_analysis(session, assessment: Assessment) -> None:
    for req in assessment.requirements:
        mapping = req.mapping
        if mapping is None:
            continue
        if not any(c.verified for c in mapping.citations) and mapping.weakness is None:
            mapping.weakness = "missing"
    session.commit()


def _stage_claims(session, assessment: Assessment) -> None:
    # Idempotent re-runs (resume after a server restart): regenerate from
    # scratch so partial rows from an interrupted run are never duplicated.
    session.query(Claim).filter(Claim.assessment_id == assessment.id).delete(
        synchronize_session=False
    )
    blocks = list(assessment.application_version.blocks)
    if not blocks:
        return
    docs = list(assessment.supporting_docs)
    evidence_text = "\n".join(_doc_meta_line(d) for d in docs) or "(no supporting-document metadata supplied)"
    # Larger chunks are fine now that extraction/claims outputs are capped and
    # max_tokens is generous; caps below keep model JSON arrays small.
    failed_chunks = 0
    for chunk in _chunk_blocks(blocks, limit=10000):
        user = (
            "Identify factual claims in this draft-application excerpt that "
            "would need evidence in a funding application (results, metrics, "
            "partnerships, credentials, outcomes). Extract AT MOST 10 claims "
            "per excerpt; keep each claim_text under 120 characters and avoid "
            "double quotes inside strings. For each claim, judge "
            "whether the SUPPLIED EVIDENCE (supporting-document metadata only) "
            "supports it. Absence of evidence metadata means support cannot be "
            "confirmed — mark unsupported rather than assuming support.\n\n"
            f"SUPPLIED EVIDENCE:\n{evidence_text}\n\n"
            f"APPLICATION EXCERPT:\n{_format_blocks(chunk)}"
        )
        try:
            result = complete_json(
                system="You extract evidence-dependent claims as structured JSON.",
                user=user,
                schema=ClaimsResult,
                max_tokens=16384,
                stage="claims",
                assessment_id=assessment.id,
            )
        except (LLMOutputError, LLMNotConfiguredError) as exc:
            logger.warning("claims chunk failed: %s", exc)
            failed_chunks += 1
            continue
        for item in result.claims:
            block = find_block_for_quote(item.quote, chunk) or find_block_for_quote(item.quote, blocks)
            session.add(
                Claim(
                    assessment_id=assessment.id,
                    block_id=block.id if block else None,
                    claim_text=item.claim_text.strip(),
                    support=item.support,
                    supporting_refs=item.supporting_refs[:],
                    source="llm",
                )
            )
    if failed_chunks:
        _set_progress(
            assessment, "claims",
            f"{failed_chunks} claims chunk(s) failed", claims_failed_chunks=failed_chunks,
        )
    session.commit()


def _stage_questions(session, assessment: Assessment) -> None:
    # Idempotent re-runs: regenerate questions from scratch (the assessment is
    # still "running", so no user answers exist yet).
    session.query(ClarificationQuestion).filter(
        ClarificationQuestion.assessment_id == assessment.id
    ).delete(synchronize_session=False)
    gaps = []
    for req in assessment.requirements:
        mapping = req.mapping
        if req.not_applicable:
            continue
        if mapping is None or mapping.weakness in ("missing", "weak", "ambiguous"):
            gaps.append(f"- {req.code} ({req.basis}): {req.title} — weakness={getattr(mapping, 'weakness', 'missing')}")
        if req.basis == "ambiguous":
            gaps.append(f"- {req.code}: unclear whether mandatory or recommended — ask for clarification")
    docs_missing = [d for d in assessment.supporting_docs if d.required and not d.present]
    for d in docs_missing:
        gaps.append(f"- missing required supporting document: {d.expected_name}")
    unsupported = [c for c in assessment.claims if c.support == "unsupported"]
    for c in unsupported[:10]:
        gaps.append(f"- unsupported claim: {c.claim_text[:120]}")
    if not gaps:
        session.commit()
        return

    user = (
        "Generate concise clarification questions a grant officer would ask "
        "the applicant to resolve these gaps. Max 20 questions. Set "
        "requirement_code when a question maps to a specific requirement code.\n\n"
        + "\n".join(gaps[:40])
    )
    try:
        result = complete_json(
            system="You generate clarification questions as structured JSON.",
            user=user,
            schema=QuestionsResult,
            stage="questions",
            assessment_id=assessment.id,
        )
    except (LLMOutputError, LLMNotConfiguredError) as exc:
        logger.warning("questions stage failed: %s", exc)
        session.commit()
        return

    code_to_id = {r.code: r.id for r in assessment.requirements}
    for item in result.questions[:20]:
        session.add(
            ClarificationQuestion(
                assessment_id=assessment.id,
                requirement_id=code_to_id.get(item.requirement_code) if item.requirement_code else None,
                question=item.question.strip(),
                priority=max(1, min(3, item.priority)),
            )
        )
    session.commit()


# ---------- orchestrator ----------

def _carry_forward_mappings(session, assessment: Assessment) -> None:
    """Copy confirmed/corrected mappings by requirement code from the prior
    assessment when the application version is unchanged."""
    prev_id = (assessment.progress or {}).get("carry_forward_from")
    if not prev_id:
        return
    prev = session.get(Assessment, prev_id)
    if prev is None or prev.application_version_id != assessment.application_version_id:
        return
    code_to_req = {r.code: r for r in assessment.requirements}
    if not code_to_req:
        return
    already_mapped = set(
        session.scalars(
            select(Mapping.requirement_id).where(
                Mapping.requirement_id.in_([r.id for r in code_to_req.values()])
            )
        ).all()
    )
    for req in prev.requirements:
        mapping = req.mapping
        if mapping is None or mapping.status not in ("confirmed", "corrected"):
            continue
        target = code_to_req.get(req.code)
        if target is None or target.id in already_mapped:
            continue
        new_mapping = Mapping(
            requirement_id=target.id,
            status=mapping.status,
            rationale=mapping.rationale,
            confidence=mapping.confidence,
            weakness=mapping.weakness,
            user_note=mapping.user_note,
            source=mapping.source,
            reviewed_at=mapping.reviewed_at,
        )
        session.add(new_mapping)
        session.flush()
        for citation in mapping.citations:
            session.add(
                MappingCitation(
                    mapping_id=new_mapping.id,
                    block_id=citation.block_id,
                    quote=citation.quote,
                    verified=citation.verified,
                )
            )
    session.commit()


def run_pipeline(assessment_id: int) -> None:
    session = SessionLocal()
    try:
        assessment = session.get(Assessment, assessment_id)
        if assessment is None:
            return
        if assessment.status != "running":
            session.commit()
            return
        if not llm_available():
            assessment.status = "error"
            _set_progress(
                assessment, "error",
                "LLM not configured. Set LLM_API_KEY in backend/.env, or use "
                "manual review (run_llm=false).",
            )
            session.commit()
            return
        try:
            if not assessment.requirements:
                _stage_extract_requirements(session, assessment)
            _carry_forward_mappings(session, assessment)
            _stage_map_requirements(session, assessment)
            _stage_gap_analysis(session, assessment)
            _stage_claims(session, assessment)
            _stage_questions(session, assessment)
            progress = assessment.progress or {}
            failed = int(progress.get("extraction_failed_chunks", 0) or 0) + int(
                progress.get("claims_failed_chunks", 0) or 0
            )
            message = "Complete — ready for review"
            if failed:
                message += f" ({failed} chunk(s) failed; results may be incomplete)"
            _set_progress(assessment, "finalize", message)
            assessment.status = "review"
            assessment.completed_at = utcnow()
        except LLMNotConfiguredError as exc:
            assessment.status = "error"
            _set_progress(assessment, "error", str(exc))
        except Exception as exc:  # noqa: BLE001 — surface any pipeline failure
            logger.exception("pipeline failed for assessment %s", assessment_id)
            assessment.status = "error"
            _set_progress(assessment, "error", f"Pipeline failed: {exc}")
        session.commit()
    finally:
        session.close()


def run_llm_pipeline(assessment_id: int) -> None:
    """Entry point used by FastAPI BackgroundTasks."""
    run_pipeline(assessment_id)
