"""Turn recordings that are versions of a song into versions of that song.

Imports before the version-type rule (version_types.py) stored e.g.
"Fireman (Live In Los Angeles)" or "Cascades (Snippet)" as separate
recordings.  For each such recording (one version, not a remix):
  * the base song is catalogued once (marker-free title, same song key; if
    several, the only released one): the version moves under that song
    (version_type / version_name set), the empty recording is removed;
  * no base song: the recording itself becomes the song (title without the
    marker) and its version gets the type -- no empty placeholder songs;
  * several possible base songs: listed for manual review, unchanged.
Release tracks keep their printed title (track_title is filled in where it
was empty), files, credits, sources and MusicBrainz IDs keep pointing to the
same version.

usage:
    python split_versions.py           # dry run
    python split_versions.py --apply   # one transaction (make a backup first)
"""

import argparse
import re
import sqlite3

from import_release import DATABASE_PATH
from song_crosscheck import song_key
from version_types import split_version


LEADING_NUMBER = re.compile(r"^\s*\d{1,2}[.)]\s+")


def lookup_key(title):
    return song_key(LEADING_NUMBER.sub("", str(title or "")))


def find_splits(connection):
    """{'move': [...], 'rename': [...], 'review': [...]} -- nothing is changed."""
    recordings = connection.execute("""
        SELECT r.id, r.title, a.availability FROM recordings r
        JOIN recording_availability a ON a.recording_id = r.id ORDER BY r.id
    """).fetchall()
    songs = {}
    for recording_id, title, availability in recordings:
        if split_version(title)[1] is None:
            songs.setdefault(lookup_key(title), []).append((recording_id, title, availability))
    plan = {"move": [], "rename": [], "review": []}
    for recording_id, title, availability in recordings:
        base, version_type, version_name = split_version(title)
        if version_type is None or version_type == "remix":
            continue  # remixes follow the remix model (remix_links.py)
        versions = connection.execute(
            "SELECT id, version_type FROM versions WHERE recording_id = ?", (recording_id,)).fetchall()
        if not versions:
            continue  # a song known only from sources (or a guest spot): nothing to split
        item = {"recording_id": recording_id, "title": title, "base_title": LEADING_NUMBER.sub("", base),
                "version_type": version_type, "version_name": version_name, "availability": availability}
        if len(versions) != 1 or versions[0][1] == "remix":
            plan["review"].append({**item, "reason": f"{len(versions)} versions"})
            continue
        item["version_id"] = versions[0][0]
        candidates = songs.get(lookup_key(base), [])
        for prefer in (lambda c: "(" not in c[1] and "[" not in c[1], lambda c: c[2] == "released"):
            if len(candidates) > 1:
                preferred = [c for c in candidates if prefer(c)]
                candidates = preferred or candidates
        if not candidates:
            plan["rename"].append(item)
        elif len(candidates) == 1:
            plan["move"].append({**item, "base_id": candidates[0][0], "base_title": candidates[0][1]})
        else:
            plan["review"].append({**item, "reason": "several base songs: "
                                   + ", ".join(f"{c[1]} [{c[0]}]" for c in candidates)})
    return plan


def keep_printed_titles(connection, version_id, title):
    connection.execute(
        "UPDATE release_tracks SET track_title = ? WHERE version_id = ? AND track_title IS NULL",
        (title, version_id))


def apply_move(connection, item):
    """Move the version under the base song and remove the empty recording."""
    old_id, base_id, version_id = item["recording_id"], item["base_id"], item["version_id"]
    keep_printed_titles(connection, version_id, item["title"])
    mbid, leak_date = connection.execute(
        "SELECT musicbrainz_recording_id, leak_date FROM recordings WHERE id = ?", (old_id,)).fetchone()
    connection.execute("UPDATE recordings SET musicbrainz_recording_id = NULL WHERE id = ?", (old_id,))
    connection.execute("""
        UPDATE versions SET recording_id = ?, version_type = ?, version_name = ?,
            musicbrainz_recording_id = COALESCE(musicbrainz_recording_id, ?),
            first_known_date = COALESCE(first_known_date, ?)
        WHERE id = ?
    """, (base_id, item["version_type"], item["version_name"], mbid, leak_date, version_id))
    for table in ("credits", "recording_sources"):
        connection.execute(f"UPDATE {table} SET recording_id = ? WHERE recording_id = ?", (base_id, old_id))
    connection.execute("""
        UPDATE recording_musicbrainz_ids SET recording_id = ?, version_id = COALESCE(version_id, ?)
        WHERE recording_id = ?
    """, (base_id, version_id, old_id))
    connection.execute("DELETE FROM recordings WHERE id = ?", (old_id,))


def apply_rename(connection, item):
    """The recording becomes the song itself; its only version gets the type."""
    keep_printed_titles(connection, item["version_id"], item["title"])
    connection.execute("UPDATE recordings SET title = ? WHERE id = ?", (item["base_title"], item["recording_id"]))
    connection.execute("UPDATE versions SET version_type = ?, version_name = ? WHERE id = ?",
                       (item["version_type"], item["version_name"], item["version_id"]))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Split version recordings into versions of their song.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    connection = sqlite3.connect(DATABASE_PATH)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        plan = find_splits(connection)
        for item in plan["move"]:
            print(f"MOVE   '{item['title']}' -> {item['version_type']} '{item['version_name']}' "
                  f"of '{item['base_title']}' [{item['base_id']}]")
        for item in plan["rename"]:
            print(f"RENAME '{item['title']}' -> song '{item['base_title']}', {item['version_type']} version")
        for item in plan["review"]:
            print(f"REVIEW '{item['title']}': {item['reason']}")
        print(f"move {len(plan['move'])}, rename {len(plan['rename'])}, review {len(plan['review'])}")
        if not args.apply:
            print("Dry run; use --apply to change the database (make a backup first).")
            return plan
        with connection:
            for item in plan["move"]:
                apply_move(connection, item)
            for item in plan["rename"]:
                apply_rename(connection, item)
        print("Applied.")
        return plan
    finally:
        connection.close()


if __name__ == "__main__":
    main()
