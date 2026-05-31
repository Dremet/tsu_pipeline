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

---

## Session 2026-05-31 (autonomous)

**Ziel:** Offene Entscheidungen archivieren, End-to-End-Validierung gegen echten Datenbestand, ELO-Vergleich mit racing-DB, mart-Views dokumentieren, WEBSITE_ANBINDUNG.md erstellen.

### Was gemacht wurde

#### Offene Entscheidungen archiviert

OFFENE_ENTSCHEIDUNGEN.md aktualisiert mit Status ENTSCHIEDEN:

- **OE-1 (Bootstrap-Strategie):** Richtung B beschlossen (eine ELO-Quelle), aber Umsetzung vertagt bis zur echten Tripleheat-Migration. Option A bleibt vorläufig implementiert.
- **OE-2 (Server-Label):** `server='heats'` bleibt dauerhaft. Kommentar in `elo.py` ergänzt: heats = Tripleheat, nicht Casual-Heat.
- **OE-3 (Hotlap-Gruppenbildung):** dbt-Logik bleibt auf `base.hotlap_events`, kein Python-Port — ggf. Phase-3-Thema.

#### batch.py optimiert + Indentierungs-Bug behoben

Zwei Verbesserungen:
1. **Performance:** Eine persistente Postgres-Connection statt eine pro Datei (`autocommit=True` + `conn.transaction()` pro File). 21.380 Dateien in 32s statt ~15+ Minuten.
2. **Indentierungs-Bug:** Der `if result["skipped"]:` Block saß außerhalb der `for`-Schleife — Zähler zeigten nur das letzte File. Behoben. Alle 25 Tests weiterhin grün.

#### End-to-End-Validierung: gesamter Datenbestand geladen

TEST-DB komplett geleert und neu befüllt. Ergebnis:

**Laden aus /home/data/hotlapping:**
| Kennzahl | Wert |
|---|---|
| Dateien gesamt | 21.380 |
| Geladen | 12.980 |
| Übersprungen (Sentinel-Hotlaps) | 8.400 |
| Fehler | 0 |
| Neue Sessions (hotlap_events + race_sessions) | 12.979 |
| Neue Teilnahmen | 4.452 |
| Neue Fahrer | 71 |
| Neue Hotlap-Runden | 36.929 |
| Laufzeit | 32s |

Hinweis: Der Hotlapping-Server hat sowohl Hotlap-Events (9.021) als auch Race-Sessions (3.958) produziert. 3.512 der 3.958 Race-Sessions sind Solo-Sessions (1 Teilnehmer) — erwartet für einen Hotlapping-Server mit gelegentlichen Zeitfahren.

**Laden aus /home/data/events:**
| Kennzahl | Wert |
|---|---|
| Dateien gesamt | 1.074 |
| Geladen | 884 |
| Übersprungen (null + Sumo + Sentinel) | 190 |
| Fehler | 0 |
| Neue Race-Sessions | 748 |
| Neue Hotlap-Events | 136 |
| Neue Teilnahmen | 4.559 |
| Neue Fahrer | 37 |
| Neue Hotlap-Runden | 5.289 |
| Laufzeit | 5s |

**Laden aus /home/data/heats (Casual-Heat, als Format-Stand-in):**
| Kennzahl | Wert |
|---|---|
| Dateien gesamt | 162 |
| Geladen | 113 |
| Übersprungen | 49 |
| Fehler | 0 |
| Neue Race-Sessions | 113 |
| Neue Teilnahmen | 1.033 |
| Neue Fahrer | 5 |
| Laufzeit | 1s |

**Gesamter DB-Bestand nach E2E-Lauf:**
| Tabelle | Zeilen |
|---|---|
| base.drivers | 233 |
| base.tracks | 165 |
| base.vehicles | 80 |
| base.race_sessions | 4.819 |
| base.race_participations | 10.044 |
| base.hotlap_events | 9.157 |
| base.hotlap_laps | 42.218 |
| base.elo_bootstrap | 120 |
| base.elo_history | 1.025 |

**Plausibilitätsprüfungen — alle bestanden:**
- ✓ Keine Sentinel-Zeiten in hotlap_laps (Schwellwert 89999s)
- ✓ Alle menschlichen Teilnahmen haben steam_id
- ✓ Keine Duplikate in race_sessions, hotlap_events, race_participations
- ✓ Alle Fahrer haben steam_id (keine Bots in drivers)

