from __future__ import annotations

import datetime as dt

from app.models import Assessment, utcnow
from app.services.completeness import (
    MappingView,
    RequirementView,
    SupportingDocView,
    compute_completion,
)


def build_completion(assessment: Assessment) -> dict:
    reqs = [
        RequirementView(
            id=r.id,
            code=r.code,
            title=r.title,
            basis=r.basis,
            not_applicable=r.not_applicable,
            required_supporting_doc_name=r.required_supporting_doc_name,
        )
        for r in assessment.requirements
    ]
    mappings = {
        r.id: MappingView(
            requirement_id=r.id,
            status=r.mapping.status,
            weakness=r.mapping.weakness,
            verified_citation_count=sum(1 for c in r.mapping.citations if c.verified),
        )
        for r in assessment.requirements
        if r.mapping is not None
    }
    docs = [
        SupportingDocView(expected_name=d.expected_name, present=d.present, required=d.required)
        for d in assessment.supporting_docs
    ]
    return compute_completion(reqs, mappings, docs)


DISCLAIMER = (
    "This is an AI-assisted completeness review only. It is not an "
    "authoritative funding-eligibility or legal determination."
)


def _pct(bucket: dict) -> str:
    return "n/a" if bucket["percentage"] is None else f"{bucket['percentage']}%"


def generate_summary_markdown(assessment: Assessment, completion: dict) -> str:
    now = utcnow().strftime("%Y-%m-%d %H:%M UTC")
    gv = assessment.guideline_version
    av = assessment.application_version
    g_title = assessment.guideline_version.document.title
    a_title = assessment.application_version.document.title

    lines: list[str] = [
        f"# Completeness Review Summary — Assessment #{assessment.id}",
        "",
        f"_Generated {now} · pipeline v{assessment.pipeline_version}"
        + (f" · model `{assessment.llm_model}`" if assessment.llm_model else " · manual")
        + "_",
        "",
        f"> **Disclaimer:** {DISCLAIMER}",
        "",
        "## Sources reviewed",
        "",
        f"- **Grant guideline:** {g_title} — version v{gv.version_no} (hash `{gv.content_hash[:12]}`)",
        f"- **Draft application:** {a_title} — version v{av.version_no} (hash `{av.content_hash[:12]}`)",
        "",
        "## Completion",
        "",
        "| Basis | Satisfied | Open | Unsatisfied | N/A | Completion |",
        "|---|---|---|---|---|---|",
    ]
    for label, key in (("Mandatory", "mandatory"), ("Recommended", "recommended")):
        b = completion[key]
        lines.append(
            f"| {label} | {b['satisfied']} | {b['open']} | {b['unsatisfied']} "
            f"| {b['not_applicable']} | {_pct(b)} |"
        )
    amb = completion["ambiguous_basis_count"]
    if amb:
        lines += ["", f"**{amb} requirement(s)** have an ambiguous mandatory/recommended basis and need human judgement."]
    att = completion["attention"]
    lines += [
        "",
        "## Needs attention",
        "",
        f"- Weak or ambiguous mappings: **{att['weak_or_ambiguous_mappings']}**",
        f"- Confirmed mappings without a verified citation: **{att['confirmed_without_verified_citation']}**",
    ]

    mand = completion["mandatory"]
    if mand["unsatisfied_items"]:
        lines += ["", "## Missing / unsatisfied mandatory requirements", ""]
        for item in mand["unsatisfied_items"]:
            lines.append(f"- **[{item['code']}]** {item['title']} — {item['reason']}")
    if mand["open_items"]:
        lines += ["", "## Mandatory requirements still open (awaiting review)", ""]
        for item in mand["open_items"]:
            w = f" ({item['weakness']})" if item.get("weakness") else ""
            lines.append(f"- **[{item['code']}]** {item['title']}{w}")

    unsupported = [c for c in assessment.claims if c.support == "unsupported"]
    if unsupported:
        lines += ["", "## Claims not supported by supplied evidence", ""]
        for c in unsupported:
            marker = " (reviewer disputes)" if c.user_accepted is False else ""
            lines.append(f"- {c.claim_text}{marker}")

    missing_docs = [d for d in assessment.supporting_docs if d.required and not d.present]
    if missing_docs:
        lines += ["", "## Missing required supporting documents", ""]
        for d in missing_docs:
            lines.append(f"- {d.expected_name}")

    open_questions = [q for q in assessment.questions if not q.answer]
    if open_questions:
        lines += ["", "## Open clarification questions", ""]
        for q in open_questions:
            lines.append(f"- ({q.priority}) {q.question}")
    answered = [q for q in assessment.questions if q.answer]
    if answered:
        lines += ["", "## Answered clarification questions", ""]
        for q in answered:
            lines.append(f"- {q.question}\n  - **Answer:** {q.answer}")

    lines += ["", "---", f"_{DISCLAIMER}_", ""]
    return "\n".join(lines)
