"""Merge recordings that are the same song under different MusicBrainz IDs.

Uses the same-song rule of duplicate_guard (same normalized title, not a
remix, duration within 15 s; generic titles such as 'Intro' within 3 s).
Safeguards (Nov 13, 2026):
  * every known duration of both recordings must lie within the limit, so
    merges cannot drift along a chain (313 s -> 308 s -> 297 s);
  * two recordings on the same release are different songs (different
    positions of one release);
  * pairs the owner marked keep_separate in data/song_review_decisions.json
    are never merged.
The older recording (lower ID) is kept; the other one's release tracks,
credits, sources, local files and versions move to it, and its MusicBrainz
Recording ID is stored as an alternate.

usage:
    python merge_recordings.py           # dry run
    python merge_recordings.py --apply   # merge all same-song pairs (backup first)
"""

import argparse
import json
import sqlite3
from itertools import combinations
from pathlib import Path

from duplicate_guard import GENERIC_SAME_SONG_MS, REVIEW, SAME, SAME_SONG_MS, is_generic_title, match_recording
from import_release import DATABASE_PATH
from titles import normalized_title


DECISIONS_PATH = Path(__file__).resolve().parent.parent / "data" / "song_review_decisions.json"


def keep_separate_pairs(path=DECISIONS_PATH):
    """{frozenset({a, b})} of recordings the owner said are different songs."""
    try:
        decisions = json.loads(Path(path).read_text(encoding="utf-8"))["decisions"]
    except (OSError, ValueError, KeyError):
        return set()
    return {frozenset(pair) for d in decisions if d.get("action") == "keep_separate"
            for pair in combinations(d.get("recordings", []), 2)}


def release_ids(connection):
    """{recording_id: set of release IDs} over all versions."""
    result = {}
    for recording_id, release_id in connection.execute("""
        SELECT DISTINCT v.recording_id, rt.release_id FROM versions v
        JOIN release_tracks rt ON rt.version_id = v.id
    """):
        result.setdefault(recording_id, set()).add(release_id)
    return result


def within_limit(title, durations_a, durations_b):
    """All known durations of both recordings lie within the same-song limit."""
    known = [d for d in durations_a | durations_b if d is not None]
    limit = GENERIC_SAME_SONG_MS if is_generic_title(title) else SAME_SONG_MS
    return bool(known) and max(known) - min(known) <= limit


def recording_durations(connection):
    """{recording_id: (title, set of durations)} for recordings with non-remix tracks."""
    result = {}
    for recording_id, title, duration in connection.execute("""
        SELECT rec.id, rec.title, rt.duration_ms
        FROM recordings rec
        JOIN versions v ON v.recording_id = rec.id AND v.version_type = 'album'
        JOIN release_tracks rt ON rt.version_id = v.id
    """):
        result.setdefault(recording_id, (title, set()))[1].add(duration)
    return result


def find_pairs(connection, separate=None):
    """Return (same_song_pairs, review_pairs) as (keep_id, drop_id, title).

    Uses the import rule (match_recording): generic titles such as 'Intro'
    need 3 s, and tracks without durations are never merged; plus the
    safeguards in the module docstring.  Each recording is merged at most
    once per run; run again after applying.
    """
    separate = keep_separate_pairs() if separate is None else separate
    releases = release_ids(connection)
    by_title = {}
    for recording_id, (title, durations) in recording_durations(connection).items():
        by_title.setdefault(normalized_title(title), []).append((recording_id, title, durations))
    same, review, dropped = [], [], set()
    for items in by_title.values():
        items.sort()
        for index, (keep_id, title, keep_durations) in enumerate(items):
            if keep_id in dropped:
                continue
            for drop_id, _, drop_durations in items[index + 1:]:
                if drop_id in dropped or frozenset((keep_id, drop_id)) in separate:
                    continue
                if releases.get(keep_id, set()) & releases.get(drop_id, set()):
                    continue  # two positions of one release
                if not within_limit(title, keep_durations, drop_durations):
                    continue
                outcomes = {
                    match_recording(title, duration, {keep_id: keep_durations},
                                    allow_unknown_duration=False)[0]
                    for duration in drop_durations if duration is not None
                }
                if SAME in outcomes:
                    same.append((keep_id, drop_id, title))
                    dropped.add(drop_id)
                elif REVIEW in outcomes:
                    review.append((keep_id, drop_id, title))
    return same, review


def album_version(connection, recording_id):
    row = connection.execute("""
        SELECT id FROM versions WHERE recording_id = ? AND version_type = 'album'
        ORDER BY id LIMIT 1
    """, (recording_id,)).fetchone()
    return row[0] if row else None