Hinweis: 6.459 Hotlap-Runden haben Zeiten ≥ 85s — das sind legitime lange Strecken (Nordschleife: 438s mit AE86 realistisch). Keine Sentinel-Werte.

#### ELO-Vergleich mit racing-DB (Größenordnungen)

Die casual-heat-Sessions aus /home/data/heats sind NICHT die echten Tripleheat-Rennen (die liegen auf dem alten racing-Server). Daher weichen die ELO-Werte nach der Berechnung ab. Das ist erwartet und kein Fehler. Wichtig: Die Grundlage (Bootstrap) stimmt.

| Fahrer | Racing-ELO | Test-ELO | Δ |
|---|---|---|---|
| HENDRIK | 1545.4 | 1535.8 | -9.6 |
| McVizn | 1530.0 | 1428.4 | -101.6 |
| Frozeni | 1467.8 | 1531.8 | +64.1 |
| Cosanderi | 1363.1 | 1246.1 | -116.9 |
| Igiava | 1332.8 | 1256.1 | -76.7 |
| Jormeli | 1276.8 | 1153.3 | -123.6 |
| Nestori | 1250.7 | 1257.2 | +6.5 |
| Dremet | 1224.3 | 1281.2 | +56.9 |
| cyberpunk_42 | 1205.0 | 1197.1 | -8.0 |
| Oompa | 1192.6 | 1286.8 | +94.2 |
| **TailGator** | **1176.4** | **1176.4** | **+0.0** |
| JBence | 1175.2 | 1218.4 | +43.2 |

**TailGator-Kontrollpunkt:** Dieser Fahrer hat keine casual-heat-Sessions gefahren → ELO kommt rein aus bootstrap → Δ=0.0 ✓ Beweist, dass der Bootstrap korrekt geseedet wurde.

Abweichungen bis ±125 erklärt durch die casual-heat-Rennen als Proxy (andere Ergebnisse als echte Tripleheats). Die ELO-Größenordnungen (1100–1536) sind konsistent mit racing-DB (1100–1545). Keine Ausreißer.

#### Mart-Views: Beispielzeilen

**mart.v_race_results** (neuestes Rennen):
```
utc_start_time   : 2026-05-30 20:49:42+02:00
server           : events
track_name       : Detroit Street Circuit ICS V1.01
driver_name      : TraNin
position         : 2
finish_time      : 1904.0019 (s)
laps_completed   : 30
participant_count: 8
elo_value        : None  (events bekommen kein ELO — korrekt)
current_elo      : 1096.62  (bootstrap-Fallback)
```

**mart.v_hotlap_results** (beste Runde, neuestes Event):
```
utc_start_time: 2026-05-30 20:43:06+02:00
server        : events
track_name    : Detroit Street Circuit ICS V1.01
driver_name   : Dremet
lap_number    : 1
lap_time      : 62.4963 s
is_best_lap   : True
```

**mart.v_driver_profile** (Fahrer mit höchstem ELO):
```
driver_name        : HENDRIK
driver_flag        : Finland
heat_elo           : 1535.82
heat_total_races   : 122  (neue Pipeline + Legacy aus racing-DB)
heat_wins          : 8
event_races        : 94
event_wins         : 20
hotlap_events      : 48
hotlap_total_laps  : 189
hotlap_alltime_best: 19.7381 s
```

### Stand am Ende der Session

```
Tests:  25/25 grün
E2E:    Gesamter Datenbestand geladen, alle Checks bestanden
```

Folgende Dateien neu erstellt / geändert (noch nicht committed):
- `OFFENE_ENTSCHEIDUNGEN.md` — 3 Entscheidungen archiviert
- `tsu_pipeline/batch.py` — Performance + Indentierungs-Bug behoben
- `tsu_pipeline/elo.py` — OE-2-Kommentar ergänzt
- `e2e_validate.py` — neues E2E-Validierungs-Skript
- `WEBSITE_ANBINDUNG.md` — Vorbereitung Website-Anbindung

### Nächste Schritte

**Prio 1 — Was noch aussteht für Phase 1:**
1. **Echte Tripleheat-Migration:** `migrate_elo_history.py --apply` auf Produktiv-Test-DB, sobald gemeinsam mit André. Tripleheat-Server auf carrot umstellen.
2. **Heat-Ingestion verdrahten:** move-Script für neuen Tripleheat-Server (analog zu events/hotlapping).

