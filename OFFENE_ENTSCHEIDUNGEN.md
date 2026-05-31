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

**STATUS: OFFEN**

**Problem:** Der Bootstrap-Stichtag (`MAX(elo_bootstrap.last_race_at)`) beträgt
`2026-05-29 19:41:47`. Die 3 letzten historischen Tripleheat-Rennen liegen jedoch
bei `21:05`, `21:20` und `21:41` — also NACH dem Stichtag.

Die alte racing-DB hat diese 3 Rennen bereits verarbeitet (ELO steckt im Bootstrap).
Der aktuelle Stichtagsschutz in `update_elo` würde sie dennoch als "neue" Sessions
einstufen und nochmals ELO berechnen → Doppelzählung.

**Ursache (Hypothese):** `last_race_at` in `elo_bootstrap` stammt aus
`tsu.elo_heat.last_timestamp`. Dieses Feld enthält wahrscheinlich den Zeitstempel
des jeweils vorletzten Rennens eines Fahrers, nicht des letzten — oder es gibt
eine systematische Verschiebung zwischen Rennzeit und ELO-Berechnungszeit.

**Zu prüfen (gemeinsam mit André, racing-DB read-only):**
1. Was genau bedeutet `tsu.elo_heat.last_timestamp`? Ist es die Startzeit des
   Rennens, das diese ELO-Zeile erzeugt hat, oder des Vorgänger-Rennens?
2. Liegt `MAX(last_race_at)` korrekt NACH `2026-05-29 21:41` oder nicht?
3. Falls nicht: Muss der Stichtag manuell auf `>= 2026-05-29 21:42` gesetzt
   werden (z.B. als Konstante in der DB oder als Override-Parameter)?

**Keine Code-Änderung bis zur Klärung.** Die 3 Rennen sind in der TEST-DB
geladen aber noch nicht von `update_elo` verarbeitet (Test-DB hat noch kein
neues ELO). Solange der Tripleheat-Server nicht auf carrot läuft, entsteht
kein unmittelbarer Schaden.
