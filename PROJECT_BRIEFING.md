# TSU / TSURA — Projekt-Briefing

Dieses Dokument ist der gemeinsame Startkontext für die Umbauarbeit an der
Turbo-Sliders-Unlimited-Infrastruktur von André (Dremet). Es fasst Ziel,
Ist-Zustand, Zielarchitektur und Reihenfolge zusammen. Es dient als Grundlage
für die `CLAUDE.md` in den Ziel-Repos.

## Ziel in einem Satz

Die heute auf mehrere Server und vier+ Repos verteilte TSU-Infrastruktur auf
**einen Linux-Server** und **eine Website** konsolidieren, mit Steam-Login,
Fahrerprofilen und einer einzigen, sauberen Datenpipeline — iterativ, als
Hobbyprojekt, mit Claude Code als ausführendem Agenten.

## Beteiligte (Stand nach Phase 1)

Server:
- Cloud-Linux-Server ("carrot"): hostet alle relevanten Dedicated Server unter
  eigenen Usern (hotlapping, events, data, tripleheat, tsura u.a.) sowie die
  tsu-Postgres (nur localhost, kein externer DB-Zugriff per ufw geblockt).
- Alter racing-Server (185.170.113.38): gilt als **abgeschaltet**. Postgres
  dort noch erreichbar für eventuelle Nachmigration, sonst nicht mehr relevant.

Relevante Server-Typen: **hotlapping**, **tripleheat** (User `tripleheat` auf
carrot), **events**. Nicht relevant (ignorieren): casual heat, topdown.

Repos:
- `tsu_data` — Konverter-Library (JSON/Log → CSV). Sauber, bleibt.
- `tsu_dbt` — dbt-Projekt (source→enriched→base→mart) + ELO-Python. Kern, bleibt.
- `tsu_analyzer` — ALTE Heat/ELO-Verarbeitung auf racing-Server. **Wird aufgelöst.**
- `tsura_server_scripts` — eigene Scripts in der Dedicated-Server-Struktur.
- `tsura_website` — ALTE Website. **Wird abgelöst.**
- `tsura2` — NEUE Website (Flask, psycopg3, uv, Bootstrap 5). Read-only Dashboard.

Datenbanken:
- tsu-DB (neu, auf carrot): Schemas source / enriched / base / mart. tsura2 liest
  aus `mart.*`.
- racing-DB (alt, auf racing-Server): `tsu.elo_heat` (append-only ELO-Historie der
  Tripleheats) ist die einzige noch aktiv gefüllte, kritische Tabelle. Wird heute
  noch von der alten Website gelesen.

## Zentrale Erkenntnis aus der Bestandsaufnahme

Es existieren **zwei parallele Welten**: die alte (tsu_analyzer + racing-DB) und
die neue (tsu_dbt + tsu-DB). Die ELO-Berechnung ist in beiden dupliziert
(`tsu_analyzer/.../check_for_stats_files_and_update.py` und
`tsu_dbt/update_elo.py`), mit einem Bug im alten Code (`break` statt `continue`
bei 0 Gegnern). Das eigentliche "vereinfachen" heißt: die alte Welt auflösen, die
Duplikation beseitigen, die Schichtung reduzieren — **nicht** pauschal das
dbt-Projekt wegwerfen, aber auch nicht alles so lassen.

Andrés konkrete Schmerzpunkte (maßgeblich):
- Pipeline soll **robuster** sein — frühere Fehler durch unerwartete Rohdaten.
  → Ursache liegt im Einlesen, nicht in dbt. Lösung: Validierung vor dem Load.
- Zu viel **Joinen** beim schnellen Nachschauen. → Schichtung reduzieren, breite
  Abfrage-Sichten anbieten.
- Es ist ein **Hobbyprojekt** — Pragmatismus vor Reinheit.

## Zielarchitektur

Leitprinzip: **Hobby-Maßstab, nicht Enterprise.** Robustheit beim Einlesen und
Bequemlichkeit beim Abfragen schlagen architektonische Reinheit.

- **Schichtung reduzieren.** Die heutige vierstufige Treppe (source→enriched→base→
  mart) ist für diesen Maßstab überdimensioniert. Ziel: realistisch zwei Ebenen —
  eine schlanke, validierte Rohdaten-Ebene und eine fertige, **breite** Abfrage-
  Ebene, in der die wichtigsten Entitäten schon vorab zusammengejoint sind
  (Fahrer + Rennen + Auto + Strecke + ELO in einer Sicht). Für "mal eben nachgucken"
  soll man NICHT fünf Tabellen joinen müssen. Bewusst denormalisieren für Komfort.
