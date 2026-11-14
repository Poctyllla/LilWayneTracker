"""Split recordings that hold several positions of one unofficial release.

Two positions of one release are different songs.  Merges made before the
merge safeguards (Nov 13, 2026) sometimes joined such tracks into one
recording (e.g. two "Wasted" of 300 s and 386 s on one bootleg).  For each
recording with several main-version (`album`) tracks on one unofficial
release:
  * the track closest in duration to the recording's other appearances stays
    (no other appearances: the first position stays);
  * every other track of that release gets a new recording (its printed
    title) with an album version; a local file moves with it when its
    duration matches that track (within 2 s) and not the kept one.
Official releases are left alone (their repeats come from MusicBrainz).
Afterwards run merge_recordings.py: the new recordings may be the same song
as tracks on other bootlegs.

usage:
    python split_release_tracks.py           # dry run
    python split_release_tracks.py --apply   # one transaction (backup first)
"""

import argparse
import sqlite3
from statistics import median

from import_release import DATABASE_PATH


FILE_MATCH_MS = 2000


def find_splits(connection):
    """[{recording_id, release_id, release_title, keep: track, move: [tracks]}]."""
    groups = connection.execute("""
        SELECT v.recording_id, rt.release_id, r.title
        FROM release_tracks rt JOIN versions v ON v.id = rt.version_id JOIN releases r ON r.id = rt.release_id
        WHERE v.version_type = 'album' AND r.discography = 'unofficial'
        GROUP BY v.recording_id, rt.release_id HAVING count(*) > 1
        ORDER BY v.recording_id, rt.release_id
    """).fetchall()
    plan = []
    for recording_id, release_id, release_title in groups:
        tracks = [dict(zip(("id", "version_id", "disc", "number", "duration", "title"), row)) for row in connection.execute("""
            SELECT rt.id, rt.version_id, rt.disc_number, rt.track_number, rt.duration_ms,
                   COALESCE(rt.track_title, rec.title)
            FROM release_tracks rt JOIN versions v ON v.id = rt.version_id JOIN recordings rec ON rec.id = v.recording_id
            WHERE v.recording_id = ? AND rt.release_id = ? AND v.version_type = 'album'
            ORDER BY rt.disc_number, rt.track_number
        """, (recording_id, release_id))]
        others = [d for (d,) in connection.execute("""
            SELECT rt.duration_ms FROM release_tracks rt JOIN versions v ON v.id = rt.version_id
            WHERE v.recording_id = ? AND rt.release_id != ? AND v.version_type = 'album'
              AND rt.duration_ms IS NOT NULL
        """, (recording_id, release_id))]
        keep = tracks[0]
        if others:
            reference = median(others)
            keep = min(tracks, key=lambda t: abs((t["duration"] or 10 ** 9) - reference))
        plan.append({"recording_id": recording_id, "release_id": release_id, "release_title": release_title,
                     "keep": keep, "move": [t for t in tracks if t is not keep]})
    return plan


def apply_split(connection, item, artist_id):
    """Give every moved track its own recording; caller commits."""
    keep_duration = item["keep"]["duration"]
    for track in item["move"]:
        recording_id = connection.execute("INSERT INTO recordings (title, artist_id) VALUES (?, ?)",
                                          (track["title"], artist_id)).lastrowid
        version_id = connection.execute("""
            INSERT INTO versions (recording_id, version_name, status, version_type)
            VALUES (?, 'Album Version', 'official_release', 'album')
        """, (recording_id,)).lastrowid
        connection.execute("UPDATE release_tracks SET version_id = ?, track_title = ? WHERE id = ?",
                           (version_id, track["title"], track["id"]))
        if track["duration"] is None:
            continue
        for file_id, seconds in connection.execute(
                "SELECT id, duration FROM files WHERE version_id = ? AND duration IS NOT NULL",
                (track["version_id"],)).fetchall():
            file_ms = seconds * 1000
            near_moved = abs(file_ms - track["duration"]) <= FILE_MATCH_MS
            near_kept = keep_duration is not None and abs(file_ms - keep_duration) <= FILE_MATCH_MS
            if near_moved and not near_kept:
                connection.execute("UPDATE files SET version_id = ? WHERE id = ?", (version_id, file_id))
        connection.execute("""
            INSERT INTO recording_sources (source_id, version_id, notes)
            SELECT source_id, ?, notes FROM recording_sources WHERE version_id = ?
        """, (version_id, track["version_id"]))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Split recordings holding several positions of one release.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    connection = sqlite3.connect(DATABASE_PATH)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        plan = find_splits(connection)
        for item in plan:
            moved = ", ".join(f"#{t['number']} '{t['title']}' {(t['duration'] or 0) // 1000}s" for t in item["move"])
            print(f"SPLIT [{item['recording_id']}] on '{item['release_title']}': keep #{item['keep']['number']} "
                  f"{(item['keep']['duration'] or 0) // 1000}s; new recordings for {moved}")
        print(f"Recordings to split: {len(plan)}")
        if not args.apply or not plan:
            if plan:
                print("Dry run; use --apply to change the database (make a backup first).")
            return plan
        artist_id = connection.execute("SELECT id FROM artists WHERE name = 'Lil Wayne'").fetchone()[0]
        with connection:
            for item in plan:
                apply_split(connection, item, artist_id)
        print("Applied.")
        return plan
    finally:
        connection.close()


if __name__ == "__main__":
    main()
