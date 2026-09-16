"""Playoff simulator tests for MONEY BY NUMBERS.

Covers ``backend/app/playoffs/`` (bracket construction, tiebreakers,
Monte Carlo engines) and ``backend/app/routers/playoffs.py``.

HONESTY RULE: every fixture here is SYNTHETIC (invented team records,
textbook Elo ratings, fabricated game lists) and exists only to verify
the math, the bracket rules, and the honest empty states. Nothing here
is presented as real NFL data.
"""

import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.playoffs import (  # noqa: E402
    DEFAULT_ADJUSTMENTS,
    PlayoffSimError,
    apply_tiebreakers,
    build_bracket_rounds,
    championship_matchup,
    divisional_matchups,
    playoff_format,
    resolve_adjustments,
    simulate_playoff_bracket,
    simulate_remaining_season,
    wild_card_matchups,
    win_probability,
)


def _require_app():
    from app.main import app

    return app


# Synthetic 14-team bracket (2020+ format), ratings invented for tests.
_AFC_14 = ["KC", "BUF", "BAL", "HOU", "DEN", "CIN", "MIA"]
_NFC_14 = ["PHI", "DET", "GB", "TB", "SF", "LAR", "SEA"]
_SEEDS_14 = {"AFC": _AFC_14, "NFC": _NFC_14}
_RATINGS_14 = {
    "KC": 1650.0, "BUF": 1620.0, "BAL": 1600.0, "HOU": 1570.0,
    "DEN": 1550.0, "CIN": 1530.0, "MIA": 1500.0,
    "PHI": 1640.0, "DET": 1610.0, "GB": 1590.0, "TB": 1560.0,
    "SF": 1540.0, "LAR": 1520.0, "SEA": 1490.0,
}

_AFC_12 = ["NE", "KC", "PIT", "JAX", "TEN", "BUF"]
_NFC_12 = ["PHI", "MIN", "LAR", "NO", "CAR", "ATL"]
_SEEDS_12 = {"AFC": _AFC_12, "NFC": _NFC_12}


# ---------------------------------------------------------------------------
# playoff_format
# ---------------------------------------------------------------------------


class TestPlayoffFormat:
    def test_2016_2019_is_12_team(self):
        for season in (2016, 2017, 2018, 2019):
            fmt = playoff_format(season)
            assert fmt["n_teams"] == 12
            assert fmt["byes"] == [1, 2]
            assert fmt["seeds_per_conference"] == 6
            assert fmt["wild_card_pairs"] == [(3, 6), (4, 5)]

    def test_2020_plus_is_14_team(self):
        for season in (2020, 2021, 2026):
            fmt = playoff_format(season)
            assert fmt["n_teams"] == 14
            assert fmt["byes"] == [1]
            assert fmt["seeds_per_conference"] == 7
            assert fmt["wild_card_pairs"] == [(2, 7), (3, 6), (4, 5)]

    def test_rejects_pre_2016(self):
        with pytest.raises(ValueError):
            playoff_format(2015)

    def test_rejects_non_integer(self):
        with pytest.raises(ValueError):
            playoff_format("2026")


# ---------------------------------------------------------------------------
# Bracket construction + reseeding
# ---------------------------------------------------------------------------


