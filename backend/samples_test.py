"""End-to-end reviewer test driven by the sample inputs in ../samples.

Uploads the production-format grant guideline and draft application, creates
an assessment (running the AI pipeline when an LLM is configured), then
verifies requirements, citations, questions, claims, completion, human-review
actions, staleness and the reviewed summary.

Usage: set BASE (default http://127.0.0.1:8000) then run:
    python samples_test.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE = os.environ.get("BASE", "http://127.0.0.1:8000")
SAMPLES = Path(__file__).resolve().parent.parent / "samples"
PIPELINE_TIMEOUT_S = int(os.environ.get("PIPELINE_TIMEOUT_S", "600"))

GUIDELINE = (SAMPLES / "grant-guideline.md").read_text(encoding="utf-8")
APPLICATION = (SAMPLES / "draft-application.md").read_text(encoding="utf-8")
SUPPORTING_DOCS = json.loads((SAMPLES / "supporting-documents.json").read_text(encoding="utf-8"))


def check(label: str, ok: bool, detail: object = "") -> None:
    status = "ok  " if ok else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        raise AssertionError(f"{label}: {detail}")


def wait_for_pipeline(c: httpx.Client, aid: int) -> dict:
    deadline = time.time() + PIPELINE_TIMEOUT_S
    last_stage = ""
    while time.time() < deadline:
        data = c.get(f"/api/assessments/{aid}").json()
        stage = (data.get("progress") or {}).get("stage", "")
        if stage != last_stage:
            print(f"  pipeline: {data['status']} / {stage}")
            last_stage = stage
        if data["status"] != "running":
            return data
        time.sleep(2)
    raise TimeoutError(f"pipeline still running after {PIPELINE_TIMEOUT_S}s")


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=120)

    health = c.get("/api/health").json()
    check("health endpoint", health["status"] == "ok", health)

    g = c.post("/api/documents/paste", json={
        "kind": "guideline", "title": "Green Horizon Call GH-2026-02", "text": GUIDELINE,
    }).json()
    a = c.post("/api/documents/paste", json={
        "kind": "application", "title": "ClosedLoop-Dye draft (VerdeTextile)", "text": APPLICATION,
    }).json()
    gv, av = g["versions"][-1], a["versions"][-1]
    check("guideline ingested", gv["block_count"] >= 30, f"v{gv['version_no']}, {gv['block_count']} blocks")
    check("application ingested", av["block_count"] >= 15, f"v{av['version_no']}, {av['block_count']} blocks")

    assessment = c.post("/api/assessments", json={
        "guideline_version_id": gv["id"],
        "application_version_id": av["id"],
        "run_llm": True,
        "supporting_docs": [
            {k: d[k] for k in ("expected_name", "required", "present", "metadata")}
            for d in SUPPORTING_DOCS
        ],
    })
    if assessment.status_code == 400 and "LLM not configured" in assessment.text:
        print("  LLM not configured — falling back to manual-review mode (run_llm=false)")
        assessment = c.post("/api/assessments", json={
            "guideline_version_id": gv["id"],
            "application_version_id": av["id"],
            "run_llm": False,
            "supporting_docs": [
                {k: d[k] for k in ("expected_name", "required", "present", "metadata")}
                for d in SUPPORTING_DOCS
            ],
        })
    assessment.raise_for_status()
    aid = assessment.json()["id"]
    print(f"assessment {aid} created: {assessment.json()}")

    ai_mode = assessment.json()["status"] == "running"
    if ai_mode:
        data = wait_for_pipeline(c, aid)
        check("pipeline completed", data["status"] != "error", data.get("progress"))
        reqs = data.get("requirements") or []
        check("requirements extracted", len(reqs) >= 8, f"{len(reqs)} requirements")
        codes = [r["code"] for r in reqs]
        check("mandatory/recommended distinction", len({r["basis"] for r in reqs}) >= 2,
              sorted({r["basis"] for r in reqs}))
        mappings = [r["mapping"] for r in reqs if r.get("mapping")]
        check("mappings with citations", len(mappings) > 0,
              f"{len(mappings)} mappings, {sum(len(m.get('citations') or []) for m in mappings)} citations")
        check("clarification questions generated", len(data.get("questions") or []) > 0,
              f"{len(data.get('questions') or [])} questions")
        check("claims analysed", len(data.get("claims") or []) > 0,
              f"{len(data.get('claims') or [])} claims")
        llm_logs = c.get(f"/api/assessments/{aid}/llm-logs").json()
        check("structured AI run log", len(llm_logs) > 0, f"{len(llm_logs)} logged calls")

        # human review: confirm / reject on real mappings
        confirmable = [m for m in mappings if m["status"] == "suggested"]
        if confirmable:
            m0 = confirmable[0]
            r = c.patch(f"/api/mappings/{m0['id']}", json={"action": "confirm", "note": "reviewer check"})
            check("confirm mapping (human review)", r.status_code == 200, r.text[:200])
            rej = next((m for m in confirmable if m["id"] != m0["id"]), None)
            if rej:
                r = c.patch(f"/api/mappings/{rej['id']}", json={"action": "reject", "note": "not applicable"})
                check("reject mapping (human review)", r.status_code == 200, r.text[:200])
    else:
        # manual mode: deterministic flow without the LLM
        blocks = c.get(f"/api/versions/{av['id']}/blocks").json()
        block = next(b for b in blocks if "registered" in b["text"] or "employees" in b["text"])
        req = c.post(f"/api/assessments/{aid}/requirements", json={
            "code": "R1", "title": "Two audited financial statements",
            "requirement_text": "Two complete audited financial statements must be uploaded.",
            "basis": "mandatory", "required_supporting_doc_name": "Audited financial statements",
        }).json()
        mapping = c.post(f"/api/requirements/{req['id']}/mappings", json={
            "quote": block["text"][:80], "block_id": block["id"], "status": "suggested",
        }).json()
        check("manual mapping verified", mapping["verified"] is True, mapping)
        r = c.patch(f"/api/mappings/{mapping['id']}", json={"action": "confirm"})
        check("confirm mapping (human review)", r.status_code == 200, r.text[:200])
        data = c.get(f"/api/assessments/{aid}").json()

    comp = data["completion"]
    check("deterministic completion computed", comp["mandatory"]["satisfied"] is not None
          or comp["mandatory"]["percentage"] is not None, comp["mandatory"])
    check("non-authoritative guardrail", "not an authoritative" in comp.get("disclaimer", ""),
          comp.get("disclaimer", "")[:80])
    check("missing supporting docs tracked",
          comp["mandatory"].get("unsatisfied", 0) >= 1 or len(data.get("supporting_docs") or []) >= 8,
          comp["mandatory"])

    summary = c.get(f"/api/assessments/{aid}/summary").json()
    check("reviewed summary generated", "Completeness Review Summary" in summary["markdown"],
          f"{len(summary['markdown'])} chars")
    check("summary disclaimer", "not an authoritative funding-eligibility" in summary["markdown"])

    # staleness: change the guideline, assessment must be marked stale
    c.post(f"/api/documents/{g['id']}/versions/paste",
           json={"text": GUIDELINE + "\n\n## 8. Amendment 1 (added during review)\n\nNew appendix requirement.\n"})
    data = c.get(f"/api/assessments/{aid}").json()
    check("stale after guideline change", data["staleness"]["stale"] is True, data["staleness"]["reasons"])

    print("SAMPLES E2E TEST PASSED" + (" (AI pipeline)" if ai_mode else " (manual mode)"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"SAMPLES E2E TEST FAILED: {exc}")
        sys.exit(1)
