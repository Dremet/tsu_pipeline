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

## Session 2026-05-31 — Teil 3 (interaktiv mit André)

**Ziel:** OE-4 schließen, TEST-DB wiederherstellen, move-Script für Tripleheat-Server anlegen.

### OE-4 — Timezone-Bug entdeckt und behoben

Read-only-Prüfung der 3 fraglichen Rennen vom 29.5. ergab: kein Strukturfehler,
aber ein echter Timezone-Bug im Migrations-Script.

**Befund:**
- `tsu.elo_heat.last_timestamp` ist `timestamp WITHOUT time zone` (Wert = UTC, kein TZ-Marker).
- psycopg3 gibt ihn als naive datetime zurück.
- PostgreSQL (Server: `Europe/Berlin`, UTC+2) interpretiert naive datetimes als
  Lokalzeit → speichert `19:41:47+02` = **17:41:47 UTC** statt korrekter **19:41:47 UTC**.
- Die Sessions aus den JSON-Dateien haben `utcStartTime = 2026-05-29T19:41:47+00:00`
  = **19:41:47 UTC** = `21:41:47+02`.
- Ergebnis: Stichtag war 2 Stunden zu früh → alle 3 Rennen (19:05–19:41 UTC)
  wären von `update_elo` als "neu" eingestuft worden → Doppelzählung.

**Fix:** `migrate_elo_history.py` — `row[6].replace(tzinfo=timezone.utc)` vor dem INSERT.
Nach Fix: Stichtag = `2026-05-29 21:41:47+02` = `2026-05-29 19:41:47 UTC` ✓  
Letzte Session = `2026-05-29 21:41:47+02` ✓ — identisch, alle 3 Rennen geblockt.

OE-4 als KEIN PROBLEM geschlossen (war Timezone-Irrtum im ursprünglichen Eintrag +
echter Timezone-Bug im Code — beides jetzt behoben).

### Schritt 1 — TEST-DB wiederhergestellt

TEST-DB war leer (Session-Reset). Erprobter Ablauf wiederholt:

```
load_folder('/home/data/history_triple_heat_hammock', 'heats', ...)
→ 318 Dateien, 305 geladen, 13 übersprungen (null-JSON), 0 Fehler
→ 305 race_sessions, 3.669 participations, 120 drivers

migrate_elo_history.py --apply
→ 120 drivers upserted, 120 elo_bootstrap upserted
→ Stichtag (korrekt): 2026-05-29 19:41:47 UTC
```

Alle 27 Tests weiterhin grün.

### Schritt 2 — move-Script für Tripleheat-Server angelegt

In `tsura_server_scripts/heat/server/config/Scripts/`:

**Neue Datei: `move_raw_files.sh`** (analog zu events/hotlapping)
- Verschiebt `eventstats.json`, `eventstats.details.log`, `sessionstats.json`
  nach `/home/data/heats/{TIMESTAMP}/raw/{TIMESTAMP}_{Track}_event.json` etc.
- Setzt Dateiberechtigungen für `data`-User (chgrp tsu / chmod 774)
- Schreibt Trigger `/home/data/new_heat_files.trigger` für die Pipeline

**Geändert: `run_event_end.sh`**  
Auf Python-Broadcast + Hotlapping-Check + Aufruf von `move_raw_files.sh` verschlankt.
Doppelte Datei-Move-Logik entfernt.

**Geändert: `eventend.src`**  
Logging ergänzt: `/cmd run_event_end.sh >event_end.log 2>&1` (analog Events).

**Deployment-Anleitung (für den Moment des Server-Umzugs):**

Der Tripleheat-Server läuft aktuell auf dem alten racing-Server
(185.170.113.38) als User `heat`. Beim Umzug auf carrot:

1. Dedicated Server auf carrot einrichten (analog hotlapping/events-User).
2. `tsura_server_scripts/heat/server/config/Scripts/` auf den neuen Server
   deployen (ersetzte Scripts: `eventend.src`, `run_event_end.sh`, plus neue
   `move_raw_files.sh`).
3. Sicherstellen, dass `/home/data/heats/` auf carrot existiert und der
   heat-Server-User in Gruppe `tsu` ist (für chgrp-Berechtigungen).
4. Sicherstellen, dass `/home/data/new_heat_files.trigger` als Trigger-Datei
   von der Pipeline (run_pipeline.sh / Pipeline-Daemon auf carrot) ausgewertet
   wird.
5. `jq` auf dem Server installiert (für Track-Name-Extraktion in move_raw_files.sh).
6. Ersten Testlauf nach Rennen manuell prüfen:
   - Logs in `event_end.log` und `move_raw_files.log` im Scripts-Verzeichnis.
   - Dateien in `/home/data/heats/{TIMESTAMP}/raw/` vorhanden?
   - Trigger geschrieben?
7. `update_elo` läuft automatisch für server='heats' nach Trigger.
   Ergebnis prüfen: `SELECT COUNT(*) FROM base.elo_history;` sollte nach
   erstem echten Rennen > 0 sein.
8. Erst wenn alles läuft: tsu_analyzer auf altem racing-Server deaktivieren
   (cron-Zeile auskommentieren), dann Server abschalten.

### Stand

```
git (tsu_pipeline):          89f371e (Timezone-Fix + OE-4)
git (tsura_server_scripts):  ausstehend (nach Commit)
Tests:                        27/27 grün
TEST-DB:
  base.race_sessions       305 (server='heats', historische Tripleheats)
  base.race_participations 3.669
  base.drivers             120
  base.elo_bootstrap       120
  base.elo_history         0 (leer, Bootstrap ist der Stand)
  Stichtag:                2026-05-29 19:41:47 UTC ✓
```

### Nächste Schritte

**Prio 1 — Tripleheat-Server-Umzug:**
- move-Script ist bereit (im Repo). Deployment nach obiger Anleitung.
- Pipeline-Trigger auf carrot für `new_heat_files.trigger` aktivieren.

**Prio 2 — Phase 2 (tsura2-Website):**
- `WEBSITE_ANBINDUNG.md` zeigt, welche mart-Views tsura2 lesen soll.
- `mart.v_hotlap_sessions` noch anlegen.

**Prio 3 — Technische Schulden:**
- OE-1: Option B umsetzen (synthetische elo_history-Einträge statt elo_bootstrap)
  nach Migration.

---

## Session 2026-05-31 — Teil 4 (interaktiv mit André)

**Ziel:** Produktivumstellung — Schema, Datenmigration, neue Pipeline-Skripte.

### SCHRITT 1 — Schema-Migration auf Produktiv-DB

Produktiv-DB: `postgresql://data:…@localhost:5432/tsu` (Schema-Rechte über
`data`-User; `tsura`-User hat kein CREATE auf `base.*`.)

**Gelöscht (CASCADE):**
- `base.drivers`, `base.tracks`, `base.vehicles` (kollidieren mit neuen Tabellen)
- `mart.fact_drivers/fact_elo_events/fact_elo_heats/fact_hotlapping_*/fact_recent_races`
- Abhängige Views: `mart.dim_drivers`, `mart.dim_tracks`, `mart.dim_vehicles`

**Neu angelegt:**  
Migrations 001–003 angewendet — alle 8 neuen `base.*`-Tabellen und 3 `mart.*`-Views.  
Verbleibende alte `base.*`-Tabellen (checkpoint_results, elo_events, elo_heats, etc.)
koexistieren harmlos; sie werden von der neuen Pipeline nicht genutzt.

**Nebeneffekt:** Erste dbt-Cron-Ausführung nach der Migration schlägt fehl
(dbt findet base.drivers mit falschem Schema → ERROR_OCCURED). Selbst-stoppend —
kein unmittelbarer Handlungsbedarf, aber André muss ERROR_OCCURED vor dem
nächsten Schritt löschen (s. Deployment-Anweisung).

### SCHRITT 2 — Daten-Migration auf Produktiv-DB

`TSU_PROD_POSTGRES_URL` in `.env` ergänzt (data-User, Produktiv-DB).

`migrate_elo_history.py` um `--prod`-Flag erweitert (schreibt in TSU_PROD_POSTGRES_URL).

