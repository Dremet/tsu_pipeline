# Offene Entscheidungen

Punkte, die eine bewusste Designentscheidung brauchen, bevor sie sich
in die falsche Richtung verhärten. Getroffene Entscheidungen werden hier
als "Entschieden" markiert.

---

## OE-1: elo_bootstrap als ewige Quelle oder Einmalinitialisierung?

**STATUS: ENTSCHIEDEN (2026-05-30)**

**Richtung:** Option B — Bootstrap nur als Einmal-Initialisierung, eine
einzige ELO-Quelle (`elo_history`). Nach der Migration werden synthetische
`elo_history`-Starteinträge für alle bekannten Fahrer angelegt; `elo_bootstrap`
kann dann gedroppt werden.

**Umsetzung:** VERTAGT bis zur echten Tripleheat-Migration, die André gemeinsam
mit Claude durchführt. Option A bleibt bis dahin implementiert — NICHT umbauen.

**Kontext:** `base.elo_bootstrap` speichert den letzten ELO-Wert jedes Fahrers
aus der alten racing-DB. `_get_current_elo_map` in `elo.py` nutzt diesen Wert
als Fallback, wenn noch kein `elo_history`-Eintrag existiert.

---

## OE-2: `server`-Label für den (umziehenden) Tripleheat-Server

**STATUS: ENTSCHIEDEN (2026-05-30)**

**Entschied:** Server-Label bleibt dauerhaft `'heats'`.

**Wichtig:** Im neuen System (tsu_pipeline) bedeutet `server = 'heats'` immer
Tripleheat — nicht den Casual-Heat-Server (der heißt `'casual_heat'` oder wird
ignoriert). Die gleichnamige Casual-Heat-Ordnerstruktur unter `/home/data/heats`
dient nur als Format-Beispiel für den Race-Modus. Tripleheat-Echtdaten liegen
heute noch auf dem alten racing-Server und landen erst nach der Migration hier.

**Kein Umbau nötig.** ELO-Filter (`server = 'heats'`) und mart-Views sind
konsistent. Eine Umbenennung würde eine separate Migration auslösen.

---

## OE-3: Hotlap-Session-Gruppenbildung (Window-Funktion aus dbt)

**STATUS: ENTSCHIEDEN (2026-05-30)**

**Entscheid:** dbt-Hotlap-Gruppenbildung (`base.hotlapping` mit `hotlap_group_id`)
läuft vorerst auf den neuen `base.hotlap_events`-Tabellen weiter. NICHT nach
Python portieren — ist nicht-trivial korrekt und ggf. Phase-3-Thema.

---

## OE-4: Bootstrap-Stichtag verifizieren (Doppelzählung der 3 Rennen vom 29.5.)

**STATUS: KEIN PROBLEM — Zeitzonenirrtum, Stichtag greift korrekt (2026-05-31)**

**Ergebnis:** Der Stichtagsschutz funktioniert korrekt. Kein Code-Änderungsbedarf.

**Ursache des scheinbaren Problems:** Zeitzonenverwirrung.
- Die "21:05–21:41"-Zeiten waren CEST-Ortszeit (UTC+2).
- Sowohl `tsu.elo_heat.last_timestamp` (racing-DB) als auch `utcStartTime` in
  den JSON-Dateien speichern **UTC** (= 19:05–19:41 UTC).
- Der Bootstrap-Stichtag (`MAX(last_race_at) = 2026-05-29 19:41:47 UTC`) ist
  damit korrekt: er ist GLEICH dem letzten Rennen (19:41:47 UTC).
- Der Filter in `update_elo` ist **strikt größer** (`>`), also blockiert er
  alle 3 Rennen (19:05 < 19:41, 19:20 < 19:41, 19:41 = 19:41 → nicht `>`).

**Fazit:** Alle 3 Rennen werden von `update_elo` korrekt als historisch
eingestuft. Keine Doppelzählung möglich. Verifiziert durch read-only-Abfragen
gegen racing-DB und History-JSON-Dateien am 2026-05-31.
