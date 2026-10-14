"""Idempotent seeding of reviewer-ready sample data.

Two sample sets are loaded into the database on first startup:

- **Sample A (worked example)** — the Green Horizon guideline, the
  ClosedLoop-Dye draft, supporting-document metadata, an assessment that has
  been through the real AI pipeline *and* a scripted human-review pass
  (confirm / correct / reject mappings, claim accept/dispute, question
  answered) so reviewers immediately see every workflow state.
- **Sample B (test-it-yourself)** — the same guideline with an intentionally
  weaker draft (AquaBright) and its supporting-document metadata, plus a
  manual-mode assessment. Reviewers click "Re-run against latest versions"
  to run a fresh AI review against the deployed instance.

Seeding is idempotent: a marker document title is checked before anything is
created, so re-boots never duplicate data. Disable with SEED_ON_STARTUP=false
or run manually with `python -m app.seed`.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.db import SessionLocal
from app.models import (
    Assessment,
    AuditLog,
    Document,
    MappingCitation,
    SupportingDoc,
    utcnow,
)
from app.services.documents import create_document_from_paste
from app.services.pipeline import PIPELINE_VERSION
from app.services.quotes import quote_in_text

logger = logging.getLogger(__name__)

SEED_VERSION = "1"

SAMPLE_A_GUIDELINE_TITLE = "[Sample A] Green Horizon guideline GH-2026-02"
SAMPLE_A_APPLICATION_TITLE = "[Sample A] ClosedLoop-Dye draft (VerdeTextile Oy)"
SAMPLE_B_APPLICATION_TITLE = "[Sample B] AquaBright draft (weaker — for testing)"

_SAMPLE_A_GUIDELINE_FILE = "grant-guideline.md"
_SAMPLE_A_APPLICATION_FILE = "draft-application.md"
_SAMPLE_A_SUPPORTING_FILE = "supporting-documents.json"
_SAMPLE_B_APPLICATION_FILE = Path("second") / "draft-application-weak.md"
_SAMPLE_B_SUPPORTING_FILE = Path("second") / "supporting-documents-weak.json"


# ---------- helpers ----------

def _samples_dir() -> Path:
    return Path(get_settings().samples_dir)


def _read_sample(relative: str | Path) -> str:
    path = _samples_dir() / relative
    if not path.is_file():
        raise FileNotFoundError(f"Sample file not found: {path}")
    return path.read_text(encoding="utf-8")


def _load_supporting_docs(relative: str | Path) -> list[dict]:
    payload = json.loads(_read_sample(relative))
    if not isinstance(payload, list):
        raise ValueError(f"Supporting-doc sample must be a JSON array: {relative}")
    return payload


def _audit(db: Session, entity: str, entity_id: int, action: str, before: dict, after: dict) -> None:
    db.add(AuditLog(entity=entity, entity_id=entity_id, action=action, before=before, after=after))


def _sentence_candidates(text: str, max_len: int = 240) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if 40 <= len(p.strip()) <= max_len]


def _pick_correction_quote(block_text: str, requirement_text: str) -> Optional[str]:
    """Pick a real (verbatim) sentence from the block that best matches the requirement."""
    candidates = _sentence_candidates(block_text)
    if not candidates:
        stripped = block_text.strip()
        return stripped[:240] or None
    keywords = [w.lower() for w in re.findall(r"[A-Za-z]{5,}", requirement_text)][:12]
    best, best_score = candidates[0], -1
    for candidate in candidates:
        lower = candidate.lower()
        score = sum(1 for kw in keywords if kw in lower)
        if score > best_score:
            best, best_score = candidate, score
    return best


# ---------- review pass ----------

def apply_sample_review_pass(db: Session, assessment: Assessment) -> dict:
    """Scripted human-review pass over the AI output of the Sample A assessment.

    Demonstrates every review state reviewers should see: confirmed mappings,
    a corrected mapping (real quote substituted for the AI citation), rejected
    mappings, claim accept/dispute, and an answered clarification question.
    Each action writes an audit-log row, exactly like the review endpoints.
    """
    assessment = db.get(
        Assessment,
        assessment.id,
        options=list(_assessment_load_options()),
    )
    stats = {"confirmed": 0, "corrected": 0, "rejected": 0, "claims": 0, "questions": 0}
    if assessment is None:
        return stats

    mappings = [
        (req, req.mapping)
        for req in assessment.requirements
        if req.mapping is not None
    ]
    # Snapshot the open queue size before any review action so the confirm cap
    # can guarantee at least one mapping stays "suggested" for the demo.
    open_pool_size = sum(1 for _, m in mappings if m.status == "suggested")

    # 1. Reject mappings with no usable evidence: nothing verified and the
    #    pipeline flagged the requirement missing/unaddressed. Mappings with
    #    verified citations are left for confirm/correct even if the model
    #    was unsure (reviewer trusts the verbatim check over the model).
    for req, mapping in mappings:
        has_verified = any(c.verified for c in mapping.citations)
        if has_verified:
            continue
        if mapping.weakness in ("missing", None):
            before = {"status": mapping.status}
            mapping.status = "rejected"
            mapping.user_note = (
                "Reviewer: no supporting evidence found in the draft — "
                "applicant must address this before submission."
            )
            mapping.reviewed_at = utcnow()
            _audit(db, "mapping", mapping.id, "reject", before, {"status": mapping.status})
            stats["rejected"] += 1

    # 2. Correct one mapping whose citations the AI hallucinated (quotes not
    #    verbatim in the cited block) by substituting a real passage. Mappings
    #    that already carry any verified citation are left alone — stripping
    #    good evidence to demonstrate a "correction" would be dishonest.
    for req, mapping in mappings:
        if mapping.status != "suggested":
            continue
        if not mapping.citations or any(c.verified for c in mapping.citations):
            continue
        citation = next((c for c in mapping.citations if c.block is not None), None)
        if citation is None:
            continue
        quote = _pick_correction_quote(citation.block.text, req.requirement_text)
        if not quote:
            continue
        block_id = citation.block.id
        before = {"status": mapping.status, "quote": citation.quote}
        for old in list(mapping.citations):
            db.delete(old)
        db.flush()
        db.add(
            MappingCitation(
                mapping_id=mapping.id,
                block_id=block_id,
                quote=quote,
                verified=True,
            )
        )
        mapping.status = "corrected"
        mapping.user_note = (
            "Reviewer: replaced the AI citation with the actual passage in "
            "the draft that addresses this requirement."
        )
        mapping.reviewed_at = utcnow()
        _audit(db, "mapping", mapping.id, "correct", before, {"status": mapping.status})
        stats["corrected"] += 1
        break

    # 3. Confirm up to 4 suggested mappings backed by verified citations and
    #    not flagged weak/ambiguous by the pipeline. Always leave at least one
    #    mapping suggested (when the pool allows) so the worked example shows
    #    the open review-queue state too.
    confirm_cap = min(4, max(1, open_pool_size - 1))
    for req, mapping in mappings:
        if stats["confirmed"] >= confirm_cap:
            break
        if mapping.status != "suggested":
            continue
        has_verified = any(c.verified for c in mapping.citations)
        if not has_verified or mapping.weakness in ("weak", "ambiguous"):
            continue
        before = {"status": mapping.status}
        mapping.status = "confirmed"
        mapping.user_note = "Reviewer: citation verified against the draft — mapping accepted."
        mapping.reviewed_at = utcnow()
        _audit(db, "mapping", mapping.id, "confirm", before, {"status": mapping.status})
        stats["confirmed"] += 1

    # 4. Leave the remaining suggested mappings untouched (fresh review queue).

    # 5. Claims: accept a supported/partial one, dispute an unsupported one.
    accepted = False
    disputed = False
    for claim in assessment.claims:
        if not accepted and claim.support in ("supported", "partial"):
            before = {"user_accepted": claim.user_accepted}
            claim.user_accepted = True
            claim.note = "Reviewer: support judged adequate against supplied evidence metadata."
            _audit(db, "claim", claim.id, "review", before, {"user_accepted": True})
            accepted = True
            stats["claims"] += 1
        elif not disputed and claim.support == "unsupported":
            before = {"user_accepted": claim.user_accepted}
            claim.user_accepted = False
            claim.note = (
                "Reviewer: not supported by supplied evidence — applicant "
                "asked to provide documentation."
            )
            _audit(db, "claim", claim.id, "review", before, {"user_accepted": False})
            disputed = True
            stats["claims"] += 1
        if accepted and disputed:
            break

    # 6. Answer the highest-priority open clarification question.
    questions = sorted(assessment.questions, key=lambda q: (q.priority, q.id))
    for question in questions:
        if question.answer:
            continue
        question.answer = (
            "Reviewer (sample walkthrough): applicant confirmed this item is "
            "in preparation and will be uploaded before the submission "
            "deadline; tracked as an open completeness gap."
        )
        question.answered_at = utcnow()
        _audit(
            db,
            "question",
            question.id,
            "answer",
            {"answer": None},
            {"answer": question.answer},
        )
        stats["questions"] += 1
        break

    # 7. If the AI produced no clarification questions (e.g. the questions
    #    stage failed schema validation), add clearly-labelled reviewer-authored
    #    follow-ups for the biggest open gaps so the sample still demonstrates
    #    the clarification workflow.
    if not assessment.questions:
        from app.models import ClarificationQuestion

        added = 0
        for req, mapping in mappings:
            if mapping.status != "rejected" or added >= 3:
                continue
            db.add(
                ClarificationQuestion(
                    assessment_id=assessment.id,
                    requirement_id=req.id,
                    question=(
                        f'Reviewer follow-up: how does the draft satisfy "{req.title}"? '
                        "No supporting passage was found in the application."
                    ),
                    priority=1,
                )
            )
            added += 1
        for doc in assessment.supporting_docs:
            if not (doc.required and not doc.present) or added >= 5:
                continue
            db.add(
                ClarificationQuestion(
                    assessment_id=assessment.id,
                    question=(
                        f'Reviewer follow-up: the required supporting document '
                        f'"{doc.expected_name}" is marked missing — please confirm '
                        "it will be uploaded before submission."
                    ),
                    priority=1,
                )
            )
            added += 1
        if added:
            _audit(
                db,
                "assessment",
                assessment.id,
                "review",
                {"questions": 0},
                {"reviewer_questions_added": added},
            )
            stats["questions"] += added

    db.commit()
    return stats


def _assessment_load_options():
    from app.models import (
        Assessment,
        Claim,
        Mapping,
        MappingCitation,
        Requirement,
    )

    return [
        joinedload(Assessment.requirements)
        .joinedload(Requirement.mapping)
        .joinedload(Mapping.citations)
        .joinedload(MappingCitation.block),
        joinedload(Assessment.requirements).joinedload(Requirement.basis_block),
        joinedload(Assessment.claims).joinedload(Claim.block),
        joinedload(Assessment.questions),
        joinedload(Assessment.supporting_docs),
    ]


# ---------- seeding ----------

def _already_seeded(db: Session) -> bool:
    return (
        db.scalar(
            select(Document.id).where(Document.title == SAMPLE_A_GUIDELINE_TITLE).limit(1)
        )
        is not None
    )


def _create_assessment(
    db: Session,
    *,
    guideline_version_id: int,
    application_version_id: int,
    guideline_hash: str,
    application_hash: str,
    supporting_docs: list[dict],
    run_ai: bool,
    model: Optional[str],
    progress: dict,
) -> Assessment:
    assessment = Assessment(
        guideline_version_id=guideline_version_id,
        application_version_id=application_version_id,
        guideline_hash=guideline_hash,
        application_hash=application_hash,
        pipeline_version=PIPELINE_VERSION,
        llm_model=model if run_ai else None,
        status="running" if run_ai else "review",
        progress=progress,
    )
    db.add(assessment)
    for doc_in in supporting_docs:
        db.add(
            SupportingDoc(
                assessment=assessment,
                expected_name=str(doc_in["expected_name"]).strip(),
                required=bool(doc_in.get("required", False)),
                present=bool(doc_in.get("present", False)),
                metadata_json=doc_in.get("metadata") or {},
                source="user",
            )
        )
    db.commit()
    return assessment


def seed_demo_data(
    *,
    run_pipeline_fn: Optional[Callable[[int], None]] = None,
    apply_review: bool = True,
    run_ai: Optional[bool] = None,
) -> dict:
    """Create the two sample sets. Safe to call repeatedly (idempotent).

    run_ai defaults to whether an LLM key is configured; pass True/False to
    override (used by tests). run_pipeline_fn is injectable for testing.
    """
    from app.services.llm import llm_available

    settings = get_settings()
    run_pipeline_fn = run_pipeline_fn or _default_pipeline_runner

    db = SessionLocal()
    try:
        if _already_seeded(db):
            logger.info("seed: sample data already present — skipping")
            return {"status": "skipped", "reason": "already seeded", "seed_version": SEED_VERSION}

        guideline = create_document_from_paste(
            db,
            kind="guideline",
            title=SAMPLE_A_GUIDELINE_TITLE,
            text=_read_sample(_SAMPLE_A_GUIDELINE_FILE),
        )
        app_a = create_document_from_paste(
            db,
            kind="application",
            title=SAMPLE_A_APPLICATION_TITLE,
            text=_read_sample(_SAMPLE_A_APPLICATION_FILE),
        )
        app_b = create_document_from_paste(
            db,
            kind="application",
            title=SAMPLE_B_APPLICATION_TITLE,
            text=_read_sample(_SAMPLE_B_APPLICATION_FILE),
        )

        g_version = guideline.active_version
        a_version = app_a.active_version
        b_version = app_b.active_version
        assert g_version and a_version and b_version

        result: dict = {
            "status": "seeded",
            "seed_version": SEED_VERSION,
            "documents": [guideline.id, app_a.id, app_b.id],
        }

        # --- Sample A: worked example (real AI run + scripted review) ---
        if run_ai is None:
            run_ai = llm_available()
        assessment_a = _create_assessment(
            db,
            guideline_version_id=g_version.id,
            application_version_id=a_version.id,
            guideline_hash=g_version.content_hash,
            application_hash=a_version.content_hash,
            supporting_docs=_load_supporting_docs(_SAMPLE_A_SUPPORTING_FILE),
            run_ai=run_ai,
            model=settings.llm_model if run_ai else None,
            progress=(
                {"stage": "queued", "message": "Queued for AI review (sample seed)"}
                if run_ai
                else {
                    "stage": "manual",
                    "message": (
                        "Sample seeded without an LLM key — set LLM_API_KEY and "
                        "use “Re-run against latest versions” to run the AI review."
                    ),
                }
            ),
        )
        result["sample_a_assessment_id"] = assessment_a.id

        if run_ai:
            logger.info("seed: running AI pipeline for Sample A (assessment %s)", assessment_a.id)
            run_pipeline_fn(assessment_a.id)
            db.expire_all()
            assessment_a = db.get(Assessment, assessment_a.id)
            if assessment_a is not None and assessment_a.status == "review" and apply_review:
                stats = apply_sample_review_pass(db, assessment_a)
                result["review_pass"] = stats
                logger.info("seed: review pass applied — %s", stats)
            elif assessment_a is not None and assessment_a.status != "review":
                result["review_pass"] = {
                    "skipped": True,
                    "reason": f"pipeline status={assessment_a.status}",
                }
                logger.warning(
                    "seed: pipeline did not reach review status (%s) — review pass skipped",
                    assessment_a.status,
                )
        else:
            logger.warning(
                "seed: LLM_API_KEY not set — Sample A assessment created in manual "
                "mode without AI results"
            )

        # --- Sample B: reviewer test set (manual until the reviewer re-runs) ---
        assessment_b = _create_assessment(
            db,
            guideline_version_id=g_version.id,
            application_version_id=b_version.id,
            guideline_hash=g_version.content_hash,
            application_hash=b_version.content_hash,
            supporting_docs=_load_supporting_docs(_SAMPLE_B_SUPPORTING_FILE),
            run_ai=False,
            model=None,
            progress={
                "stage": "manual",
                "message": (
                    "Sample B — click “Re-run against latest versions” to run a "
                    "fresh AI review of this weaker draft."
                ),
            },
        )
        result["sample_b_assessment_id"] = assessment_b.id

        _audit(
            db,
            "seed",
            0,
            "seed",
            None,
            {
                "seed_version": SEED_VERSION,
                "sample_a_assessment_id": assessment_a.id,
                "sample_b_assessment_id": assessment_b.id,
                "ai_pipeline_run": run_ai,
            },
        )
        db.commit()
        logger.info("seed: complete — %s", result)
        return result
    finally:
        db.close()


def _default_pipeline_runner(assessment_id: int) -> None:
    from app.services.pipeline import run_pipeline

    run_pipeline(assessment_id)


if __name__ == "__main__":  # pragma: no cover — manual CLI entry
    logging.basicConfig(level="INFO")
    print(json.dumps(seed_demo_data(), indent=2, default=str))
