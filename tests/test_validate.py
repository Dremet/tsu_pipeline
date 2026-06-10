"""
Tests for tsu_pipeline.validate
"""

import json
from pathlib import Path

import pytest

from tsu_pipeline.validate import validate_event

FIXTURES = Path(__file__).parent / "fixtures"

# Real data paths
REAL_HOTLAP = Path(
    "/home/data/hotlapping/archive/20251223_170021/raw/20251223_170021_OffTheRoad_event.json"
)

REAL_HEAT = Path(
    "/home/data/heats/archive/20260513_211653/raw/"
    "20260513_211653_JäädytettyIndeksi-ClubLayout_event.json"
)


def test_sentinel_hotlap_rejected():
    """An event where ALL players have sentinel lap times must be rejected."""
    data = json.loads((FIXTURES / "sentinel_hotlap.json").read_text())
    valid, reason = validate_event(data)
    assert not valid
    assert "sentinel" in reason.lower()


def test_real_hotlap_accepted():
    """A real hotlap session with completed laps must be accepted."""
    data = json.loads(REAL_HOTLAP.read_text())
    valid, reason = validate_event(data)
    assert valid, f"Expected valid but got: {reason}"


def test_stopped_give_points_accepted():
    """Race events with finishedState='Stopped_GivePoints' must be accepted."""
    data = json.loads(REAL_HEAT.read_text())
    assert data["finishedState"] == "Stopped_GivePoints"
    valid, reason = validate_event(data)
    assert valid, f"Expected valid but got: {reason}"


def test_race_always_accepted():
    """Any non-hotlapping event is accepted regardless of lap times."""
    data = {
        "finishedState": "Finished",
        "raceStats": {
            "hotlapping": False,
            "raceRanking": {"entries": []},
            "lapRanking": {"entries": []},
            "playerStats": [],
        },
    }
    valid, _ = validate_event(data)
    assert valid


def test_partial_sentinel_hotlap_accepted():
    """Hotlap event where at least one player has real laps must be accepted."""
    data = {
        "finishedState": "Finished",
        "raceStats": {
            "hotlapping": True,
            "raceRanking": {
                "entries": [
                    {"playerIndex": 0, "time": 0, "lapsCompleted": -1, "lastCheckpoint": 0},
                    {"playerIndex": 1, "time": 1234567, "lapsCompleted": 3, "lastCheckpoint": 0},
                ]
            },
            "lapRanking": {
                "entries": [
                    {"playerIndex": 0, "time": 900000000, "cFlags": 0, "lap": 0},
                    {"playerIndex": 1, "time": 330000, "cFlags": 0, "lap": 2},
                ]
            },
            "playerStats": [
                {"startTime": 60000, "lapsToFinish": 10, "checkpointTimes": [{"cFlags": 0, "times": []}]},
                {"startTime": 60000, "lapsToFinish": 10, "checkpointTimes": [
                    {"cFlags": 0, "times": [100000, 120000, 140000]},
                    {"cFlags": 0, "times": [430000, 450000, 470000]},
                    {"cFlags": 0, "times": [760000, 780000, 800000]},
                    {"cFlags": 0, "times": [1090000, 1110000, 1130000]},
                ]},
            ],
        },
    }
    valid, reason = validate_event(data)
    assert valid, f"Expected valid but got: {reason}"
