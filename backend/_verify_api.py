from fastapi.testclient import TestClient

from app.main import app

with TestClient(app) as client:
    docs = client.get("/api/documents").json()
    print("documents:", " | ".join(d["title"] for d in docs))

    lst = client.get("/api/assessments").json()
    print("assessments:", " | ".join(f"{a['id']} {a['status']} {a['application_title']}" for a in lst))

    a1 = client.get("/api/assessments/1").json()
    mand = a1["completion"]["mandatory"]
    print(f"a1 completion: mandatory {mand['satisfied']}/{mand['total']} ({mand['percentage']}%)")
    print("a1 staleness:", a1["staleness"])
    statuses = [r["mapping"]["status"] for r in a1["requirements"] if r["mapping"]]
    counts: dict[str, int] = {}
    for s in statuses:
        counts[s] = counts.get(s, 0) + 1
    print("a1 mapping statuses:", counts)
    print("a1 supporting docs:", len(a1["supporting_docs"]), "| missing required:",
          sum(1 for d in a1["supporting_docs"] if d["required"] and not d["present"]))
    print("a1 claims:", len(a1["claims"]),
          "| accepted:", sum(1 for c in a1["claims"] if c["user_accepted"] is True),
          "| disputed:", sum(1 for c in a1["claims"] if c["user_accepted"] is False))
    print("a1 questions:", len(a1["questions"]),
          "| answered:", sum(1 for q in a1["questions"] if q["answer"]))

    logs = client.get("/api/assessments/1/llm-logs").json()
    print("llm-logs rows:", len(logs))

    summ = client.get("/api/assessments/1/summary").json()
    print("summary md length:", len(summ["markdown"]))

    a2 = client.get("/api/assessments/2").json()
    print("a2 (sample B):", a2["status"], "| requirements:", len(a2["requirements"]),
          "| stage:", a2["progress"].get("stage"))

    spa = client.get("/")
    print("SPA served:", spa.status_code, "len", len(spa.content))
