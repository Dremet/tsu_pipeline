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

## Themenspezifisch (nur wenn relevant)
- **WEBSITE_ANBINDUNG.md** — Vorbereitung für Phase 2 (tsura2 auf neue
  mart-Views umstellen). Erst in Phase 2 relevant.

## Archiv (nicht aktiv laden, nur bei Rückfragen zur Historie)
- **BESTANDSAUFNAHME.md** — initiale Erkundung der Repos/DBs.
- **PHASE0_ANALYSE.md** — Auswertung der Beispieldateien + dbt-Empfehlung.

## Grundregeln (gelten immer)
- Test-DB (TSU_TEST_POSTGRES_URL) beschreiben. /home/data, Produktiv-tsu-DB
  und racing-DB NUR lesen.
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
│   ├── elo.py        # update_elo()  — ELO nur für server='heats' (Tripleheat)
│   └── keys.py       # stabile md5-basierte IDs (session, participation, …)
├── migrations/
│   ├── 001_base_schema.sql   # base.* Tabellen
│   ├── 002_elo_bootstrap.sql # base.elo_bootstrap (Legacy-ELO-Seed)
│   └── 003_mart_views.sql    # mart.v_race_results, v_hotlap_results, v_driver_profile
├── tests/            # 27 Tests, alle grün
├── migrate_elo_history.py  # Einmalig: ELO-Historie aus racing-DB importieren
├── e2e_validate.py         # E2E-Validierung gegen echten Datenbestand
├── PROJECT_BRIEFING.md     # ← hier lesen
└── LOGBUCH.md              # ← hier lesen
```

### Umgebung + Datenbanken
```bash
# .env liegt in ~/bestandsaufnahme/.env (ein Verzeichnis höher)
TSU_TEST_POSTGRES_URL   # Test-DB auf localhost — hier darf geschrieben werden
TSU_HOTLAPPING_POSTGRES_URL  # Produktiv-tsu-DB    — NUR lesen
OLD_RACING_POSTGRES_URL      # Alter racing-Server — NUR lesen

# Abhängigkeiten + Tests
uv run pytest tests/        # alle Tests ausführen
uv run migrate_elo_history.py           # Dry-run: ELO-Vergleich anzeigen
uv run migrate_elo_history.py --apply   # scharf: in Test-DB schreiben
uv run e2e_validate.py                  # vollständige E2E-Validierung
```

### Datenpfade (read-only)
```
/home/data/hotlapping/          # Hotlap-Server-Daten (21.380 Dateien)
/home/data/events/              # Liga-Event-Daten (1.074 Dateien)
/home/data/heats/               # Casual-Heat (162 Dateien, nicht projektrelevant)
/home/data/history_triple_heat_hammock/  # historische Tripleheat-Rennen (305 geladen)
```

### Wichtige Designentscheidungen (Kurzfassung)
Vollständig in PROJECT_BRIEFING.md — hier nur die häufig vergessenen:

- **ELO nur für `server='heats'`** — das ist Tripleheat. Liga-Events und
  Hotlapping bekommen strukturell kein ELO (SQL-Filter in `update_elo`).
- **Bootstrap-Stichtag** — `update_elo` überspringt automatisch Sessions vor
  `MAX(elo_bootstrap.last_race_at)`. Historische Rennen sind Display-Daten,
  ihr ELO steckt bereits im Bootstrap. Nie `update_elo` auf historische
  Sessions forcieren.
- **Bots** — kein FK auf `base.drivers`, kein ELO, keine Statistiken.
  Strukturell ausgeschlossen, nicht nur "nicht vorgesehen".
- **`finishedState` egal** — `'Finished'` und `'Stopped_GivePoints'` werden
  gleich behandelt.