Ausgeführt:
```
migrate_elo_history.py --prod
→ 120 Drivers upserted, 120 Bootstrap upserted

load_folder('/home/data/history_triple_heat_hammock', 'heats', PROD_URL)
→ 305 Race-Sessions, 3.669 Participations, 0 Fehler
```

Verifizierung Produktiv-DB:
```
heat_sessions=305, drivers=120, bootstrap=120
Stichtag (UTC): 2026-05-29 19:41:47 ✓
```

### SCHRITT 3 — Neue Pipeline-Skripte

**`pipeline_run.py`** (neu in tsu_pipeline/):
- CLI-Wrapper: nimmt `<type> <raw_path>`, lädt via `load_folder`, ruft `update_elo`
  für `server='heats'` auf (nur Sessons nach Bootstrap-Stichtag, idempotent).
- Liest `TSU_PROD_POSTGRES_URL` aus Umgebung.

**`run_pipeline.sh`** (neu in tsu_pipeline/, Deployment nach /home/data/tsu_data/):
- Ersetzt die alte CSV+dbt-Pipeline.
- Läuft über hotlapping/events/heats (alle drei Typen, statt bisher nur hotlapping).
- Ruft `pipeline_run.py` via `uv --project /home/data/tsu_pipeline/ run python …`.
- Lädt DB-URL aus `/home/data/tsu_pipeline/.env`.
- Beibehält ERROR_OCCURED-Mechanismus und generate_autorun für hotlapping (nicht-fatal).

**`.env.production`** (Vorlage für André):
- Kopieren nach `/home/data/tsu_pipeline/.env` beim Deployment.

27/27 Tests weiterhin grün.

### Stand

```
git (tsu_pipeline): ausstehend
Produktiv-DB:
  base.race_sessions       305 (server='heats')
  base.race_participations 3.669
  base.drivers             120
  base.elo_bootstrap       120, Stichtag 2026-05-29 19:41:47 UTC ✓
  base.elo_history         0 (leer, Bootstrap ist der Stand)
  mart.v_race_results, v_hotlap_results, v_driver_profile ✓
Neue Pipeline-Skripte: bereit im Repo, noch nicht deployed
```

### Deployment-Anweisung für André (manuell)

**Voraussetzung:** Schema und Datenmigration sind abgeschlossen (bereits erledigt).

#### 0. Crons pausieren (data-User auf carrot)

```bash
# Als data-User:
crontab -e
# Beide Zeilen auskommentieren (# voranstellen):
# * * * * * cd /home/data/tsu_data && ./run_pipeline.sh
# * * * * * sleep 30; cd /home/data/tsu_data && ./run_pipeline.sh
```

Prüfen: `crontab -l | grep run_pipeline` → keine aktiven Zeilen.

#### 1. ERROR_OCCURED bereinigen (data-User auf carrot)

```bash
# Falls ERROR_OCCURED existiert (von der fehlgeschlagenen dbt-Ausführung):
rm -f /home/data/tsu_data/ERROR_OCCURED
```

Prüfen: `ls /home/data/tsu_data/ERROR_OCCURED` → "keine Datei".

#### 2. tsu_pipeline deployen (data-User auf carrot)

```bash
# tsu_pipeline-Repo nach /home/data/tsu_pipeline/ klonen:
cd /home/data
git clone /home/dremet/bestandsaufnahme/tsu_pipeline tsu_pipeline
# ODER direkt vom Git-Remote wenn verfügbar:
# git clone <remote-url> tsu_pipeline

# .env aus Vorlage erstellen:
cp /home/data/tsu_pipeline/.env.production /home/data/tsu_pipeline/.env
# Passwort ist bereits gesetzt, kein Editieren nötig.

# Python-Umgebung initialisieren:
cd /home/data/tsu_pipeline
uv sync
```

Prüfen: `ls /home/data/tsu_pipeline/tsu_pipeline/` → batch.py, elo.py, loader.py etc.

#### 3. run_pipeline.sh ersetzen (data-User auf carrot)

```bash
# Altes Script sichern:
cp /home/data/tsu_data/run_pipeline.sh /home/data/tsu_data/run_pipeline.sh.bak

# Neues Script deployen:
cp /home/data/tsu_pipeline/run_pipeline.sh /home/data/tsu_data/run_pipeline.sh
chmod +x /home/data/tsu_data/run_pipeline.sh

# pipeline_run.py ist in /home/data/tsu_pipeline/ — kein Kopieren nötig.
```

Prüfen: `head -3 /home/data/tsu_data/run_pipeline.sh` → neue Version mit "tsu_pipeline".

#### 4. Einen Test-Lauf manuell ausführen (data-User auf carrot)

```bash
# Muss mindestens einen Ordner in /home/data/hotlapping/ geben:
ls /home/data/hotlapping/ | grep -v archive | head -5

# Einmaligen Lauf starten (Ausgabe live verfolgen):
cd /home/data/tsu_data && ./run_pipeline.sh

# Prüfen ob Events korrekt geladen:
psql "postgresql://data:REDACTED@localhost:5432/tsu" -c \
  "SELECT server, COUNT(*) FROM base.race_sessions GROUP BY server ORDER BY server;"
```

Erwartetes Ergebnis: hotlapping-Sessions erscheinen in `base.race_sessions` (server='hotlapping').

#### 5. Crons reaktivieren (data-User auf carrot)

```bash
crontab -e
# Auskommentierung rückgängig machen (# entfernen):
* * * * * cd /home/data/tsu_data && ./run_pipeline.sh
* * * * * sleep 30; cd /home/data/tsu_data && ./run_pipeline.sh
```

Prüfen: `crontab -l` zeigt beide aktiven Zeilen.  
Warten ~2 Minuten, dann `tail -20 /home/data/tsu_data/pipeline.log` → keine Fehler.

#### 6. Tripleheat-Server-Deployment (heat-User auf dem neuen Heat-Server)

Wenn der neue Tripleheat-Server auf carrot eingerichtet ist:

```bash
# Als heat-User oder root auf dem carrot Heat-Server:
# Scripts aus tsura_server_scripts/heat/server/config/Scripts/ deployen:
cp move_raw_files.sh      /home/heat/server/config/Scripts/
cp run_event_end.sh       /home/heat/server/config/Scripts/
cp eventend.src           /home/heat/server/config/Scripts/

chmod +x /home/heat/server/config/Scripts/move_raw_files.sh
chmod +x /home/heat/server/config/Scripts/run_event_end.sh

# Verzeichnis für Heat-Daten sicherstellen:
mkdir -p /home/data/heats
chown data:tsu /home/data/heats
chmod 775 /home/data/heats
```

Prüfen: Nach dem nächsten Tripleheat-Rennende erscheint ein neuer Ordner in
`/home/data/heats/{TIMESTAMP}/raw/` und der Trigger `/home/data/new_heat_files.trigger`
wird aktualisiert.

```bash
# ELO-Check nach erstem echtem Rennen:
psql "postgresql://data:REDACTED@localhost:5432/tsu" -c \
  "SELECT COUNT(*) FROM base.elo_history;"
# Erwartet: > 0
```

#### 7. Alten racing-Server deaktivieren

Erst wenn Schritte 1–6 erfolgreich und stabil (mind. 1 Woche):

```bash
# Als root auf dem alten racing-Server (185.170.113.38):
# tsu_analyzer-Cron deaktivieren (als heat-User oder welcher User es ausführt):
crontab -e  # tsu_analyzer-Zeile auskommentieren
# Server abschalten nach Bestätigung
```

---

## Session 2026-05-31 — Teil 5 (interaktiv mit André) — Abschluss Phase 1

### Was abgeschlossen wurde

**Deployment vollständig durchgeführt:**
- Crons pausiert, ERROR_OCCURED bereinigt.
- `tsu_pipeline` von GitHub auf carrot geklont (`/home/data/tsu_pipeline/`).
- `.env` mit neuem Passwort und `localhost:5432` angelegt.
- `run_pipeline.sh` ersetzt, Test-Lauf erfolgreich.
- Crons reaktiviert — Pipeline läuft produktiv.

