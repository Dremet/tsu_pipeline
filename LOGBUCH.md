# Logbuch tsu_pipeline

Chronologische Aufzeichnung der Arbeit pro Session.

---

## Session 2026-05-30 (autonomous)

**Ziel:** Arbeitspaket Tasks 1–4 aus der User-Anforderung abarbeiten.
**Ausgangspunkt:** Initial build aus Session 1 war committed (72286d1),
22 Tests grün, git-Repo noch nicht initialisiert.

### Was gemacht wurde

#### Vorbereitung
- `git init` + Erstcommit mit dem existierenden Code (72286d1)
- PROJECT_BRIEFING.md gelesen: neue geklärte Entscheidung #6 registriert:
  **ELO nur für Tripleheat**, Liga-Events bekommen kein ELO.
  `fact_elo_events` wird NICHT übernommen.
- Racing-DB inspiziert: 3642 ELO-Einträge, 120 Fahrer, 75 Rennwochen,
  Dec 2024 – Mai 2026. `tsu.elo_heat` hat eine Zeile pro Fahrer pro Rennen
  (gleicher `created_at` = selbes Rennen).

#### Task 1: Batch-Loader ✅ (cb7e59c)
- `tsu_pipeline/batch.py`: `load_folder(path, server, db_url)` 
  - Findet rekursiv alle `*_event.json`
  - Eine DB-Transaktion pro Datei → Fehler einer Datei stoppt nie den ganzen Lauf
  - **Zwei neue Fehlertypen entdeckt** in echten Daten:
    - `null`-JSON-Dateien (enthalten buchstäblich `null`) → früher Error, jetzt Skip
    - `eventType: "Sumo"` (kein `raceStats`-Key) → früher Error, jetzt Skip
  - Loader.py gepatcht: Guards für `data is None` und fehlendes `raceStats`
  - **Ergebnis gegen /home/data/events:** 1074 Dateien, 884 geladen,
    190 übersprungen (incl. null + Sumo), 0 Fehler
  - **Ergebnis gegen /home/data/heats:** 162 Dateien, 113 geladen, 49 übersprungen

#### Task 2: update_elo Tripleheat-only ✅ (cb7e59c)
- `elo.py`: `update_elo(session_ids, conn, *, server='heats')` 
  - Neuer `server`-Parameter (default `'heats'`), filtert auf SQL-Ebene
  - Liga-Events können strukturell kein ELO bekommen
- Neue Tests in `test_loader.py`:
  - `test_elo_events_server_never_gets_elo` — Events bleiben ELO-frei
  - `test_elo_two_human_heats_race` — korrekte ELO für 2-Fahrer-Heat
  - `test_elo_chronological_multi_race` — Race2 nutzt Race1-ELO als Basis;
    konkrete Werte für DriverA (P1 in R1 → +10, P2 in R2) verifiziert
  - `test_elo_idempotent` — zweiter Aufruf fügt 0 Zeilen ein
- 5 neue Tests, 25 total, alle grün

#### Task 3: migrate_elo_history.py ✅ (82fab67)
- `migrations/002_elo_bootstrap.sql`: neue Tabelle `base.elo_bootstrap`
  (steam_id PK FK auf drivers, elo_value, number_races, last_race_at, source)
- `migrate_elo_history.py`:
  - Liest `OLD_RACING_POSTGRES_URL` → `tsu.elo_heat` + `tsu.drivers` (read-only)
  - Dry-run default, `--apply` schreibt in `TSU_TEST_POSTGRES_URL`
  - Zeigt Zusammenfassung, Top-10-ELO-Tabelle, Verteilungshistogramm
  - Idempotent (ON CONFLICT DO UPDATE)
- `elo.py`: `_get_current_elo_map` nutzt `elo_bootstrap` als Fallback
  (Priorität: live elo_history > bootstrap > 1000)
- **Getestet:** 120 Fahrer + 120 ELO-Werte in Test-DB geschrieben
- Alle 25 Tests weiterhin grün

#### Task 4: Mart-Views ausbauen + Fahrerprofil-Sicht ✅ (b3e8906)
- `migrations/003_mart_views.sql`:
  - `mart.v_race_results`: um `driver_clan`, `participant_count`, `current_elo`
    (live > bootstrap > NULL) erweitert
  - `mart.v_hotlap_results`: um `driver_clan`, `track_type`, `is_best_lap`
    (Window-Funktion: ist diese Runde die Bestzeit des Fahrers in diesem Event?)
  - `mart.v_driver_profile` (NEU): eine Zeile pro echtem Fahrer mit:
    - Tripleheat-ELO (live + bootstrap-Fallback)
    - `heat_total_races` = neue Pipeline + Legacy-Rennen aus racing-DB
    - Siege, Bestposition, letztes Rennen
    - Liga-Event-Statistiken (keine ELO)
    - Hotlap-Statistiken (Anzahl Events, Runden, Alltime-Bestzeit)
- `001_base_schema.sql` angepasst: DROPs vor Views damit 003 idempotent läuft
- `conftest.py`: wendet alle 3 Migrationen pro Test-Session an

### Stand am Ende der Session

```
master: b3e8906
Tests:  25/25 grün
```

**Befüllte Test-DB:**
- 120 Fahrer (incl. bootstrap-ELO aus racing-DB)
- 113 casual-heat-Sessions (als Stand-in für Tripleheat-Format)
- 884 Liga-Event-Sessions
- 4559 race_participations
- 1025 elo_history-Einträge
- Hotlap-Laps aus den neuesten 20 Sessions

### Nächste Schritte (für nächste Session)

**Prio 1 — Was noch aussteht für Phase 1:**
1. **migrate_elo_history.py auf produktive Test-DB anwenden** wenn der
   Tripleheat-Server umgezogen ist (oder vorab gegen Test-DB testen mit
   `--apply`). Das Skript ist fertig und bereit.

2. **Heat-Ingestion auf move→pipeline-Weg** (move-Script für neuen
   Tripleheat-Server schreiben, analogous zu events). Das ist Phase 1,
   Schritt 3 in der Roadmap. Voraussetzung: Der Tripleheat-Server muss
   auf carrot laufen.

3. **Verifizieren mit echten Tripleheat-Dateien** sobald die ersten vom
   neuen Server kommen. Die Dateien liegen heute noch auf dem alten
   racing-Server (kein direkter Zugriff aus tsu_pipeline).

**Prio 2 — Technische Schulden:**
- `conftest.py` apply_schema: leichte Redundanz (002+003 werden bei jedem
  Testlauf neu angewandt; akzeptabel aber langsam bei vielen Migrationen)
- Hotlap-Gruppenbildung (OE-3): dbt-Window-Funktion noch nicht in Pipeline
- `migrate_elo_history.py --apply` löscht bootstrap NICHT vor dem Upsert
  (d.h. veraltete Fahrer in bootstrap die aus racing-DB verschwinden,
  bleiben erhalten → kein Problem, nur Kosmetik)

**Offene Entscheidungen:** siehe OFFENE_ENTSCHEIDUNGEN.md (OE-1, OE-2, OE-3)

---

*Ende Session 2026-05-30*
