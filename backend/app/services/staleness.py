from __future__ import annotations

from app.models import Assessment, Document


def compute_staleness(
    assessment: Assessment,
    guideline_doc: Document,
    application_doc: Document,
) -> dict:
    """An assessment is stale when a newer version of either source document exists."""
    g_current = guideline_doc.active_version
    a_current = application_doc.active_version
    guideline_stale = g_current is not None and g_current.id != assessment.guideline_version_id
    application_stale = a_current is not None and a_current.id != assessment.application_version_id
    reasons: list[str] = []
    if guideline_stale:
        reasons.append(
            f"Grant guideline has a newer version (v{g_current.version_no}); "
            f"this assessment used v{assessment.guideline_version.version_no}."
        )
    if application_stale:
        reasons.append(
            f"Draft application has a newer version (v{a_current.version_no}); "
            f"this assessment used v{assessment.application_version.version_no}."
        )
    return {
        "stale": guideline_stale or application_stale,
        "guideline_stale": guideline_stale,
        "application_stale": application_stale,
        "reasons": reasons,
    }
