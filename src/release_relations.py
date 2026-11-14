"""Singles of an album: stored MusicBrainz links plus links inferred from
shared recordings (read-only).

A single is "from" an album when MusicBrainz says so ('single_from' in
release_group_relations) or when a track of the single is the same recording
as a track of the album.  Inferred links are computed, not stored.

usage:  python release_relations.py "Tha Carter III"
"""

import sqlite3
import sys

from import_release import DATABASE_PATH


def album_release_groups(connection, title):
    return connection.execute("""
        SELECT DISTINCT musicbrainz_release_group_id, title FROM releases
        WHERE title LIKE ? AND release_type IN (
            'studio_album', 'collaboration_album', 'group_album', 'ep', 'mixtape')
        AND musicbrainz_release_group_id IS NOT NULL
    """, (f"%{title}%",)).fetchall()


def singles_of_album(connection, album_release_group_id):
    """Return [(single title, single release group ID, evidence)] sorted by title."""
    singles = {}
    for group_id, title in connection.execute("""
        SELECT DISTINCT rel.musicbrainz_release_group_id, rel.title
        FROM release_group_relations link
        JOIN releases rel ON rel.musicbrainz_release_group_id = link.musicbrainz_release_group_id
        WHERE link.related_musicbrainz_release_group_id = ?
        AND link.relation_type = 'single_from'
    """, (album_release_group_id,)):
        singles.setdefault(group_id, [title, set()])[1].add("MusicBrainz")

    for group_id, title in connection.execute("""
        SELECT DISTINCT
            -- Singles known only from LilWayneHQ have no release group ID.
            COALESCE(single.musicbrainz_release_group_id, 'release:' || single.id),
            single.title
        FROM releases single
        JOIN release_tracks single_track ON single_track.release_id = single.id
        JOIN versions single_version ON single_version.id = single_track.version_id
        JOIN versions album_version ON album_version.recording_id = single_version.recording_id
        JOIN release_tracks album_track ON album_track.version_id = album_version.id
        JOIN releases album ON album.id = album_track.release_id
        WHERE single.release_type IN ('single', 'group_single')
        AND album.musicbrainz_release_group_id = ?
    """, (album_release_group_id,)):
        evidence = "LilWayneHQ" if group_id.startswith("release:") else "shared recording"
        singles.setdefault(group_id, [title, set()])[1].add(evidence)

    return sorted(
        (title, group_id, ", ".join(sorted(evidence)))
        for group_id, (title, evidence) in singles.items()
    )


def main():
    title = " ".join(sys.argv[1:]) or input("Album title: ").strip()
    connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
    try:
        albums = album_release_groups(connection, title)
        if not albums:
            print("Album not found.")
        for group_id, album_title in albums:
            singles = singles_of_album(connection, group_id)
            print(f"\n{album_title} — singles: {len(singles)}")
            for single_title, _, evidence in singles:
                print(f"   {single_title}  ({evidence})")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