- **dbt: abspecken, nicht wegwerfen (ENTSCHIEDEN).** Die Phase-0-Analyse hat
  bestätigt: die nicht-triviale Logik (Hotlapping-Session-Gruppierung per
  Window-Funktion, Konsistenz-Check, ELO-Fenster) lohnt nicht, in Python neu
  gebaut zu werden. Der enriched-Layer dagegen ist strukturell kaputt (fragiler
  `driver_index`-Join, der nur event-lokal eindeutig ist, plus `LIMIT 1`-Bug) und
  fällt weg. Zielbild: source → Python-Loader (berechnet stabile, steam-id-basierte
  Keys + validiert beim Laden) → base → breite mart-Views. Vier Schichten werden
  zwei echte Transformationsebenen.
- **Robustheit beim Einlesen ist das eigentliche dbt-Problem.** Frühere dbt-Fehler
  kamen von überraschenden Rohdaten — der Schaden entsteht beim Parsen/Laden, dbt
  zeigt ihn nur später. Lösung: Validierung der Stats-Dateien VOR dem DB-Load, mit
  klaren Fehlermeldungen. (Siehe Phase 0.)
- **ELO-Logik wird konsolidiert** auf eine einzige Implementierung (die korrekte
  aus `update_elo.py`), `tsu_analyzer` wird abgelöst. ELO bleibt ein Python-Schritt
  (chronologisch-iterativ, passt nicht in deklaratives SQL/dbt).
- **DB ändert sich bewusst mit dem Login.** Die neue DB bekommt Nutzer-/Session-
  Tabellen, und die **Steam-ID wird zum harten Verknüpfungsschlüssel** über alles:
  sie steht in den Ergebnisdateien (`player.id`) UND kommt beim Steam-Login zurück.
  Eine Fahrer-Identität über alle Server-Typen. Das Schema-Design soll das von
  Anfang an mitdenken, auch wenn die Login-Oberfläche erst in Phase 4 kommt.
- **Postgres bleibt.** Kein DB-System-Wechsel; Vereinfachung passiert in der Struktur.
- **Zwei Repos am Ende:** Ingestion/Pipeline und Website (tsura2 mit Login + Profilen).
  Die DB ist die Schnittstelle.
- **Ein Verarbeitungsmuster für alle:** Server schreibt Stats-Dateien → move-Script
  verschiebt sie + setzt Trigger → Pipeline konvertiert, validiert, lädt, berechnet
  ELO. Heat muss auf dieses Muster umgestellt werden (hat heute kein move-Script).
- **Achtung Ordner in `/home/data`:** `heat` ignorieren, `heats` = Casual-Heat
  (nicht projektrelevant). Beim Vereinheitlichen aufräumen, aber keine
  Tripleheat-Daten hier erwarten — die liegen auf dem alten racing-Server.

## Datenformat (Kurzreferenz)

- **Zwei relevante Spielmodi mit unterschiedlicher Ergebnisstruktur:**
  - **Hotlapping** — Bestzeit-Jagd, es zählt die schnellste Einzelrunde. Keine ELO.
  - **Race** — Zielreihenfolge/Position zählt; daraus wird ELO berechnet. Sowohl
    Tripleheat als auch Events sind Race-Modus.
  - (Weitere Spielmodi existieren, sind aber für das Projekt nicht relevant.)
  → Die beiden Modi dürfen bewusst getrennte Abfrage-Sichten bekommen, statt in
    ein gemeinsames Schema gezwängt zu werden — das vereinfacht eher, als es kompliziert.
- Stats kommen als `*_event.json` (alle Server), Events zusätzlich `*_event_details.log`.
- Zeiten: Integer ÷ 10000 = Sekunden.
- Steam-ID: in `players[].player.id` (BigInt), in der DB als `steam_id`.
- ELO-Formel (nur Race-Modus): Elo-artig, K=20, D=400, Minimum 100, Startwert 1000.
  Position-basiert gegen alle Gegner im Rennen.
- **Ordner in `/home/data`:** `heat` ignorieren. `heats` = Casual-Heat (nicht relevant
  fürs Projekt, aber als Format-Beispiel für Race-Modus brauchbar). Echte Tripleheat-
  Dateien liegen auf dem alten racing-Server, nicht hier.