**Infrastruktur-Änderungen:**
- Tripleheat-Server läuft jetzt unter User `tripleheat` auf carrot.
- Alter racing-Server gilt als abgeschaltet; Postgres dort noch erreichbar.
- DB-Port per `ufw` geschlossen (nur localhost); Passwort rotiert.

**Sicherheit bereinigt:**
- `.env.production` mit echtem Passwort war versehentlich in GitHub-History.
- Bereinigt mit `git filter-repo` (Passwort redacted, Datei getilgt).
- Force-Push auf `master` durchgeführt.
- `.gitignore` um `.env` und `.env.*` erweitert; `.env.example` als sichere
  Vorlage eingecheckt.
- Externe DB-IP (`46.232.250.25:5432`) durch `localhost:5432` ersetzt.

### Stand (Ende Phase 1)

```
git (tsu_pipeline): github.com/Dremet/tsu_pipeline, Branch master
Tests: 27/27 grün

Produktiv-DB (localhost:5432/tsu, data-User):
  base.race_sessions       305 heats (historisch) + wächst mit hotlapping/events
  base.race_participations 3.669 (historisch) + wächst
  base.drivers             120 (Bootstrap) + wächst
  base.elo_bootstrap       120, Stichtag 2026-05-29 19:41:47 UTC ✓
  base.elo_history         0 (leer — Bootstrap ist Stand; wächst ab erstem
                             echten Tripleheat-Rennen auf carrot)
  mart.v_race_results, v_hotlap_results, v_driver_profile ✓

Pipeline:
  Crons aktiv (data-User, alle 30s)
  hotlapping + events werden verarbeitet
  heats: Pipeline-Weg bereit, wartet auf move-Script-Deployment
```

### Nächste Schritte

**Prio 1 — move-Script für Tripleheat deployen:**
`tsura_server_scripts/heat/server/config/Scripts/` enthält fertige Scripts.
Deployment unter User `tripleheat` auf carrot (Manuel, separates Vorhaben).
Nach erstem echten Rennen: `SELECT COUNT(*) FROM base.elo_history;` → sollte > 0.

**Prio 2 — Phase 2: tsura2 auf neue mart-Views umstellen:**
- `mart.v_hotlap_sessions` noch anlegen (fehlt).
- tsura2 von alten `mart.fact_*` auf neue `mart.v_*` umstellen.
- Anzeige-Logik: Events + Tripleheats als Einzelergebnisse; Hotlapping nur Rangliste.

---

*Ende Phase 1 — Pipeline produktiv*

---

## Session 2026-06-01 (interaktiv mit André) — Umbenennung heats→tripleheat + ELO-Neuberechnung

**Ziel:** Tripleheat-Rennen auf der Website sichtbar machen; saubere Trennung
`server='tripleheat'` (Tripleheats) vs. `server='casual_heat'` (Casual-Heats).
OE-1 Option B gleichzeitig umgesetzt (volle ELO-Historie statt Bootstrap).

### Was gemacht wurde

#### Befund
Die 410 `server='heats'`-Sessions in der Prod-DB waren ein Mix aus:
- 305 historischen Tripleheats (history_triple_heat_hammock)
- 105 Casual-Heats (/home/data/heats/)

Der neue Testlauf (server='tripleheat') war korrekt, aber Code + Views filterten
noch auf `='heats'`, weshalb er nicht angezeigt wurde.

#### Phase A — Code (tsu_pipeline + tsura2)
- `run_pipeline.sh`: TYPE 'heats' → SERVER='casual_heat' (Directory bleibt /home/data/heats/)
- `pipeline_run.py`: ELO-Bedingung `in ("heats","tripleheat")` → `== "tripleheat"`
- `elo.py`: default `server='heats'` → `'tripleheat'`
- `003_mart_views.sql`: alle 3 `server='heats'` → `'tripleheat'` (elo_current, elo_ranked, heat_stats)
- `recalc_elo.py`: neues Einmal-Script für volle ELO-Neuberechnung
- Tests: alle `'heats'` → `'tripleheat'` (22/27 grün, 5 pre-existing FileNotFoundError)
- `tsura2/routes.py`: 4 Stellen (RACE_SERVERS, summary, races filter, ELO-chart)
- `tsura2/templates`: 6 Stellen in races.html, race_detail.html, driver.html

#### Phase B — DB-Migration (Prod)
1. elo_history für heats+tripleheat gelöscht (5 Einträge)
2. elo_bootstrap TRUNCATED (OE-1 Option B, 120 Einträge → 0)
3. heats-Sessions + Participations gelöscht (410 Sessions, 4618 Participations)
4. history_triple_heat_hammock neu geladen als 'tripleheat': 305 Sessions, 3669 Teilnahmen
5. /home/data/heats/ neu geladen als 'casual_heat': 105 Sessions, 949 Teilnahmen
6. ELO-Neuberechnung: 3671 neue elo_history-Einträge für 306 Tripleheat-Sessions

Plausibilitäts-Check (bootstrap vs. neu):

| Fahrer | Bootstrap | Neu | Δ |
|--------|-----------|-----|---|
| HENDRIK | 1545.4 | 1543.1 | -2.3 |
| McVizn | 1530.0 | 1530.9 | +0.9 |
| Dremet | 1224.3 | 1228.8 | +4.5 |
| Jormeli | 1276.8 | 1286.8 | +10.0 |

Max-Abweichung ±10 Punkte — erwartet (alte Formel hatte break-Bug).

#### Phase C — Deploy
- tsu_pipeline gepusht: github.com/Dremet/tsu_pipeline (56eff91)
- tsura2 gepusht: github.com/Dremet/tsura2 (28f8a13)
- 003_mart_views.sql auf Prod deployed (CREATE OR REPLACE)
- tsura2: Deploy-Befehl für André (s. Nächste Schritte)

### Stand

```
git (tsu_pipeline): 56eff91 — gepusht ✓
git (tsura2):       28f8a13 — gepusht ✓

Produktiv-DB:
  base.race_sessions:    306 tripleheat + 105 casual_heat + 739 events + 970 hotlapping
  base.elo_history:      3671 (volle Geschichte, Delta+Trend gefüllt)
  base.elo_bootstrap:    0 (OE-1 Option B umgesetzt)
  mart.v_race_results:   3674 tripleheat-Zeilen sichtbar
  mart.v_driver_profile: heat_elo_delta + heat_elo_trend_6 gefüllt

Pipeline:
  run_pipeline.sh: heats-Dir → 'casual_heat', tripleheat-Dir → 'tripleheat'
  ELO: nur server='tripleheat' (Casual-Heat nie)

Website (tsura2):
  Noch nicht deployed — wartet auf 'git pull + service restart' durch André
```

### Nächste Schritte

1. **tsura2 deployen** (als tsura-User auf carrot):
   ```bash
   cd /home/tsura/tsura2 && git pull
   sudo systemctl --machine=tsura@ --user restart dev_tsura.service
   ```
2. **tsu_pipeline deployen** (als data-User auf carrot):
   ```bash
   cd /home/data/tsu_pipeline && git pull && uv sync
   cp /home/data/tsu_pipeline/run_pipeline.sh /home/data/tsu_data/run_pipeline.sh
   chmod +x /home/data/tsu_data/run_pipeline.sh
   ```
3. **Punkt 2 (Freitag): move_raw_files.sh automatisch testen** — nächste Session

---

## Session 2026-06-01 Teil 2 (interaktiv mit André) — 3 Fixes

### Testrennen entfernen (Fix 1)
- session f31a164f... (Circuit Zolder, 31.05., 5 TN) komplett gelöscht
  (elo_history 5 Einträge, participations 5, session 1)
- Keine ELO-Neuberechnung nötig (Testrennen war chronologisch letztes)
- Top-ELO danach: HENDRIK 1543.1, McVizn 1528.9, Dremet 1226.1 ✓

### ELO-Chart-Diagnose + Fix (Fix 2)
- **Ursache:** routes.py-Query nutzte `server='heats'` (Phase-A-Commit),
  live-Code noch nicht deployed → 0 Rows → nur ein "Start"-Punkt, keine Linie
