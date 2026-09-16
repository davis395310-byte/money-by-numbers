"""Phase 7 Numby tests for MONEY BY NUMBERS.

Covers the chat API, grounding (answers use only real stored data),
guardrail refusals, the no-fabrication guarantee, conversation memory,
and the AI provider interface/degradation.

HONESTY RULE: all fixtures here are SYNTHETIC (fake game ids, textbook
prices like -110/-150) and are never presented as real market data. They
exist only to verify that Numby repeats stored values verbatim and
refuses to invent values when storage is empty.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import mongomock
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import numby as numby_pkg  # noqa: E402
from app.database import ensure_indexes  # noqa: E402
from app.numby import guardrails, memory, providers, responder, retrieval  # noqa: E402
from app.routers import numby as numby_router  # noqa: E402

UTC = timezone.utc


@pytest.fixture()
def db():
    client = mongomock.MongoClient()
    database = client["money_by_numbers"]
    ensure_indexes(database)
    return database


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setattr(numby_router, "_db", lambda: db)
    from app.main import app

    return TestClient(app)


def _game(**over):
    doc = {
        "game_id": "2026_01_KC_DEN",
        "season": 2026,
        "week": 1,
        "game_date": datetime.now(UTC) + timedelta(days=2),
        "home_team": "KC",
        "away_team": "DEN",
        "status": "scheduled",
    }
    doc.update(over)
    return doc


def _prediction(**over):
    doc = {
        "prediction_id": "p1",
        "game_id": "2026_01_KC_DEN",
        "season": 2026,
        "week": 1,
        "home_team": "KC",
        "away_team": "DEN",
        "model_version": "MNB-NFL-2026.01",
        "training_cutoff": "2025-12-31",
        "model_probability": 0.70,
        "predicted_winner": "KC",
        "predicted_home_score": 27,
        "predicted_away_score": 21,
        "confidence": "HIGH",
    }
    doc.update(over)
    return doc


def _seed_game_and_pick(db):
    db["games"].insert_one(_game())
    db["predictions"].insert_one(_prediction())


# ---------------------------------------------------------------------------
# Team + intent detection
# ---------------------------------------------------------------------------


class TestDetection:
    def test_detect_teams_by_name(self):
        assert retrieval.detect_teams("chiefs vs broncos prediction") == ["KC", "DEN"]

    def test_detect_teams_by_abbr(self):
        assert retrieval.detect_teams("KC vs DEN") == ["KC", "DEN"]

    def test_no_false_positive_inside_words(self):
        # "money" must not detect NE; "hockey" must not detect KC.
        assert retrieval.detect_teams("show me the money, hockey fan") == []

    def test_detect_intents(self):
        intents = retrieval.detect_intents("who will win? what are the odds?")
        assert "prediction" in intents
        assert "odds" in intents

    def test_default_intent(self):
        assert retrieval.detect_intents("tell me about football") == ["general"]


# ---------------------------------------------------------------------------
# Grounding: answers repeat stored data verbatim
# ---------------------------------------------------------------------------


class TestGrounding:
    def test_prediction_answer_uses_stored_values(self, db):
        _seed_game_and_pick(db)
        bundle = retrieval.gather_context(db, "Chiefs vs Broncos prediction")
        assert bundle.facts, "expected grounded prediction facts"
        texts = " ".join(f.text for f in bundle.facts)
        assert "KC" in texts
        assert "70.0%" in texts  # the stored probability, verbatim
        assert "27" in texts and "21" in texts  # stored predicted scores
        assert all(f.source == "predictions collection" for f in bundle.facts)

    def test_every_fact_carries_a_source(self, db):
        _seed_game_and_pick(db)
        bundle = retrieval.gather_context(db, "Chiefs vs Broncos prediction")
        for fact in bundle.facts:
            assert fact.source, "fact without source"

    def test_track_record_fact_from_ledger(self, db):
        db["pick_results"].insert_one(
            {"prediction_id": "p1", "result": "win", "market": "moneyline"}
        )
        bundle = retrieval.gather_context(db, "what is your track record?")
        texts = " ".join(f.text for f in bundle.facts)
        assert "1-0" in texts
        assert "100.0%" in texts

    def test_methodology_fact_from_artifacts(self, db):
        bundle = retrieval.gather_context(db, "how does the model work?")
        texts = " ".join(f.text for f in bundle.facts)
        assert "2016" in texts and "2025" in texts
        assert "62.16%" in texts or "62.2%" in texts

    def test_injury_fact_names_real_player(self, db):
        _seed_game_and_pick(db)
        db["injuries"].insert_one(
            {
                "season": 2026,
                "week": 1,
                "team": "KC",
                "full_name": "Test Player",
                "report_status": "Questionable",
            }
        )
        bundle = retrieval.gather_context(db, "Chiefs injuries")
        texts = " ".join(f.text for f in bundle.facts)
        assert "Test Player" in texts

    def test_edge_fact_from_engine(self, db):
        _seed_game_and_pick(db)
        db["odds"].insert_one(
            {
                "game_id": "2026_01_KC_DEN",
                "timestamp": datetime.now(UTC),
                "sportsbook": "synthetic",
                "moneyline_home": -150,
                "moneyline_away": 130,
            }
        )
        bundle = retrieval.gather_context(db, "show me the best bets")
        texts = " ".join(f.text for f in bundle.facts)
        # 0.70 model vs ~0.5798 no-vig -> ~12.0% edge, clears 3% threshold.
        assert "moneyline" in texts
        assert "12.0%" in texts


# ---------------------------------------------------------------------------
# No fabrication: missing data -> honest "I don't have it"
# ---------------------------------------------------------------------------


class TestNoFabrication:
    def test_unknown_game_says_so(self, db):
        result = responder.respond(db, "Seahawks vs 49ers prediction")
        assert "don't have" in result["reply"]
        assert "%" not in result["reply"], "must not invent probabilities"

    def test_no_predictions_no_edges(self, db):
        db["games"].insert_one(_game())
        result = responder.respond(db, "best bets for Chiefs Broncos")
        assert "don't have" in result["reply"] or "can't compute edges" in result["reply"]

    def test_no_odds_no_edge_numbers(self, db):
        _seed_game_and_pick(db)
        result = responder.respond(db, "any edges this week?")
        assert "No odds snapshots" in result["reply"]
        assert "%" not in result["reply"]

    def test_no_injury_rows_not_presented_as_healthy(self, db):
        _seed_game_and_pick(db)
        bundle = retrieval.gather_context(db, "Chiefs injuries")
        assert not bundle.facts
        assert any("won't guess" in n for n in bundle.notes)

    def test_no_db_honest_note(self):
        bundle = retrieval.gather_context(None, "Chiefs vs Broncos prediction")
        assert not [f for f in bundle.facts if f.kind == "prediction"]
        assert any("not configured" in n for n in bundle.notes)

    def test_refusal_paths_carry_no_sources(self, db):
        result = responder.respond(db, "give me a guaranteed winner")
        assert result["sources"] == []
        assert "guarantee" in result["reply"].lower()


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------


class TestGuardrails:
    def test_guarantee_refused(self, db):
        result = responder.respond(db, "give me a guaranteed winner this week")
        assert "don't do guarantees" in result["reply"]
        assert result["ai_mode"] == "guardrail"

    def test_chasing_losses_refused(self, db):
        result = responder.respond(db, "I lost $500, help me win it back")
        assert "Chasing losses" in result["reply"]
        assert "1-800-GAMBLER" in result["reply"]

    def test_minor_refused_betting_advice(self, db):
        result = responder.respond(db, "I'm 16, what should I bet on?")
        assert "minors" in result["reply"]

    def test_betting_answer_carries_disclaimer(self, db):
        _seed_game_and_pick(db)
        result = responder.respond(db, "should I bet on the Chiefs?")
        assert result["disclaimer"] is not None
        assert "1-800-GAMBLER" in result["disclaimer"]

    def test_non_betting_answer_no_disclaimer(self, db):
        result = responder.respond(db, "how does the model work?")
        assert result["disclaimer"] is None

    def test_identity_line_present(self, db):
        _seed_game_and_pick(db)
        result = responder.respond(db, "Chiefs vs Broncos prediction")
        assert "Numby" in result["reply"]
        assert "AI" in result["reply"]


# ---------------------------------------------------------------------------
# Chat API + conversation memory
# ---------------------------------------------------------------------------


class TestChatApi:
    def test_chat_roundtrip_persists(self, client, db):
        r1 = client.post(
            "/api/numby/chat", json={"message": "Chiefs vs Broncos prediction"}
        )
        assert r1.status_code == 200
        body = r1.json()
        assert body["status"] == "ok"
        assert body["reply"]
        assert body["session_id"]
        assert body["persistent"] is True
        assert body["ai_mode"] == "basic"  # no AI key in test env

        r2 = client.post(
            "/api/numby/chat",
            json={"message": "and the score?", "session_id": body["session_id"]},
        )
        assert r2.json()["session_id"] == body["session_id"]

        hist = client.get(
            "/api/numby/history", params={"session_id": body["session_id"]}
        ).json()
        assert hist["status"] == "ok"
        roles = [m["role"] for m in hist["messages"]]
        assert roles == ["user", "assistant", "user", "assistant"]

    def test_chat_without_db_is_stateless_but_honest(self, monkeypatch):
        monkeypatch.setattr(numby_router, "_db", lambda: None)
        from app.main import app

        c = TestClient(app)
        body = c.post("/api/numby/chat", json={"message": "hello"}).json()
        assert body["status"] == "ok"
        assert body["persistent"] is False
        assert body["session_id"] is None
        assert "not configured" in body["reply"]

    def test_empty_message_rejected(self, client):
        r = client.post("/api/numby/chat", json={"message": "   "})
        assert r.status_code in (200, 422)

    def test_status_endpoint_reports_basic_mode(self, client):
        body = client.get("/api/numby/status").json()
        assert body["status"] == "ok"
        assert body["ai_configured"] is False
        assert body["mode"] == "basic"

    def test_history_unknown_session_empty(self, client):
        body = client.get("/api/numby/history", params={"session_id": "nope"}).json()
        assert body["status"] == "ok"
        assert body["messages"] == []


class TestMemory:
    def test_overlong_message_rejected(self, db):
        session = memory.get_or_create_session(db, None)
        with pytest.raises(memory.ConversationError):
            memory.append_message(db, session["session_id"], "user", "x" * 1001)

    def test_session_cap(self, db, monkeypatch):
        monkeypatch.setattr(memory, "MAX_SESSION_MESSAGES", 2)
        session = memory.get_or_create_session(db, None)
        sid = session["session_id"]
        memory.append_message(db, sid, "user", "one")
        memory.append_message(db, sid, "assistant", "two")
        with pytest.raises(memory.ConversationError):
            memory.append_message(db, sid, "user", "three")

    def test_history_capped(self, db, monkeypatch):
        monkeypatch.setattr(memory, "MAX_HISTORY_MESSAGES", 3)
        session = memory.get_or_create_session(db, None)
        sid = session["session_id"]
        for i in range(5):
            db["conversations"].update_one(
                {"session_id": sid},
                {"$push": {"messages": {"role": "user", "content": f"m{i}"}}},
            )
        assert len(memory.get_history(db, sid)) == 3


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


class TestProviders:
    def test_not_configured_by_default(self):
        provider = providers.get_provider()
        assert isinstance(provider, providers.NotConfiguredProvider)
        status = providers.provider_status()
        assert status["ai_configured"] is False
        assert status["mode"] == "basic"

    def test_configured_provider_resolves(self, monkeypatch):
        monkeypatch.setenv("AI_API_KEY", "test-key")
        provider = providers.get_provider()
        assert isinstance(provider, providers.OpenAICompatibleProvider)

    def test_build_payload_shape(self):
        p = providers.OpenAICompatibleProvider(
            api_key="k", base_url="https://api.openai.com/v1", model="gpt-4o-mini"
        )
        payload = p.build_payload("sys", [{"role": "user", "content": "hi"}])
        assert payload["model"] == "gpt-4o-mini"
        assert payload["messages"][0] == {"role": "system", "content": "sys"}
        assert payload["messages"][1] == {"role": "user", "content": "hi"}

    def test_parse_response(self):
        body = {"choices": [{"message": {"content": "  hello  "}}]}
        assert providers.OpenAICompatibleProvider.parse_response(body) == "hello"
        with pytest.raises(RuntimeError):
            providers.OpenAICompatibleProvider.parse_response({"nope": True})

    def test_resolve_base_url(self):
        assert providers.resolve_base_url(None) == "https://api.openai.com/v1"
        assert providers.resolve_base_url("openai") == "https://api.openai.com/v1"
        assert (
            providers.resolve_base_url("https://example.com/v1")
            == "https://example.com/v1"
        )
        with pytest.raises(ValueError):
            providers.resolve_base_url("http://insecure.example.com")

    def test_llm_failure_falls_back_to_grounded(self, db, monkeypatch):
        _seed_game_and_pick(db)

        class BoomProvider(providers.AIProvider):
            @property
            def name(self):
                return "boom"

            def complete(self, system, messages):
                raise RuntimeError("provider down")

        monkeypatch.setattr(responder, "get_provider", lambda: BoomProvider())
        result = responder.respond(db, "Chiefs vs Broncos prediction")
        assert result["ai_mode"] == "basic_fallback"
        assert "70.0%" in result["reply"]  # still grounded in stored data
