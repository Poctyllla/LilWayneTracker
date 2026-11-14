"""Turn remixes that were imported as separate recordings into remix versions.

Packages say which tracks are remixes (``remix_of_recording_id``).  Releases
imported before the remix model stored such a remix as its own recording.
This tool moves that recording's version under the original song recording
(version_type 'remix', its MusicBrainz Recording ID on the version) and
removes the now empty recording.  Release tracks, credits, sources and local
files keep pointing to the same version.

usage:
    python relink_remixes.py           # dry run: list what would change
    python relink_remixes.py --apply   # apply in one transaction
"""

import argparse
import sqlite3

from import_release import DATABASE_PATH
from release_group_package import (
    get_package_validation_issues,
    iter_package_files,
    iter_resolved_editions,
    load_release_group_package,
)
from remix_links import remix_version_name


def find_relinks(connection, packages):
    """Return remix tracks of the packages that are separate recordings in SQLite."""
    found = {}
    for package in packages:
        for _, edition in iter_resolved_editions(package):
            for track in edition["tracks"]:
                remix_id = track.get("musicbrainz_recording_id")
                original_id = track.get("remix_of_recording_id")
                if not remix_id or not original_id or remix_id in found:
                    continue
                row = connection.execute(
                    "SELECT id, title FROM recordings WHERE musicbrainz_recording_id = ?",
                    (remix_id,),
                ).fetchone()
                if row:
                    found[remix_id] = {
                        "recording_id": row[0],
                        "title": row[1],
                        "remix_musicbrainz_id": remix_id,
                        "original_musicbrainz_id": original_id,
                        "original_title": track.get("remix_of_title"),
                        "version_name": remix_version_name(track.get("title")),
                    }
    return list(found.values())


def relink_remix(connection, item, artist_id):
    """Move one separate remix recording under its original; caller commits."""
    old_id = item["recording_id"]
    versions = connection.execute(
        "SELECT id FROM versions WHERE recording_id = ?", (old_id,)
    ).fetchall()
    if len(versions) != 1:
        raise ValueError(
            f"Recording {old_id} ('{item['title']}') has {len(versions)} versions; "
            "relink it manually."
        )
    version_id = versions[0][0]

    original = connection.execute(
        "SELECT id FROM recordings WHERE musicbrainz_recording_id = ?",
        (item["original_musicbrainz_id"],),
    ).fetchone()
    if original is None:
        if not item.get("original_title"):
            raise ValueError(
                f"Original of '{item['title']}' is not catalogued and has no "
                "MusicBrainz title; relink it manually."
            )
        cursor = connection.execute("""
            INSERT INTO recordings (title, artist_id, musicbrainz_recording_id)
            VALUES (?, ?, ?)
        """, (item["original_title"], artist_id, item["original_musicbrainz_id"]))
        original = (cursor.lastrowid,)
    original_id = original[0]
    if original_id == old_id:
        raise ValueError(f"'{item['title']}' would be linked to itself.")

    connection.execute(
        "UPDATE recordings SET musicbrainz_recording_id = NULL WHERE id = ?", (old_id,)
    )
    connection.execute("""
        UPDATE versions
        SET recording_id = ?, version_type = 'remix', version_name = ?,
            musicbrainz_recording_id = ?
        WHERE id = ?
    """, (original_id, item["version_name"], item["remix_musicbrainz_id"], version_id))
    connection.execute(
        "UPDATE credits SET recording_id = ? WHERE recording_id = ?", (original_id, old_id)
    )
    connection.execute(
        "UPDATE recording_sources SET recording_id = ? WHERE recording_id = ?",
        (original_id, old_id),
    )
    connection.execute("DELETE FROM recordings WHERE id = ?", (old_id,))
    return original_id, version_id


def unlink_remix_version(connection, musicbrainz_recording_id, note=None):
    """Undo a remix version that turned out to be the same song.

    The remix version's release tracks, credits, sources and files move to the
    recording's album version (created if missing); its MusicBrainz ID becomes
    an alternate ID of the recording.  Caller commits.
    """
    row = connection.execute("""
        SELECT id, recording_id FROM versions
        WHERE musicbrainz_recording_id = ? AND version_type = 'remix'
    """, (musicbrainz_recording_id,)).fetchone()
    if row is None:
        raise ValueError(f"No remix version with MusicBrainz ID {musicbrainz_recording_id}.")
    version_id, recording_id = row
    album = connection.execute("""
        SELECT id FROM versions WHERE recording_id = ? AND version_type = 'album'
        ORDER BY id LIMIT 1
    """, (recording_id,)).fetchone()
    if album is None:
        connection.execute("""
            UPDATE versions SET version_type = 'album', version_name = 'Album Version',
            musicbrainz_recording_id = NULL WHERE id = ?
        """, (version_id,))
        album_id = version_id
    else:
        album_id = album[0]
        for table in ("release_tracks", "files", "credits", "recording_sources"):
            connection.execute(
                f"UPDATE {table} SET version_id = ? WHERE version_id = ?", (album_id, version_id)
            )
        # Alternate IDs of the former remix now belong to the song itself.
        connection.execute(
            "UPDATE recording_musicbrainz_ids SET version_id = NULL WHERE version_id = ?",
            (version_id,),
        )
        connection.execute("DELETE FROM versions WHERE id = ?", (version_id,))
    connection.execute("""
        INSERT OR IGNORE INTO recording_musicbrainz_ids (recording_id, musicbrainz_recording_id, source, note)
        VALUES (?, ?, 'same-song rule', ?)
    """, (recording_id, musicbrainz_recording_id, note or "Former remix version; same song"))
    return recording_id, album_id


def load_valid_packages():
    packages = []
    for path in iter_package_files():
        package = load_release_group_package(path)
        errors, _ = get_package_validation_issues(package)
        if errors:
            print(f"SKIPPED (package has validation errors): {path.name}")
            continue
        packages.append(package)
    return packages


def main(argv=None):
    parser = argparse.ArgumentParser(description="Relink remixes imported as recordings.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    connection = sqlite3.connect(DATABASE_PATH)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        items = find_relinks(connection, load_valid_packages())
        if not items:
            print("Nothing to relink.")
            return
        for item in items:
            print(f"'{item['title']}' (recording {item['recording_id']}) -> version "
                  f"'{item['version_name']}' of {item['original_musicbrainz_id']}"
                  f" ({item.get('original_title') or 'catalogued original'})")
        if not args.apply:
            print("Dry run; use --apply to change the database (make a backup first).")
            return
        artist_id = connection.execute(
            "SELECT id FROM artists WHERE name = 'Lil Wayne'").fetchone()[0]
        with connection:
            for item in items:
                relink_remix(connection, item, artist_id)
        print(f"Relinked: {len(items)}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
