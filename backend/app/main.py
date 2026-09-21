"""Money by Numbers API — FastAPI application entrypoint."""

import logging
import os
import sys
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_settings
from .middleware import configure_cors
from .routers import admin, affiliate, auth, billing, data_health, game_context, health, numby, odds, playoffs, predictions, track_record

log = logging.getLogger("mnb.scheduler")


def _scheduler_loop(stop: threading.Event, interval_s: int = 300) -> None:
    """In-process scheduler tick (Phase 10 roster).

    The pipeline scheduler was built to run as ``python
    pipelines/run_scheduler.py --once`` from cron. On Render's free tier
    there is no always-on cron host, so the web process ticks the same
    ``run_due_jobs`` itself every ``interval_s`` seconds. The runner's
    DB-backed locking makes concurrent tickers safe, and jobs only run
    when the process is awake — a lightweight external waker keeps the
    cadence honest during the season. Disable with
    ``SCHEDULER_LOOP_ENABLED=0``.
    """
    try:
        repo_root = os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
        )
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from pipelines.runner import SchedulerRunner

        from .database import get_database_name, get_mongo_client

        client = get_mongo_client()
        if client is None:
            log.warning("scheduler loop disabled: MONGODB_URI not configured")
            return
        runner = SchedulerRunner(client[get_database_name()])
    except Exception as exc:  # noqa: BLE001 - the API must boot regardless
        log.warning("scheduler loop disabled: %s", exc)
        return
    while not stop.wait(interval_s):
        try:
            records = runner.run_due_jobs()
            for record in records:
                log.info(
                    "scheduler: job=%s status=%s",
                    getattr(record, "job_id", "?"),
                    getattr(record.status, "value", record.status),
                )
        except Exception as exc:  # noqa: BLE001 - one bad tick never kills the loop
            log.warning("scheduler tick failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop = threading.Event()
    thread = None
    if os.environ.get("SCHEDULER_LOOP_ENABLED", "1") == "1":
        thread = threading.Thread(
            target=_scheduler_loop, args=(stop,), daemon=True, name="mnb-scheduler"
        )
        thread.start()
    yield
    stop.set()
    if thread is not None:
        thread.join(timeout=10)


app = FastAPI(
    title="Money by Numbers API",
    version=get_settings().APP_VERSION,
    lifespan=lifespan,
)

# Phase 12: security headers, per-IP rate limits on abuse-sensitive public
# endpoints, and explicit-origin CORS (refuses wildcard in production).
configure_cors(app)

app.include_router(health.router)
app.include_router(data_health.router)
app.include_router(auth.router)
app.include_router(predictions.router)
app.include_router(odds.router)
app.include_router(game_context.router)
app.include_router(playoffs.router)
app.include_router(track_record.router)
app.include_router(numby.router)
app.include_router(affiliate.router)
app.include_router(admin.router)
app.include_router(billing.router)


@app.get("/")
def root():
    return {"service": "money-by-numbers", "version": get_settings().APP_VERSION}