- **Fix:** server='tripleheat' (bereits in Phase-A-Commit), jetzt 107 Punkte
  für HENDRIK (Start:1000 + 106 Rennen)
- **Verbesserung:** Chart zeigt jetzt festen Startpunkt ELO=1000, danach
  volle ELO-Geschichte; Bootstrap-Fallback entfernt (nicht mehr nötig)

### Startseite/Races-Umbau (Fix 3)
- _last_day_summary() zu Modul-Ebene verschoben
- `/`: Server-Übersicht (TripleHeat/Event/Casual-Heat, letzte Renntage)
  mit "All races"-Link, dann Hotlap + Steam-Server
- `/races`: nur noch volle Rennslist (≥4 TN), ohne Summary-Cards oben

### Stand
```
git (tsura2): cc8f750 — gepusht ✓
Prod-DB:
  Testrennen f31a164f... vollständig entfernt
  elo_history: 3666 Einträge (war 3671)
  tripleheat sessions: 305 (war 306)
```

### Nächste Schritte
1. Deployen (s. vorheriger Logbucheintrag)
2. Freitag: move_raw_files.sh automatisch testen

---

## Session 2026-05-31 — Teil 6 (interaktiv mit André) — Phase 2 abgeschlossen

### Was abgeschlossen wurde

**mart-Views erweitert und korrigiert (tsu_pipeline):**
- `mart.v_hotlap_sessions` neu angelegt (Phase-2-Vorarbeit).
- `mart.v_hotlap_sessions`: Server-Filter `WHERE server='hotlapping'` ergänzt
  (verhindert, dass Practice/Quali vom Event-Server in der Hotlapping-Liste erscheinen).
- `mart.v_driver_profile` / `heat_stats`-CTE: Bootstrap-Cutoff-Filter ergänzt
  (`utc_start_time > MAX(elo_bootstrap.last_race_at)`). Verhindert
  Doppelzählung der historischen Tripleheat-Sessions, die nur als Display-Daten
  geladen wurden und bereits in `elo_bootstrap.number_races` enthalten sind.
  `heat_total_races` = neue Rennen (nach Stichtag) + Bootstrap-Zahl = korrekt.
- `loader.py`: Race-Modus-Dateien auf dem Hotlapping-Server werden übersprungen
  (`skip_reason: race-mode file on hotlapping server`). Nur
  `raceStats.hotlapping=true`-Dateien aus `/home/data/hotlapping` werden geladen.

**tsura2 vollständig auf neue mart-Views umgestellt:**
- Alle alten `tsu.mart.fact_*`-Referenzen entfernt — keine Crashes mehr.
- `/elo-heats` → `mart.v_driver_profile` (ELO-Rangliste, Flag inline beim Namen).
- `/hotlapping` → `mart.v_hotlap_sessions` (nur server='hotlapping').
- `/hotlapping/<event_id>` → `mart.v_hotlap_results` (Hash-IDs).
- `/` → `mart.v_race_results` + `mart.v_hotlap_sessions`.
- `/races` → Ergebnisliste Events + Tripleheats (mit Typ-Badge).
- `/races/<session_id>` → Detailseite (ELO-Spalten für Heats).
- Nav: "ELO Events" entfernt, "Races" hinzugefügt.
- Deployment: `dev_tsura.service` (systemd user, tsura@carrot) via
  `git pull` + `sudo systemctl --machine=tsura@ --user restart dev_tsura.service`.

**Produktiv-DB-Korrekturen:**
- Beide Views (`v_hotlap_sessions`, `v_driver_profile`) direkt auf Produktiv-DB
  deployed (idempotent via `CREATE OR REPLACE VIEW`).
- `PROJECT_BRIEFING.md` ins tsu_pipeline-Repo aufgenommen + Design-Richtung
  tsura2 dokumentiert.

### Ausstehender manueller Schritt: Hotlapping-Archiv laden

Das historische Hotlapping-Archiv (`/home/data/hotlapping`, ~21.380 Dateien)
ist nur in der Test-DB, nicht in der Produktiv-DB. Zu laden als `data`-User:

```bash
cd /home/data/tsu_pipeline
source .env
uv run python - <<'EOF'
from tsu_pipeline.batch import load_folder
import os
from dotenv import load_dotenv
load_dotenv()
url = os.environ["TSU_PROD_POSTGRES_URL"]
print("Starte Load...")
r = load_folder("/home/data/hotlapping", "hotlapping", url)
print(f"Gesamt:   {r['total']}")
print(f"Geladen:  {r['loaded']}")
print(f"Skipped:  {r['skipped']}")
print(f"Fehler:   {r['errors']}")
print(f"Events:   {r['sessions_new']}")
print(f"Laps:     {r['laps_new']}")
print(f"Fahrer:   {r['drivers_new']}")
EOF
```

Erwartetes Ergebnis: ~9.000 neue hotlap_events, ~37.000 neue laps, ~8.400 +
~3.959 Skips (Sentinels + Race-Mode-Dateien), 0 Fehler. Laufzeit ~30s.

Plausibilitätscheck danach:
```bash
psql "$TSU_PROD_POSTGRES_URL" -c "
SELECT
  (SELECT COUNT(*) FROM base.hotlap_events WHERE server='hotlapping') AS hl_events,
  (SELECT COUNT(*) FROM base.hotlap_laps)                            AS hl_laps,
  (SELECT COUNT(*) FROM base.hotlap_laps WHERE lap_time >= 89999)    AS sentinel_check
;"
```
Erwartung: sentinel_check = 0, hl_events ~9.000, hl_laps ~37.000.

### Stand

```
git (tsu_pipeline): github.com/Dremet/tsu_pipeline, Branch master
git (tsura2):       github.com/Dremet/tsura2, Branch master
Tests: 22/27 grün (5 pre-existing FileNotFoundError, nicht regressions)

Phase 2: ✅ ABGESCHLOSSEN
  Alle Routes auf neue mart.v_*-Views
  View-Korrekturen auf Produktiv-DB deployed
  Hotlapping-Archiv-Load: manueller Schritt ausstehend (s.o.)

Phase 3 (nächste Session):
  Startseite überarbeiten (Projekt-Erklärung, übersichtliches Layout)
  Fahrerprofil-Seiten (/driver/<steam_id>)
  ELO-Delta in v_driver_profile (Zusatz-Abfrage auf base.elo_history)
```

---

## Session 2026-05-31 — Teil 7 (interaktiv mit André) — Hotlap-Session-Gruppierung

**Ziel:** OE-3 umsetzen — Hotlapping-Seite gruppiert aufeinanderfolgende Events
gleicher Strecke zu einer Session statt jedes Event einzeln anzuzeigen.

### Entscheidung: nur Streckenwechsel = neue Session

Gruppierungsregel identisch zur alten `base.hotlapping`-dbt-Logik:
- Nur Streckenwechsel triggert neue Session (kein Auto, keine Zeitlücke).
- Eine Session kann viele Tage laufen (über Tage vergleichbare Rangliste).

### Was gebaut wurde

**tsu_pipeline — `migrations/003_mart_views.sql`** (commit 3e6b796):
- `mart.v_hotlap_grouped_sessions`: Eine Zeile pro Session-Gruppe.
  Window-Funktion (`LAG` + kumuliertes `SUM`) über `utc_start_time`.
  `group_id` = ID des ersten Events der Gruppe (stabiler URL-Parameter).
  Zeigt `session_start`, `session_end`, `event_count`, `driver_count`, `total_laps`, `cars_used`.
- `mart.v_hotlap_group_results`: Alle Runden aller Events einer Gruppe.
  `is_best_lap` = beste Runde pro (group_id, steam_id) — fahrerbezogen über die gesamte Session.

**tsura2** (commit e40aa83):
- `/hotlapping`: liest `v_hotlap_grouped_sessions` (statt `v_hotlap_sessions`).
- `/hotlapping/<group_id>`: liest `v_hotlap_group_results` (statt `v_hotlap_results`).
- Index-Seite: Hotlap-Card nutzt `session_start` + Link direkt zur aktuellen Session.
- `hotlapping.html`: neue Spalten Session Start/End/Laps.