class TestBracketConstruction:
    def test_14_team_wild_card_pairs_and_home_field(self):
        matchups = wild_card_matchups(_SEEDS_14, 2026)
        by_conf = {}
        for m in matchups:
            by_conf.setdefault(m["conference"], []).append((m["home_seed"], m["away_seed"]))
            # Higher seed (division winner or better record) hosts.
            assert m["home_seed"] < m["away_seed"]
            assert not m["neutral"]
        assert sorted(by_conf["AFC"]) == [(2, 7), (3, 6), (4, 5)]
        assert sorted(by_conf["NFC"]) == [(2, 7), (3, 6), (4, 5)]
        # Spot-check team mapping: #2 hosts #7.
        m27 = next(m for m in matchups if m["conference"] == "AFC" and m["home_seed"] == 2)
        assert (m27["home_team"], m27["away_team"]) == ("BUF", "MIA")

    def test_12_team_wild_card_pairs(self):
        matchups = wild_card_matchups(_SEEDS_12, 2018)
        pairs = sorted((m["home_seed"], m["away_seed"]) for m in matchups if m["conference"] == "AFC")
        assert pairs == [(3, 6), (4, 5)]

    def test_build_bracket_rounds_byes(self):
        b14 = build_bracket_rounds(_SEEDS_14, 2026)
        assert b14["byes"] == {"AFC": ["KC"], "NFC": ["PHI"]}
        assert len(b14["wild_card"]) == 6
        b12 = build_bracket_rounds(_SEEDS_12, 2018)
        assert b12["byes"] == {"AFC": ["NE", "KC"], "NFC": ["PHI", "MIN"]}
        assert len(b12["wild_card"]) == 4

    def test_seed_count_mismatch_raises(self):
        with pytest.raises(ValueError, match="needs 7 seeds"):
            build_bracket_rounds({"AFC": _AFC_14[:6], "NFC": _NFC_14}, 2026)

    def test_14_team_divisional_reseeding(self):
        # WC winners: #7 upset, #3, #4 -> #1 hosts lowest remaining (#7),
        # #3 hosts #4.
        winners = {"AFC": ["MIA", "BAL", "HOU"], "NFC": ["SEA", "GB", "TB"]}
        games = divisional_matchups(winners, _SEEDS_14, 2026)
        afc = sorted(
            ((g["home_seed"], g["away_seed"]) for g in games if g["conference"] == "AFC")
        )
        assert afc == [(1, 7), (3, 4)]
        g17 = next(g for g in games if g["conference"] == "AFC" and g["away_seed"] == 7)
        assert (g17["home_team"], g17["away_team"]) == ("KC", "MIA")

    def test_12_team_divisional_reseeding(self):
        # Byes #1/#2; WC winners #3 and #6 -> #1 hosts #6, #2 hosts #3.
        winners = {"AFC": ["PIT", "BUF"], "NFC": ["LAR", "ATL"]}
        games = divisional_matchups(winners, _SEEDS_12, 2018)
        afc = sorted(
            ((g["home_seed"], g["away_seed"]) for g in games if g["conference"] == "AFC")
        )
        assert afc == [(1, 6), (2, 3)]

    def test_championship_higher_seed_hosts(self):
        winners = {"AFC": ["MIA", "KC"], "NFC": ["PHI", "SEA"]}
        games = championship_matchup(winners, _SEEDS_14, 2026)
        afc = next(g for g in games if g["conference"] == "AFC")
        assert (afc["home_team"], afc["away_team"]) == ("KC", "MIA")
        assert (afc["home_seed"], afc["away_seed"]) == (1, 7)


# ---------------------------------------------------------------------------
# win_probability
# ---------------------------------------------------------------------------


class TestWinProbability:
    def test_neutral_hand_computation(self):
        # 100 Elo diff, neutral: 1 / (1 + 10^(-100/400)).
        assert win_probability(1600, 1500, neutral=True) == pytest.approx(0.640064999, rel=1e-6)

    def test_home_field_shifts_probability(self):
        p_home = win_probability(1500, 1500, neutral=False)
        p_neutral = win_probability(1500, 1500, neutral=True)
        assert p_neutral == pytest.approx(0.5)
        assert p_home == pytest.approx(1 / (1 + 10 ** (-35 / 400)), rel=1e-9)
        assert p_home > 0.5

    def test_symmetric(self):
        assert win_probability(1600, 1500, neutral=True) == pytest.approx(
            1 - win_probability(1500, 1600, neutral=True)
        )

    def test_rejects_non_numeric(self):
        with pytest.raises(PlayoffSimError):
            win_probability("good", 1500)


