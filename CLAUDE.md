# CLAUDE.md — Wegweiser für Claude Code

Diese Datei wird automatisch beim Start gelesen. Sie sagt dir, welche
weiteren Dokumente du heranziehen sollst.

## Immer zu Beginn lesen
- **PROJECT_BRIEFING.md** — maßgebliche Quelle der Wahrheit: Ziel,
  Architektur, alle getroffenen Designentscheidungen (nummerierte Liste),
  Roadmap, Stolpersteine. Was hier steht, gilt.
- **LOGBUCH.md** — chronologischer Arbeitsstand: was zuletzt passiert ist
  und wo wir stehen. Hier ablesen, woran als Nächstes gearbeitet wird.

## Bei autonomem Arbeiten (wenn der User offline ist / nicht antwortet)
- **OFFENE_ENTSCHEIDUNGEN.md** — echte Designweichen NICHT blockierend
  erfragen, sondern hier mit Begründung + Optionen eintragen und mit einer
  begründeten Annahme weiterarbeiten. Entschiedene Punkte als ENTSCHIEDEN
  markieren und ins PROJECT_BRIEFING.md übernehmen.

## Archiv (nicht aktiv laden, nur bei Rückfragen zur Historie)
- **BESTANDSAUFNAHME.md** — initiale Erkundung der Repos/DBs.
- **PHASE0_ANALYSE.md** — Auswertung der Beispieldateien + dbt-Empfehlung.

## Grundregeln (gelten immer)
- Test-DB (TSU_TEST_POSTGRES_URL) beschreiben. /home/data und Produktiv-tsu-DB
  nur bei explizit bewusstem Deployment-Schritt schreiben.
- Nach jedem Teilschritt committen und LOGBUCH.md aktuell halten.
- Am Ende jeder Session: sauberer Abschluss-Eintrag im LOGBUCH (Stand +
  nächste Schritte).

---

## Technische Orientierung

### Was dieses Paket macht
`tsu_pipeline` ist die Datenpipeline für Turbo-Sliders-Unlimited (TSU):
Sie liest rohe `*_event.json`-Dateien von den Spielservern ein, validiert
sie, schreibt in die tsu-Postgres (`base.*`-Schema) und berechnet ELO für
Tripleheat-Rennen. Die mart-Views (`mart.*`) aggregieren die Daten für die
tsura2-Website.

### Repo-Struktur
```
tsu_pipeline/
├── tsu_pipeline/
│   ├── batch.py      # load_folder() — rekursiv alle *_event.json laden
│   ├── loader.py     # load_event()  — eine Datei parsen + in DB schreiben
│   ├── validate.py   # Sentinel-Hotlaps und ungültige Events rausfiltern
│   ├── elo.py        # update_elo()  — ELO nur für server='tripleheat'
│   └── keys.py       # stabile md5-basierte IDs (session, participation, …)
├── migrations/
│   ├── 001_base_schema.sql   # base.* Tabellen
│   ├── 002_elo_bootstrap.sql # base.elo_bootstrap (Legacy-ELO-Seed, leer nach OE-1)
│   ├── 003_mart_views.sql    # mart.v_race_results, v_hotlap_results, v_driver_profile, …
│   └── 004_fastest_lap.sql   # fastest_lap-Spalte in race_participations
├── tests/            # 22 Unit-Tests grün (5 pre-existing FileNotFoundError)
├── migrate_elo_history.py  # Werkzeug: ELO-Historie aus racing-DB importieren
├── recalc_elo.py           # Werkzeug: ELO-Vollneuberechnung für alle tripleheat-Sessions
├── e2e_validate.py         # E2E-Validierung gegen echten Datenbestand (Test-DB)
├── pipeline_run.py         # CLI-Wrapper für run_pipeline.sh
├── run_pipeline.sh         # Deployment-Script nach /home/data/tsu_data/
├── PROJECT_BRIEFING.md     # ← hier lesen
└── LOGBUCH.md              # ← hier lesen
```

### Umgebung + Datenbanken
```bash
# .env liegt in ~/bestandsaufnahme/.env (ein Verzeichnis höher)
TSU_TEST_POSTGRES_URL    # Test-DB auf localhost — hier darf geschrieben werden
TSU_PROD_POSTGRES_URL    # Produktiv-tsu-DB (data-User)

# Abhängigkeiten + Tests
uv run pytest tests/        # alle Tests ausführen
uv run e2e_validate.py      # vollständige E2E-Validierung gegen Test-DB
```

### Datenpfade (auf carrot, read-only für dremet)
```
/home/data/hotlapping/                   # Hotlap-Server-Daten
/home/data/events/                       # Liga-Event-Daten
/home/data/heats/                        # Casual-Heat-Rohdaten (server='casual_heat')
/home/data/tripleheat/                   # Tripleheat-Rohdaten (server='tripleheat')
/home/data/history_triple_heat_hammock/  # historische Tripleheat-Rennen (305 Sessions)
```

### Wichtige Designentscheidungen (Kurzfassung)
Vollständig in PROJECT_BRIEFING.md — hier nur die häufig vergessenen:

- **ELO nur für `server='tripleheat'`** — Liga-Events und Hotlapping
  bekommen strukturell kein ELO (SQL-Filter in `update_elo`).
- **Bootstrap-Stichtag** — `update_elo` überspringt automatisch Sessions vor
  `MAX(elo_bootstrap.last_race_at)` (solange elo_bootstrap noch Einträge hat).
  Historische Rennen sind Display-Daten, ihr ELO steckt bereits in elo_history.
- **Bots** — kein FK auf `base.drivers`, kein ELO, keine Statistiken.
  Strukturell ausgeschlossen, nicht nur "nicht vorgesehen".
- **`finishedState` egal** — `'Finished'` und `'Stopped_GivePoints'` werden
  gleich behandelt.
- **Server-Labels:** `'tripleheat'` = TripleHeat-Rennen, `'casual_heat'` =
  Casual-Heat, `'events'` = Liga-Events, `'hotlapping'` = Hotlapping-Server.
  Der Ordner `/home/data/heats/` enthält Casual-Heat-Daten und wird mit
  server='casual_heat' geladen.
