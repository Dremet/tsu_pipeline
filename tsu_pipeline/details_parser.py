"""
details_parser.py — parse *_event_details.log into tire telemetry structures.

Logic ported from the reference helpers.py (parse_driver_info, parse_events,
find_best_lap_time, build_stints) with:
  - No pandas dependency (plain Python dicts/lists)
  - Comma→dot normalisation for all float fields (EU locale robustness)
  - Structured output for DB insertion via loader._load_details

Key algorithm (build_stints, ported exactly):
  * PitIn  → close current stint (first PitIn in that lap wins)
  * PitOut → open new stint (last PitOut in the same lap wins for compound)
  * PitIn/PitOut lap adjustment: if time since last Lap > best_lap_time × 0.75
    the pit happened before the start/finish line → lap += 1
  * Finished → close stint
  * End-of-data → close remaining open stints
"""

from __future__ import annotations

from pathlib import Path


def _nf(s: str) -> float:
    """Parse float, accepting both dot and comma as decimal separator."""
    return float(s.replace(",", "."))


def parse_header(lines: list[str]) -> tuple[dict[int, int], dict[int, dict], float | None]:
    """
    Parse the header section of a details.log.

    Returns
    -------
    player_steam_ids : dict[int, int]
        player_index -> steam_id (as integer)
    compounds : dict[int, dict]
        compound_index -> {"name": str, "max_wear": int, "max_perf": float}
    max_fuel : float | None
    """
    players: dict[int, int] = {}
    compounds: dict[int, dict] = {}
    max_fuel: float | None = None
    mode: str | None = None

    for line in lines:
        raw = line.strip()
        if raw.startswith("Events"):
            break
        if raw.startswith("PlayerCount"):
            mode = "players"
            continue
        if raw.startswith("TireCompoundCount"):
            mode = "tires"
            continue
        if raw.startswith("MaxFuel"):
            parts = raw.split()
            if len(parts) > 1:
                try:
                    max_fuel = _nf(parts[1])
                except ValueError:
                    pass
            continue
        if not mode or not raw or raw.startswith("#"):
            continue

        if mode == "players":
            parts = raw.split(maxsplit=3)
            if len(parts) < 2:
                continue
            try:
                players[int(parts[0])] = int(parts[1])
            except ValueError:
                pass

        elif mode == "tires":
            parts = raw.split()
            if len(parts) < 4:
                continue
            try:
                idx = int(parts[0])
                name = parts[1]
                max_wear = int(_nf(parts[2]))
                max_perf = _nf(parts[3])
                compounds[idx] = {"name": name, "max_wear": max_wear, "max_perf": max_perf}
            except ValueError:
                pass

    return players, compounds, max_fuel


def parse_raw_events(
    lines: list[str],
    compounds: dict[int, dict],
    max_fuel: float | None,
) -> list[dict]:
    """
    Parse the Events section into a list of event dicts, sorted by time.

    Each dict has keys:
        time, type, player, laps, fuel, tire_wear, tire_compound, hit_points, tire_pct
    """
    found = False
    events: list[dict] = []

    for line in lines:
        raw = line.strip()
        if raw.startswith("Events"):
            found = True
            continue
        if not found or not raw or raw.startswith("#"):
            continue

        parts = raw.split()
        if len(parts) < 8:
            continue

        try:
            time_   = int(parts[0])
            etype   = parts[1]
            player  = int(parts[2])
            laps    = int(parts[3])
            fuel    = _nf(parts[4])
            wear    = _nf(parts[5])
            comp    = int(parts[6])
            hp      = int(parts[7])
        except ValueError:
            continue

        cdata    = compounds.get(comp, {"max_wear": 1})
        max_wear = max(float(cdata["max_wear"]), 1.0)
        mf       = max_fuel if (max_fuel and max_fuel > 0) else 1.0
        tire_pct = 100.0 - (wear / max_wear * 100.0)

        events.append({
            "time":          time_,
            "type":          etype,
            "player":        player,
            "laps":          laps,
            "fuel":          fuel,
            "tire_wear":     wear,
            "tire_compound": comp,
            "hit_points":    hp,
            "tire_pct":      tire_pct,
        })

    events.sort(key=lambda e: e["time"])
    return events


def find_best_lap_time(events: list[dict]) -> int:
    """
    Minimum time gap (ms) between consecutive Lap events for any single driver.
    Returns 200000 as fallback when not enough data exists.
    """
    times_by_driver: dict[int, list[int]] = {}
    for ev in events:
        if ev["type"] == "Lap":
            times_by_driver.setdefault(ev["player"], []).append(ev["time"])

    best: int | None = None
    for times in times_by_driver.values():
        for i in range(len(times) - 1):
            dt = times[i + 1] - times[i]
            if best is None or dt < best:
                best = dt

    return best if best is not None else 200000