# ---------------------------------------------------------------------------
# resolve_adjustments
# ---------------------------------------------------------------------------


class TestAdjustments:
    def test_defaults_are_elo_only(self):
        adj = resolve_adjustments(None)
        assert adj == DEFAULT_ADJUSTMENTS
        assert adj["hfa_elo"] == 35.0
        for key, value in adj.items():
            if key != "hfa_elo":
                assert value == 0.0

    def test_unknown_key_rejected(self):
        with pytest.raises(PlayoffSimError, match="unknown adjustment"):
            resolve_adjustments({"home_cooking": 10.0})

    def test_override_applies(self):
        adj = resolve_adjustments({"bye_rest_elo": 12.5})
        assert adj["bye_rest_elo"] == 12.5
        assert adj["hfa_elo"] == 35.0


# ---------------------------------------------------------------------------
# simulate_playoff_bracket
# ---------------------------------------------------------------------------


class TestSimulateBracket:
    def test_deterministic_with_seed(self):
        a = simulate_playoff_bracket(_SEEDS_14, _RATINGS_14, 2026, n_sims=2000, rng_seed=7)
        b = simulate_playoff_bracket(_SEEDS_14, _RATINGS_14, 2026, n_sims=2000, rng_seed=7)
        assert a == b

    def test_different_seeds_differ(self):
        a = simulate_playoff_bracket(_SEEDS_14, _RATINGS_14, 2026, n_sims=3000, rng_seed=7)
        b = simulate_playoff_bracket(_SEEDS_14, _RATINGS_14, 2026, n_sims=3000, rng_seed=8)
        assert a["teams"]["KC"]["p_win_sb"] != b["teams"]["KC"]["p_win_sb"]

    def test_bye_teams_have_no_wc_game(self):
        res = simulate_playoff_bracket(_SEEDS_14, _RATINGS_14, 2026, n_sims=500, rng_seed=3)
        assert res["teams"]["KC"]["p_win_wc"] is None  # #1 seed bye (14-team)
        assert res["teams"]["BUF"]["p_win_wc"] is not None
        res12 = simulate_playoff_bracket(_SEEDS_12, {t: 1500.0 for conf in _SEEDS_12.values() for t in conf},
                                         2018, n_sims=500, rng_seed=3)
        assert res12["teams"]["NE"]["p_win_wc"] is None
        assert res12["teams"]["KC"]["p_win_wc"] is None  # #2 seed bye (12-team)
        assert res12["teams"]["PIT"]["p_win_wc"] is not None

    def test_probabilities_sane(self):
        res = simulate_playoff_bracket(_SEEDS_14, _RATINGS_14, 2026, n_sims=4000, rng_seed=11)
        # Exactly one Super Bowl champion per sim.
        assert sum(t["p_win_sb"] for t in res["teams"].values()) == pytest.approx(1.0)
        for team, row in res["teams"].items():
            for key in ("p_win_div", "p_win_conf", "p_win_sb"):
                assert 0.0 <= row[key] <= 1.0, (team, key)
            assert row["n_sims"] == 4000
        # Best team with a bye should beat the worst team's title odds.
        assert res["teams"]["KC"]["p_win_sb"] > res["teams"]["MIA"]["p_win_sb"]
        assert res["adjustments_used"] == DEFAULT_ADJUSTMENTS

    def test_missing_rating_raises(self):
        bad = dict(_RATINGS_14)
        del bad["MIA"]
        with pytest.raises(PlayoffSimError, match="ratings missing"):
            simulate_playoff_bracket(_SEEDS_14, bad, 2026, n_sims=100, rng_seed=1)

    def test_bad_n_sims_raises(self):
        with pytest.raises(PlayoffSimError):
            simulate_playoff_bracket(_SEEDS_14, _RATINGS_14, 2026, n_sims=0)
        with pytest.raises(PlayoffSimError):
            simulate_playoff_bracket(_SEEDS_14, _RATINGS_14, 2026, n_sims=50001)

    def test_bye_rest_adjustment_moves_needle(self):
        base = simulate_playoff_bracket(_SEEDS_14, _RATINGS_14, 2026, n_sims=4000, rng_seed=5)
        boosted = simulate_playoff_bracket(
            _SEEDS_14, _RATINGS_14, 2026, n_sims=4000, rng_seed=5,
            adjustments={"bye_rest_elo": 100.0},
        )
        # Same RNG stream, huge bye boost: #1 seeds must gain title equity.
        assert boosted["teams"]["KC"]["p_win_sb"] > base["teams"]["KC"]["p_win_sb"]
        assert boosted["adjustments_used"]["bye_rest_elo"] == 100.0