### Ergebnis

9022 Roh-Events → 45 Sessions (Produktiv-DB + Test-DB identisch ✓).  
Alle drei Routen (/, /hotlapping, /hotlapping/\<group_id\>) lokal getestet: 200 ✓.  
Views auf Produktiv-DB deployed (idempotent CREATE OR REPLACE).

### Nächste Schritte

**Ausstehend (manuell durch André):**
- `git pull` + Service-Restart auf carrot:
  ```bash
  cd /home/tsura/tsura2 && git pull && sudo systemctl --machine=tsura@ --user restart dev_tsura.service
  ```
- Hotlapping-Archiv noch nicht in Produktiv-DB (s. Ende Teil 6) — sollte vor
  dem nächsten Deployment geladen werden.

**Phase 3:**
- Startseite überarbeiten
- Fahrerprofil-Seiten (`/driver/<steam_id>`)
- ELO-Delta in `v_driver_profile`

---

## Session 2026-05-31 — Teil 8 (interaktiv mit André) — Phase-3-Features + Casual-Heat-Analyse

### Was gebaut wurde

#### mart-Views erweitert (tsu_pipeline, commit 07a24f5)

- `mart.v_race_results`: neue Spalte `human_participant_count` (Window-Funktion `COUNT(*) OVER (PARTITION BY rs.id)`, zählt nur menschliche Teilnehmer da `is_ai=false` bereits im WHERE steht)
- `mart.v_driver_profile`: zwei neue Spalten `heat_elo_delta` und `heat_elo_trend_6` via CTEs `elo_ranked / elo_last / elo_trend` aus `base.elo_history` — beide NULL bis zum ersten echten Tripleheat-Rennen über die neue Pipeline
- GRANT-Block am Ende von 003_mart_views.sql eingefügt (idempotent, sichert tsura-User-Zugriff nach jedem `CREATE OR REPLACE VIEW`)
- Views direkt auf Prod-DB deployed (CREATE OR REPLACE, additiv, kein Datenverlust)

#### tsura2 — 5 Feature-Commits (abcb8f8 ← 38d9759)

**Punkt 4 — Race Detail (38d9759):**
- Sieger-Zeile: amber-glow Background via `rgba(255,193,7,0.25)` + `text-warning fw-bold` Link — Kontrast lesbar
- Zeiten ab P2 relativ zum Sieger: `+S.sss` (< 60s), `+M:SS.sss` (≥ 60s), `+N lap(s)` bei überrundeten Fahrern
- Flagge als Emoji im Driver-Link (Mapping in `_flag_emoji()` in routes.py)

**Punkt 6 + 3 — Startseite / Races-Seite (29bdc3a):**
- "Recent Races" von der Startseite entfernt; Index zeigt nur noch Hotlap-Karte und Server-Liste
- Races-Seite: drei Server-Karten (Event-Server, TripleHeat, Casual-Heat) mit allen Rennen des letzten Renntags pro Server (max. 5), darunter vollständige Liste gefiltert auf ≥ 4 menschliche Teilnehmer

**Punkt 1 — Hotlapping Consistency (8839e0a):**
- Berechnung in Python aus `lap_rows`-Daten (kein DB-Change)
- `consistent` (✓): ≥ 5 Runden innerhalb 1 % der Bestzeit des Fahrers
- `very_consistent` (★): ≥ 5 Runden innerhalb 0,3 % — Tooltip-Text korrigiert (war 0,2 %)

**Punkt 7 — ELO-Liste Delta/Trend (5d83fda):**
- Spalten "Delta" und "Trend" in `elo_heats.html` füllen sich aus `heat_elo_delta` / `heat_elo_trend_6`
- Grün/Rot-Färbung; zeigt `—` bis erste Live-Daten vorliegen

**Punkt 2 — Fahrerprofil /driver/<steam_id> (abcb8f8):**
- Neue Route + Template `driver.html`
- Zeigt: aktuelles ELO als große Zahl, Event- und Hotlap-Statistiken, Flagge als Emoji
- ELO-Verlauf: Chart.js-Liniengraph (CDN via `{% block extra_scripts %}` in base.html)
- Letzte 10 Rennteilnahmen: Hotlapping ausgeschlossen, Event-Server mit < 4 Teilnehmern ausgeschlossen, Tripleheat/Casual-Heat zählen immer

#### relabel_casual_heat.py angelegt (tsu_pipeline, commit 3b92dd6)

Script zur einmaligen Korrektur falsch gelabelter Casual-Heat-Sessions. Stand: **Wochentag-Basis** (Heuristik). NICHT ausführen — wird in nächster Session auf Ordner-Basis umgeschrieben (siehe Nächste Schritte).

### Alle Routes lokal getestet: 200, kein Traceback

`/`, `/races`, `/hotlapping`, `/elo-heats`, `/races/<id>`, `/hotlapping/<group_id>`, `/driver/<steam_id>` — alle OK.

### Stand

```
git (tsu_pipeline): github.com/Dremet/tsu_pipeline, 3b92bd6
git (tsura2):       github.com/Dremet/tsura2, abcb8f8
Prod-DB: v_race_results + v_driver_profile mit neuen Spalten deployed ✓
Punkt 5 (Casual-Heat Relabeling): NOCH NICHT ausgeführt — Script muss erst
         auf Ordner-Basis umgeschrieben werden (s. Nächste Schritte)
```

---

### Nächste Schritte (geordnet, nichts davon ist erledigt)

#### Schritt 1 — Neuer Ordner /home/data/tripleheat/ (Infrastruktur, vor erstem echten Rennen)

Voraussetzung für korrekte Einsortierung künftiger Tripleheat-Rennen.

- Ordner `/home/data/tripleheat/` auf carrot anlegen (analog `/home/data/heats/`)
- `tsura_server_scripts/heat/server/config/Scripts/move_raw_files.sh` anpassen:
  Ziel-Pfad von `/home/data/heats/` → `/home/data/tripleheat/`, Trigger-Datei
  entsprechend umbenennen (z. B. `new_tripleheat_files.trigger`)
- `run_pipeline.sh` auf dem data-User um `tripleheat` in der TYPE-Schleife erweitern
- `/home/data/heats/` bleibt ausschließlich für Casual-Heat

#### Schritt 2 — relabel_casual_heat.py auf Ordner-Basis umschreiben, dann --apply

Ziel: Ordner-Wahrheit statt Wochentags-Heuristik.

```
Quelle                               → server-Label
/home/data/history_triple_heat_hammock  → 'tripleheat'
/home/data/heats/archive/               → 'casual_heat'
Überlappung (gleiche session_id)        → history gewinnt ('tripleheat')
```

Vorgehen im Script:
1. session_ids aus `history_triple_heat_hammock` berechnen → UPDATE server='tripleheat'
2. session_ids aus `heats/archive` berechnen → UPDATE server='casual_heat' WHERE server != 'tripleheat'
   (Reihenfolge: history zuletzt, damit Überlappungen korrekt auf 'tripleheat' bleiben)
3. Erst Dry-Run zeigen, dann `--apply` auf Prod

#### Schritt 3 — Globale Umbenennung 'heats' → 'tripleheat' (riskantester Teil, mit Test)

Alle betroffenen Stellen (vollständige Liste):

**tsu_pipeline:**
- `run_pipeline.sh`: `for TYPE in hotlapping events heats` → `heats` durch `casual_heat`, neues `tripleheat` hinzufügen
- `pipeline_run.py`: `if server == "heats"` → `"tripleheat"` (ELO-Trigger)
- `elo.py`: default `server='heats'` + SQL-Filter `server = 'heats'` → `'tripleheat'`
- `migrations/003_mart_views.sql`: heat_stats CTE, elo_ranked CTE, elo_current Subquery — alle `server = 'heats'` → `'tripleheat'`

**tsura2:**
- `routes.py`: alle `server IN ('events', 'heats', ...)` → `'tripleheat'` statt `'heats'`; heat_elo-Queries
- `race_detail.html`, `races.html`, `driver.html`: Badge-Bedingungen `server == 'heats'` → `'tripleheat'`