def build_stints(events: list[dict], best_lap_time: int) -> dict[int, list[dict]]:
    """
    Build tire stints per driver from the parsed event list.

    Returns
    -------
    dict[player_index, list[stint]]
    Each stint: {"lap_start": int, "lap_end": int, "compound_index": int, "end_tire_pct": int}

    Rules (ported exactly from reference helpers.py):
    - PitIn  → close current stint at that lap (first PitIn per lap only)
    - PitOut → open new stint from that lap; last PitOut in the same lap wins compound
    - Lap adjustment: if time since last Lap event > best_lap_time × 0.75 → lap += 1
    - Finished → close stint
    - End of events → close remaining open stints
    """
    stints_map:               dict[int, dict]        = {}
    last_event_by_drv:        dict[int, dict]        = {}
    last_lap_event_time_by_drv: dict[int, int]       = {}
    used_pit_in:              dict[int, set]          = {}
    used_pit_out:             dict[int, set]          = {}
    results:                  dict[int, list[dict]]  = {}

    def close_stint(drv: int, close_lap: int, tire_pct: float) -> None:
        if drv not in stints_map:
            return
        sd = stints_map.pop(drv)
        results.setdefault(drv, []).append({
            "lap_start":     sd["lap_start"],
            "lap_end":       close_lap,
            "compound_index": sd["compound"],
            "end_tire_pct":  round(tire_pct),
        })

    def open_stint(drv: int, lap_start: int, compound: int) -> None:
        stints_map[drv] = {"lap_start": lap_start, "compound": compound}

    for ev in events:
        drv   = ev["player"]
        time_ = ev["time"]
        etype = ev["type"]
        lap   = ev["laps"]
        tpct  = ev["tire_pct"]
        cmpd  = ev["tire_compound"]

        if etype in ("PitIn", "PitOut"):
            if drv not in last_lap_event_time_by_drv:
                continue
            if time_ - last_lap_event_time_by_drv[drv] > best_lap_time * 0.75:
                lap += 1

        last_event_by_drv[drv] = ev

        if drv not in stints_map:
            open_stint(drv, lap, cmpd)

        if etype in ("Lap", "Start"):
            last_lap_event_time_by_drv[drv] = time_

        if etype == "PitIn":
            used_laps = used_pit_in.setdefault(drv, set())
            if lap not in used_laps:
                close_stint(drv, lap, tpct)
                used_laps.add(lap)

        elif etype == "PitOut":
            used_lap_out = used_pit_out.setdefault(drv, set())
            if lap not in used_lap_out:
                if drv not in stints_map:
                    open_stint(drv, lap, cmpd)
                else:
                    stints_map[drv]["compound"] = cmpd
                    stints_map[drv]["lap_start"] = lap
                used_lap_out.add(lap)
            else:
                if drv in stints_map:
                    stints_map[drv]["compound"] = cmpd
                    stints_map[drv]["lap_start"] = lap

        elif etype == "Finished":
            close_stint(drv, lap, tpct)

    for drv in list(stints_map.keys()):
        last_ev = last_event_by_drv.get(drv)
        if last_ev:
            close_stint(drv, last_ev["laps"], last_ev["tire_pct"])

    return results


def _assign_stint_numbers(
    lap_events: list[dict],
    stints: dict[int, list[dict]],
) -> list[dict]:
    """
    Annotate each Lap event with its stint_number (1-based).
    Lap N belongs to stint S when S["lap_start"] < N <= S["lap_end"].
    """
    annotated: list[dict] = []
    for ev in lap_events:
        drv = ev["player"]
        n   = ev["laps"]
        driver_stints = stints.get(drv, [])
        stint_num = 1
        for i, s in enumerate(driver_stints, 1):
            if s["lap_start"] < n <= s["lap_end"]:
                stint_num = i
                break
        else:
            # Outside all known stint intervals (e.g. DNF edge case) → last stint
            if driver_stints:
                stint_num = len(driver_stints)
        annotated.append({**ev, "stint_number": stint_num})
    return annotated


def parse_details_log(log_path: Path) -> dict | None:
    """
    Parse a *_event_details.log file.

    Returns None if the file has no tire compound data (no TireCompoundCount or
    zero compounds defined) — these sessions have no meaningful tire telemetry.

    Returns
    -------
    {
        "compounds":        dict[int, dict]  compound_index → {name, max_wear, max_perf}
        "max_fuel":         float | None
        "player_steam_ids": dict[int, int]   player_index → steam_id
        "lap_telemetry":    list[dict]
            {player_index, lap_number, compound_index, tire_wear,
             fuel_remaining, hit_points, stint_number}
    }
    """
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    lines = text.splitlines()
    players, compounds, max_fuel = parse_header(lines)

    if not compounds:
        return None

    events      = parse_raw_events(lines, compounds, max_fuel)
    best_lap_t  = find_best_lap_time(events)
    stints      = build_stints(events, best_lap_t)

    lap_events  = [ev for ev in events if ev["type"] == "Lap"]
    annotated   = _assign_stint_numbers(lap_events, stints)

    lap_telemetry: list[dict] = []
    for ev in annotated:
        fuel_remaining = int(ev["fuel"]) if (max_fuel and max_fuel > 0) else None
        lap_telemetry.append({
            "player_index":  ev["player"],
            "lap_number":    ev["laps"],
            "compound_index": ev["tire_compound"],
            "tire_wear":     int(ev["tire_wear"]),
            "fuel_remaining": fuel_remaining,
            "hit_points":    ev["hit_points"],
            "stint_number":  ev["stint_number"],
        })

    return {
        "compounds":        compounds,
        "max_fuel":         max_fuel,
        "player_steam_ids": players,
        "lap_telemetry":    lap_telemetry,
    }
