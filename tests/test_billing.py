"""Phase 9 billing tests: Stripe wiring honesty, webhook security, plan
gating, and the no-fabrication rule.

HONESTY RULE: all Stripe objects here are SYNTHETIC (fake event ids,
price ids, customer ids). No real Stripe account is touched; the HTTP
layer is monkeypatched. Webhook signatures are computed with HMAC-SHA256
exactly as Stripe documents them.
"""

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

import mongomock
import pytest
from fastapi.testclient import TestClient

from app import routers as _routers  # noqa: F401  (ensures package import)
from app.auth_sessions import create_session_value
from app.billing import identity as billing_identity
from app.billing import stripe_client as stripe_client_mod
from app.billing.stripe_client import StripeError
from app.billing.plans import (
    PLANS,
    paid_plans,
    plan_for_price_id,
    plan_meets,
    plan_rank,
    price_id_for_plan,
)
from app.billing.webhooks import parse_event, verify_stripe_signature
from app.database import ensure_indexes
from app.routers import billing as billing_router
from app.routers import numby as numby_router

WEBHOOK_SECRET = "whsec_test_12345"
STRIPE_SECRET = "sk_test_12345"


@pytest.fixture()
def db():
    client = mongomock.MongoClient()
    database = client["money_by_numbers"]
    ensure_indexes(database)
    return database


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setattr(billing_router, "_db", lambda: db)
    from app.main import app

    return TestClient(app)


