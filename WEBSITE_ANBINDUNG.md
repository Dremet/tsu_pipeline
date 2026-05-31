# WEBSITE_ANBINDUNG — tsura2 auf neue mart-Views umstellen

Dieses Dokument ist eine **Vorbereitung** für Phase 2/3. Es beschreibt,
welche mart-Views die tsura2-Website künftig lesen soll, und zeigt den
Unterschied zu den alten Views. **tsura2 wird hier NICHT angefasst.**

Quelle: `tsura2/tsura/app/blueprints/main/routes.py` (alle DB-Abfragen).

---

## Aktuelle alte Views (tsu.mart.*)

Die alte Website liest diese Tabellen/Views aus der tsu-DB:

| Alt (tsu.mart.*) | Genutzt in Route |
|---|---|
| `fact_recent_races` | `GET /` (Index: letzte Rennen) |
| `fact_hotlapping_list` | `GET /` + `GET /hotlapping` |
| `fact_hotlapping_results_best` | `GET /hotlapping/<id>` |
| `fact_hotlapping_results_all` | `GET /hotlapping/<id>` |
| `fact_elo_events` | `GET /elo-events` |
| `fact_elo_heats` | `GET /elo-heats` |

---

## Mapping: alt → neu

### 1. `fact_recent_races` → `mart.v_race_results`

**Alt:** Eine Zeile pro Rennen (aggregiert: winner, participants, cars als Text).

**Neu:** Eine Zeile pro Teilnehmer. Für "letzte Rennen" muss die Abfrage
auf Session-Ebene aggregieren:

```sql
-- Letzte 20 Rennen (eine Zeile pro Session)
SELECT
    r.session_id,
    r.utc_start_time,
    r.server,
    r.track_name,
    r.participant_count,
    STRING_AGG(DISTINCT r.vehicle_name, ', ') AS cars,
    MIN(r.driver_name) FILTER (WHERE r.position = 1) AS winner
FROM mart.v_race_results r
WHERE r.server IN ('events', 'heats')
GROUP BY r.session_id, r.utc_start_time, r.server, r.track_name, r.participant_count
ORDER BY r.utc_start_time DESC
LIMIT 20;
```

**Wichtig:** Neuer Primärschlüssel ist `session_id` (Hash-String), kein Integer.
URL-Routing in tsura2 muss angepasst werden (z.B. `GET /races/<session_id>`).

---

### 2. `fact_hotlapping_list` → fehlt noch (neue View nötig)

**Alt:** Eine Zeile pro Hotlap-Event mit: `h_h_id` (INT), `tr_name`, `event_start`,
`cars_used` (Text), `number_of_race_results`.

**Neu:** `mart.v_hotlap_results` enthält Einzelrunden, nicht Event-Ebene.
Für die Event-Liste braucht tsura2 eine Aggregation:

```sql
-- Event-Liste für Hotlapping
SELECT
    he.id        AS event_id,
    he.utc_start_time,
    t.name       AS track_name,
    COUNT(DISTINCT hl.steam_id) AS driver_count,
    COUNT(*)     AS total_laps,
    STRING_AGG(DISTINCT v.name, ', ') AS cars_used
FROM base.hotlap_events he
JOIN base.tracks t ON t.guid = he.track_guid
JOIN base.hotlap_laps hl ON hl.event_id = he.id
LEFT JOIN base.vehicles v ON v.guid = hl.vehicle_guid
GROUP BY he.id, he.utc_start_time, t.name
ORDER BY he.utc_start_time DESC;
```

**Empfehlung:** Als neue View `mart.v_hotlap_sessions` anlegen, damit
tsura2 einfach abfragen kann.

**Wichtig:** `event_id` ist jetzt Hash-String statt Integer. URL-Routing
`/hotlapping/<event_number>` muss auf `/hotlapping/<event_id>` umgestellt
werden.

---

### 3. `fact_hotlapping_results_best` → `mart.v_hotlap_results` (is_best_lap=true)

**Alt:** Eine Zeile pro Fahrer im Event (beste Runde), mit Diff-zum-Besten,
Konsistenz-Flags, Sektorzeiten.

**Neu:** Abfrage auf `mart.v_hotlap_results`:

```sql
-- Beste Runde pro Fahrer im Event
SELECT
    r.steam_id,
    r.driver_name,
    r.vehicle_name,
    r.lap_time,
    r.sector_times,
    r.lap_time - MIN(r.lap_time) OVER () AS diff_to_best
FROM mart.v_hotlap_results r
WHERE r.event_id = %s
  AND r.is_best_lap = true
ORDER BY r.lap_time;
```