**Prio 2 — Phase 2 (tsura2-Website):**
3. **Website-Anbindung:** `WEBSITE_ANBINDUNG.md` zeigt, welche mart-Views tsura2 lesen soll. Noch keine tsura2-Änderungen.

**Prio 3 — Technische Schulden:**
- OE-1: Option B umsetzen (synthetische elo_history-Einträge statt elo_bootstrap) — nach Migration
- OE-3: Hotlap-Gruppenbildung evaluieren (Phase 3)
- `migrate_elo_history.py --apply` löscht veraltete bootstrap-Einträge nicht (kosmetisch)

---

## Session 2026-05-31 — Teil 2 (interaktiv mit André)

**Etappes 1–3: Tripleheat-Migration in Test-DB abgeschlossen.**

### Etappe 1 — Backup racing-DB
```
backups/racing_db_20260531_090729.dump  (96 MB, pg_dump custom format)
```
Vollständiges Backup als Sicherheitspuffer. Operativ relevant daraus: nur
`tsu.elo_heat` + `tsu.drivers`.

### Etappe 2 — Historische Tripleheat-Rennen als Display-Daten geladen
Quelle: `/home/data/history_triple_heat_hammock`

| | |
|---|---|
| Dateien | 318 (13 null-JSON übersprungen) |
| Geladen | 305 Race-Sessions, 3.669 Teilnahmen, 120 Fahrer |
| Zeitraum | Dez 2024 – Mai 2026 |
| finishedState | 255× Finished, 45× Stopped_GivePoints, 5× Stopped_NoPoints |

Diese Sessions liegen in `base.race_sessions` für Profilanzeige und Rennergebnisse.
Sie tragen **nicht** zur ELO-Berechnung bei — ihr ELO-Beitrag steckt im Bootstrap.

**Korrektur zu den 3 Rennen vom 29.5. (21:05–21:41):** Diese waren zunächst als
"Limbo" eingestuft, sind es aber nicht. Die alte racing-DB hat sie bereits
(einen Tag vor Projektstart) verarbeitet — ihr ELO steckt im Bootstrap.
Problem: der ermittelte Bootstrap-Stichtag `2026-05-29 19:41:47`
(`MAX(last_race_at)`) liegt VOR diesen 3 Rennen. Damit würde `update_elo` sie
als "neu" einstufen und nochmals ELO berechnen → Doppelzählung. Offener
Prüfpunkt, siehe OFFENE_ENTSCHEIDUNGEN.md OE-4.

### Etappe 3 — ELO-Bootstrap 1:1 aus racing-DB übernommen
`migrate_elo_history.py --apply` ausgeführt:
- 120 Fahrer in `base.drivers` geupserted
- 120 ELO-Werte in `base.elo_bootstrap` gesetzt
- Bootstrap-Stichtag: `2026-05-29 19:41:47` (`MAX(last_race_at)`)

Vergleich racing-DB ↔ Test-DB: **Δ=0.0 für alle 120 Fahrer** — exakte Kopie ✓  
`base.elo_history` ist leer (absichtlich): der Bootstrap IST der ELO-Stand.

### Code-Schutz: Stichtagsschutz in `update_elo` verankert
`update_elo` prüft per SQL-Filter automatisch:
```sql
AND rs.utc_start_time > COALESCE(
    (SELECT MAX(last_race_at) FROM base.elo_bootstrap),
    '-infinity'::timestamptz
)
```
Historische Sessions (≤ Stichtag) werden strukturell blockiert, nicht durch
Disziplin. Ohne Bootstrap → Cutoff = -infinity → alle Sessions verarbeitet.

2 neue Tests: `test_elo_bootstrap_cutoff_blocks_historical_sessions`,
`test_elo_no_bootstrap_processes_all_sessions`. **27/27 Tests grün.**

### Stand

```
git: 3fb24c5 (CLAUDE.md) → 9029571 (Migration + Cutoff-Schutz)
Tests: 27/27 grün
TEST-DB:
  base.race_sessions       305 (server='heats', historische Tripleheats)
  base.race_participations 3.669
  base.drivers             120
  base.elo_bootstrap       120 (Δ=0.0 zur racing-DB)
  base.elo_history         0 (leer — Bootstrap ist der Stand)
```

### Nächster Schritt

**Tripleheat-Server auf carrot umstellen** (move-Script schreiben, analog
zu events/hotlapping). Danach landen neue Rennen hier rein, `update_elo`
rechnet ab dem Stichtag weiter.

---

*Ende Session 2026-05-31 Teil 2*