# ---------------------------------------------------------------------------
# apply_tiebreakers
# ---------------------------------------------------------------------------


def _row(team, conf, div, w, l, t=0, evidence=None):
    return {
        "team": team, "conference": conf, "division": div,
        "wins": w, "losses": l, "ties": t, "evidence": evidence or {},
    }


class TestTiebreakers:
    def test_seeds_by_record_no_ties(self):
        rows = [
            _row("KC", "AFC", "West", 14, 3), _row("DEN", "AFC", "West", 8, 9),
            _row("BUF", "AFC", "East", 12, 5), _row("MIA", "AFC", "East", 6, 11),
            _row("BAL", "AFC", "North", 11, 6), _row("CIN", "AFC", "North", 9, 8),
            _row("HOU", "AFC", "South", 10, 7), _row("IND", "AFC", "South", 5, 12),
            _row("PHI", "NFC", "East", 13, 4), _row("DAL", "NFC", "East", 7, 10),
            _row("DET", "NFC", "North", 12, 5), _row("GB", "NFC", "North", 9, 8),
            _row("TB", "NFC", "South", 9, 8), _row("ATL", "NFC", "South", 4, 13),
            _row("SF", "NFC", "West", 11, 6), _row("LAR", "NFC", "West", 10, 7),
        ]
        res = apply_tiebreakers(rows, season=2026)
        assert res["status"] == "ok"
        assert res["seeds"]["AFC"] == ["KC", "BUF", "BAL", "HOU", "CIN", "DEN", "MIA"]
        assert res["seeds"]["NFC"][0] == "PHI"
        assert res["division_winners"]["AFC"] == ["KC", "BUF", "BAL", "HOU"]
        assert res["unresolved"] == []

    def test_head_to_head_breaks_division_tie(self):
        rows = [
            _row("KC", "AFC", "West", 12, 5, evidence={"h2h": {"DEN": "W"}}),
            _row("DEN", "AFC", "West", 12, 5, evidence={"h2h": {"KC": "L"}}),
            _row("BUF", "AFC", "East", 13, 4),
            _row("BAL", "AFC", "North", 11, 6),
            _row("HOU", "AFC", "South", 10, 7),
        ]
        res = apply_tiebreakers(rows, season=2026)
        assert res["status"] == "ok"
        # BUF 13-4 is #1; KC wins the West on head-to-head, seeded above DEN.
        assert res["seeds"]["AFC"][:3] == ["BUF", "KC", "BAL"]
        assert res["seeds"]["AFC"].index("KC") < res["seeds"]["AFC"].index("DEN")
        assert any(e["step"] == "head_to_head" and e["resolution"] == "ordered"
                   for e in res["tiebreak_log"])

    def test_unresolvable_tie_marked_not_verified(self):
        rows = [
            _row("KC", "AFC", "West", 12, 5),
            _row("DEN", "AFC", "West", 12, 5),
            _row("BUF", "AFC", "East", 13, 4),
            _row("BAL", "AFC", "North", 11, 6),
            _row("HOU", "AFC", "South", 10, 7),
        ]
        res = apply_tiebreakers(rows, season=2026)
        assert res["status"] == "partial"
        assert res["unresolved"], "tied clubs must be listed, not silently ordered"
        assert any(e["resolution"] == "unresolved" and "NOT VERIFIED" in e["detail"]
                   for e in res["tiebreak_log"])

    def test_conference_record_breaks_cross_division_tie(self):
        rows = [
            _row("BUF", "AFC", "East", 12, 5,
                 evidence={"conf": {"w": 9, "l": 3, "t": 0}}),
            _row("BAL", "AFC", "North", 12, 5,
                 evidence={"conf": {"w": 7, "l": 5, "t": 0}}),
            _row("KC", "AFC", "West", 14, 3),
            _row("HOU", "AFC", "South", 10, 7),
        ]
        res = apply_tiebreakers(rows, season=2026)
        assert res["seeds"]["AFC"][:3] == ["KC", "BUF", "BAL"]

    def test_missing_step_evidence_skipped_honestly(self):
        rows = [
            _row("KC", "AFC", "West", 12, 5,
                 evidence={"conf": {"w": 9, "l": 3, "t": 0}}),
            _row("DEN", "AFC", "West", 12, 5),  # no evidence at all
            _row("BUF", "AFC", "East", 13, 4),
            _row("BAL", "AFC", "North", 11, 6),
            _row("HOU", "AFC", "South", 10, 7),
        ]
        res = apply_tiebreakers(rows, season=2026)
        assert any(e["resolution"] == "skipped_no_evidence"
                   and "DATA UNAVAILABLE" in e["detail"]
                   for e in res["tiebreak_log"])

    def test_rejects_bad_rows(self):
        with pytest.raises(ValueError, match="'team'"):
            apply_tiebreakers([{"conference": "AFC", "division": "West"}])

    def test_multi_spot_wild_card_tie_reapplies_to_remaining_clubs(self):
        """Regression: the 2020 AFC pattern — BAL/CLE (North) + IND at 11-5.

        Historical order: BAL #5, CLE #6, IND #7 (seeds_verified.json). The
        old code permanently discarded CLE after BAL won the North
        subgroup, producing BAL #5, IND #6, CLE out — wrong. Correct
        procedure: BAL takes #5, then CLE *re-enters* as the North's next
        club and takes #6.
        BAL>CLE (2020 sweep) and BAL>IND (2020 Week 9) use real
        head-to-head evidence. The CLE>IND head-to-head below is
        ILLUSTRATIVE — this test pins the re-entry mechanics, not the
        historical reason CLE finished ahead of IND.
        """
        rows = [
            _row("KC", "AFC", "West", 14, 2),
            _row("BUF", "AFC", "East", 13, 3),
            _row("PIT", "AFC", "North", 12, 4),
            # TEN swept IND in 2020: real head-to-head evidence for the
            # South title (both finished 11-5).
            _row("TEN", "AFC", "South", 11, 5,
                 evidence={"h2h": {"IND": "W"}}),
            _row("BAL", "AFC", "North", 11, 5,
                 evidence={"h2h": {"CLE": "W", "IND": "W"}}),
            _row("CLE", "AFC", "North", 11, 5,
                 evidence={"h2h": {"BAL": "L", "IND": "W"}}),
            _row("IND", "AFC", "South", 11, 5,
                 evidence={"h2h": {"BAL": "L", "CLE": "L", "TEN": "L"}}),
        ]
        res = apply_tiebreakers(rows, season=2020)
        assert res["status"] == "ok"
        assert res["seeds"]["AFC"] == ["KC", "BUF", "PIT", "TEN", "BAL", "CLE", "IND"]
        assert res["unresolved"] == []


