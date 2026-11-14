"""Remove one release edition (by MusicBrainz Release ID) from SQLite.

Only data that belongs exclusively to that edition is removed:

* its release_tracks and its release row;
* versions used by no other release, and recordings left without versions;
* credits and source links of those versions/recordings;
* sources linked only to this release (e.g. its MusicBrainz release URL),
  including their links to shared versions.

Shared recordings and versions stay untouched.  Removal is refused when a
local audio file (files table) points to a version that would be removed.
Run inside the caller's transaction; the CLI makes a dry run first.
"""

import sqlite3

from import_release import DATABASE_PATH


def _ids(connection, sql, params=()):
    return sorted(row[0] for row in connection.execute(sql, params).fetchall())


def _placeholders(values):
    return ", ".join("?" for _ in values)


def plan_release_removal(connection, musicbrainz_release_id):
    """Return what would be removed; raises ValueError if removal is unsafe."""
    row = connection.execute(
        "SELECT id, title FROM releases WHERE musicbrainz_release_id = ?",
        (musicbrainz_release_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Release not found: {musicbrainz_release_id}")
    release_id, title = row

    version_ids = _ids(connection, """
        SELECT DISTINCT version_id FROM release_tracks WHERE release_id = ?
    """, (release_id,))
    orphan_versions = [
        version_id for version_id in version_ids
        if not connection.execute("""
            SELECT 1 FROM release_tracks
            WHERE version_id = ? AND release_id != ? LIMIT 1
        """, (version_id, release_id)).fetchone()
    ]

    if orphan_versions:
        files = connection.execute(
            f"SELECT COUNT(*) FROM files WHERE version_id IN ({_placeholders(orphan_versions)})",
            orphan_versions,
        ).fetchone()[0]
        if files:
            raise ValueError(
                f"{files} local file(s) are linked to versions of this release; "
                "unlink them before removing the release."
            )

    recording_ids = _ids(connection, f"""
        SELECT DISTINCT recording_id FROM versions
        WHERE id IN ({_placeholders(orphan_versions)})
    """, orphan_versions) if orphan_versions else []
    orphan_recordings = [
        recording_id for recording_id in recording_ids
        if not connection.execute(f"""
            SELECT 1 FROM versions WHERE recording_id = ?
            AND id NOT IN ({_placeholders(orphan_versions)}) LIMIT 1
        """, [recording_id, *orphan_versions]).fetchone()
    ]

    release_sources = _ids(connection, """
        SELECT DISTINCT source_id FROM recording_sources WHERE release_id = ?
    """, (release_id,))
    exclusive_sources = [
        source_id for source_id in release_sources
        if not connection.execute("""
            SELECT 1 FROM recording_sources
            WHERE source_id = ? AND release_id IS NOT NULL AND release_id != ? LIMIT 1
        """, (source_id, release_id)).fetchone()
    ]

    return {
        "release_id": release_id,
        "title": title,
        "musicbrainz_release_id": musicbrainz_release_id,
        "release_tracks": connection.execute(
            "SELECT COUNT(*) FROM release_tracks WHERE release_id = ?", (release_id,)
        ).fetchone()[0],
        "versions": orphan_versions,
        "recordings": orphan_recordings,
        "shared_versions_kept": len(version_ids) - len(orphan_versions),
        "sources": exclusive_sources,
    }


def remove_release(connection, musicbrainz_release_id):
    """Remove the release as planned; returns the plan.  Caller commits."""
    plan = plan_release_removal(connection, musicbrainz_release_id)
    release_id = plan["release_id"]
    versions, recordings, sources = plan["versions"], plan["recordings"], plan["sources"]

    def delete(sql, values):
        if values:
            connection.execute(sql.format(_placeholders(values)), values)

    delete("DELETE FROM recording_sources WHERE source_id IN ({})", sources)
    connection.execute("DELETE FROM recording_sources WHERE release_id = ?", (release_id,))
    delete("DELETE FROM recording_sources WHERE version_id IN ({})", versions)
    delete("DELETE FROM recording_sources WHERE recording_id IN ({})", recordings)
    delete("DELETE FROM credits WHERE version_id IN ({})", versions)
    delete("DELETE FROM credits WHERE recording_id IN ({})", recordings)
    connection.execute("DELETE FROM release_tracks WHERE release_id = ?", (release_id,))
    delete("DELETE FROM versions WHERE id IN ({})", versions)
    delete("DELETE FROM recordings WHERE id IN ({})", recordings)
    delete("DELETE FROM sources WHERE id IN ({})", sources)
    connection.execute("DELETE FROM releases WHERE id = ?", (release_id,))
    return plan


def print_plan(plan):
    print(f"Release: {plan['title']} (database ID {plan['release_id']}, "
          f"MusicBrainz {plan['musicbrainz_release_id']})")
    print(f"Release tracks to remove: {plan['release_tracks']}")
    print(f"Versions/recordings only on this release: "
          f"{len(plan['versions'])} / {len(plan['recordings'])}")
    print(f"Shared versions kept: {plan['shared_versions_kept']}")
    print(f"Sources only for this release: {len(plan['sources'])}")


def main():
    musicbrainz_release_id = input("MusicBrainz Release ID to remove: ").strip()
    if not musicbrainz_release_id:
        return

    connection = sqlite3.connect(DATABASE_PATH)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            plan = plan_release_removal(connection, musicbrainz_release_id)
        except ValueError as error:
            print(f"CANNOT REMOVE: {error}")
            return
        print_plan(plan)
        print("\nMake a database backup before continuing.")
        if input("Remove this release? (yes/no): ").strip().lower() != "yes":
            print("Cancelled; nothing was changed.")
            return
        with connection:
            remove_release(connection, musicbrainz_release_id)
        print("Release removed.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
