"""Phase 8 affiliate tests: partners, tracked redirects, CTA gating,
disclosure, admin CRUD, conversion stats, and the no-hardcoded-URL rule."""

import os
import re
from datetime import datetime, timezone

import mongomock
import pytest
from fastapi.testclient import TestClient

from app import routers as _routers  # noqa: F401  (ensures package import)
from app.database import ensure_indexes
from app.routers import admin as admin_router
from app.routers import affiliate as affiliate_router

ADMIN_HEADERS = {"X-Admin-Key": "test-admin-key"}
PARTNER_URL = "https://sportsbook.example/aff/go?campaign=mbn_001"


@pytest.fixture()
def db():
    client = mongomock.MongoClient()
    database = client["money_by_numbers"]
    ensure_indexes(database)
    return database


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setattr(affiliate_router, "_db", lambda: db)
    monkeypatch.setattr(admin_router, "_db", lambda: db)
    from app.main import app

    return TestClient(app)


@pytest.fixture()
def admin_env(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")


def _create_partner(client, url=PARTNER_URL, name="Example Sportsbook", **kw):
    body = {"name": name, "affiliate_url": url}
    body.update(kw)
    r = client.post("/api/admin/sportsbooks", json=body, headers=ADMIN_HEADERS)
    assert r.status_code == 200, r.text
    return r.json()["sportsbook"]


# ---------------------------------------------------------------------------
# Tracked redirect
# ---------------------------------------------------------------------------


class TestTrackedReferral:
    def test_redirect_logs_click_and_302s_to_configured_url(
        self, client, db, admin_env
    ):
        partner = _create_partner(client)
        r = client.get(
            f"/r/{partner['sportsbook_id']}?cta_location=best_bets&game_id=2026_01_NE_SEA",
            follow_redirects=False,
        )
        assert r.status_code == 302
        # Destination is the configured URL verbatim — never invented.
        assert r.headers["location"] == PARTNER_URL
        clicks = list(db["affiliate_clicks"].find({}))
        assert len(clicks) == 1
        click = clicks[0]
        assert click["destination"] == PARTNER_URL
        assert click["sportsbook"] == "Example Sportsbook"
        assert click["cta_location"] == "best_bets"
        assert click["game_id"] == "2026_01_NE_SEA"
        assert click["click_id"]

    def test_unknown_sportsbook_404s(self, client, db):
        r = client.get("/r/no-such-book", follow_redirects=False)
        assert r.status_code == 404

    def test_partner_without_url_never_redirects(self, client, db, admin_env):
        partner = _create_partner(client, url=None)
        r = client.get(f"/r/{partner['sportsbook_id']}", follow_redirects=False)
        assert r.status_code == 404
        assert "no affiliate URL" in r.json()["detail"]
        assert db["affiliate_clicks"].count_documents({}) == 0

    def test_inactive_partner_never_redirects(self, client, db, admin_env):
        partner = _create_partner(client)
        sid = partner["sportsbook_id"]
        r = client.delete(f"/api/admin/sportsbooks/{sid}", headers=ADMIN_HEADERS)
        assert r.status_code == 200
        r = client.get(f"/r/{sid}", follow_redirects=False)
        assert r.status_code == 404
        assert db["affiliate_clicks"].count_documents({}) == 0

    def test_click_context_fields_recorded(self, client, db, admin_env):
        partner = _create_partner(client)
        client.get(
            f"/r/{partner['sportsbook_id']}?market=moneyline&side=home&edge=0.07"
            f"&model_version=MNB-NFL-2026.01&session_id=anon-123",
            follow_redirects=False,
        )
        click = db["affiliate_clicks"].find_one({})
        assert click["session_id"] == "anon-123"
        assert click["edge"] == 0.07
        assert click["model_version"] == "MNB-NFL-2026.01"


# ---------------------------------------------------------------------------
# CTA gating (server-side)
# ---------------------------------------------------------------------------


class TestCtaGating:
    def test_no_partners_means_ctas_disabled(self, client, db):
        body = client.get("/api/affiliate/cta-status").json()
        assert body["ctas_enabled"] is False
        assert body["sportsbooks"] == []

    def test_partner_without_url_keeps_ctas_disabled(self, client, db, admin_env):
        _create_partner(client, url=None)
        body = client.get("/api/affiliate/cta-status").json()
        assert body["ctas_enabled"] is False

    def test_active_partner_with_url_enables_ctas(self, client, db, admin_env):
        partner = _create_partner(client)
        body = client.get("/api/affiliate/cta-status").json()
        assert body["ctas_enabled"] is True
        assert body["sportsbooks"] == [
            {"sportsbook_id": partner["sportsbook_id"], "name": "Example Sportsbook"}
        ]
        # Affiliate URLs are never leaked to the public gate response.
        assert "affiliate_url" not in str(body["sportsbooks"])
        assert PARTNER_URL not in str(body)

    def test_deactivated_partner_disables_ctas(self, client, db, admin_env):
        partner = _create_partner(client)
        client.delete(
            f"/api/admin/sportsbooks/{partner['sportsbook_id']}", headers=ADMIN_HEADERS
        )
        body = client.get("/api/affiliate/cta-status").json()
        assert body["ctas_enabled"] is False


# ---------------------------------------------------------------------------
# Disclosure
# ---------------------------------------------------------------------------


class TestDisclosure:
    def test_default_disclosure(self, client):
        body = client.get("/api/affiliate/disclosure").json()
        assert body["configured"] is False
        assert "compensation" in body["disclosure"]
        assert "sportsbook" in body["disclosure"].lower()

    def test_configured_disclosure_text(self, client, monkeypatch):
        monkeypatch.setenv("AFFILIATE_DISCLOSURE_TEXT", "Custom disclosure text.")
        body = client.get("/api/affiliate/disclosure").json()
        assert body["configured"] is True
        assert body["disclosure"] == "Custom disclosure text."


# ---------------------------------------------------------------------------
# Admin gate + CRUD
# ---------------------------------------------------------------------------


class TestAdminGate:
    def test_no_admin_key_configured_is_501(self, client, db):
        r = client.get("/api/admin/sportsbooks", headers=ADMIN_HEADERS)
        assert r.status_code == 501

    def test_wrong_key_is_403(self, client, db, admin_env):
        r = client.get("/api/admin/sportsbooks", headers={"X-Admin-Key": "wrong"})
        assert r.status_code == 403

    def test_missing_key_is_403(self, client, db, admin_env):
        r = client.get("/api/admin/sportsbooks")
        assert r.status_code == 403


class TestAdminCrud:
    def test_partners_table_starts_empty(self, client, db, admin_env):
        body = client.get("/api/admin/sportsbooks", headers=ADMIN_HEADERS).json()
        assert body["sportsbooks"] == []
        assert "no sportsbook partnerships" in body["note"]

    def test_create_rejects_non_web_url(self, client, db, admin_env):
        r = client.post(
            "/api/admin/sportsbooks",
            json={"name": "Shady Book", "affiliate_url": "javascript:alert(1)"},
            headers=ADMIN_HEADERS,
        )
        assert r.status_code == 422

    def test_create_list_patch_flow(self, client, db, admin_env):
        partner = _create_partner(client)
        assert partner["cta_enabled"] is True

        # Clearing the URL disables CTAs without deleting the partner.
        r = client.patch(
            f"/api/admin/sportsbooks/{partner['sportsbook_id']}",
            json={"affiliate_url": None},
            headers=ADMIN_HEADERS,
        )
        assert r.status_code == 200
        assert r.json()["sportsbook"]["affiliate_url"] is None
        assert client.get("/api/affiliate/cta-status").json()["ctas_enabled"] is False

        # Unknown id -> 404.
        r = client.patch(
            "/api/admin/sportsbooks/nope", json={"name": "X"}, headers=ADMIN_HEADERS
        )
        assert r.status_code == 404

    def test_delete_deactivates_and_preserves_history(self, client, db, admin_env):
        partner = _create_partner(client)
        sid = partner["sportsbook_id"]
        client.get(f"/r/{sid}", follow_redirects=False)
        r = client.delete(f"/api/admin/sportsbooks/{sid}", headers=ADMIN_HEADERS)
        assert r.json()["status"] == "deactivated"
        # Partner row still exists (inactive); the click is still logged.
        row = db["sportsbooks"].find_one({"sportsbook_id": sid})
        assert row["active"] is False
        assert db["affiliate_clicks"].count_documents({}) == 1


# ---------------------------------------------------------------------------
# Conversions + stats honesty
# ---------------------------------------------------------------------------


class TestConversionsAndStats:
    def _record_click_and_conversion(self, client, db, admin_env, revenue=25.0):
        partner = _create_partner(client)
        client.get(f"/r/{partner['sportsbook_id']}", follow_redirects=False)
        click = db["affiliate_clicks"].find_one({})
        body = {
            "sportsbook_id": partner["sportsbook_id"],
            "click_id": click["click_id"],
            "converted_at": datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc).isoformat(),
            "revenue_amount": revenue,
            "notes": "September affiliate report",
        }
        r = client.post("/api/admin/conversions", json=body, headers=ADMIN_HEADERS)
        assert r.status_code == 200, r.text
        return partner

    def test_stats_honest_empty_states(self, client, db, admin_env):
        body = client.get("/api/admin/affiliate/stats", headers=ADMIN_HEADERS).json()
        assert body["clicks"] == {
            "total": 0,
            "has_data": False,
            "note": "no clicks recorded yet",
        }
        assert body["conversions"]["total"] == 0
        assert body["conversions"]["conversion_rate"] is None
        # Revenue unknown — never presented as zero performance.
        assert body["revenue"]["total"] is None
        assert body["revenue"]["has_data"] is False
        assert "not zero" in body["revenue"]["note"]

    def test_stats_from_real_data(self, client, db, admin_env):
        partner = self._record_click_and_conversion(client, db, admin_env, revenue=25.0)
        body = client.get("/api/admin/affiliate/stats", headers=ADMIN_HEADERS).json()
        assert body["clicks"]["total"] == 1
        assert body["conversions"]["total"] == 1
        assert body["conversions"]["conversion_rate"] == 1.0
        assert body["revenue"]["total"] == 25.0
        assert body["revenue"]["has_data"] is True
        per_book = body["by_sportsbook"][0]
        assert per_book["name"] == "Example Sportsbook"
        assert per_book["clicks"] == 1
        assert per_book["conversions"] == 1
        assert per_book["revenue"] == 25.0

    def test_conversion_without_revenue_stays_unknown(self, client, db, admin_env):
        self._record_click_and_conversion(client, db, admin_env, revenue=None)
        body = client.get("/api/admin/affiliate/stats", headers=ADMIN_HEADERS).json()
        assert body["revenue"]["total"] is None
        assert body["revenue"]["has_data"] is False

    def test_conversion_requires_known_sportsbook(self, client, db, admin_env):
        r = client.post(
            "/api/admin/conversions",
            json={
                "sportsbook_id": "nope",
                "converted_at": datetime.now(timezone.utc).isoformat(),
            },
            headers=ADMIN_HEADERS,
        )
        assert r.status_code == 404

    def test_conversions_list_empty_note(self, client, db, admin_env):
        body = client.get("/api/admin/conversions", headers=ADMIN_HEADERS).json()
        assert body["conversions"] == []
        assert "no conversions recorded yet" in body["note"]


