"""
Tests for tsu_pipeline.elo
"""

import math
import pytest

from tsu_pipeline.elo import (
    _calc_elo_for_session,
    _expected_score,
    MIN_ELO,
    START_ELO,
    K_FACTOR,
    D,
)


def test_expected_score_equal_elo():
    """Equal ELO → expected score = 0.5."""
    assert _expected_score(1000.0, 1000.0) == pytest.approx(0.5)


def test_expected_score_higher_elo():
    """Higher ELO → expected score > 0.5."""
    assert _expected_score(1200.0, 1000.0) > 0.5


def test_elo_formula_known_values():
    """
    Two drivers at 1000 ELO each: winner (pos=1) gains points, loser (pos=2) loses.
    With K=20, n=2, opp_count=1, denom=1:
        expected = 0.5 (equal ELO)
        scoring_winner = (2-1)/1 = 1.0
        delta_winner = 20 * 1 * (1.0 - 0.5) = +10
        scoring_loser  = (2-2)/1 = 0.0
        delta_loser  = 20 * 1 * (0.0 - 0.5) = -10
    """
    participants = [
        {"id": "p1", "steam_id": 1001, "position": 1},
        {"id": "p2", "steam_id": 1002, "position": 2},
    ]
    elo_map = {1001: 1000.0, 1002: 1000.0}
    results = _calc_elo_for_session(participants, elo_map)
    by_pid = {r["participation_id"]: r for r in results}

    assert by_pid["p1"]["elo_delta"] == pytest.approx(+10.0)
    assert by_pid["p2"]["elo_delta"] == pytest.approx(-10.0)
    assert by_pid["p1"]["elo_value"] == pytest.approx(1010.0)
    assert by_pid["p2"]["elo_value"] == pytest.approx(990.0)


def test_bot_filtering():
    """
    Bots must never appear in _calc_elo_for_session results.
    The caller filters bots; this test passes only humans.
    """
    humans = [
        {"id": "p1", "steam_id": 1001, "position": 1},
        {"id": "p2", "steam_id": 1002, "position": 2},
    ]
    elo_map = {1001: 1000.0, 1002: 1000.0}
    results = _calc_elo_for_session(humans, elo_map)
    steam_ids = {r["steam_id"] for r in results}
    assert steam_ids == {1001, 1002}


def test_solo_race_no_elo_entry():
    """Single participant → no opponents → no ELO entries."""
    participants = [{"id": "p1", "steam_id": 1001, "position": 1}]
    results = _calc_elo_for_session(participants, {1001: 1000.0})
    assert results == []


def test_minimum_elo_floor():
    """ELO can never drop below MIN_ELO (100)."""
    # Driver at 105 ELO finishing last in a big field should hit the floor
    participants = [
        {"id": f"p{i}", "steam_id": 1000 + i, "position": i}
        for i in range(1, 8)
    ]
    # Player 7 at very low ELO
    elo_map = {p["steam_id"]: START_ELO for p in participants}
    elo_map[1007] = 105.0

    results = _calc_elo_for_session(participants, elo_map)
    last = next(r for r in results if r["steam_id"] == 1007)
    assert last["elo_value"] >= MIN_ELO


def test_three_drivers_sum_zero():
    """ELO deltas in a symmetric field should sum to approximately zero."""
    # Three drivers at equal ELO
    participants = [
        {"id": "p1", "steam_id": 1001, "position": 1},
        {"id": "p2", "steam_id": 1002, "position": 2},
        {"id": "p3", "steam_id": 1003, "position": 3},
    ]
    elo_map = {1001: 1000.0, 1002: 1000.0, 1003: 1000.0}
    results = _calc_elo_for_session(participants, elo_map)
    total_delta = sum(r["elo_delta"] for r in results)
    assert total_delta == pytest.approx(0.0, abs=1e-9)