# ---------------------------------------------------------------------------
# simulate_remaining_season
# ---------------------------------------------------------------------------


def _teams_state():
    # Synthetic mid-season records; divisions synthetic.
    return {
        "KC": {"wins": 8, "losses": 2, "ties": 0, "conference": "AFC", "division": "West"},
        "DEN": {"wins": 6, "losses": 4, "ties": 0, "conference": "AFC", "division": "West"},
        "BUF": {"wins": 7, "losses": 3, "ties": 0, "conference": "AFC", "division": "East"},
        "MIA": {"wins": 5, "losses": 5, "ties": 0, "conference": "AFC", "division": "East"},
        "BAL": {"wins": 7, "losses": 3, "ties": 0, "conference": "AFC", "division": "North"},
        "CIN": {"wins": 4, "losses": 6, "ties": 0, "conference": "AFC", "division": "North"},
        "HOU": {"wins": 6, "losses": 4, "ties": 0, "conference": "AFC", "division": "South"},
        "IND": {"wins": 3, "losses": 7, "ties": 0, "conference": "AFC", "division": "South"},
        "PHI": {"wins": 8, "losses": 2, "ties": 0, "conference": "NFC", "division": "East"},
        "DAL": {"wins": 5, "losses": 5, "ties": 0, "conference": "NFC", "division": "East"},
        "DET": {"wins": 7, "losses": 3, "ties": 0, "conference": "NFC", "division": "North"},
        "GB": {"wins": 6, "losses": 4, "ties": 0, "conference": "NFC", "division": "North"},
        "TB": {"wins": 6, "losses": 4, "ties": 0, "conference": "NFC", "division": "South"},
        "ATL": {"wins": 4, "losses": 6, "ties": 0, "conference": "NFC", "division": "South"},
        "SF": {"wins": 7, "losses": 3, "ties": 0, "conference": "NFC", "division": "West"},
        "LAR": {"wins": 5, "losses": 5, "ties": 0, "conference": "NFC", "division": "West"},
    }


