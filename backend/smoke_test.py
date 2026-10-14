"""Live smoke test against a running uvicorn instance.

Usage: set SMOKE_BASE (default http://127.0.0.1:8001) then run.
"""
import os
import sys

import httpx

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:8001")

GUIDELINE = """# Small Grants Program

## Eligibility

Applicants must be registered charities with annual income below 2 million EUR.

## Submission requirements

A completed budget table is required.

Applicants should attach a logic model (recommended).
"""

APPLICATION = """# Our Project

We are a registered charity operating since 2005.

# Budget

Total cost: 30,000 EUR.
"""


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=30)

    health = c.get("/api/health").json()
    assert health["status"] == "ok", health
    print("health:", health)

    g = c.post("/api/documents/paste", json={"kind": "guideline", "title": "Smoke Grant", "text": GUIDELINE}).json()
    a = c.post("/api/documents/paste", json={"kind": "application", "title": "Smoke App", "text": APPLICATION}).json()
    print(f"docs created: guideline v{g['versions'][-1]['version_no']} ({g['versions'][-1]['block_count']} blocks), "
          f"application v{a['versions'][-1]['version_no']}")

    assessment = c.post("/api/assessments", json={
        "guideline_version_id": g["versions"][-1]["id"],
        "application_version_id": a["versions"][-1]["id"],
        "run_llm": False,
        "supporting_docs": [{"expected_name": "Audit report", "required": True}],
    }).json()
    aid = assessment["id"]
    print("assessment:", assessment)

    # manual requirement + mapping
    blocks = c.get(f"/api/versions/{a['versions'][-1]['id']}/blocks").json()
    block = next(b for b in blocks if "registered charity" in b["text"])
    req = c.post(f"/api/assessments/{aid}/requirements", json={
        "code": "R1", "title": "Charity status",
        "requirement_text": "Applicants must be registered charities.",
        "basis": "mandatory", "required_supporting_doc_name": "Audit report",
    }).json()
    mapping = c.post(f"/api/requirements/{req['id']}/mappings", json={
        "quote": "registered charity", "block_id": block["id"], "status": "confirmed",
    }).json()
    assert mapping["verified"], mapping

    data = c.get(f"/api/assessments/{aid}").json()
    comp = data["completion"]["mandatory"]
    print("completion (audit report missing -> not satisfied):", comp)
    assert comp["satisfied"] == 0 and comp["unsatisfied"] == 1

    doc_id = data["supporting_docs"][0]["id"]
    c.patch(f"/api/supporting-docs/{doc_id}", json={"present": True})
    data = c.get(f"/api/assessments/{aid}").json()
    assert data["completion"]["mandatory"]["satisfied"] == 1
    print("after marking audit report present:", data["completion"]["mandatory"]["percentage"], "%")

    summary = c.get(f"/api/assessments/{aid}/summary").json()
    assert "Completeness Review Summary" in summary["markdown"]
    assert "not an authoritative funding-eligibility" in summary["markdown"]
    print("summary OK, length:", len(summary["markdown"]))

    # staleness
    c.post(f"/api/documents/{g['id']}/versions/paste", json={"text": GUIDELINE + "\n\nNew section.\n"})
    data = c.get(f"/api/assessments/{aid}").json()
    assert data["staleness"]["stale"] is True
    print("staleness OK:", data["staleness"]["reasons"])

    print("SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
