"""
Event validation before DB load.

A hotlap event where every player has sentinel values (never drove a valid lap)
is skipped entirely. A race event is always accepted regardless of finishedState.
"""


def _player_is_sentinel(lap_ranking_entry: dict, playerStats_entry: dict) -> bool:
    """True if this player's data represents a sentinel (no valid lap recorded)."""
    if lap_ranking_entry.get("time") == 900000000:
        return True
    # Belt-and-suspenders: also flag completely empty checkpoint data
    cp_times = playerStats_entry.get("checkpointTimes", [])
    if not cp_times or all(not cp.get("times") for cp in cp_times):
        return True
    return False


def validate_event(data: dict) -> tuple[bool, str]:
    """
    Returns (is_valid, reason).

    Invalid events should be skipped without error.
    Valid race events always return True (finishedState is irrelevant).
    """
    rs = data.get("raceStats", {})
    is_hotlapping = rs.get("hotlapping", False)

    if not is_hotlapping:
        # Race events are always accepted
        return True, ""

    # Hotlapping: skip only when ALL players have sentinel values
    lap_ranking = {e["playerIndex"]: e for e in rs.get("lapRanking", {}).get("entries", [])}
    player_stats = rs.get("playerStats", [])

    all_sentinel = True
    for pi, ps in enumerate(player_stats):
        lr_entry = lap_ranking.get(pi, {"time": 900000000})
        if not _player_is_sentinel(lr_entry, ps):
            all_sentinel = False
            break

    if all_sentinel:
        return False, "sentinel hotlap – all players have no valid laps"

    return True, ""


def player_has_valid_laps(player_index: int, data: dict) -> bool:
    """True if a specific player in a hotlap event has at least one valid lap."""
    rs = data["raceStats"]
    lap_ranking = {e["playerIndex"]: e for e in rs.get("lapRanking", {}).get("entries", [])}
    player_stats = rs.get("playerStats", [])
    if player_index >= len(player_stats):
        return False
    lr_entry = lap_ranking.get(player_index, {"time": 900000000})
    return not _player_is_sentinel(lr_entry, player_stats[player_index])
