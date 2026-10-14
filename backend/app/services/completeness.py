from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RequirementView:
    id: int
    code: str
    title: str
    basis: str  # mandatory | recommended | ambiguous
    not_applicable: bool = False
    required_supporting_doc_name: str | None = None


@dataclass
class MappingView:
    requirement_id: int
    status: str  # suggested | confirmed | corrected | rejected
    weakness: str | None = None
    verified_citation_count: int = 0


@dataclass
class SupportingDocView:
    expected_name: str
    present: bool
    required: bool = False


def _supporting_ok(req: RequirementView, docs: list[SupportingDocView]) -> bool:
    if not req.required_supporting_doc_name:
        return True
    wanted = req.required_supporting_doc_name.strip().casefold()
    for doc in docs:
        if doc.expected_name.strip().casefold() == wanted:
            return doc.present
    return False


def _satisfied(req: RequirementView, mapping: MappingView | None, docs) -> bool:
    if mapping is None or mapping.status not in ("confirmed", "corrected"):
        return False
    if mapping.verified_citation_count < 1:
        return False
    return _supporting_ok(req, docs)


def _bucket(reqs, mappings, docs, basis: str) -> dict:
    total = satisfied = open_ = unsatisfied = na = 0
    open_items: list[dict] = []
    unsatisfied_items: list[dict] = []
    for req in reqs:
        if req.basis != basis:
            continue
        if req.not_applicable:
            na += 1
            continue
        total += 1
        mapping = mappings.get(req.id)
        if _satisfied(req, mapping, docs):
            satisfied += 1
        elif mapping is not None and mapping.status == "suggested":
            open_ += 1
            open_items.append({"code": req.code, "title": req.title, "weakness": mapping.weakness})
        else:
            unsatisfied += 1
            reason = "rejected by reviewer" if mapping and mapping.status == "rejected" else "no supporting evidence mapped"
            unsatisfied_items.append({"code": req.code, "title": req.title, "reason": reason})
    percentage = round(100.0 * satisfied / total, 1) if total else None
    return {
        "total": total,
        "satisfied": satisfied,
        "open": open_,
        "unsatisfied": unsatisfied,
        "not_applicable": na,
        "percentage": percentage,
        "open_items": open_items,
        "unsatisfied_items": unsatisfied_items,
    }


def compute_completion(
    requirements: list[RequirementView],
    mappings: dict[int, MappingView],
    supporting_docs: list[SupportingDocView],
) -> dict:
    """Deterministic completeness computation. Never an eligibility verdict."""
    weak = sum(1 for m in mappings.values() if m.weakness in ("weak", "ambiguous"))
    unverified = sum(
        1
        for m in mappings.values()
        if m.status in ("confirmed", "corrected") and m.verified_citation_count < 1
    )
    return {
        "disclaimer": (
            "This is an AI-assisted completeness review only. It is not an "
            "authoritative funding-eligibility or legal determination."
        ),
        "mandatory": _bucket(requirements, mappings, supporting_docs, "mandatory"),
        "recommended": _bucket(requirements, mappings, supporting_docs, "recommended"),
        "ambiguous_basis_count": sum(1 for r in requirements if r.basis == "ambiguous"),
        "attention": {
            "weak_or_ambiguous_mappings": weak,
            "confirmed_without_verified_citation": unverified,
        },
    }
