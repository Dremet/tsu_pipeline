"""
Batch-Loader: process all *_event.json files in a folder tree.

Usage:
    from tsu_pipeline.batch import load_folder
    summary = load_folder("/home/data/hotlapping", "hotlapping", db_url)
    print(summary)

One DB transaction per file: a corrupt file never aborts the whole run.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import psycopg

from .loader import load_event

logger = logging.getLogger(__name__)


def load_folder(
    folder_path: str | Path,
    server: str,
    db_url: str,
    *,
    recursive: bool = True,
    progress_fn: Callable[[int, int, str], None] | None = None,
) -> dict:
    """
    Load all ``*_event.json`` files found under ``folder_path``.

    Parameters
    ----------
    folder_path  : root directory to scan
    server       : server label written to DB ('hotlapping', 'events', 'heats')
    db_url       : psycopg3-compatible connection string
    recursive    : whether to search subdirectories (default True)
    progress_fn  : optional callback(current_index, total, filepath) for progress

    Returns
    -------
    dict with keys:
        total          – files found
        loaded         – events written to DB (not skipped, no error)
        skipped        – sentinel hotlaps filtered out
        errors         – files that raised an exception
        sessions_new   – new race_sessions / hotlap_events inserted
        participations_new – new race_participations inserted
        drivers_new    – new base.drivers rows
        laps_new       – new hotlap_laps rows
        error_files    – list of (path, error_message) tuples
    """
    folder = Path(folder_path)
    pattern = "**/*_event.json" if recursive else "*_event.json"
    files = sorted(folder.glob(pattern))

    summary: dict = {
        "total": len(files),
        "loaded": 0,
        "skipped": 0,
        "errors": 0,
        "sessions_new": 0,
        "participations_new": 0,
        "drivers_new": 0,
        "laps_new": 0,
        "error_files": [],
    }

    for i, f in enumerate(files):
        if progress_fn:
            progress_fn(i + 1, len(files), str(f))

        try:
            with psycopg.connect(db_url) as conn:
                cursor = conn.cursor()
                result = load_event(f, server, cursor)
                # psycopg3: context manager commits on clean exit
        except Exception as exc:
            summary["errors"] += 1
            summary["error_files"].append((str(f), str(exc)))
            logger.warning("Error loading %s: %s", f, exc)
            continue

        if result["skipped"]:
            summary["skipped"] += 1
            logger.debug("Skipped %s: %s", f, result["skip_reason"])
        else:
            summary["loaded"] += 1
            summary["sessions_new"] += result["sessions"]
            summary["participations_new"] += result["participations"]
            summary["drivers_new"] += result["drivers_new"]
            summary["laps_new"] += result["laps"]

    return summary