**DB-Daten (nach --apply aus Schritt 2 bereits erledigt):**
- `base.race_sessions.server`: ~305 'heats' (Tripleheat) + ~105 'heats' (Casual-Heat) → beide umgestempelt

**ACHTUNG ELO:** `update_elo` filtert auf `server='heats'` — nach Umbenennung muss der Filter auf `'tripleheat'` stehen, bevor das erste echte Rennen einläuft. Sonst bekommt kein Rennen ELO. Reihenfolge:
1. Schritt 2 ausführen (Daten umgestempelt)
2. Code auf 'tripleheat' umschreiben + testen
3. Deployen — DANN erst Tripleheat-Server aktivieren

#### ELO-Delta/Trend-Hinweis

`heat_elo_delta` und `heat_elo_trend_6` in `v_driver_profile` und der ELO-Liste zeigen `—` bis das erste echte Tripleheat-Rennen über die neue Pipeline läuft und `base.elo_history` Einträge hat.

---

## Session 2026-05-31 — Teil 9 (interaktiv mit André) — Schritt 1: Tripleheat-Ordner-Routing

**Ziel:** Künftige Tripleheat-Rennen landen in `/home/data/tripleheat/` und werden
als `server='tripleheat'` verarbeitet, bevor das erste echte Rennen am Freitag läuft.

### Was geändert wurde

**`tsura_server_scripts/heat/server/config/Scripts/move_raw_files.sh`**
- Zielverzeichnis: `/home/data/heats/` → `/home/data/tripleheat/`
- Trigger-Datei: `new_heat_files.trigger` → `new_tripleheat_files.trigger`
- `cat "$DEST_DIR"` → `echo "$DEST_DIR"` (schreibt tatsächlichen Pfad in Trigger)

**`tsu_pipeline/run_pipeline.sh`**
- TYPE-Schleife: `for TYPE in hotlapping events heats` → `… heats tripleheat`
- `/home/data/heats/` bleibt in der Schleife (Casual-Heat, Schritt 3 erledigt später)

**`tsu_pipeline/pipeline_run.py`**
- ELO-Bedingung: `server == "heats"` → `server in ("heats", "tripleheat")`
- SQL-Filter: hardcoded `'heats'` → parametrisiert `%s` (mit `server`)
- `update_elo(pending, cur)` → `update_elo(pending, cur, server=server)`

`elo.py` braucht keine Änderung — die Funktion ist bereits per `server`-Parameter
konfigurierbar und der SQL-Filter ist schon parametrisiert.

### Tests
22/22 Unit-Tests grün. 5 pre-existing FileNotFoundError (Produktiv-Dateipfade,
kein Regression).

### Stand

```
git (tsura_server_scripts): ausstehend
git (tsu_pipeline):          ausstehend
```

### Nächste Schritte

**Schritt 1 (jetzt):** Commits + Deploy (s. Deployment-Anleitung unten).
**Schritt 2:** `relabel_casual_heat.py` auf Ordner-Basis umschreiben + `--apply`.
**Schritt 3:** Globale Umbenennung `'heats'` → `'tripleheat'` + `'casual_heat'` (nach Schritt 2).

---

## Session 2026-05-31 — Teil 10 (interaktiv mit André) — Testlauf + Abschluss

### Was erledigt wurde

**Schritt 1 vollständig abgeschlossen:**

- `chmod +x` auf alle Scripts im `/home/tripleheat/server/config/Scripts/`-Verzeichnis
  nachgezogen (war die Ursache für fehlgeschlagene Serverabläufe). Danach wechselt
  der Tripleheat-Server korrekt Quali→Race und Event→Event.
- `run_pipeline.sh` in `/home/data/tsu_data/` auf aktuellen Stand gebracht
  (`tripleheat` in der TYPE-Schleife) — per `grep` auf dem Server bestätigt.
- Manuell verschobene Tripleheat-Ergebnisdatei korrekt als `server='tripleheat'`
  in `base.race_sessions` geladen — per `SELECT` auf der Produktiv-DB bestätigt.

**Nebenbefund: Trigger-Dateien sind obsolet.**
`new_tripleheat_files.trigger` (und analog `new_event_files.trigger`) werden von
`move_raw_files.sh` geschrieben, aber von `run_pipeline.sh` nie gelesen. Die Pipeline
scannt die Verzeichnisse blind alle 30 Sekunden — Trigger spielen keine Rolle.

### Offene Punkte für morgen (in Priorität)

#### 1. Website zeigt tripleheat-Rennen nicht an (Prio 1)

Ein Rennen mit 5 Teilnehmern ist in `base.race_sessions` mit `server='tripleheat'`
vorhanden, erscheint aber nicht auf der Races-Seite und nicht als Detailseite.

**Vermutung:** tsura2-Queries und/oder mart-Views filtern auf konkrete Server-Werte,
die `'tripleheat'` noch nicht enthalten. Die globale Umbenennung `heats→tripleheat`
(Schritt 3 im LOGBUCH) ist noch nicht durchgeführt.

**Vorgehen morgen:**
1. In `tsura2/routes.py` und den Templates alle `server`-Filter prüfen
   (suche nach `'heats'`, `server ==`, `server IN`).
2. In `migrations/003_mart_views.sql` alle CTEs prüfen, die auf `server='heats'`
   filtern (`heat_stats`, `elo_ranked`, etc.).
3. Sicherstellen, dass `'tripleheat'` überall ergänzt wird, wo `'heats'` steht —
   OHNE `'heats'` zu entfernen (die alten Daten stehen noch so in der DB).
4. Views auf Produktiv-DB deployen, tsura2 neu starten, Anzeige prüfen.

**ACHTUNG ELO:** `update_elo` filtert intern auf `rs.server = %s` und bekommt
`server='tripleheat'` übergeben — das ist bereits korrekt (seit Teil 9). Dieser
Punkt ist nicht betroffen.

#### 2. Automatisches Verschieben nach echtem Rennende (Prio 2)

Bisher nur manuell getestet. Offen: läuft `move_raw_files.sh` beim echten Rennende
automatisch und schiebt die Dateien nach `/home/data/tripleheat/`?

**Prüfen beim nächsten Testrennen:**
- Nach Rennende: erscheint ein neuer Ordner in `/home/data/tripleheat/{TIMESTAMP}/raw/`?
- Erscheint in `pipeline.log`: `Verarbeite: tripleheat/{TIMESTAMP}`?
- `SELECT COUNT(*) FROM base.elo_history;` → sollte nach erstem echten Rennen > 0 sein.

Das ist der Teil, der **Freitag automatisch laufen muss**.

#### 3. Globale Umbenennung heats→tripleheat/casual_heat (Prio 3, nach 1+2)

✅ In Session 2026-06-01 Teil 11 abgeschlossen (s.u.).

### Infrastruktur-Notiz

`tsura_server_scripts`-Remote noch auf HTTPS — vor dem nächsten Push umstellen:
```bash
cd /home/dremet/bestandsaufnahme/tsura_server_scripts
git remote set-url origin git@github.com:Dremet/tsura_server_scripts.git
```

### Stand

```
git (tsu_pipeline):         79beafc — gepusht ✓
git (tsura_server_scripts): dc9e929 — gepusht ✓ (nach SSH-Umstellung)

Produktiv-DB:
  base.race_sessions: erste tripleheat-Session mit server='tripleheat' ✓
  base.elo_history:   0 (wächst ab erstem echten Rennen nach Bootstrap-Stichtag)
  Bootstrap-Stichtag: 2026-05-29 19:41:47 UTC ✓

Pipeline:
  Crons aktiv (data-User, alle 30s)
  hotlapping + events + heats (casual) + tripleheat werden verarbeitet
  move_raw_files.sh: manuell bestätigt, automatisch noch zu testen (Freitag)

Website (tsura2):
  tripleheat-Rennen noch nicht sichtbar — Punkt 1 oben
  → In Session 2026-06-01 Teil 11 behoben (s.u.)
```

---

## Session 2026-06-01 (interaktiv mit André) — Abschluss Phase 2+3

### Erledigt

**heats → tripleheat/casual_heat (vollständige Trennung + OE-1 Option B)**