def _ratings_state():
    return {t: 1500.0 + (i % 5) * 20.0 for i, t in enumerate(_teams_state())}


class TestRemainingSeason:
    def test_deterministic_with_seed(self):
        games = [("DEN", "KC"), ("MIA", "BUF"), ("GB", "DET"), ("ATL", "TB")]
        a = simulate_remaining_season(_teams_state(), games, _ratings_state(), 2026,
                                      n_sims=800, rng_seed=21)
        b = simulate_remaining_season(_teams_state(), games, _ratings_state(), 2026,
                                      n_sims=800, rng_seed=21)
        assert a == b

    def test_probabilities_sane(self):
        games = [("DEN", "KC"), ("MIA", "BUF"), ("CIN", "BAL"), ("IND", "HOU"),
                 ("DAL", "PHI"), ("GB", "DET"), ("ATL", "TB"), ("LAR", "SF")]
        res = simulate_remaining_season(_teams_state(), games, _ratings_state(), 2026,
                                        n_sims=1500, rng_seed=21)
        afc = [t for t, s in _teams_state().items() if s["conference"] == "AFC"]
        nfc = [t for t, s in _teams_state().items() if s["conference"] == "NFC"]
        # 7 playoff teams per conference in the 14-team format.
        assert sum(res["teams"][t]["p_make_playoffs"] for t in afc) == pytest.approx(7.0)
        assert sum(res["teams"][t]["p_make_playoffs"] for t in nfc) == pytest.approx(7.0)
        # One Super Bowl champion per simulated season.
        assert sum(t["p_win_sb"] for t in res["teams"].values()) == pytest.approx(1.0)
        # Seed probabilities partition playoff probability.
        for t in afc:
            row = res["teams"][t]
            assert sum(row[f"p_seed_{i}"] for i in range(1, 8)) == pytest.approx(
                row["p_make_playoffs"]
            )
            assert row["p_win_division"] <= row["p_make_playoffs"]
        # KC (8-2, strong rating) should be a near-lock; IND (3-7) a longshot.
        assert res["teams"]["KC"]["p_make_playoffs"] > 0.9
        assert res["teams"]["IND"]["p_make_playoffs"] < 0.2
        assert res["n_remaining_games"] == 8

    def test_no_games_means_current_records_decide(self):
        res = simulate_remaining_season(_teams_state(), [], _ratings_state(), 2026,
                                        n_sims=300, rng_seed=9)
        # No simulated games: 8-2 KC and PHI are #1 seeds in every sim.
        assert res["teams"]["KC"]["p_seed_1"] == pytest.approx(1.0)
        assert res["teams"]["PHI"]["p_seed_1"] == pytest.approx(1.0)

    def test_neutral_game_tuple_accepted(self):
        games = [("DEN", "KC", True)]
        res = simulate_remaining_season(_teams_state(), games, _ratings_state(), 2026,
                                        n_sims=200, rng_seed=4)
        assert res["n_remaining_games"] == 1

    def test_unknown_team_in_game_raises(self):
        with pytest.raises(PlayoffSimError, match="not in teams_state"):
            simulate_remaining_season(_teams_state(), [("DEN", "XX")],
                                      _ratings_state(), 2026, n_sims=10)

    def test_missing_rating_raises(self):
        ratings = _ratings_state()
        del ratings["KC"]
        with pytest.raises(PlayoffSimError, match="ratings missing"):
            simulate_remaining_season(_teams_state(), [], ratings, 2026, n_sims=10)


