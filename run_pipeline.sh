#!/usr/bin/env bash
#
# run_pipeline.sh — neue Pipeline auf Basis von tsu_pipeline
#
# Deployment-Pfad: /home/data/tsu_data/run_pipeline.sh
# tsu_pipeline-Paket erwartet unter: /home/data/tsu_pipeline/
#
# Cron-Aufruf (data-User):
#   * * * * *        cd /home/data/tsu_data && ./run_pipeline.sh
#   * * * * * sleep 30; cd /home/data/tsu_data && ./run_pipeline.sh
#
set -e
set -o pipefail

PIPELINE_DIR="/home/data/tsu_pipeline"
LOGFILE="/home/data/tsu_data/pipeline.log"
ERROR_FILE="/home/data/tsu_data/ERROR_OCCURED"
MAXSIZE=10485760  # 10 MB

timestamp() { date +"%Y-%m-%dT%H:%M:%S"; }

# DB-URL aus tsu_pipeline/.env laden
if [ -f "${PIPELINE_DIR}/.env" ]; then
  # shellcheck disable=SC1090
  set -a; source "${PIPELINE_DIR}/.env"; set +a
fi
export TSU_PROD_POSTGRES_URL

###############################################################################
# Prüfe ERROR_OCCURED
###############################################################################
if [ -f "$ERROR_FILE" ]; then
  echo "[$(timestamp)] ERROR_OCCURED gefunden – Pipeline abgebrochen." >> "$LOGFILE"
  exit 1
fi

###############################################################################
# Fehler-Handler
###############################################################################
trap '
  touch "$ERROR_FILE"
  echo "[$(timestamp)] Fehler – ERROR_OCCURED geschrieben." >> "$LOGFILE"
  {
    echo "Pipeline-Fehler auf $(hostname)"
    echo "Zeitpunkt: $(date +"%Y-%m-%d %H:%M:%S")"
    echo
    echo "=== Letzte 50 Zeilen aus $LOGFILE ==="
    tail -n 50 "$LOGFILE"
  } | mail -r "data@tsura.org" -s "Pipeline-Fehler auf $(hostname)" carrot@andrepetersen.de
' ERR

###############################################################################
# Log-Rotation (~10 MB)
###############################################################################
if [ -f "$LOGFILE" ]; then
  ACTUALSIZE=$(du -b "$LOGFILE" | cut -f1)
  if [ "$ACTUALSIZE" -ge "$MAXSIZE" ]; then
    mv "$LOGFILE" "$LOGFILE.$(date +%Y%m%d-%H%M%S)"
    gzip "$LOGFILE."*
  fi
fi

exec > >(tee -a "$LOGFILE") 2> >(tee -a "$LOGFILE" >&2)

echo "[$(timestamp)] *** Pipeline gestartet ***"

###############################################################################
# Datentypen verarbeiten
###############################################################################
for TYPE in hotlapping events heats tripleheat; do
  BASE_DIR="/home/data/${TYPE}"
  ARCHIVE_DIR="${BASE_DIR}/archive"
  mkdir -p "${ARCHIVE_DIR}"

  for SUBDIR in $(ls -1d "${BASE_DIR}"/*/ 2>/dev/null | sort); do
    SUBDIR="${SUBDIR%/}"
    BN=$(basename "$SUBDIR")
    [ "$BN" = "archive" ] && continue

    RAW_PATH="${SUBDIR}/raw"
    [ ! -d "$RAW_PATH" ] && continue

    echo "[$(timestamp)] Verarbeite: ${TYPE}/${BN}"

    # tsu_pipeline: Laden + Validierung + ELO (für heats)
    uv --project "${PIPELINE_DIR}" run python "${PIPELINE_DIR}/pipeline_run.py" \
      "${TYPE}" "${RAW_PATH}"

    # Hotlapping: In-Game-Bestzeiten aktualisieren (nicht-fatal)
    if [ "${TYPE}" = "hotlapping" ]; then
      (
        trap - ERR; set +e
        EVENT_FILE=$(find "${RAW_PATH}" -maxdepth 1 -name "*event.json" | head -n 1)
        if [ -f "$EVENT_FILE" ]; then
          uv --project /home/data/tsu_data run python /home/data/tsu_data/generate_autorun.py \
            "${EVENT_FILE}" --autorun-path /home/hotlapping/server/config/Scripts/autorun.src
          echo "[$(timestamp)]   generate_autorun: OK"
        fi
      ) || echo "[$(timestamp)]   generate_autorun fehlgeschlagen (wird ignoriert)"
    fi

    # Archivieren
    mv "$SUBDIR" "${ARCHIVE_DIR}/"
    echo "[$(timestamp)] Archiviert: ${TYPE}/${BN}"
  done
done

echo "[$(timestamp)] *** Pipeline abgeschlossen ***"