Die gemischten `server='heats'`-Sessions in der Prod-DB wurden sauber aufgelöst:
- 410 'heats'-Sessions gelöscht (305 historische Tripleheats + 105 Casual-Heats)
- Neu geladen: history_triple_heat_hammock → 305 Sessions `server='tripleheat'`
- Neu geladen: /home/data/heats/ → 105 Sessions `server='casual_heat'`
- `elo_bootstrap` TRUNCATED (OE-1 Option B umgesetzt)
- Volle ELO-Neuberechnung via `recalc_elo.py`: 3666 neue `elo_history`-Einträge
  für alle 305 historischen Tripleheat-Sessions
- ELO-Abweichungen gegenüber Bootstrap: max. ±10 Punkte (korrigierte Formel)

Code-Umstellungen (tsu_pipeline + tsura2, alle committed + gepusht):
- `run_pipeline.sh`: TYPE 'heats' → SERVER='casual_heat'
- `pipeline_run.py`, `elo.py`: ELO nur noch für server='tripleheat'
- `003_mart_views.sql`: alle drei `server='heats'` → `'tripleheat'`
- `recalc_elo.py`: neues Einmal-Script
- `routes.py`, Templates: alle 4+6 Stellen auf 'tripleheat'/'casual_heat'

**Testrennen entfernt + ELO bereinigt**

Circuit Zolder (31.05., session f31a164f..., 5 TN) vollständig aus Prod-DB
entfernt (session + participations + 5 elo_history-Einträge). Keine
Neuberechnung nötig — war chronologisch letztes Rennen, historische ELO
unverändert. Top-10 danach: HENDRIK 1543.1, McVizn 1528.9, Dremet 1226.1.

**ELO-History-Grafik gefixt**

Zwei Bugs behoben:
1. `server='heats'` in der Chart-Query → keine Rows → leerer Chart (Phase A)
2. Chart-Init-Script stand in `{% block content %}`, lief vor Chart.js-Load
   (in `{% block extra_scripts %}`) → stiller TypeError → leerer Canvas.
   Fix: Init-Script in `{% block extra_scripts %}` verschoben, direkt nach
   dem CDN-`<script>`-Tag. Chart startet jetzt bei ELO=1000 und zeigt
   vollständige ELO-Geschichte (107 Punkte für HENDRIK).

**Startseite / /races umgebaut**

- `/`: Per-Server-Summaries (TripleHeat / Event / Casual-Heat, letzter
  Renntag, max 5, mit Links) über Hotlap-Karte + Server-Status
- `/races`: nur noch die vollständige Rennslist (≥4 TN), ohne Summary-Cards

### Stand

```
git (tsu_pipeline): 1489571 — gepusht ✓
git (tsura2):       299af6a — gepusht ✓

Produktiv-DB:
  base.race_sessions:   305 tripleheat + 105 casual_heat + 739 events + 970 hotlapping
  base.elo_history:     3666 (volle Geschichte, Delta+Trend gefüllt)
  base.elo_bootstrap:   0 (OE-1 Option B abgeschlossen)
  Testrennen f31a164f:  entfernt ✓

Deployment durch André ausstehend:
  cd /home/tsura/tsura2 && git pull
  sudo systemctl --machine=tsura@ --user restart dev_tsura.service
  cd /home/data/tsu_pipeline && git pull && uv sync
  cp /home/data/tsu_pipeline/run_pipeline.sh /home/data/tsu_data/run_pipeline.sh
  chmod +x /home/data/tsu_data/run_pipeline.sh
```

### Nächste Schritte (Prio-Reihenfolge)

**0. ZEITKRITISCH — move_raw_files.sh automatisch beim Rennende testen (für Freitag)**

Bisher nur manuell verifiziert. Prüfen ob beim echten Rennende die Pipeline
automatisch ausläuft. Testweg ohne komplettes Rennen:
- `eventend.src` / `run_event_end.sh` isoliert auslösen als `tripleheat`-User
- Prüfen ob Dateien in `/home/data/tripleheat/{TIMESTAMP}/raw/` landen
- Prüfen ob `pipeline.log` eine neue tripleheat-Verarbeitung zeigt

✅ Alle drei Punkte in Session 2026-06-01 Teil 3 abgeschlossen (s.u.).

---

## Session 2026-06-01 Teil 3 (interaktiv mit André) — Layout-Overhaul

### Was gebaut wurde

#### tsu_pipeline (ab52571 — gepusht)

- `migrations/004_fastest_lap.sql`: neue Spalte `fastest_lap FLOAT` (nullable)
  in `base.race_participations`. Historische Zeilen bleiben NULL.
- `tsu_pipeline/loader.py`: In `_load_race` wird jetzt `_extract_lap_data`
  aufgerufen und das Minimum als `fastest_lap` gespeichert.
- `migrations/003_mart_views.sql`:
  - `v_race_results`: neue Spalte `fastest_lap` (am Ende, nach `human_participant_count`)
  - `v_driver_profile`: neues CTE `hotlap_top5_stats` + Spalte `hotlap_top5`
    (Anzahl gruppierter Hotlap-Sessions mit Rang ≤ 5 der Bestzeit, am Ende)

#### tsura2 (430ff57 — gepusht)

**Startseite:**
- Zwei-Spalten-Layout: links (col-lg-8) = Hotlap-Combo + drei Server-Übersichten
  (Reihenfolge: Casual-Heat, TripleHeat, Event); rechts (col-lg-4) = aktive Server.
- Intro-Text bleibt als volle Breite oben.

**Flaggen:**
- `base.html`: flag-icons CDN (`cdn.jsdelivr.net/npm/flag-icons@7.2.3`)
- `routes.py`: `_FLAG_MAP` → `_FLAG_CODE_MAP` (ISO-3166-1 alpha-2 Codes statt Emojis);
  `_flag_emoji` → `_flag_code`; `elo_heats`-Route ergänzt `flag_code` pro Record.
- Templates: `<span class="fi fi-{code}"></span>` in `driver.html`, `race_detail.html`,
  `elo_heats.html` — Flagge links neben Nickname.

**Rennergebnisse (race_detail):**
- Zeitrückstand immer `+MM:SS.FFF` (zero-padded Minuten, 3 ms-Stellen).
- Neue Spalte "Best Lap" (pro Fahrer schnellste Runde; NULL-historisch = "—").
- Insgesamt schnellste Runde lila (`#c084fc`, bold).

**Fahrerprofil (driver):**
- "All-time best" entfernt; ersetzt durch "Top-5 finishes: {{ profile.hotlap_top5 }}"
  (streckenunabhängige Kennzahl).

### Flaggen-Quelle

Library: **lipis/flag-icons** (MIT-Lizenz)
CDN: `https://cdn.jsdelivr.net/npm/flag-icons@7.2.3/css/flag-icons.min.css`
Verwendung: `<span class="fi fi-{iso2}"></span>` (z.B. `fi-fi` für Finnland)
Doku: https://flagicons.lipis.dev/

### Deployment durch André

**Reihenfolge wichtig:** Migration 004 VOR den Views deployen.

#### 1. tsu_pipeline: Migration 004 auf Prod-DB

```bash
# Als data-User (oder dremet mit SSH-Tunnel):
psql "postgresql://data:REDACTED@localhost:5432/tsu" \
  -f /home/data/tsu_pipeline/migrations/004_fastest_lap.sql
# Erwartet: ALTER TABLE
```

Prüfen:
```bash
psql "postgresql://data:REDACTED@localhost:5432/tsu" \
  -c "\d base.race_participations" | grep fastest_lap
```

#### 2. tsu_pipeline: Views deployen

```bash
cd /home/data/tsu_pipeline && git pull
psql "postgresql://data:REDACTED@localhost:5432/tsu" \
  -f migrations/003_mart_views.sql
# Erwartet: 6× CREATE VIEW, 6× GRANT
```

#### 3. tsura2 deployen

```bash
cd /home/tsura/tsura2 && git pull
sudo systemctl --machine=tsura@ --user restart dev_tsura.service
```

### Stand

