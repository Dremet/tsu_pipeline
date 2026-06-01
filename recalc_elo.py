#!/usr/bin/env python3
"""
recalc_elo.py — ELO-Neuberechnung für alle tripleheat-Sessions.

Einmalig nach DB-Migration auszuführen (wenn elo_history + elo_bootstrap
geleert wurden). Verarbeitet alle server='tripleheat'-Sessions chronologisch.

Usage:
    uv run python recalc_elo.py [--dry-run]

--dry-run: zeigt nur Anzahl Sessions und Teilnehmer, schreibt nichts.
"""

import os
import sys
from dotenv import load_dotenv
import psycopg

load_dotenv()

from tsu_pipeline.elo import update_elo

DRY_RUN = "--dry-run" in sys.argv


def main() -> None:
    db_url = os.environ.get("TSU_PROD_POSTGRES_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: TSU_PROD_POSTGRES_URL not set", file=sys.stderr)
        sys.exit(1)

    with psycopg.connect(db_url) as conn:
        cur = conn.cursor()

        # Aktueller Stand vor Neuberechnung
        cur.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM base.race_sessions WHERE server = 'tripleheat') AS sessions,
                (SELECT COUNT(*) FROM base.elo_history) AS existing_history,
                (SELECT COUNT(*) FROM base.elo_bootstrap) AS bootstrap_entries
            """
        )
        row = cur.fetchone()
        print(f"Tripleheat-Sessions:   {row[0]}")
        print(f"elo_history (vorher):  {row[1]}")
        print(f"elo_bootstrap-Einträge: {row[2]}")
        if row[2] > 0:
            print("WARNUNG: elo_bootstrap ist nicht leer — Bootstrap-Cutoff greift!")
            print("         Bitte TRUNCATE base.elo_bootstrap ausführen und nochmals aufrufen.")
            sys.exit(1)

        if DRY_RUN:
            print("[dry-run] Keine Schreibvorgänge.")
            return

        # Alle tripleheat-Sessions holen (chronologisch)
        cur.execute(
            """
            SELECT id FROM base.race_sessions
            WHERE server = 'tripleheat'
            ORDER BY utc_start_time ASC
            """
        )
        session_ids = [r[0] for r in cur.fetchall()]
        print(f"Starte ELO-Berechnung für {len(session_ids)} Sessions...")

        inserted = update_elo(session_ids, cur, server="tripleheat")
        conn.commit()

        print(f"ELO-Neuberechnung abgeschlossen: {inserted} neue elo_history-Einträge.")

        # Plausibilitäts-Check: ELO-Werte für Top-Fahrer
        cur.execute(
            """
            SELECT d.name, eh.elo_value
            FROM base.elo_history eh
            JOIN base.race_participations rp ON rp.id = eh.participation_id
            JOIN base.race_sessions rs ON rs.id = rp.session_id
            JOIN base.drivers d ON d.steam_id = rp.steam_id
            WHERE rs.server = 'tripleheat'
            ORDER BY rs.utc_start_time DESC, eh.elo_value DESC
            LIMIT 1
            """
        )
        latest = cur.fetchone()
        if latest:
            print(f"Letzter ELO-Eintrag: {latest[0]} → {latest[1]:.1f}")

        # ELO-Verteilung
        cur.execute(
            """
            SELECT
                d.name,
                (SELECT eh2.elo_value
                 FROM base.elo_history eh2
                 JOIN base.race_participations rp2 ON rp2.id = eh2.participation_id
                 JOIN base.race_sessions rs2 ON rs2.id = rp2.session_id
                 WHERE rp2.steam_id = d.steam_id AND rs2.server = 'tripleheat'
                 ORDER BY rs2.utc_start_time DESC
                 LIMIT 1) AS current_elo
            FROM base.drivers d
            WHERE EXISTS (
                SELECT 1 FROM base.race_participations rp
                JOIN base.race_sessions rs ON rs.id = rp.session_id
                WHERE rp.steam_id = d.steam_id AND rs.server = 'tripleheat'
            )
            ORDER BY current_elo DESC NULLS LAST
            LIMIT 10
            """
        )
        rows = cur.fetchall()
        print("\nTop-10-ELO nach Neuberechnung:")
        print(f"{'Fahrer':<20} {'ELO':>8}")
        print("-" * 30)
        for r in rows:
            elo_str = f"{r[1]:.1f}" if r[1] else "—"
            print(f"{r[0]:<20} {elo_str:>8}")


if __name__ == "__main__":
    main()
