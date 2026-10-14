import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routers import assessments, documents, health

settings = get_settings()

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


def _resume_interrupted_assessments() -> None:
    """Re-queue assessments stuck at 'running' after a restart or crash.

    The pipeline runs as an in-process background task, so a process restart
    kills it and nothing else would ever finish these. Guarded by
    progress.resume_attempts (max 2) so a pipeline that crashes the server
    can never crash-loop it.
    """
    from app.db import SessionLocal
    from app.models import Assessment, utcnow
    from app.services.pipeline import run_pipeline

    with SessionLocal() as session:
        stuck_ids = [
            row.id
            for row in session.query(Assessment)
            .filter(Assessment.status == "running")
            .order_by(Assessment.id)
            .all()
        ]

    for assessment_id in stuck_ids:
        with SessionLocal() as session:
            assessment = session.get(Assessment, assessment_id)
            if assessment is None or assessment.status != "running":
                continue
            progress = dict(assessment.progress or {})
            try:
                attempts = int(progress.get("resume_attempts", 0) or 0)
            except (TypeError, ValueError):
                attempts = 0
            if attempts >= 2:
                assessment.status = "error"
                progress.update(
                    {
                        "stage": "error",
                        "message": (
                            "Interrupted by server restart — please Re-run "
                            "against latest versions."
                        ),
                        "updated_at": utcnow().isoformat(),
                    }
                )
                assessment.progress = progress
                session.commit()
                logger.info(
                    "assessment %s: resume limit reached — marked error",
                    assessment_id,
                )
                continue
            progress.update(
                {
                    "resume_attempts": attempts + 1,
                    "message": "Resuming after server restart…",
                    "updated_at": utcnow().isoformat(),
                }
            )
            assessment.progress = progress
            session.commit()
        logger.info(
            "resuming interrupted assessment %s (attempt %s)",
            assessment_id,
            attempts + 1,
        )
        run_pipeline(assessment_id)


def _startup_background() -> None:
    """Seed (if enabled), then resume interrupted assessments — one thread so
    seeding and resume never race on the same assessment."""

    def _run() -> None:
        if settings.seed_on_startup:
            try:
                from app.services.seed import seed_demo_data

                result = seed_demo_data()
                logger.info("startup seed: %s", result)
            except Exception:  # noqa: BLE001 — seeding must never take the server down
                logger.exception("startup seeding failed (server continues without samples)")
        try:
            _resume_interrupted_assessments()
        except Exception:  # noqa: BLE001 — recovery must never take the server down
            logger.exception("resume of interrupted assessments failed")

    threading.Thread(target=_run, daemon=True, name="startup-tasks").start()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _startup_background()
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "AI-assisted completeness review of draft funding applications "
        "against grant guidelines. Not a funding-eligibility or legal "
        "determination."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(documents.router)
app.include_router(assessments.router)


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


@app.get("/")
def root():
    # With a built frontend present (Docker image / local build), "/" serves
    # the app UI; otherwise it returns the API metadata JSON.
    if (FRONTEND_DIST / "index.html").is_file():
        return FileResponse(FRONTEND_DIST / "index.html")
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
        "note": "Completeness review only; not a funding-eligibility determination.",
    }


# ---------- built-frontend (SPA) serving ----------
# When frontend/dist exists (Docker image or a local `npm run build`), the same
# FastAPI process serves the UI. API routes above always take precedence.

if FRONTEND_DIST.is_dir():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    _dist_root = FRONTEND_DIST.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = (_dist_root / full_path).resolve()
        try:
            candidate.relative_to(_dist_root)
        except ValueError:
            raise HTTPException(status_code=404, detail="Not found") from None
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_dist_root / "index.html")

    logger.info("serving built frontend from %s", FRONTEND_DIST)