## Verarbeitungs- und Anzeige-Regeln (gelten als gesetzt)

Diese Regeln sind entschieden und strukturell im Code verankert:

- **Verarbeitet werden alle drei Server-Typen:** events, tripleheat (`heats`),
  hotlapping. Die Pipeline (`run_pipeline.sh`) iteriert über alle drei.
- **ELO ausschließlich für Tripleheat.** `update_elo` filtert per SQL auf
  `server='heats'`. Events und Hotlapping bekommen strukturell kein ELO —
  nicht nur "nicht vorgesehen", sondern durch den Filter ausgeschlossen.
- **Hotlapping speist die Bestzeiten-Rangliste** (wie bisher). Die schnellste
  Runde pro Fahrer pro Strecke ist die relevante Kennzahl.
- **Website-Anzeige (Phase 2):**
  - Events und Tripleheats: Einzelergebnisse sichtbar (Rennergebnis-Ansicht).
  - Hotlapping: NUR als Rangliste, keine Einzelsession-Ansicht.

## Sicherheit (Stand nach Phase 1)

- DB-Port 5432 per `ufw` geschlossen; Postgres nur über `localhost` erreichbar.
  Externer Zugriff nur über SSH-Tunnel möglich.
- DB-Passwort rotiert (2026-05-31, nach versehentlichem Commit).
- `.env` und `.env.*` in `.gitignore` des tsu_pipeline-Repos; Historie mit
  `git filter-repo` bereinigt. `.env.example` mit Platzhaltern ist sicher
  eincheckt.

## Geklärte Entscheidungen (Phase 0)

Diese Punkte sind entschieden und gelten als gesetzt:

1. **`finishedState` ist egal.** `'Finished'` und `'Stopped_GivePoints'` werden in
   ALLEN Fällen gleich behandelt. Der `finished_state = 'Finished'`-Filter in
   `update_elo.py` muss raus / auf beide Werte erweitert werden — sonst bekämen
   Tripleheats nie ELO. (War Problem 2 der Analyse.)
2. **Hotlapping-Leerläufe rausfiltern.** Einträge mit Sentinel-Werten
   (`lapsCompleted = -1`, Rundenzeit = 90.000 s / `900000000`, leeres `times`-Array)
   werden beim Einlesen verworfen, bevor sie in die DB gelangen. Teil der
   Einlese-Validierung. (War Problem 1.)
3. **Tripleheat erzeugt JSON UND `*_event_details.log`** (Fuel/Tire). Der neue
   Heat-Ingestion-Weg muss beide verarbeiten. Fuel/Tire-Telemetrie nicht wegwerfen —
   optional mitführen (wie bei Events), evtl. später für Renndetails/Profile nützlich.
   Korrigiert die Annahme "Log nur für Events".

4. **Bots/AI haben keine rennübergreifende Identität.** Echte Fahrer-Tabelle
   (`drivers`, Schlüssel `steam_id`) enthält AUSSCHLIESSLICH Menschen. Bots werden
   nur in der Teilnahme-Zeile des jeweiligen Rennens als Textname + `is_ai`-Flag
   gespeichert, OHNE Fremdschlüssel auf `drivers`. Folgen:
   - Sichtbar in Einzelrennen-Ansichten (Name steht in der Teilnahme-Zeile).
   - Strukturell ausgeschlossen aus allen Statistiken (ELO, Profile, Bestzeiten),
     weil diese über `drivers`/`steam_id` joinen — Bots existieren dort nicht.
   - Zwei Bots gleichen Namens in verschiedenen Rennen sind NICHT dieselbe Entität
     (keine gemeinsame ID, keine Aggregation möglich).
   Bewusst KEINE synthetischen IDs vergeben — Bots bekommen strukturell gar keine
   Identität, damit Aggregation unmöglich (nicht nur "nicht vorgesehen") ist.
5. **Bots zählen nicht in die ELO-Berechnung.** Vor der ELO-Formel werden Bots aus
   dem Teilnehmerfeld gefiltert; gewertet wird nur das Feld der echten Fahrer (ein
   echter Fahrer hinter Bots wird nur gegen die anderen echten Fahrer gewertet).
   Bots sind heute faktisch nie in ELO-Rennen — das ist aber explizit im Code zu
   prüfen/abzusichern, nicht nur anzunehmen.
