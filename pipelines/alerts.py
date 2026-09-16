"""Failure/missed-run alerting for the MONEY BY NUMBERS scheduler.

Phase 10 (spec section 58). Alerts are:

1. Persisted to the ``scheduler_alerts`` collection (auditable history).
2. Logged as structured JSON.
3. Forwarded to ``ALERT_WEBHOOK_URL`` when configured (generic JSON
   webhook — Slack/Discord/PagerDuty all accept this shape). The POST
   goes through the ``curl`` binary because Python HTTP clients stall
   through this sandbox's egress proxy; in production either transport
   works.

Alerting never fails a job: a broken webhook is logged, not raised.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

log = logging.getLogger("mbn.scheduler.alerts")


def utcnow() -> datetime:
    # Naive UTC: MongoDB stores datetimes as naive UTC, so alert docs
    # stay comparable with the scheduler's other timestamps.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def emit_alert(db: Any, job_name: str, kind: str, message: str,
               run_id: Optional[str] = None) -> Dict[str, Any]:
    """Record an alert and forward it to the webhook when configured.

    ``kind`` is one of: "job_failed", "job_crashed", "run_missed".
    Returns the alert document. Never raises.
    """
    alert = {
        "alert_id": uuid.uuid4().hex,
        "job_name": job_name,
        "kind": kind,
        "message": message[:1000],
        "run_id": run_id,
        "created_at": utcnow(),
        "webhook_delivered": False,
        "webhook_error": None,
    }
    try:
        if db is not None:
            db["scheduler_alerts"].insert_one(dict(alert))
    except Exception as exc:  # noqa: BLE001 - alerting must not crash the runner
        log.error(json.dumps({"event": "alert_persist_failed", "job": job_name,
                              "kind": kind, "error": str(exc)[:200]}))
    log.warning(json.dumps({"event": "scheduler_alert", "job": job_name,
                            "kind": kind, "message": alert["message"]}))

    webhook_url = os.environ.get("ALERT_WEBHOOK_URL", "").strip()
    if webhook_url:
        payload = {
            "text": f"[money-by-numbers scheduler] {kind}: {job_name}: {message[:500]}",
            "job_name": job_name,
            "kind": kind,
            "run_id": run_id,
        }
        try:
            proc = subprocess.run(
                ["curl", "-sS", "-m", "10", "-X", "POST", webhook_url,
                 "-H", "Content-Type: application/json",
                 "--data-binary", "@-"],
                input=json.dumps(payload).encode("utf-8"),
                capture_output=True,
                timeout=15,
            )
            if proc.returncode == 0:
                alert["webhook_delivered"] = True
            else:
                alert["webhook_error"] = proc.stderr.decode("utf-8", "replace")[:300]
        except Exception as exc:  # noqa: BLE001
            alert["webhook_error"] = str(exc)[:300]
        try:
            if db is not None:
                db["scheduler_alerts"].update_one(
                    {"alert_id": alert["alert_id"]},
                    {"$set": {"webhook_delivered": alert["webhook_delivered"],
                              "webhook_error": alert["webhook_error"]}},
                )
        except Exception:  # noqa: BLE001 - best effort
            pass
        if alert["webhook_error"]:
            log.error(json.dumps({"event": "alert_webhook_failed", "job": job_name,
                                  "error": alert["webhook_error"][:200]}))
    return alert
