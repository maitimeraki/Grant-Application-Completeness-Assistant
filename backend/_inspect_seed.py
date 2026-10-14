import sys

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.db import SessionLocal
from app.models import Assessment, LLMCallLog, Mapping, Requirement

assessment_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
db = SessionLocal()
a = db.get(
    Assessment,
    assessment_id,
    options=[
        joinedload(Assessment.requirements)
        .joinedload(Requirement.mapping)
        .joinedload(Mapping.citations),
        joinedload(Assessment.claims),
        joinedload(Assessment.questions),
    ],
)
print("status:", a.status, "| model:", a.llm_model)
print("requirements:", len(a.requirements))
for r in a.requirements:
    m = r.mapping
    v = sum(1 for c in m.citations if c.verified) if m else 0
    total = len(m.citations) if m else 0
    status = m.status if m else "NO MAPPING"
    print(f"  {r.code} [{r.basis:9}] {r.title[:55]!r} -> {status:9} weakness={m.weakness} verified={v}/{total}")
print("claims:", [(c.support, c.user_accepted) for c in a.claims])
print("questions:", len(a.questions))
logs = db.execute(
    select(LLMCallLog.stage, func.count())
    .where(LLMCallLog.assessment_id == assessment_id)
    .group_by(LLMCallLog.stage)
).all()
print("llm calls by stage:", logs)
db.close()
