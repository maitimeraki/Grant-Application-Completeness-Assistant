"""One-off maintenance: keep the database from filling up on small instances.

Deletes aged operational rows that accumulate fastest (AI call logs, audit
rows) and clears assessments stuck in "running" longer than a threshold —
a crashed pipeline leaves the row "running" forever, and the UI polls it.

Usage (from backend/):

    python scripts/purge_old_records.py --llm-days 14 --audit-days 30 --stuck-hours 6
    python scripts/purge_old_records.py --dry-run

After a large purge on Postgres, reclaim space with:

    VACUUM (NOT FULL) analyze;   -- safe online; run via psql/Render shell
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Assessment, AuditLog, LLMCallLog, utcnow  # noqa: E402


def purge(llm_days: int, audit_days: int, stuck_hours: int, dry_run: bool) -> None:
    now = utcnow()
    llm_cutoff = now - dt.timedelta(days=llm_days)
    audit_cutoff = now - dt.timedelta(days=audit_days)
    stuck_cutoff = now - dt.timedelta(hours=stuck_hours)

    with SessionLocal() as session:
        stale_running = session.scalars(
            select(Assessment.id).where(
                Assessment.status == "running",
                Assessment.created_at < stuck_cutoff,
            )
        ).all()
        llm_q = delete(LLMCallLog).where(LLMCallLog.created_at < llm_cutoff)
        audit_q = delete(AuditLog).where(AuditLog.created_at < audit_cutoff)

        llm_count = session.execute(
            select(LLMCallLog.id).where(LLMCallLog.created_at < llm_cutoff)
        ).rowcount
        audit_count = session.execute(
            select(AuditLog.id).where(AuditLog.created_at < audit_cutoff)
        ).rowcount

        print(f"llm_call_logs older than {llm_days}d: {llm_count}")
        print(f"audit_log rows older than {audit_days}d: {audit_count}")
        print(f"assessments stuck in 'running' over {stuck_hours}h: {len(stale_running)} {list(stale_running)}")

        if dry_run:
            print("dry-run: no changes written")
            return

        session.execute(llm_q)
        session.execute(audit_q)
        for aid in stale_running:
            assessment = session.get(Assessment, aid)
            if assessment is not None:
                assessment.status = "error"
                progress = dict(assessment.progress or {})
                progress.update(
                    {
                        "stage": "error",
                        "message": (
                            "Pipeline did not finish (server restarted or ran out "
                            "of resources). Re-run the assessment."
                        ),
                    }
                )
                assessment.progress = progress
        session.commit()
        print("done — run VACUUM (NOT FULL) on Postgres to reclaim disk space")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm-days", type=int, default=14)
    parser.add_argument("--audit-days", type=int, default=30)
    parser.add_argument("--stuck-hours", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    purge(args.llm_days, args.audit_days, args.stuck_hours, args.dry_run)


if __name__ == "__main__":
    main()
