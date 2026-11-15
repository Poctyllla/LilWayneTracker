"""Studios of the songs from Genius: recorded at (the song's recording location), mixed
at and mastered at (the "Mixed At" / "Mastered At" credits).  Read from the Genius cache
(genius_details.py --locations keeps the recording location), stored in song_studios.

usage:
    python studios.py            # dry run
    python studios.py --apply    # rebuild song_studios (back up first: python database.py --backup <label>)
"""

import argparse
import json
import re
import sqlite3
from pathlib import Path

from import_release import DATABASE_PATH

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GENIUS_PATH = PROJECT_ROOT / "exports" / "genius_song_details.json"
LABELS = {"Mixed At": "mixed", "Mastered At": "mastered", "Recorded At": "recorded"}


def song_studios(details):
    """[(kind, studio)] of one Genius song."""
    found = []
    location = (details.get("recording_location") or "").strip()
    if location:
        # "CMR Studios (New Orleans, LA) & Hit Factory" -> two studios; commas inside brackets stay
        for part in re.split(r"\s+&\s+|;\s*|\s*/\s*", location):
            if part.strip():
                found.append(("recorded", part.strip()))
    for performance in details.get("custom_performances") or []:
        kind = LABELS.get(performance.get("label"))
        if kind:
            found.extend((kind, name.strip()) for name in performance.get("artists") or [] if name.strip())
    return list(dict.fromkeys(found))


def plan(connection):
    genius = json.loads(GENIUS_PATH.read_text(encoding="utf-8")) if GENIUS_PATH.exists() else {}
    result = {}
    for recording_id, genius_id in connection.execute("""
            SELECT id, genius_song_id FROM recordings WHERE genius_song_id IS NOT NULL
            UNION SELECT recording_id, genius_song_id FROM versions WHERE genius_song_id IS NOT NULL"""):
        details = genius.get(str(genius_id)) or {}
        for kind, studio in song_studios(details):
            result.setdefault((recording_id, kind, studio), details.get("url"))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Recording / mixing / mastering studios from Genius.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        rows = plan(connection)
        kinds = {}
        for (_rid, kind, _studio) in rows:
            kinds[kind] = kinds.get(kind, 0) + 1
        print(f"Studio facts: {len(rows)} {kinds}; songs: {len({k[0] for k in rows})}")
        if not args.apply:
            print("Dry run; --apply stores them (back up first).")
            return
        with connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS song_studios (
                    recording_id INTEGER NOT NULL REFERENCES recordings(id),
                    kind TEXT NOT NULL,      -- recorded, mixed, mastered
                    studio TEXT NOT NULL,
                    source TEXT,
                    PRIMARY KEY (recording_id, kind, studio))""")
            connection.execute("DELETE FROM song_studios")
            connection.executemany("INSERT INTO song_studios (recording_id, kind, studio, source) VALUES (?, ?, ?, ?)",
                                   [(rid, kind, studio, url) for (rid, kind, studio), url in rows.items()])
        print(f"Stored. Foreign keys: {connection.execute('PRAGMA foreign_key_check').fetchall()[:3]}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