# ---------------------------------------------------------------------------
# Router: /api/playoffs
# ---------------------------------------------------------------------------


def _bracket_body(**over):
    body = {
        "season": 2026,
        "seeds": {
            "AFC": {str(i + 1): t for i, t in enumerate(_AFC_14)},
            "NFC": {str(i + 1): t for i, t in enumerate(_NFC_14)},
        },
        "ratings": dict(_RATINGS_14),
        "n_sims": 400,
        "rng_seed": 42,
    }
    body.update(over)
    return body


class TestPlayoffRouter:
    def test_formats_static_doc(self):
        body = TestClient(_require_app()).get("/api/playoffs/formats").json()
        assert body["status"] == "ok"
        assert body["formats"]["2020-present"]["byes"] == [1]
        assert body["formats"]["2016-2019"]["byes"] == [1, 2]
        assert "re-seeded" in body["seeding"]
        assert "NOT VERIFIED" in body["tiebreak_order"]["not_implemented"]
        # Adjustment placeholders are labeled honestly.
        assert body["adjustment_defaults"]["bye_rest_elo"]["default"] == 0.0
        assert "PLACEHOLDER" in body["adjustment_defaults"]["bye_rest_elo"]["status"]

    def test_simulate_bracket_ok(self):
        r = TestClient(_require_app()).post(
            "/api/playoffs/simulate-bracket", json=_bracket_body()
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["n_sims"] == 400
        assert body["rng_seed"] == 42
        assert body["adjustments_used"] == DEFAULT_ADJUSTMENTS
        assert set(body["teams"]) == set(_AFC_14) | set(_NFC_14)
        kc = body["teams"]["KC"]
        assert kc["seed"] == 1 and kc["conference"] == "AFC"
        assert kc["p_win_wc"] is None
        assert "not guarantees" in body["disclaimer"]

    def test_simulate_bracket_missing_rating_422(self):
        ratings = dict(_RATINGS_14)
        del ratings["SEA"]
        r = TestClient(_require_app()).post(
            "/api/playoffs/simulate-bracket", json=_bracket_body(ratings=ratings)
        )
        assert r.status_code == 422
        assert "ratings missing" in r.json()["detail"]

    def test_simulate_bracket_rejects_too_many_sims(self):
        r = TestClient(_require_app()).post(
            "/api/playoffs/simulate-bracket", json=_bracket_body(n_sims=60000)
        )
        assert r.status_code == 422

    def test_simulate_bracket_bad_season_422(self):
        r = TestClient(_require_app()).post(
            "/api/playoffs/simulate-bracket", json=_bracket_body(season=2010)
        )
        assert r.status_code == 422

    def test_retrospective_missing_file_honest(self, monkeypatch, tmp_path):
        import app.routers.playoffs as pr

        monkeypatch.setattr(pr, "_RETRO_PATH", tmp_path / "does-not-exist.json")
        body = TestClient(_require_app()).get("/api/playoffs/retrospective").json()
        assert body["status"] == "unavailable"
        assert "DATA UNAVAILABLE" in body["reason"]
        assert body["seasons"] is None

    def _write_retro(self, path):
        doc = {
            "artifact": "playoff_retrospective_2016_2025",
            "generated_at": "2026-09-10T00:00:00Z",
            "generated_by": "synthetic-test-fixture",
            "method": "synthetic fixture for router tests only",
            "seasons": [
                {
                    "season": 2016,
                    "format": {"n_teams": 12, "byes": [1, 2]},
                    "seeds": {"AFC": ["NE"], "NFC": ["DAL"]},
                    "actual": {
                        "wild_card": [], "divisional": [],
                        "conference_championship": [],
                        "super_bowl": {
                            "champion": "NE", "champion_seed": 1,
                            "champion_conference": "AFC", "runner_up": "ATL",
                        },
                    },
                    "model": {"NE": {"p_win_wc": None, "p_win_div": 0.7,
                                     "p_win_conf": 0.5, "p_win_sb": 0.3,
                                     "n_sims": 1000}},
                    "notes": "synthetic",
                },
                {
                    "season": 2020,
                    "format": {"n_teams": 14, "byes": [1]},
                    "seeds": {"AFC": ["KC"], "NFC": ["GB"]},
                    "actual": {
                        "wild_card": [], "divisional": [],
                        "conference_championship": [],
                        "super_bowl": {
                            "champion": "TB", "champion_seed": 5,
                            "champion_conference": "NFC", "runner_up": "KC",
                        },
                    },
                    "model": None,
                    "notes": "synthetic",
                },
            ],
        }
        path.write_text(json.dumps(doc))
        return doc

    def test_retrospective_reads_artifact(self, monkeypatch, tmp_path):
        import app.routers.playoffs as pr

        path = tmp_path / "retro.json"
        self._write_retro(path)
        monkeypatch.setattr(pr, "_RETRO_PATH", path)
        body = TestClient(_require_app()).get("/api/playoffs/retrospective").json()
        assert body["status"] == "ok"
        assert body["artifact"] == "playoff_retrospective_2016_2025"
        assert len(body["seasons"]) == 2

    def test_retrospective_season_filter(self, monkeypatch, tmp_path):
        import app.routers.playoffs as pr

        path = tmp_path / "retro.json"
        self._write_retro(path)
        monkeypatch.setattr(pr, "_RETRO_PATH", path)
        client = TestClient(_require_app())
        one = client.get("/api/playoffs/retrospective?season=2016").json()
        assert one["status"] == "ok"
        assert [s["season"] for s in one["seasons"]] == [2016]
        missing = client.get("/api/playoffs/retrospective?season=2019").json()
        assert missing["status"] == "no_such_season"

    def test_no_write_routes_on_playoffs_paths(self):
        app = _require_app()
        offenders = [
            (sorted(r.methods), r.path)
            for r in app.routes
            if hasattr(r, "path")
            and r.path.startswith("/api/playoffs")
            and {"PUT", "PATCH", "DELETE"}.intersection(r.methods or set())
        ]
        assert offenders == []
        # The single POST route is compute-only: the router module never
        # touches the database layer or performs any persistence. (Checked
        # against real DB/persistence API names, not prose words.)
        import app.routers.playoffs as pr

        source = open(pr.__file__).read()
        assert "get_mongo_client" not in source
        assert "get_database" not in source
        for banned in ("insert_one", "insert_many", "update_one", "update_many",
                       "delete_one", "delete_many", "find_one", ".find(",
                       "count_documents"):
            assert banned not in source