# ---------------------------------------------------------------------------
# No hardcoded affiliate URLs anywhere in shipped code
# ---------------------------------------------------------------------------

AFFILIATE_URL_MARKERS = (
    "affiliate",
    "aff_id",
    "affid",
    "referral",
    "ascsubtag",
    "clickid",
)


def test_no_hardcoded_affiliate_urls_in_shipped_code():
    """Fail if any shipped source file embeds an affiliate-looking URL.

    Partner URLs live in the sportsbooks collection (admin-configured).
    Scans backend/app and frontend source (excluding tests and docs).
    """
    repo = os.path.join(os.path.dirname(__file__), "..")
    url_re = re.compile(r"https?://[^\s\"'`)>\]]+")
    offenders = []
    for root, dirs, files in os.walk(repo):
        # Skip everything that is not shipped product source.
        if any(
            skip in root
            for skip in (
                "node_modules",
                ".next",
                "__pycache__",
                ".pytest_cache",
                "/tests",
                "/docs",
                ".git/",
            )
        ):
            continue
        for fname in files:
            if not fname.endswith((".py", ".ts", ".tsx", ".js", ".jsx")):
                continue
            path = os.path.join(root, fname)
            with open(path, encoding="utf-8", errors="ignore") as fh:
                for lineno, line in enumerate(fh, 1):
                    for match in url_re.finditer(line):
                        url = match.group(0).rstrip(".,;")
                        lowered = url.lower()
                        if "example.com" in lowered:
                            continue  # test/example fixtures only
                        if any(m in lowered for m in AFFILIATE_URL_MARKERS):
                            offenders.append(f"{path}:{lineno}: {url}")
    assert not offenders, (
        "Hardcoded affiliate-looking URLs found in shipped code:\n"
        + "\n".join(offenders)
    )
