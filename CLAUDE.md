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