6. **ELO gibt es NUR für Tripleheat.** Nur Rennen vom (umziehenden) Tripleheat-Server
   bekommen eine ELO-Wertung. Liga-Events werden weiter verarbeitet und angezeigt
   (Ergebnisse/Statistiken), aber OHNE ELO. Das alte `fact_elo_events`-Modell wird
   NICHT übernommen. Es gibt also nur EINE ELO-Wertung (Tripleheat), nicht zwei.
   `update_elo` läuft ausschließlich über Tripleheat-Sessions. Hotlapping ohnehin
   ohne ELO.
7. **Historische Tripleheat-ELO via Bootstrap (entschieden).** Die fertigen ELO-
   Endwerte aus der alten racing-DB (`tsu.elo_heat`) werden als Startzustand
   übernommen, NICHT aus Rohdateien neu berechnet. Grund: Die Fahrer kennen diese
   Werte; ein Neuberechnen mit der leicht geänderten (bug-bereinigten) Formel würde
   abweichende historische Werte erzeugen und Verwirrung stiften. Ab dem Umzug
   rechnet die neue saubere Formel weiter. Die historischen Ergebnisdateien
   (`/home/data/history_triple_heat_hammock`) werden NICHT für ELO gebraucht, aber
   für die Renn-Historie/Profile geladen (Positionen, Zeiten, Strecken, Teilnehmer).

## Roadmap (Reihenfolge)

Priorität nach Andrés Vorgabe, mit den technischen Abhängigkeiten verzahnt:

### Phase 0 — Anschauen, Validieren, Entscheiden ✅ ABGESCHLOSSEN
Dateiformat analysiert, dbt-Entscheidung getroffen (abspecken statt wegwerfen),
Validierung beim Einlesen eingebaut, Minimal-Tests geschrieben.

### Phase 1 — Tripleheat-Migration + Produktivbetrieb ✅ ABGESCHLOSSEN (2026-05-31)
- ELO-Bootstrap aus racing-DB migriert (120 Fahrer, Stichtag 2026-05-29 19:41:47 UTC).
- Historische Tripleheat-Rennen als Display-Daten geladen (305 Sessions).
- Neues schlankes Schema (`base.*` + `mart.*`) in Produktiv-DB angelegt.
- Alte CSV+dbt-Pipeline durch `tsu_pipeline`-Paket ersetzt.
- Tripleheat-Server läuft unter User `tripleheat` auf carrot; alter racing-Server
  gilt als abgeschaltet.
- Pipeline (Crons als `data`-User) läuft produktiv gegen `localhost:5432`.
- Repo: `github.com/Dremet/tsu_pipeline`, Deployment via `git clone/pull`.
- Offene Folgepunkte Phase 1: move-Script für `tripleheat`-User auf carrot
  deployen + testen (tsura_server_scripts/heat/…); erstes echtes Tripleheat-Rennen
  verifizieren.

### Phase 2 — Website tsura2 auf neue Views umstellen (Prio 1, als nächstes)
tsura2 liest aktuell noch aus den alten `mart.fact_*`-Tabellen (die nicht mehr
befüllt werden). Umstellen auf neue `mart.v_*`-Views:
- `mart.v_race_results` → Einzelergebnisse Events + Tripleheats
- `mart.v_driver_profile` → ELO-Liste, Fahrerprofil
- `mart.v_hotlap_results` → Bestzeiten-Rangliste (kein Einzelsession-View)
- `mart.v_hotlap_sessions` noch anlegen (fehlt noch).

### Phase 3 — Fahrerprofil-Seiten + Steam-Login (Prio 2)
Profilseiten bündeln Heat-ELO, Event-Resultate, Hotlap-Bestzeiten.
Steam OpenID-Login: Steam-ID ist bereits der Identitätsschlüssel im Datenmodell.

### Phase 4 — Aufräumen (Prio 3, nach Stabilisierung)
Alte `source.*`/`enriched.*`-Tabellen entfernen, `tsu_analyzer`-Reste tilgen,
OE-1 umsetzen (synthetische elo_history-Starteinträge statt elo_bootstrap).

## Design-Richtung tsura2 (entschieden 2026-05-31)

Diese Richtung gilt als gesetzt. Sie ersetzt keine Entscheidungen aus dem
Briefing, sondern ergänzt sie um das konkrete Look-&-Feel.

### Referenz und Ausgangspunkt
- **tsura2 ist die Design-Referenz** — der bestehende Look (Bootstrap 5,
  Formula-1-Dunkelthema, Farben, Fonts) wird übernommen, nicht neu erfunden.