@pytest.fixture()
def stripe_env(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", STRIPE_SECRET)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("STRIPE_PRICE_ID_STARTER", "price_starter_123")
    monkeypatch.setenv("STRIPE_PRICE_ID_PRO", "price_pro_123")
    monkeypatch.setenv("STRIPE_PRICE_ID_AGENCY", "price_agency_123")


@pytest.fixture()
def session_env(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")


def _signin(client, sub="user_123", email="user@example.com"):
    token = create_session_value({"sub": sub, "email": email})
    client.cookies.set("mbn_session", token)
    return sub


def _sign(body: bytes, secret: str = WEBHOOK_SECRET, ts: int | None = None) -> str:
    ts = ts if ts is not None else int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def _subscription_event(
    event_id="evt_test_1",
    event_type="customer.subscription.created",
    price_id="price_pro_123",
    user_key="user_123",
    stripe_status="active",
    sub_id="sub_test_1",
):
    return {
        "id": event_id,
        "type": event_type,
        "data": {
            "object": {
                "id": sub_id,
                "customer": "cus_test_1",
                "status": stripe_status,
                "current_period_end": int(time.time()) + 30 * 86400,
                "cancel_at_period_end": False,
                "metadata": {"user_key": user_key} if user_key else {},
                "items": {"data": [{"price": {"id": price_id}}]},
            }
        },
    }


def _post_webhook(client, event, secret=WEBHOOK_SECRET, ts=None):
    body = json.dumps(event).encode()
    headers = {"Stripe-Signature": _sign(body, secret, ts)}
    return client.post("/api/billing/webhook", content=body, headers=headers)


def _insert_subscription(db, user_id, plan="starter", status="active"):
    db["subscriptions"].insert_one(
        {
            "subscription_id": "subrow_test_1",
            "user_id": user_id,
            "plan": plan,
            "status": status,
            "stripe_customer_id": "cus_test_1",
            "stripe_subscription_id": "sub_test_1",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
    )


# ---------------------------------------------------------------------------
# Plan helpers (unit)
# ---------------------------------------------------------------------------


class TestPlans:
    def test_rank_order(self):
        assert plan_rank("free") < plan_rank("starter") < plan_rank("pro") < plan_rank(
            "agency"
        )

    def test_unknown_plan_ranks_as_free(self):
        assert plan_rank("nonsense") == 0

    def test_plan_meets(self):
        assert plan_meets("pro", "starter")
        assert plan_meets("starter", "starter")
        assert not plan_meets("free", "starter")

    def test_free_is_generous(self):
        assert PLANS["free"]["numby_questions_per_day"] >= 10

    def test_price_id_mapping_roundtrip(self, stripe_env):
        assert price_id_for_plan("pro") == "price_pro_123"
        assert plan_for_price_id("price_pro_123") == "pro"
        assert plan_for_price_id("price_unknown") is None
        assert price_id_for_plan("free") is None

    def test_paid_plans_excludes_free(self):
        assert "free" not in paid_plans()
        assert set(paid_plans()) == {"starter", "pro", "agency"}


# ---------------------------------------------------------------------------
# GET /api/billing/status — honest states, no secret leaks
# ---------------------------------------------------------------------------


class TestBillingStatus:
    def test_unconfigured_shows_data_unavailable(self, client):
        r = client.get("/api/billing/status")
        assert r.status_code == 200
        body = r.json()
        assert body["stripe_configured"] is False
        assert len(body["plans"]) == 4
        for plan in body["plans"]:
            if plan["plan"] != "free":
                assert plan["price"]["amount_cents"] is None
                assert "DATA UNAVAILABLE" in plan["price"]["note"]
        assert body["current_user"]["plan"] == "free"
        assert body["current_user"]["signed_in"] is False
        assert "sk_test" not in r.text and "sk_live" not in r.text

    def test_configured_prices_come_from_stripe(self, client, stripe_env, monkeypatch):
        def fake_retrieve(price_id):
            return {
                "id": price_id,
                "unit_amount": 999,
                "currency": "USD",
                "interval": "month",
                "active": True,
            }

        monkeypatch.setattr(
            "app.routers.billing.retrieve_price", fake_retrieve
        )
        r = client.get("/api/billing/status")
        assert r.status_code == 200
        body = r.json()
        assert body["stripe_configured"] is True
        pro = next(p for p in body["plans"] if p["plan"] == "pro")
        assert pro["price"]["amount_cents"] == 999
        assert pro["price"]["currency"] == "USD"
        assert STRIPE_SECRET not in r.text

    def test_status_reflects_db_subscription_not_client_input(
        self, client, db, session_env, stripe_env
    ):
        _signin(client, sub="user_123")
        _insert_subscription(db, "user_123", plan="pro", status="active")
        r = client.get("/api/billing/status")
        body = r.json()
        assert body["current_user"]["signed_in"] is True
        assert body["current_user"]["plan"] == "pro"
        assert body["current_user"]["subscription"]["status"] == "active"

    def test_past_due_falls_back_to_free(self, client, db, session_env):
        _signin(client, sub="user_123")
        _insert_subscription(db, "user_123", plan="pro", status="past_due")
        r = client.get("/api/billing/status")
        assert r.json()["current_user"]["plan"] == "free"


# ---------------------------------------------------------------------------
# Webhook signature verification (unit)
# ---------------------------------------------------------------------------


class TestSignatureVerification:
    def test_valid_signature(self):
        body = b'{"id":"evt_1"}'
        assert verify_stripe_signature(body, _sign(body), WEBHOOK_SECRET) is True

    def test_wrong_secret(self):
        body = b'{"id":"evt_1"}'
        assert verify_stripe_signature(body, _sign(body, "whsec_wrong"), WEBHOOK_SECRET) is False

    def test_tampered_body(self):
        body = b'{"id":"evt_1"}'
        header = _sign(body)
        assert verify_stripe_signature(b'{"id":"evt_2"}', header, WEBHOOK_SECRET) is False

    def test_missing_header(self):
        assert verify_stripe_signature(b"{}", None, WEBHOOK_SECRET) is False

    def test_stale_timestamp(self):
        body = b"{}"
        header = _sign(body, ts=int(time.time()) - 3600)
        assert verify_stripe_signature(body, header, WEBHOOK_SECRET) is False

    def test_malformed_header(self):
        assert verify_stripe_signature(b"{}", "garbage", WEBHOOK_SECRET) is False

    def test_parse_event_rejects_non_json(self):
        with pytest.raises(ValueError):
            parse_event(b"not json")

    def test_parse_event_rejects_missing_fields(self):
        with pytest.raises(ValueError):
            parse_event(b'{"foo": 1}')


# ---------------------------------------------------------------------------
# POST /api/billing/webhook — security + idempotency + state updates
# ---------------------------------------------------------------------------


class TestWebhook:
    def test_501_when_secret_unconfigured(self, client, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", STRIPE_SECRET)
        r = client.post("/api/billing/webhook", content=b"{}")
        assert r.status_code == 501

    def test_rejects_missing_signature(self, client, stripe_env):
        r = client.post("/api/billing/webhook", content=b"{}")
        assert r.status_code == 400

    def test_rejects_bad_signature(self, client, stripe_env):
        r = client.post(
            "/api/billing/webhook",
            content=b"{}",
            headers={"Stripe-Signature": "t=123,v1=deadbeef"},
        )
        assert r.status_code == 400

    def test_rejects_malformed_json(self, client, stripe_env):
        body = b"not json"
        r = client.post(
            "/api/billing/webhook",
            content=body,
            headers={"Stripe-Signature": _sign(body)},
        )
        assert r.status_code == 400

    def test_subscription_created_upserts_row(self, client, db, stripe_env):
        r = _post_webhook(client, _subscription_event())
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "processed"
        row = db["subscriptions"].find_one({"stripe_subscription_id": "sub_test_1"})
        assert row is not None
        assert row["plan"] == "pro"
        assert row["status"] == "active"
        assert row["user_id"] == "user_123"

    def test_webhook_is_idempotent(self, client, db, stripe_env):
        event = _subscription_event(event_id="evt_dup_1")
        r1 = _post_webhook(client, event)
        r2 = _post_webhook(client, event)
        assert r1.json()["status"] == "processed"
        assert r2.json()["status"] == "already_processed"
        assert db["subscriptions"].count_documents({}) == 1
        assert db["stripe_events"].count_documents({"event_id": "evt_dup_1"}) == 1

    def test_subscription_deleted_cancels(self, client, db, stripe_env):
        _post_webhook(client, _subscription_event())
        deleted = _subscription_event(
            event_id="evt_del_1", event_type="customer.subscription.deleted"
        )
        r = _post_webhook(client, deleted)
        assert r.status_code == 200
        row = db["subscriptions"].find_one({"stripe_subscription_id": "sub_test_1"})
        assert row["status"] == "canceled"
        assert row["plan"] == "free"

    def test_payment_failed_marks_past_due(self, client, db, stripe_env):
        _post_webhook(client, _subscription_event())
        failed = {
            "id": "evt_fail_1",
            "type": "invoice.payment_failed",
            "data": {"object": {"id": "in_1", "subscription": "sub_test_1"}},
        }
        r = _post_webhook(client, failed)
        assert r.status_code == 200
        row = db["subscriptions"].find_one({"stripe_subscription_id": "sub_test_1"})
        assert row["status"] == "past_due"

    def test_unknown_event_type_recorded_without_side_effects(
        self, client, db, stripe_env
    ):
        event = {"id": "evt_unknown_1", "type": "coupon.created", "data": {"object": {}}}
        r = _post_webhook(client, event)
        assert r.status_code == 200
        assert r.json()["status"] == "processed"
        assert db["subscriptions"].count_documents({}) == 0
        rec = db["stripe_events"].find_one({"event_id": "evt_unknown_1"})
        assert rec["processed"] is True

    def test_event_without_user_link_fabricates_nothing(self, client, db, stripe_env):
        event = _subscription_event(event_id="evt_nouser_1", user_key=None)
        r = _post_webhook(client, event)
        assert r.status_code == 200
        assert db["subscriptions"].count_documents({}) == 0


# ---------------------------------------------------------------------------
# POST /api/billing/checkout
# ---------------------------------------------------------------------------


class TestCheckout:
    def test_501_when_stripe_unconfigured(self, client, session_env):
        _signin(client)
        r = client.post("/api/billing/checkout", json={"plan": "pro"})
        assert r.status_code == 501

    def test_401_when_anonymous(self, client, stripe_env):
        r = client.post("/api/billing/checkout", json={"plan": "pro"})
        assert r.status_code == 401

    def test_422_for_free_and_unknown_plans(self, client, stripe_env, session_env):
        _signin(client)
        for bad in ("free", "enterprise"):
            r = client.post("/api/billing/checkout", json={"plan": bad})
            assert r.status_code == 422, bad

    def test_501_when_price_missing(self, client, session_env, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", STRIPE_SECRET)
        _signin(client)
        r = client.post("/api/billing/checkout", json={"plan": "pro"})
        assert r.status_code == 501

    def test_checkout_ok(self, client, db, stripe_env, session_env, monkeypatch):
        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            return {"id": "cs_test_1", "url": "https://checkout.stripe.com/pay/cs_test_1"}

        monkeypatch.setattr(
            "app.routers.billing.create_checkout_session", fake_create
        )
        _signin(client, sub="user@example.com", email="user@example.com")
        r = client.post("/api/billing/checkout", json={"plan": "pro"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["checkout_url"].startswith("https://checkout.stripe.com/")
        assert captured["price_id"] == "price_pro_123"
        assert captured["user_key"] == "user@example.com"
        assert captured["customer_email"] == "user@example.com"
        assert "/pricing" in captured["success_url"]

    def test_checkout_stripe_error_is_502(
        self, client, stripe_env, session_env, monkeypatch
    ):
        def boom(**kwargs):
            raise StripeError("card declined by test double")

        from app.billing import stripe_client as sc

        monkeypatch.setattr(sc, "create_checkout_session", boom)
        monkeypatch.setattr(
            "app.routers.billing.create_checkout_session", boom
        )
        _signin(client)
        r = client.post("/api/billing/checkout", json={"plan": "starter"})
        assert r.status_code == 502


# ---------------------------------------------------------------------------
# POST /api/billing/portal
# ---------------------------------------------------------------------------


class TestPortal:
    def test_404_without_subscription(self, client, stripe_env, session_env, db):
        _signin(client, sub="user_123")
        r = client.post("/api/billing/portal")
        assert r.status_code == 404

    def test_portal_ok(self, client, db, stripe_env, session_env, monkeypatch):
        _insert_subscription(db, "user_123", plan="pro", status="active")

        def fake_portal(**kwargs):
            assert kwargs["customer_id"] == "cus_test_1"
            return {"id": "bps_1", "url": "https://billing.stripe.com/session/bps_1"}

        monkeypatch.setattr("app.routers.billing.create_portal_session", fake_portal)
        _signin(client, sub="user_123")
        r = client.post("/api/billing/portal")
        assert r.status_code == 200
        assert r.json()["portal_url"].startswith("https://billing.stripe.com/")

    def test_portal_401_anonymous(self, client, stripe_env):
        r = client.post("/api/billing/portal")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/billing/export/picks — server-side plan gate
# ---------------------------------------------------------------------------


class TestExport:
    def test_401_anonymous(self, client):
        r = client.get("/api/billing/export/picks")
        assert r.status_code == 401

    def test_402_for_free_user(self, client, db, session_env):
        _signin(client, sub="user_123")
        r = client.get("/api/billing/export/picks")
        assert r.status_code == 402

    def test_ok_for_starter_with_rows(self, client, db, session_env):
        _signin(client, sub="user_123")
        _insert_subscription(db, "user_123", plan="starter", status="active")
        db["pick_results"].insert_one(
            {
                "prediction_id": "p1",
                "game_id": "2024_01_KC_BUF",
                "season": 2024,
                "week": 1,
                "home_team": "KC",
                "away_team": "BUF",
                "model_version": "MNB-NFL-2026.01",
                "market": "moneyline",
                "side": "home",
                "confidence": "high",
                "model_probability": 0.7,
                "predicted_winner": "KC",
                "result": "win",
                "actual_home_score": 27,
                "actual_away_score": 24,
                "actual_winner": "KC",
                "brier_contribution": 0.09,
                "settled_at": datetime.now(timezone.utc),
            }
        )
        r = client.get("/api/billing/export/picks")
        assert r.status_code == 200, r.text
        assert "text/csv" in r.headers["content-type"]
        assert "prediction_id" in r.text.splitlines()[0]
        assert "p1" in r.text
        assert "MNB-NFL-2026.01" in r.text

    def test_empty_ledger_is_header_only(self, client, db, session_env):
        _signin(client, sub="user_123")
        _insert_subscription(db, "user_123", plan="pro", status="active")
        r = client.get("/api/billing/export/picks")
        assert r.status_code == 200
        lines = [ln for ln in r.text.splitlines() if ln.strip()]
        assert len(lines) == 1  # header only — honest empty state


# ---------------------------------------------------------------------------
# /api/billing/alerts — server-side plan gate
# ---------------------------------------------------------------------------


class TestAlerts:
    def test_401_anonymous(self, client):
        assert client.get("/api/billing/alerts").status_code == 401
        assert (
            client.post("/api/billing/alerts", json={"name": "x"}).status_code == 401
        )

    def test_402_for_free_user(self, client, db, session_env):
        _signin(client, sub="user_123")
        assert client.get("/api/billing/alerts").status_code == 402
        r = client.post("/api/billing/alerts", json={"name": "KC edges"})
        assert r.status_code == 402

    def test_crud_for_starter(self, client, db, session_env):
        _signin(client, sub="user_123")
        _insert_subscription(db, "user_123", plan="starter", status="active")

        r = client.get("/api/billing/alerts")
        assert r.json()["alerts"] == []

        r = client.post(
            "/api/billing/alerts",
            json={"name": "KC edges", "team": "KC", "edge_threshold": 0.05},
        )
        assert r.status_code == 200, r.text
        alert = r.json()["alert"]
        assert alert["team"] == "KC"

        r = client.get("/api/billing/alerts")
        assert len(r.json()["alerts"]) == 1

        r = client.delete(f"/api/billing/alerts/{alert['alert_id']}")
        assert r.status_code == 200
        assert client.get("/api/billing/alerts").json()["alerts"] == []

    def test_invalid_team_rejected(self, client, db, session_env):
        _signin(client, sub="user_123")
        _insert_subscription(db, "user_123", plan="starter", status="active")
        r = client.post("/api/billing/alerts", json={"name": "x", "team": "XX"})
        assert r.status_code == 422

    def test_cannot_delete_another_users_alert(self, client, db, session_env):
        _signin(client, sub="user_123")
        _insert_subscription(db, "user_123", plan="starter", status="active")
        db["alerts"].insert_one(
            {
                "alert_id": "alert_other",
                "user_id": "user_999",
                "name": "theirs",
                "created_at": datetime.now(timezone.utc),
            }
        )
        r = client.delete("/api/billing/alerts/alert_other")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Identity helpers (unit)
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_get_plan_free_without_db(self):
        assert billing_identity.get_plan(None, "user_123") == "free"

    def test_get_plan_free_when_anonymous(self, db):
        assert billing_identity.get_plan(db, None) == "free"

    def test_get_plan_reads_active_subscription(self, db):
        _insert_subscription(db, "user_123", plan="agency", status="trialing")
        assert billing_identity.get_plan(db, "user_123") == "agency"

    def test_quota_allows_below_limit(self, db):
        billing_identity.check_numby_quota(db, "user:x", "free")  # no raise

    def test_quota_blocks_at_limit(self, db):
        from fastapi import HTTPException

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        db["usage_counters"].insert_one(
            {"key": "user:x", "day": today, "numby_questions": 20}
        )
        with pytest.raises(HTTPException) as excinfo:
            billing_identity.check_numby_quota(db, "user:x", "free")
        assert excinfo.value.status_code == 402

    def test_record_usage_increments(self, db):
        billing_identity.record_numby_usage(db, "user:x")
        billing_identity.record_numby_usage(db, "user:x")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        doc = db["usage_counters"].find_one({"key": "user:x", "day": today})
        assert doc["numby_questions"] == 2


# ---------------------------------------------------------------------------
# Numby quota enforcement (integration through the chat endpoint)
# ---------------------------------------------------------------------------


class TestNumbyQuota:
    @pytest.fixture()
    def numby_client(self, db, monkeypatch):
        monkeypatch.setattr(billing_router, "_db", lambda: db)
        monkeypatch.setattr(numby_router, "_db", lambda: db)
        from app.main import app

        return TestClient(app)

    def _set_usage(self, db, key, n):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        db["usage_counters"].update_one(
            {"key": key, "day": today},
            {"$set": {"key": key, "day": today, "numby_questions": n}},
            upsert=True,
        )

    def test_anonymous_quota_enforced(self, numby_client, db):
        self._set_usage(db, "anon:sess-quota", 20)
        r = numby_client.post(
            "/api/numby/chat", json={"message": "hi", "session_id": "sess-quota"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "quota_exceeded"
        assert body["upgrade_url"] == "/pricing"

    def test_below_limit_answers(self, numby_client, db):
        self._set_usage(db, "anon:sess-ok", 3)
        r = numby_client.post(
            "/api/numby/chat", json={"message": "hi", "session_id": "sess-ok"}
        )
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_signed_in_starter_gets_higher_limit(
        self, numby_client, db, session_env
    ):
        _signin(numby_client, sub="user_123")
        _insert_subscription(db, "user_123", plan="starter", status="active")
        # At the free limit (20) — a starter (limit 100) still gets answers.
        self._set_usage(db, "user:user_123", 20)
        r = numby_client.post("/api/numby/chat", json={"message": "hi"})
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_usage_recorded_after_answer(self, numby_client, db):
        r = numby_client.post(
            "/api/numby/chat", json={"message": "hi", "session_id": "sess-count"}
        )
        assert r.json()["status"] == "ok"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        doc = db["usage_counters"].find_one({"key": "anon:sess-count", "day": today})
        assert doc is not None and doc["numby_questions"] >= 1
