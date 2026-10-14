"""Extended live no-LLM API test against a running instance."""
import io
import os
import sys

import httpx

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:8000")

GUIDELINE = "# Program\n\nApplicants must submit an annual report.\n\nA logo is recommended.\n"
APPLICATION = "# Project\n\nWe deliver training to 200 participants yearly.\n\nAttached: annual report summary.\n"


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=30)
    passed = []

    def check(name, cond, extra=""):
        status = "PASS" if cond else "FAIL"
        passed.append(cond)
        print(f"[{status}] {name}{(' — ' + str(extra)) if extra and not cond else ''}")

    # 1. upload endpoints (multipart, all supported formats)
    r = c.post("/api/documents", data={"kind": "guideline", "title": "Up GL"},
               files={"file": ("g.md", GUIDELINE.encode(), "text/markdown")})
    check("upload markdown guideline", r.status_code == 201, r.text)
    gl = r.json()

    r = c.post("/api/documents", data={"kind": "application", "title": "Up App"},
               files={"file": ("a.txt", APPLICATION.encode(), "text/plain")})
    check("upload txt application", r.status_code == 201, r.text)
    app = r.json()

    r = c.post("/api/documents", data={"kind": "application", "title": "Bad"},
               files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    check("reject unsupported filetype", r.status_code == 400, r.text)

    # 2. docx round-trip (minimal docx via python-docx)
    from docx import Document as Dx
    buf = io.BytesIO()
    dx = Dx()
    dx.add_paragraph("Applicants must register before the deadline.")
    dx.save(buf)
    r = c.post("/api/documents", data={"kind": "guideline", "title": "DOCX GL"},
               files={"file": ("g.docx", buf.getvalue(),
                               "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    check("upload docx", r.status_code == 201 and r.json()["versions"][-1]["block_count"] >= 1, r.text)

    # 3. versioning via upload
    r = c.post(f"/api/documents/{gl['id']}/versions",
               files={"file": ("g2.md", (GUIDELINE + "\n\nDeadline: 1 May.\n").encode(), "text/markdown")})
    check("new uploaded version increments", r.status_code == 201 and r.json()["version_no"] == 2, r.text)

    # 4. assessment: wrong kinds rejected; valid manual created
    r = c.post("/api/assessments", json={
        "guideline_version_id": app["versions"][-1]["id"],
        "application_version_id": gl["versions"][-1]["id"], "run_llm": False})
    check("wrong document kinds rejected", r.status_code == 400, r.text)

    r = c.post("/api/assessments", json={
        "guideline_version_id": gl["versions"][-1]["id"],
        "application_version_id": app["versions"][-1]["id"], "run_llm": False,
        "supporting_docs": [{"expected_name": "Logo file", "required": True}]})
    check("manual assessment created", r.status_code == 201, r.text)
    aid = r.json()["id"]

    # 5. manual requirement + mapping (verified quote)
    blocks = c.get(f"/api/versions/{app['versions'][-1]['id']}/blocks").json()
    blk = next(b for b in blocks if "annual report" in b["text"])
    req = c.post(f"/api/assessments/{aid}/requirements", json={
        "code": "R1", "title": "Annual report", "requirement_text": "Must submit annual report.",
        "basis": "mandatory", "required_supporting_doc_name": None}).json()
    r = c.post(f"/api/requirements/{req['id']}/mappings",
               json={"quote": "annual report summary", "block_id": blk["id"], "status": "confirmed"})
    check("mapping quote verified", r.status_code == 201 and r.json()["verified"], r.text)

    # duplicate code -> 409
    r = c.post(f"/api/assessments/{aid}/requirements", json={
        "code": "R1", "title": "Dup", "requirement_text": "x", "basis": "mandatory"})
    check("duplicate requirement code 409", r.status_code == 409, r.text)

    # 6. correct with bogus quote -> 400; reject; re-confirm; correct properly
    mid = c.get(f"/api/assessments/{aid}").json()["requirements"][0]["mapping"]["id"]
    r = c.patch(f"/api/mappings/{mid}", json={"action": "confirm", "quote": "nope nope", "block_id": blk["id"]})
    check("correct with fake quote -> 400", r.status_code == 400, r.text)
    r = c.patch(f"/api/mappings/{mid}", json={"action": "reject", "note": "wrong"})
    check("reject works", r.status_code == 200 and r.json()["status"] == "rejected", r.text)
    data = c.get(f"/api/assessments/{aid}").json()
    check("rejected -> unsatisfied", data["completion"]["mandatory"]["unsatisfied"] == 1)
    r = c.patch(f"/api/mappings/{mid}", json={"action": "correct", "quote": "annual report summary",
                                              "block_id": blk["id"], "note": "cite report"})
    check("correct works", r.status_code == 200 and r.json()["status"] == "corrected", r.text)
    data = c.get(f"/api/assessments/{aid}").json()
    check("corrected + verified -> satisfied", data["completion"]["mandatory"]["satisfied"] == 1)

    # 7. N/A and basis override
    r = c.patch(f"/api/requirements/{req['id']}", json={"not_applicable": True})
    data = c.get(f"/api/assessments/{aid}").json()
    check("N/A excludes from pool", data["completion"]["mandatory"]["total"] == 0
          and data["completion"]["mandatory"]["not_applicable"] == 1)
    c.patch(f"/api/requirements/{req['id']}", json={"not_applicable": False, "basis": "recommended"})
    data = c.get(f"/api/assessments/{aid}").json()
    check("basis override -> recommended", data["requirements"][0]["basis"] == "recommended")

    # 8. supporting doc toggle changes satisfaction (R1 no longer requires it; add R2 that does)
    req2 = c.post(f"/api/assessments/{aid}/requirements", json={
        "code": "R2", "title": "Logo", "requirement_text": "Logo required.", "basis": "mandatory",
        "required_supporting_doc_name": "Logo file"}).json()
    blk2 = next(b for b in blocks if "deliver training" in b["text"])
    r = c.post(f"/api/requirements/{req2['id']}/mappings",
               json={"quote": "deliver training", "block_id": blk2["id"], "status": "confirmed"})
    check("R2 mapping verified", r.status_code == 201 and r.json()["verified"], r.text)
    data = c.get(f"/api/assessments/{aid}").json()
    check("missing required doc blocks satisfaction",
          data["completion"]["mandatory"]["satisfied"] == 0
          and data["completion"]["mandatory"]["unsatisfied"] == 1)
    logo = next(d for d in data["supporting_docs"] if d["expected_name"] == "Logo file")
    c.patch(f"/api/supporting-docs/{logo['id']}", json={"present": True})
    data = c.get(f"/api/assessments/{aid}").json()
    check("doc present -> 100%", data["completion"]["mandatory"]["percentage"] == 100.0)

    # 9. claims + questions review (seeded via direct DB equivalent: use API only — none exist without LLM)
    check("no LLM artifacts in manual mode",
          data["claims"] == [] and data["questions"] == [])

    # 10. summary + staleness + rerun-without-LLM-key -> 400
    s = c.get(f"/api/assessments/{aid}/summary").json()
    check("summary markdown", "Completeness Review Summary" in s["markdown"]
          and "not an authoritative funding-eligibility" in s["markdown"])
    c.post(f"/api/documents/{gl['id']}/versions/paste", json={"text": GUIDELINE + "\n\nUpdated.\n"})
    data = c.get(f"/api/assessments/{aid}").json()
    check("stale after new guideline version", data["staleness"]["stale"] is True
          and data["staleness"]["guideline_stale"] is True)
    r = c.post(f"/api/assessments/{aid}/rerun")
    check("rerun without LLM key -> 400", r.status_code == 400 and "LLM" in r.json()["detail"], r.text)

    # 11. run_llm=true without key -> 400
    r = c.post("/api/assessments", json={
        "guideline_version_id": gl["versions"][-1]["id"],
        "application_version_id": app["versions"][-1]["id"], "run_llm": True})
    check("run_llm without key -> 400", r.status_code == 400, r.text)

    # 12. 404s
    check("missing assessment 404", c.get("/api/assessments/99999").status_code == 404)
    check("missing document 404", c.get("/api/documents/99999").status_code == 404)

    total, ok = len(passed), sum(passed)
    print(f"\n{ok}/{total} checks passed")
    return 0 if all(passed) else 1


if __name__ == "__main__":
    sys.exit(main())