- Die alte `tsura_website` ist irrelevant und wird nicht betrachtet.

### Was aus tsura2 unverändert übernommen wird
- Genereller Look (Dark Theme, Farbpalette, Navbar-Struktur)
- Hotlapping-Zeiten-Darstellung (`M:SS.ffff`-Format, Sektor-Hervorhebung)
- Live-Serveranzeige ("wer ist gerade auf welchem Server") auf der Startseite

### Was gezielt neu gebaut oder angepasst wird
- **Einzelergebnis-Ansichten für Events + Tripleheats** — fehlten in tsura2
  komplett oder waren kaputt. Jede Race-Session bekommt eine eigene Seite mit
  Startreihenfolge, Endposition, Zeiten, Fahrzeugen, Teilnehmerfeld.
- **Startseite überarbeitet** — übersichtlich zeigen, was die Seite kann;
  außerdem eine kurze englische Erklärung, was tsura.org überhaupt ist (für
  Außenstehende verständlich, ohne zu lang zu werden).
- **Hotlapping: nur Rangliste** — keine Einzelsession-Ansicht. Anzeige-Regel
  aus Briefing (Abschnitt "Verarbeitungs- und Anzeige-Regeln").
- **Events + Tripleheats: Einzelergebnisse** — Rennergebnis-Seite pro Session.

### Login-Prinzip (gilt ab Phase 4)
- Steam-Login ist **additiv** — ohne Login ist die gesamte Seite einsehbar.
  Login schaltet nie Inhalte weg, sondern fügt personalisierte Features hinzu
  (z.B. eigenes Profil hervorheben, Favoriten).
- Die DB ist darauf ausgelegt: `steam_id` ist der Identitätsschlüssel.

### Deployment
- Entwicklung: `/home/dremet/tsura/tsura2/` (dremet-User), Push nach
  `github.com/Dremet/tsura2` (SSH).
- Deployment: `git pull` als `tsura`-User auf carrot, danach Service neu starten.

```bash
# Als tsura-User auf carrot:
git stash && git pull && git stash drop   # stash nur nötig wenn uncommitted changes

# Service neu starten (als dremet mit sudo oder als tsura):
sudo systemctl --machine=tsura@ --user restart dev_tsura.service
sudo systemctl --machine=tsura@ --user status  dev_tsura.service
```

Hintergrund: tsura2 läuft als Gunicorn (3 Workers, Unix-Socket
`/run/tsura/dev_tsura.sock`) hinter nginx. Service-Datei:
`/home/tsura/.config/systemd/user/dev_tsura.service`.
nginx selbst muss bei Code-Updates nicht neugestartet werden.

## Arbeitsweise

- Claude Code arbeitet als User `dremet` in einem Arbeitsbereich unter `~`, getrennt
  vom Live-Betrieb der Server-User. Git ist die Quelle der Wahrheit; Deployment auf
  die Live-Struktur ist ein bewusster, separater Schritt.
- Nicht als root arbeiten.
- Iterativ in klar umrissenen Häppchen pro Session (Pro-Limit; ggf. zeitweise Max).
- Read-only-DB-Zugang für Analyse; Schreibzugriff nur bei bewussten Migrations-/
  Deployment-Schritten.

## Bekannte Stolpersteine / Offene Punkte

- **move-Script für tripleheat noch nicht deployed:** `tsura_server_scripts/heat/
  server/config/Scripts/` enthält die fertigen Scripts (`move_raw_files.sh`,
  `run_event_end.sh`, `eventend.src`). Müssen noch unter User `tripleheat` auf
  carrot eingespielt und getestet werden.
- **tsura2 noch auf alten Tabellen:** Alte `mart.fact_*`-Tabellen werden nicht
  mehr befüllt. tsura2 zeigt veraltete Daten bis Phase 2 abgeschlossen ist.
- **OE-1 ausstehend:** elo_bootstrap als Einmalinitialisierung war die beschlossene
  Richtung; synthetische elo_history-Starteinträge noch nicht umgesetzt.
- **ELO-Berechnung bleibt Python-Schritt** (chronologisch-iterativ, passt nicht
  in SQL/dbt) — ist so gewollt und implementiert.
- **Dedicated-Server-Standardcode** liegt in keinem Repo — bei Unklarheiten zum
  Datei-Ablageort die offizielle TSU-Doku oder André fragen.
