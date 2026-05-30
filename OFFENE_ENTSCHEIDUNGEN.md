# Offene Entscheidungen

Punkte, die eine bewusste Designentscheidung brauchen, bevor sie sich
in die falsche Richtung verhärten. Getroffene Entscheidungen werden in
PROJECT_BRIEFING.md übernommen und hier archiviert.

---

## OE-1: elo_bootstrap als ewige Quelle oder Einmalinitialisierung?

**Kontext:** `base.elo_bootstrap` speichert die letzte bekannte ELO-Wert
jedes Fahrers aus der alten racing-DB. `_get_current_elo_map` in `elo.py`
nutzt diesen Wert als Fallback, wenn kein `elo_history`-Eintrag existiert
(d.h. Fahrer hat noch kein Tripleheat-Rennen auf dem neuen System gefahren).

**Option A** (aktuell implementiert): Bootstrap bleibt dauerhaft als Fallback.
Sobald der Fahrer das erste neue Rennen fährt, übernimmt `elo_history` und
`elo_bootstrap` wird nie wieder herangezogen.

**Option B**: Bootstrap nur für den Migrationsmoment. Nach der Migration
wird `elo_history`-Startwerte für alle bekannten Fahrer mit ihren Legacy-ELOs
vorbesetzt (synthetische Einträge ohne `participation_id`). `elo_bootstrap`
kann dann gedroppt werden.

**Empfehlung:** Option A ist einfacher und braucht keinen Schema-Umbau.
Option B wäre sauberer (kein "zwei Quellen"-Problem), erfordert aber eine
zusätzliche Datenbankänderung und mehr Code.

**Entscheid steht aus** — kein Blocker für aktuellen Betrieb.

---

## OE-2: `server`-Label für den (umziehenden) Tripleheat-Server

**Kontext:** Tripleheat zieht auf den neuen carrot-Server. Heute heißt
der Server-Label in den racing-DB-Daten implizit "heats". Im neuen Schema
wird `base.race_sessions.server = 'heats'` für alle Tripleheat-Rennen
gesetzt. Dieser Label bestimmt, ob ELO berechnet wird
(`update_elo(..., server='heats')`).

**Frage:** Bleibt der Label dauerhaft `'heats'` oder ändert er sich wenn
der Tripleheat-Server umgezogen ist (z.B. `'tripleheat'`)?

**Anmerkung:** Wenn der Label geändert wird, müssen alle `update_elo`-
Aufrufe und die `mart.v_driver_profile`-View angepasst werden. Konsistenz
ist wichtiger als der genaue Name.

**Entscheid steht aus** — Empfehlung: jetzt mit `'heats'` lassen, da das
der aktuell etablierte Wert ist und eine Umbenennung eine separate Migration
auslöst.

---

## OE-3: Hotlap-Session-Gruppenbildung (Window-Funktion aus dbt)

**Kontext:** Im alten dbt-Projekt `base.hotlapping` gibt es eine
Window-Funktion, die zusammenhängende Hotlap-Sessions (mit Track-Wechsel)
gruppiert. Diese Logik ist im neuen Pipeline-Code noch NICHT implementiert.
Im Moment sind `base.hotlap_events` einzelne Rohdaten-Events; die
dbt-Gruppenbildung auf `base.hotlapping` (mit `hotlap_group_id`) ist noch
eine Stufe obendrauf.

**Frage:** Brauchen wir diese Gruppenbildung in der neuen Pipeline direkt,
oder reicht es, die dbt-Logik auf den neuen `base.hotlap_events`-Tabellen
weiter laufen zu lassen?

**Empfehlung:** dbt-Logik vorläufig auf den neuen Daten weiter laufen
lassen (sie ist korrekt und nicht-trivial). Erst bei Phase 3 entscheiden
ob sie in Python übertragen wird.
