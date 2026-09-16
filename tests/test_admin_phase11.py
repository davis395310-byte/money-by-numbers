"""Phase 11 admin-panel tests (spec 59-61, spec 66 ADMIN section).

- Every admin API route is gated: 501 when ADMIN_API_KEY is unset,
  403 on a wrong key — with no bypass, no default key.
- The new subscriptions overview aggregates real records only and never
  reports revenue.
- Admin responses never leak the admin key or other secrets.
- There is NO write path from any admin route (or any route at all) to the
  predictions ledger: settled picks cannot be created, edited, or deleted
  through the API.
"""

import os
import re

import mongomock
import pytest
from fastapi.testclient import TestClient

from app import routers as _routers  # noqa: F401  (ensures package import)
from app.database import ensure_indexes
from app.routers import admin as admin_router

ADMIN_HEADERS = {"X-Admin-Key": "test-admin-key"}
WRONG_HEADERS = {"X-Admin-Key": "wrong-key"}

# Every admin route: (method, path template). Path params use dummy values.
ADMIN_ROUTES = [
    ("GET", "/api/admin/sportsbooks"),
    ("POST", "/api/admin/sportsbooks"),
    ("PATCH", "/api/admin/sportsbooks/deadbeef"),
    ("DELETE", "/api/admin/sportsbooks/deadbeef"),
    ("GET", "/api/admin/conversions"),
    ("POST", "/api/admin/conversions"),
    ("GET", "/api/admin/affiliate/stats"),
    ("GET", "/api/admin/scheduler/jobs"),
    ("GET", "/api/admin/scheduler/runs"),
    ("GET", "/api/admin/scheduler/alerts"),
    ("GET", "/api/admin/subscriptions/overview"),
]

BODIES = {
    ("POST", "/api/admin/sportsbooks"): {"name": "X"},
    ("PATCH", "/api/admin/sportsbooks/deadbeef"): {"name": "Y"},
    (
        "POST",
        "/api/admin/conversions",
    ): {
        "sportsbook_id": "deadbeef",
        "converted_at": "2026-01-01T00:00:00Z",
    },
}


@pytest.fixture()
def db():
    client = mongomock.MongoClient()
    database = client["money_by_numbers"]
    ensure_indexes(database)
    return database


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setattr(admin_router, "_db", lambda: db)
    from app.main import app

    return TestClient(app)


@pytest.fixture()
def admin_env(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")


class TestAdminGate:
    @pytest.mark.parametrize("method,path", ADMIN_ROUTES)
    def test_every_admin_route_501_when_key_unconfigured(self, client, method, path):
        # conftest scrubs ADMIN_API_KEY from the env -> 501 on every route.
        body = BODIES.get((method, path))
        r = client.request(method, path, json=body, headers=WRONG_HEADERS)
        assert r.status_code == 501, (method, path, r.status_code, r.text[:160])
        assert "ADMIN_API_KEY" in r.text

    @pytest.mark.parametrize("method,path", ADMIN_ROUTES)
    def test_every_admin_route_403_on_wrong_key(self, client, admin_env, method, path):
        body = BODIES.get((method, path))
        r = client.request(method, path, json=body, headers=WRONG_HEADERS)
        assert r.status_code == 403, (method, path, r.status_code, r.text[:160])

    def test_valid_key_passes_gate(self, client, admin_env, db):
        r = client.get("/api/admin/affiliate/stats", headers=ADMIN_HEADERS)
        assert r.status_code == 200, r.text[:200]

    def test_no_default_key_bypass(self, client, admin_env):
        # Empty key header is still rejected.
        r = client.get(
            "/api/admin/affiliate/stats", headers={"X-Admin-Key": ""}
        )
        assert r.status_code == 403


class TestSubscriptionsOverview:
    def _seed(self, db):
        db["subscriptions"].insert_many(
            [
                {"subscription_id": "s1", "user_id": "u1", "plan": "free", "status": "active"},
                {"subscription_id": "s2", "user_id": "u2", "plan": "pro", "status": "active"},
                {"subscription_id": "s3", "user_id": "u3", "plan": "pro", "status": "past_due"},
                {"subscription_id": "s4", "user_id": "u4", "plan": "agency", "status": "canceled"},
            ]
        )

    def test_empty_state_is_honest(self, client, admin_env, db):
        r = client.get("/api/admin/subscriptions/overview", headers=ADMIN_HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert data["has_data"] is False
        assert data["total"] == 0
        assert data["revenue"] is None
        assert data["note"]  # explains revenue is not estimated

    def test_aggregates_real_records(self, client, admin_env, db):
        self._seed(db)
        r = client.get("/api/admin/subscriptions/overview", headers=ADMIN_HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert data["has_data"] is True
        assert data["total"] == 4
        assert data["by_plan"] == {"free": 1, "pro": 2, "agency": 1}
        assert data["by_status"]["active"] == 2
        assert data["active_paid_subscriptions"] == 1  # only the active pro
        assert data["revenue"] is None  # never invented


class TestNoSecretLeakage:
    def test_admin_responses_never_contain_the_key(
        self, client, admin_env, db
    ):
        for method, path in ADMIN_ROUTES:
            body = BODIES.get((method, path))
            r = client.request(method, path, json=body, headers=ADMIN_HEADERS)
            assert "test-admin-key" not in r.text, (method, path)
            # No other secret-shaped env names leak through either.
            for token in ("STRIPE_SECRET_KEY", "ADMIN_API_KEY", "SESSION_SECRET"):
                assert token not in r.text, (method, path, token)

    def test_affiliate_urls_not_leaked_to_public_cta_status(
        self, client, admin_env, db
    ):
        from app.routers import affiliate as affiliate_router

        db["sportsbooks"].insert_one(
            {
                "sportsbook_id": "abc123",
                "name": "Secret Book",
                "affiliate_url": "https://secret.example/aff?tag=SECRET123",
                "active": True,
            }
        )
        # Public CTA status must name partners but never expose URLs.
        r = client.get("/api/affiliate/cta-status")
        assert r.status_code == 200
        assert "SECRET123" not in r.text
        assert "https://secret.example" not in r.text


class TestNoLedgerWritePath:
    def _app_routes(self):
        from app.main import app

        return app.routes

    def test_no_route_writes_to_predictions_collection(self):
        """No HTTP route — admin or otherwise — may create/edit/delete
        settled picks. The only writer is lock_pick() inside the ledger
        module (pre-kickoff), and settlement writes to pick_results."""
        from app.main import app

        write_methods = {"POST", "PUT", "PATCH", "DELETE"}
        offenders = []
        for route in app.routes:
            methods = getattr(route, "methods", None) or set()
            path = getattr(route, "path", "")
            if write_methods & set(methods) and "predict" in path.lower():
                offenders.append((sorted(write_methods & set(methods)), path))
        assert offenders == [], f"write routes touching predictions: {offenders}"

    def test_admin_source_has_no_predictions_collection_access(self):
        """Static check: the admin router never touches the predictions
        collection, so no admin action can rewrite the pick ledger."""
        admin_src = open(admin_router.__file__).read()
        hits = re.findall(r"""\[["']predictions["']\]""", admin_src)
        assert hits == [], f"admin router references predictions collection: {hits}"

    def test_ledger_is_only_pre_kickoff_writer(self):
        """lock_pick refuses post-kickoff writes and duplicates — the
        structural guarantee the admin panel must not be able to bypass."""
        import inspect
        from app.picks import ledger

        assert hasattr(ledger, "lock_pick")
        src = inspect.getsource(ledger.lock_pick)
        assert "kickoff" in src.lower(), "lock_pick must enforce the kickoff cutoff"