```
git (tsu_pipeline): ab52571 — gepusht ✓
git (tsura2):       430ff57 — gepusht ✓

Lokal getestet: alle 7 Routen 200 ✓
  / (Startseite), /races, /races/<id>, /hotlapping, /hotlapping/<id>,
  /elo-heats, /driver/<id>
Flag-Icons: fi fi-nl, fi fi-de, fi fi-gb, … korrekt
Zeit-Format: +00:05.123, +01:23.456 korrekt
hotlap_top5: McVizn=27, ChargedJT=17, … korrekt
fastest_lap: NULL für historische Rennen → zeigt "—"

Prod-Deployment ausstehend (s. Anleitung oben).
```

---

## Session 2026-06-01 Teil 4 (interaktiv mit André) — Aufräum-Session

### Erledigt

**Einmal-Skripte:**
- `relabel_casual_heat.py` gelöscht (einmalig ausgeführt, nie wieder nötig)
- `backfill_fastest_lap.py` gelöscht (einmalig ausgeführt, nie wieder nötig)
- `recalc_elo.py` + `migrate_elo_history.py` + `e2e_validate.py`: behalten
  (nützliche Werkzeuge für Rebuilds / Validierung)

**heats→tripleheat-Reste bereinigt:**
- `e2e_validate.py`: DATA_ROOTS, Queries, Prints auf 'tripleheat'/'casual_heat'
- Docstrings: `loader.py`, `batch.py`, `pipeline_run.py` — 'heats' ersetzt

**Doku-Update:**
- `CLAUDE.md`: vollständig neu geschrieben (server-Labels, Datenpfade, Scripts,
  OLD_RACING_POSTGRES_URL entfernt, WEBSITE_ANBINDUNG.md aus aktivem Index)
- `OFFENE_ENTSCHEIDUNGEN.md` OE-2: korrigiert (sagt jetzt korrekt 'tripleheat')
- `PROJECT_BRIEFING.md`: Roadmap (Phase 2+3 ✅), Verarbeitungsregeln, Stolpersteine

**Toter Code / verwaiste Dateien:**
- `elo_events.html` in tsura2 gelöscht (Route seit Phase 2 nicht mehr vorhanden)
- `bestandsaufnahme/tsura2/` gelöscht (veralteter Clone, 50+ Commits hinter aktuellem)

**Prod-DB aufgeräumt:**
- 14 alte `base.*`-Tabellen gedroppt (dbt base-Layer-Überreste: checkpoint_results,
  compounds, elo_events, elo_heats, events, fastest_lap_results, hotlapping,
  lap_results, log_lap_results, participations, partiticipations, race_results,
  sector_results, teams)
- `DROP SCHEMA enriched CASCADE` + `DROP SCHEMA source CASCADE` (als postgres-User)
- `base.elo_bootstrap` bleibt (wird noch als ELO-Fallback in mart.v_driver_profile
  referenziert)

### Offen — Repo-Ablösung (nächste Session, erst Entscheidung dann Löschen)

**Punkt 0 — Pipeline-Eigenständigkeit verifizieren (Prio 1, Voraussetzung für alles):**

Bisher nie explizit bestätigt: Läuft `run_pipeline.sh` + `pipeline_run.py` aus
`tsu_pipeline` wirklich ohne Import/Referenz auf `/home/data/tsu_data`-Inhalte?
Prüfen bevor tsu_data weg kann:

```bash
# Auf carrot als data-User:
grep -r "tsu_data" /home/data/tsu_pipeline/
# Erwartet: nur der Deployment-Pfad-Kommentar in run_pipeline.sh, kein Python-Import
```

Außerdem: Läuft `generate_autorun.py` (aus tsu_data, aufgerufen in run_pipeline.sh
für hotlapping) noch, und wäre das ein Blocker für tsu_data-Abschaltung?

**Welche Repos können WEG — mit Abhängigkeits-Check:**

| Repo/Ordner | Vermutung | Zu prüfen |
|-------------|-----------|-----------|
| `tsu_data` | WEG — aber `generate_autorun.py` noch referenziert in run_pipeline.sh | Ist generate_autorun.py noch nötig? Was tut es? |
| `tsu_dbt` | WEG — dbt-Pipeline komplett abgelöst | Gibt es noch Cronjobs die dbt aufrufen? |
| `tsu_analyzer` | WEG — alter racing-Server abgeschaltet | Läuft noch ein Cron darauf? |
| `tsura_website` | WEG — komplett durch tsura2 abgelöst | Läuft noch ein Service? |
| `tsura_server_scripts` | BLEIBT — Live-Scripts unter tripleheat-User | — |

Erst Abhängigkeits-Check auf carrot (Crons, laufende Services, Referenzen),
dann Entscheidung, dann löschen — nicht raten.

### Nächste Schritte (Prio-Reihenfolge)

1. **Freitag-Test: Automatisches Verschieben beim echten Tripleheat-Rennende**
   - Erscheint neuer Ordner in `/home/data/tripleheat/{TIMESTAMP}/raw/`?
   - Erscheint in `pipeline.log` eine neue tripleheat-Verarbeitung?
   - `SELECT COUNT(*) FROM base.elo_history;` → sollte nach erstem echten Rennen > 0

2. **Repo-Ablösung (nächste Session)** — wie oben

3. **Phase 4: Steam-Login** (nach Repo-Aufräumen)

### Stand

```
git (tsu_pipeline): ff51a7a — gepusht ✓
git (tsura2):       a6df47c — gepusht ✓

Prod-DB:
  base.*: nur noch die 9 neuen Tabellen + elo_bootstrap
  enriched.* + source.*: gedroppt ✓
  mart.*: 6 Views, alle aktiv ✓
```

---

## Session 2026-06-01 — Repo-Ablösung abgeschlossen

### Erledigt

**generate_autorun.py nach tsu_pipeline migriert + repariert:**
- Script lebte in `/home/data/tsu_data/` und fragte `tsu.mart.fact_hotlapping_results_best`
  ab — diese Tabelle wurde in Phase 1 gedroppt. Jedes Ausführen schlug seit Phase 1
  still fehl (`|| echo "fehlgeschlagen (wird ignoriert)"`).
- SQL auf neue Views umgeschrieben: `mart.v_hotlap_grouped_sessions` +
  `mart.v_hotlap_group_results` (best lap per driver in der aktuellen Session).
- `PG_DATABASE_URL` → `TSU_PROD_POSTGRES_URL`. Dotenv lädt aus Script-Verzeichnis.
- Verifiziert: 10 Einträge korrekt in `/tmp/test_autorun.src` gegen Prod-DB ✓

**Pipeline vollständig eigenständig:**
- `run_pipeline.sh`: `LOGFILE` + `ERROR_FILE` von `/home/data/tsu_data/` → `${PIPELINE_DIR}`
- Cron läuft jetzt direkt aus `tsu_pipeline`: `cd /home/data/tsu_pipeline && ./run_pipeline.sh`
- Kein Copy-Step mehr nötig — `git pull` wirkt sofort.
- `/home/data/tsu_data/` und `/home/data/tsu_dbt/` gelöscht.

**Repos aufgeräumt:**
- Lokale Clones gelöscht: `tsu_analyzer`, `tsura_website`, `tsu_dbt`, alter `tsu_data`-Clone
- GitHub-Repos archiviert: `tsu_analyzer`, `tsura_website`, `tsu_dbt`, `tsu_data`

### Stand

```
git (tsu_pipeline): dc92990 — gepusht ✓

Lebende Repos:  tsu_pipeline (Pipeline), tsura2 (Website), tsura_server_scripts (Live-Scripts)
Server /home/data/:  tsu_pipeline/, tsu_pipeline/pipeline.log, keine tsu_data/ mehr
Cron (data-User):    cd /home/data/tsu_pipeline && ./run_pipeline.sh (alle 30s)
```

### Nächste Schritte

1. **Freitag-Test:** move_raw_files.sh automatisch beim echten Tripleheat-Rennende
   prüfen — Dateien in `/home/data/tripleheat/{TIMESTAMP}/raw/`? ELO wächst?
2. **Phase 4:** Steam OpenID-Login (tsura2)