def merge(connection, keep_id, drop_id):
    """Merge recording drop_id into keep_id; caller commits."""
    if keep_id == drop_id:
        raise ValueError("Cannot merge a recording into itself.")
    drop_title, drop_genius = connection.execute(
        "SELECT title, genius_song_id FROM recordings WHERE id = ?", (drop_id,)).fetchone()
    keep_title = connection.execute("SELECT title FROM recordings WHERE id = ?", (keep_id,)).fetchone()[0]
    # Tracks keep the title printed on their edition.
    connection.execute("""
        UPDATE release_tracks SET track_title = ?
        WHERE track_title IS NULL AND version_id IN (SELECT id FROM versions WHERE recording_id = ?)
    """, (drop_title, drop_id))
    keep_version = album_version(connection, keep_id)
    for (version_id, version_type) in connection.execute(
        "SELECT id, version_type FROM versions WHERE recording_id = ?", (drop_id,)
    ).fetchall():
        # Remixes, demos, snippets, live ... stay versions of their own.
        if version_type != "album" or keep_version is None:
            connection.execute(
                "UPDATE versions SET recording_id = ? WHERE id = ?", (keep_id, version_id)
            )
            if keep_version is None and version_type == "album":
                keep_version = version_id
            continue
        connection.execute(
            "UPDATE release_tracks SET version_id = ? WHERE version_id = ?",
            (keep_version, version_id),
        )
        connection.execute(
            "UPDATE files SET version_id = ? WHERE version_id = ?", (keep_version, version_id)
        )
        connection.execute("""
            DELETE FROM credits WHERE version_id = ? AND EXISTS (
                SELECT 1 FROM credits AS kept WHERE kept.version_id = ?
                AND kept.artist_id = credits.artist_id AND kept.role = credits.role)
        """, (version_id, keep_version))
        connection.execute(
            "UPDATE credits SET version_id = ? WHERE version_id = ?", (keep_version, version_id)
        )
        connection.execute("""
            DELETE FROM recording_sources WHERE version_id = ? AND EXISTS (
                SELECT 1 FROM recording_sources AS kept WHERE kept.version_id = ?
                AND kept.source_id = recording_sources.source_id)
        """, (version_id, keep_version))
        connection.execute(
            "UPDATE recording_sources SET version_id = ? WHERE version_id = ?",
            (keep_version, version_id),
        )
        connection.execute(
            "UPDATE recording_musicbrainz_ids SET version_id = ? WHERE version_id = ?",
            (keep_version, version_id),
        )
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'version_links'").fetchone():
            # streaming track links: the kept version's own link of a service wins
            connection.execute("""
                DELETE FROM version_links WHERE version_id = ? AND EXISTS (
                    SELECT 1 FROM version_links AS kept WHERE kept.version_id = ?
                    AND kept.service = version_links.service)
            """, (version_id, keep_version))
            connection.execute(
                "UPDATE version_links SET version_id = ? WHERE version_id = ?", (keep_version, version_id)
            )
        connection.execute("DELETE FROM versions WHERE id = ?", (version_id,))

    connection.execute(
        "UPDATE credits SET recording_id = ? WHERE recording_id = ?", (keep_id, drop_id)
    )
    connection.execute(
        "UPDATE recording_sources SET recording_id = ? WHERE recording_id = ?", (keep_id, drop_id)
    )
    connection.execute(
        "UPDATE recording_musicbrainz_ids SET recording_id = ? WHERE recording_id = ?",
        (keep_id, drop_id),
    )
    # Other spellings: the dropped recording's aliases and its own title.
    connection.execute("UPDATE OR IGNORE recording_aliases SET recording_id = ? WHERE recording_id = ?",
                       (keep_id, drop_id))
    connection.execute("DELETE FROM recording_aliases WHERE recording_id = ?", (drop_id,))
    if drop_title and drop_title != keep_title:
        connection.execute("INSERT OR IGNORE INTO recording_aliases (recording_id, title, source, note) "
                           "VALUES (?, ?, 'merge', ?)", (keep_id, drop_title, f"Merged recording {drop_id}"))
    connection.execute("UPDATE recordings SET genius_song_id = COALESCE(genius_song_id, ?) WHERE id = ?",
                       (drop_genius, keep_id))
    # videos, chart peaks and certifications (tables added Nov 15, 2026) follow the song
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "video_recordings" in tables:
        connection.execute("UPDATE OR IGNORE video_recordings SET recording_id = ? WHERE recording_id = ?", (keep_id, drop_id))
        connection.execute("DELETE FROM video_recordings WHERE recording_id = ?", (drop_id,))
    for table in ("chart_entries", "certifications"):
        if table in tables:
            connection.execute(f"UPDATE {table} SET recording_id = ? WHERE recording_id = ?", (keep_id, drop_id))
    drop_musicbrainz_id = connection.execute(
        "SELECT musicbrainz_recording_id FROM recordings WHERE id = ?", (drop_id,)
    ).fetchone()[0]
    connection.execute("DELETE FROM recordings WHERE id = ?", (drop_id,))
    if drop_musicbrainz_id:
        connection.execute("""
            INSERT INTO recording_musicbrainz_ids (recording_id, musicbrainz_recording_id, source, note)
            VALUES (?, ?, 'merge', ?)
        """, (keep_id, drop_musicbrainz_id, f"Merged recording {drop_id} (same-song rule)"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Merge same-song recordings.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    connection = sqlite3.connect(DATABASE_PATH)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        same, review = find_pairs(connection)
        for keep_id, drop_id, title in same:
            print(f"MERGE   '{title}': recording {drop_id} -> {keep_id}")
        for keep_id, drop_id, title in review:
            print(f"REVIEW  '{title}': recordings {keep_id} and {drop_id} (5-15 s apart)")
        if not same:
            print("Nothing to merge.")
            return
        if not args.apply:
            print("Dry run; use --apply to merge (back up first: python database.py --backup <label>).")
            return
        with connection:
            for keep_id, drop_id, _ in same:
                merge(connection, keep_id, drop_id)
        print(f"Merged: {len(same)}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
