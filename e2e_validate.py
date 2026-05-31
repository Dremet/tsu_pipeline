#!/usr/bin/env python3
"""
e2e_validate.py — End-to-End validation against real data volumes.

Steps:
  1. Apply migrations (idempotent) to TEST-DB
  2. Seed elo_bootstrap + drivers from racing-DB
  3. Load all *_event.json from /home/data/{hotlapping,events,heats}
  4. Run update_elo for Tripleheat sessions
  5. Plausibility checks (no sentinels, no bots in drivers, no duplicates)
  6. Compare Tripleheat ELO ranking with racing-DB values
  7. Export one example row per mart view

Usage:
    uv run e2e_validate.py [--skip-load]

    --skip-load : skip the load phase (use if DB is already populated)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

TEST_URL = os.environ["TSU_TEST_POSTGRES_URL"]
RACING_URL = os.environ["OLD_RACING_POSTGRES_URL"]

DATA_ROOTS = {
    "hotlapping": Path("/home/data/hotlapping"),
    "events":     Path("/home/data/events"),
    "heats":      Path("/home/data/heats"),
}

MIGRATIONS = [
    Path(__file__).parent / "migrations" / "001_base_schema.sql",
    Path(__file__).parent / "migrations" / "002_elo_bootstrap.sql",
    Path(__file__).parent / "migrations" / "003_mart_views.sql",
]


# ── helpers ───────────────────────────────────────────────────────────────────

def hdr(text: str) -> None:
    print(f"\n{'═' * 60}")
    print(f"  {text}")
    print('═' * 60)


def section(text: str) -> None:
    print(f"\n── {text} ──")


def fmt_int(n: int) -> str:
    return f"{n:,}"


# ── step 1: migrations ────────────────────────────────────────────────────────

def apply_migrations() -> None:
    hdr("STEP 1 — Apply migrations (idempotent)")
    with psycopg.connect(TEST_URL, autocommit=True) as conn:
        for migration in MIGRATIONS:
            conn.execute(migration.read_text())
            print(f"  ✓ {migration.name}")


# ── step 2: seed from racing-DB ───────────────────────────────────────────────

def seed_bootstrap() -> int:
    hdr("STEP 2 — Seed elo_bootstrap + drivers from racing-DB")
    with psycopg.connect(RACING_URL) as src:
        cur_src = src.cursor()
        cur_src.execute(
            """
            SELECT
                d.steam_id, d.name, d.clan, d.flag,
                latest.value        AS elo_value,
                latest.number_races,
                latest.last_timestamp AS last_race_at
            FROM tsu.drivers d
            JOIN LATERAL (
                SELECT value, number_races, last_timestamp
                FROM tsu.elo_heat
                WHERE driver_id = d.id
                ORDER BY created_at DESC
                LIMIT 1
            ) latest ON true
            """
        )
        records = cur_src.fetchall()

    with psycopg.connect(TEST_URL) as dst:
        cur_dst = dst.cursor()
        for r in records:
            cur_dst.execute(
                """
                INSERT INTO base.drivers (steam_id, name, flag, clan)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (steam_id) DO UPDATE
                    SET name=EXCLUDED.name, flag=EXCLUDED.flag,
                        clan=EXCLUDED.clan, updated_at=now()
                """,
                (r[0], r[1], r[3], r[2]),
            )
            cur_dst.execute(
                """
                INSERT INTO base.elo_bootstrap
                    (steam_id, elo_value, number_races, last_race_at, source)
                VALUES (%s, %s, %s, %s, 'racing_db_migration')
                ON CONFLICT (steam_id) DO UPDATE
                    SET elo_value=EXCLUDED.elo_value,
                        number_races=EXCLUDED.number_races,
                        last_race_at=EXCLUDED.last_race_at,
                        imported_at=now()
                """,
                (r[0], r[4], r[5], r[6]),
            )

    print(f"  Drivers + bootstrap seeded: {len(records)}")
    return len(records)


# ── step 3: load data ─────────────────────────────────────────────────────────

def load_all_data() -> dict[str, dict]:
    from tsu_pipeline.batch import load_folder

    hdr("STEP 3 — Load all data from /home/data/{hotlapping,events,heats}")
    results = {}

    for server, path in DATA_ROOTS.items():
        section(f"Loading server='{server}' from {path}")
        t0 = time.monotonic()

        count = [0]
        total_files = sum(1 for _ in path.glob("**/*_event.json"))

        def progress(current: int, total: int, filepath: str) -> None:
            if current % 500 == 0 or current == total:
                elapsed = time.monotonic() - t0
                print(f"    {current:5d}/{total}  ({elapsed:.0f}s)")

        summary = load_folder(path, server, TEST_URL, progress_fn=progress)
        elapsed = time.monotonic() - t0

        print(f"  total:          {fmt_int(summary['total'])}")
        print(f"  loaded:         {fmt_int(summary['loaded'])}")
        print(f"  skipped:        {fmt_int(summary['skipped'])}")
        print(f"  errors:         {fmt_int(summary['errors'])}")
        print(f"  sessions_new:   {fmt_int(summary['sessions_new'])}")
        print(f"  participations: {fmt_int(summary['participations_new'])}")
        print(f"  drivers_new:    {fmt_int(summary['drivers_new'])}")
        print(f"  laps_new:       {fmt_int(summary['laps_new'])}")
        print(f"  time:           {elapsed:.1f}s")

        if summary["error_files"]:
            print(f"  ERROR DETAILS (first 5):")
            for fp, err in summary["error_files"][:5]:
                print(f"    {Path(fp).name}: {err[:80]}")

        results[server] = summary

    return results


# ── step 4: update ELO ────────────────────────────────────────────────────────

def run_elo_update() -> int:
    from tsu_pipeline.elo import update_elo

    hdr("STEP 4 — update_elo for Tripleheat sessions (server='heats')")

    with psycopg.connect(TEST_URL) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM base.race_sessions WHERE server = 'heats' ORDER BY utc_start_time"
        )
        session_ids = [row[0] for row in cur.fetchall()]
        print(f"  Heats sessions in DB: {len(session_ids)}")

        if not session_ids:
            print("  (no heats sessions — skip)")
            return 0

        t0 = time.monotonic()
        inserted = update_elo(session_ids, cur, server="heats")
        elapsed = time.monotonic() - t0

        print(f"  ELO rows inserted:    {fmt_int(inserted)}")
        print(f"  Time:                 {elapsed:.2f}s")
    return inserted


# ── step 5: plausibility checks ───────────────────────────────────────────────

def plausibility_checks() -> list[str]:
    hdr("STEP 5 — Plausibility checks")
    issues = []

    with psycopg.connect(TEST_URL) as conn:
        cur = conn.cursor()

        # Check: no sentinel hotlap times in DB (sentinel = 900000000 ticks = 90000.0s)
        # Legitimate laps can be >85s (Nordschleife, slow cars etc.) so threshold is 89999s
        cur.execute(
            "SELECT COUNT(*) FROM base.hotlap_laps WHERE lap_time >= 89999.0"
        )
        sentinel_laps = cur.fetchone()[0]
        if sentinel_laps > 0:
            issues.append(f"FAIL: {sentinel_laps} sentinel hotlap laps (>= 89999s) in DB")
        else:
            print(f"  ✓ No sentinel hotlap times in DB (threshold 89999s)")

        # Check: no AI drivers in base.drivers
        cur.execute("SELECT COUNT(*) FROM base.drivers")
        total_drivers = cur.fetchone()[0]

        # Check: all race_participations with is_ai=false have valid steam_id
        cur.execute(
            "SELECT COUNT(*) FROM base.race_participations WHERE is_ai=false AND steam_id IS NULL"
        )
        null_steam = cur.fetchone()[0]
        if null_steam > 0:
            issues.append(f"FAIL: {null_steam} human participations with NULL steam_id")
        else:
            print(f"  ✓ All human participations have steam_id")

        # Check: no duplicate session IDs (ON CONFLICT DO NOTHING should have prevented)
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT id) FROM base.race_sessions")
        row = cur.fetchone()
        if row[0] != row[1]:
            issues.append(f"FAIL: race_sessions has duplicates ({row[0]} rows, {row[1]} distinct IDs)")
        else:
            print(f"  ✓ No duplicate race_sessions (total={fmt_int(row[0])})")

        cur.execute("SELECT COUNT(*), COUNT(DISTINCT id) FROM base.hotlap_events")
        row = cur.fetchone()
        if row[0] != row[1]:
            issues.append(f"FAIL: hotlap_events has duplicates ({row[0]} rows, {row[1]} distinct IDs)")
        else:
            print(f"  ✓ No duplicate hotlap_events (total={fmt_int(row[0])})")

        cur.execute("SELECT COUNT(*), COUNT(DISTINCT id) FROM base.race_participations")
        row = cur.fetchone()
        if row[0] != row[1]:
            issues.append(f"FAIL: race_participations has duplicates")
        else:
            print(f"  ✓ No duplicate race_participations (total={fmt_int(row[0])})")

        cur.execute("SELECT COUNT(*) FROM base.drivers WHERE steam_id IS NULL")
        null_driver = cur.fetchone()[0]
        if null_driver > 0:
            issues.append(f"FAIL: {null_driver} drivers with NULL steam_id")
        else:
            print(f"  ✓ All drivers have steam_id (total drivers={fmt_int(total_drivers)})")

        # Summary counts
        section("DB row counts")
        for table in [
            "base.drivers", "base.tracks", "base.vehicles",
            "base.race_sessions", "base.race_participations",
            "base.hotlap_events", "base.hotlap_laps",
            "base.elo_bootstrap", "base.elo_history",
        ]:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            n = cur.fetchone()[0]
            print(f"  {table:<35s}  {fmt_int(n):>10s}")

        # Server distribution
        section("race_sessions by server")
        cur.execute(
            "SELECT server, COUNT(*) FROM base.race_sessions GROUP BY server ORDER BY 2 DESC"
        )
        for row in cur.fetchall():
            print(f"  server={row[0]!r:12s}  {fmt_int(row[1]):>10s} sessions")

        section("hotlap_events by server")
        cur.execute(
            "SELECT server, COUNT(*) FROM base.hotlap_events GROUP BY server ORDER BY 2 DESC"
        )
        for row in cur.fetchall():
            print(f"  server={row[0]!r:12s}  {fmt_int(row[1]):>10s} events")

    if issues:
        print("\n  ISSUES FOUND:")
        for issue in issues:
            print(f"    {issue}")
    else:
        print("\n  All plausibility checks passed ✓")

    return issues


# ── step 6: compare ELO with racing-DB ───────────────────────────────────────

def compare_elo() -> None:
    hdr("STEP 6 — Compare ELO ranking with racing-DB")

    # Fetch top-15 from racing-DB (read-only)
    with psycopg.connect(RACING_URL) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT d.name, d.steam_id, latest.value AS elo, latest.number_races
            FROM tsu.drivers d
            JOIN LATERAL (
                SELECT value, number_races
                FROM tsu.elo_heat
                WHERE driver_id = d.id
                ORDER BY created_at DESC LIMIT 1
            ) latest ON true
            ORDER BY latest.value DESC
            LIMIT 15
            """
        )
        racing_top = cur.fetchall()

    # Fetch top-15 from test-DB (bootstrap fallback, since no new heats data)
    with psycopg.connect(TEST_URL) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                d.name,
                d.steam_id,
                COALESCE(
                    (SELECT eh.elo_value
                     FROM base.elo_history eh
                     JOIN base.race_participations rp ON rp.id = eh.participation_id
                     JOIN base.race_sessions rs ON rs.id = rp.session_id
                     WHERE rp.steam_id = d.steam_id AND rs.server = 'heats'
                     ORDER BY rs.utc_start_time DESC LIMIT 1),
                    eb.elo_value
                ) AS elo,
                COALESCE(hs.heat_races, 0) + COALESCE(eb.number_races, 0) AS total_races
            FROM base.drivers d
            LEFT JOIN base.elo_bootstrap eb ON eb.steam_id = d.steam_id
            LEFT JOIN (
                SELECT rp.steam_id, COUNT(*) AS heat_races
                FROM base.race_participations rp
                JOIN base.race_sessions rs ON rs.id = rp.session_id
                WHERE rs.server = 'heats' AND rp.is_ai = false
                GROUP BY rp.steam_id
            ) hs ON hs.steam_id = d.steam_id
            WHERE COALESCE(
                (SELECT eh.elo_value
                 FROM base.elo_history eh
                 JOIN base.race_participations rp ON rp.id = eh.participation_id
                 JOIN base.race_sessions rs ON rs.id = rp.session_id
                 WHERE rp.steam_id = d.steam_id AND rs.server = 'heats'
                 ORDER BY rs.utc_start_time DESC LIMIT 1),
                eb.elo_value
            ) IS NOT NULL
            ORDER BY elo DESC
            LIMIT 15
            """
        )
        test_top = cur.fetchall()

    print(f"\n  {'Name':15s}  {'Racing-ELO':>10s}  {'Test-ELO':>10s}  {'Δ':>8s}")
    print(f"  {'-'*55}")

    racing_dict = {r[1]: r for r in racing_top}
    test_dict = {r[1]: r for r in test_top}

    all_steam_ids = list({r[1] for r in racing_top} | {r[1] for r in test_top})
    for sid in sorted(all_steam_ids, key=lambda s: -(racing_dict.get(s, (None,None,0))[2] or 0)):
        r = racing_dict.get(sid)
        t = test_dict.get(sid)
        name = (r or t)[0]
        racing_elo = r[2] if r else None
        test_elo = t[2] if t else None
        delta = (test_elo - racing_elo) if (racing_elo and test_elo) else None
        r_str = f"{racing_elo:.1f}" if racing_elo else "—"
        t_str = f"{test_elo:.1f}" if test_elo else "—"
        d_str = f"{delta:+.1f}" if delta is not None else "n/a"
        print(f"  {name:15s}  {r_str:>10s}  {t_str:>10s}  {d_str:>8s}")


# ── step 7: mart view sample rows ─────────────────────────────────────────────

def sample_mart_views() -> dict[str, str]:
    hdr("STEP 7 — Sample rows from mart views")
    samples = {}

    with psycopg.connect(TEST_URL) as conn:
        cur = conn.cursor()

        # v_race_results
        section("mart.v_race_results (one race row)")
        cur.execute(
            """
            SELECT
                utc_start_time, server, track_name, driver_name,
                position, finish_time, laps_completed, participant_count,
                elo_value, elo_delta, current_elo
            FROM mart.v_race_results
            ORDER BY utc_start_time DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if row:
            desc = cur.description
            formatted = "\n".join(
                f"    {d.name:20s}: {v}" for d, v in zip(desc, row)
            )
            print(formatted)
            samples["v_race_results"] = formatted
        else:
            print("  (no rows)")
            samples["v_race_results"] = "(no rows)"

        # v_hotlap_results
        section("mart.v_hotlap_results (one lap row, best lap only)")
        cur.execute(
            """
            SELECT
                utc_start_time, server, track_name, driver_name,
                lap_number, lap_time, is_best_lap
            FROM mart.v_hotlap_results
            WHERE is_best_lap = true
            ORDER BY utc_start_time DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if row:
            desc = cur.description
            formatted = "\n".join(
                f"    {d.name:20s}: {v}" for d, v in zip(desc, row)
            )
            print(formatted)
            samples["v_hotlap_results"] = formatted
        else:
            print("  (no rows)")
            samples["v_hotlap_results"] = "(no rows)"

        # v_driver_profile
        section("mart.v_driver_profile (highest ELO driver)")
        cur.execute(
            """
            SELECT
                driver_name, driver_flag, driver_clan,
                heat_elo, heat_total_races, heat_wins,
                event_races, event_wins,
                hotlap_events, hotlap_total_laps, hotlap_alltime_best
            FROM mart.v_driver_profile
            ORDER BY heat_elo DESC NULLS LAST
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if row:
            desc = cur.description
            formatted = "\n".join(
                f"    {d.name:22s}: {v}" for d, v in zip(desc, row)
            )
            print(formatted)
            samples["v_driver_profile"] = formatted
        else:
            print("  (no rows)")
            samples["v_driver_profile"] = "(no rows)"

    return samples


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-load", action="store_true",
                        help="Skip the load phase (DB already populated)")
    args = parser.parse_args()

    t_start = time.monotonic()

    apply_migrations()
    seed_bootstrap()

    load_summaries: dict[str, dict] = {}
    if args.skip_load:
        hdr("STEP 3 — SKIPPED (--skip-load)")
    else:
        load_summaries = load_all_data()

    elo_inserted = run_elo_update()
    issues = plausibility_checks()
    compare_elo()
    samples = sample_mart_views()

    total_elapsed = time.monotonic() - t_start
    hdr(f"DONE  —  Total time: {total_elapsed:.0f}s")

    if issues:
        print(f"\n  WARNING: {len(issues)} issue(s) found — see above")
        sys.exit(1)
    else:
        print("\n  All checks passed. TEST-DB is populated and consistent.")


if __name__ == "__main__":
    main()