**Hinweis:** Konsistenz-Flags (`h_is_consistent`, `h_is_very_consistent`)
aus dem alten Mart existieren in der neuen Pipeline noch NICHT. Diese
Logik (Standardabweichung der Rundenzeiten) fehlt noch in den mart-Views
und müsste als neues computed column ergänzt werden (Phase 3).

---

### 4. `fact_hotlapping_results_all` → `mart.v_hotlap_results` (alle Runden)

**Alt:** Alle Runden im Event (bis 500), mit Sektorzeiten.

**Neu:**

```sql
-- Alle Runden im Event
SELECT
    r.steam_id,
    r.driver_name,
    r.vehicle_name,
    r.lap_number,
    r.lap_time,
    r.sector_times
FROM mart.v_hotlap_results r
WHERE r.event_id = %s
ORDER BY r.lap_time
LIMIT 500;
```

---

### 5. `fact_elo_events` → WIRD WEGGELASSEN

**Entscheidung #6:** ELO gibt es NUR für Tripleheat. Liga-Events bekommen
keine ELO-Wertung. Die Route `GET /elo-events` entfällt oder wird
ersetzt durch eine normale Event-Ergebnisseite ohne ELO.

---

### 6. `fact_elo_heats` → `mart.v_driver_profile`

**Alt:** Eine Zeile pro Fahrer mit aktuellem ELO, Delta, Delta-5,
Rennen-Anzahl.

**Neu:** `mart.v_driver_profile` enthält mehr Information (Hotlap + Events):

```sql
-- ELO-Rangliste (Tripleheat)
SELECT
    steam_id,
    driver_name,
    driver_flag,
    driver_clan,
    heat_elo,
    heat_total_races,
    heat_wins,
    heat_last_race_at
FROM mart.v_driver_profile
WHERE heat_elo IS NOT NULL
ORDER BY heat_elo DESC;
```

**Hinweis:** ELO-Delta (`ee_elo_delta`, `ee_elo_delta_6`) fehlt noch in der
View. Das `v_driver_profile` zeigt nur den aktuellen ELO-Stand. Für
Delta-Berechnung (letzte N Rennen) wäre eine Zusatz-Abfrage auf
`base.elo_history` nötig — Phase 3 / Profilseiten.

---

## Fehlende mart-Views (müssen noch erstellt werden)

| View | Zweck | Priorität |
|---|---|---|
| `mart.v_hotlap_sessions` | Event-Liste für Hotlapping-Übersicht | Phase 2 |
| ELO-Delta in `v_driver_profile` | Δ letzte N Rennen | Phase 2/3 |
| Konsistenz-Flags in Hotlap-Views | Std.abw. der Rundenzeiten | Phase 3 |

---

## Strukturelle Unterschiede alt → neu

| Aspekt | Alt (tsu.mart) | Neu (mart.*) |
|---|---|---|
| ID-Typ | Integer (`d_d_id`, `h_h_id`) | Hash-String (`steam_id`, `event_id`) |
| URL-Routing | `/hotlapping/42`, `/driver/42` | `/hotlapping/<hash>`, `/driver/<steam_id>` |
| ELO für Events | Ja (fact_elo_events) | **Nein** (Entscheidung #6) |
| Bots in Ergebnissen | Unklar | Strukturell ausgeschlossen (is_ai filter) |
| Steam-Login-Link | Kein FK in DB | steam_id = direkter Login-Schlüssel (Phase 4) |

---

## Reihenfolge der Umstellung

1. **Phase 2 (nach Tripleheat-Migration):**
   - `mart.v_hotlap_sessions` anlegen
   - Route `/elo-heats` auf `mart.v_driver_profile` umstellen
   - Testen: ELO-Rangliste zeigt korrekte Werte nach Migration

2. **Phase 3 (Pipeline aufräumen):**
   - Restliche Hotlapping-Routes umstellen
   - Route `/elo-events` entfernen oder in normale Event-Seite umbauen
   - ELO-Delta und Konsistenz-Flags ergänzen

3. **Phase 4 (Steam-Login):**
   - Fahrerprofil-Seiten auf `v_driver_profile` aufbauen
   - Login verbindet steam_id direkt mit Profil-Daten
