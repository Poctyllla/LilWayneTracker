"""Shared SQLite import logic for one release edition.

import_release_data() runs inside the caller's transaction; the entry point is
import_release_group.py, which imports all editions of a package atomically.
"""

from pathlib import Path

from duplicate_guard import (
    NEW, REVIEW, SAME, load_catalogue_from_connection, match_recording, song_without_main_version,
)
from titles import normalized_title
from remix_links import remix_version_name


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = PROJECT_ROOT / "data" / "lilwayne.db"


def get_or_create_artist(connection, name):
    if not name or name.strip().lower() == "unknown artist":
        raise ValueError("Invalid artist name.")

    name = name.strip()

    connection.execute("""
        INSERT OR IGNORE INTO artists (name)
        VALUES (?)
    """, (name,))

    result = connection.execute("""
        SELECT id FROM artists
        WHERE name = ?
    """, (name,)).fetchone()

    return result[0]


def get_or_create_source(connection, source_data):
    name = source_data["name"]
    url = source_data.get("url")

    result = connection.execute("""
        SELECT id FROM sources
        WHERE name = ? AND url IS ?
    """, (name, url)).fetchone()

    if result:
        return result[0]

    cursor = connection.execute("""
        INSERT INTO sources (name, url)
        VALUES (?, ?)
    """, (name, url))

    return cursor.lastrowid


def add_source_link(
    connection,
    source_id,
    version_id=None,
    release_id=None,
    notes=None
):
    if version_id is not None:
        existing = connection.execute("""
            SELECT id FROM recording_sources
            WHERE source_id = ? AND version_id = ?
        """, (source_id, version_id)).fetchone()

        if existing:
            return

        connection.execute("""
            INSERT INTO recording_sources (
                source_id, version_id, notes
            )
            VALUES (?, ?, ?)
        """, (source_id, version_id, notes))

    elif release_id is not None:
        existing = connection.execute("""
            SELECT id FROM recording_sources
            WHERE source_id = ? AND release_id = ?
        """, (source_id, release_id)).fetchone()

        if existing:
            return

        connection.execute("""
            INSERT INTO recording_sources (
                source_id, release_id, notes
            )
            VALUES (?, ?, ?)
        """, (source_id, release_id, notes))


def update_musicbrainz_id(
    connection,
    table,
    column,
    record_id,
    musicbrainz_id
):
    if not musicbrainz_id:
        return

    existing = connection.execute(
        f"""
        SELECT id FROM {table}
        WHERE {column} = ?
        """,
        (musicbrainz_id,)
    ).fetchone()

    if existing and existing[0] != record_id:
        raise ValueError(
            f"MusicBrainz ID conflict: {musicbrainz_id} "
            f"already belongs to {table} ID {existing[0]}."
        )

    current = connection.execute(
        f"""
        SELECT {column} FROM {table}
        WHERE id = ?
        """,
        (record_id,)
    ).fetchone()

    if not current:
        raise ValueError(
            f"Record not found: {table} ID {record_id}"
        )

    current_id = current[0]

    if current_id and current_id != musicbrainz_id:
        raise ValueError(
            f"Conflicting MusicBrainz ID for "
            f"{table} ID {record_id}: "
            f"database={current_id}, JSON={musicbrainz_id}"
        )

    connection.execute(
        f"""
        UPDATE {table}
        SET {column} = ?
        WHERE id = ?
        """,
        (musicbrainz_id, record_id)
    )


def get_or_create_release(connection, release_data, artist_id):
    title = release_data["title"]
    musicbrainz_id = release_data.get("musicbrainz_release_id")
    release_group_id = release_data.get(
        "musicbrainz_release_group_id"
    )

    # 1. Search by MusicBrainz Release ID.
    result = None

    if musicbrainz_id:
        result = connection.execute("""
            SELECT id FROM releases
            WHERE musicbrainz_release_id = ?
        """, (musicbrainz_id,)).fetchone()

    # 2. If not found, search by title and artist.
    if not result:
        candidates = connection.execute("""
            SELECT id FROM releases
            WHERE title = ?
            AND artist_id = ?
            AND musicbrainz_release_id IS NULL
            ORDER BY id
        """, (title, artist_id)).fetchall()

        if len(candidates) > 1:
            candidate_ids = [row[0] for row in candidates]

            raise ValueError(
                f"Ambiguous release match for '{title}'. "
                f"Multiple existing releases found: {candidate_ids}. "
                "Manual selection is required."
            )

        if len(candidates) == 1:
            result = candidates[0]

    # 3. Update an existing release.
    if result:
        release_id = result[0]

        update_musicbrainz_id(
            connection,
            "releases",
            "musicbrainz_release_id",
            release_id,
            musicbrainz_id
        )

        # Release Group ID can be shared by different editions.
        if release_group_id:
            current = connection.execute("""
                SELECT musicbrainz_release_group_id
                FROM releases
                WHERE id = ?
            """, (release_id,)).fetchone()

            if not current:
                raise ValueError(
                    f"Release not found: ID {release_id}"
                )

            current_group_id = current[0]

            if current_group_id and current_group_id != release_group_id:
                raise ValueError(
                    f"Conflicting Release Group ID for "
                    f"release ID {release_id}: "
                    f"database={current_group_id}, "
                    f"JSON={release_group_id}"
                )

            if current_group_id is None:
                connection.execute("""
                    UPDATE releases
                    SET musicbrainz_release_group_id = ?
                    WHERE id = ?
                """, (release_group_id, release_id))

        # Official/unofficial discography: fill if missing, never flip silently.
        discography = release_data.get("discography")
        if discography:
            current_discography = connection.execute(
                "SELECT discography FROM releases WHERE id = ?", (release_id,)
            ).fetchone()[0]
            if current_discography and current_discography != discography:
                raise ValueError(
                    f"Conflicting discography for release ID {release_id}: "
                    f"database={current_discography}, JSON={discography}"
                )
            if current_discography is None:
                connection.execute(
                    "UPDATE releases SET discography = ? WHERE id = ?",
                    (discography, release_id),
                )

        # Prefer the more precise release date.
        # Example: 1999 -> 1999-11-02.
        connection.execute("""
            UPDATE releases
            SET
                release_date = CASE
                    WHEN release_date IS NULL
                         OR LENGTH(?) > LENGTH(release_date)
                    THEN ?
                    ELSE release_date
                END,
                release_type = COALESCE(release_type, ?)
            WHERE id = ?
        """, (
            release_data.get("release_date"),
            release_data.get("release_date"),
            release_data.get("release_type"),
            release_id
        ))

        return release_id, False

    # 4. Otherwise create a new release.
    cursor = connection.execute("""
        INSERT INTO releases (
            title,
            release_type,
            release_date,
            artist_id,
            musicbrainz_release_id,
            musicbrainz_release_group_id,
            discography
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        title,
        release_data.get("release_type"),
        release_data.get("release_date"),
        artist_id,
        musicbrainz_id,
        release_group_id,
        release_data.get("discography")
    ))

    return cursor.lastrowid, True


def get_or_create_recording(
    connection,
    track,
    artist_id,
    existing_recording_id=None
):
    recording_id_from_json = track.get(
        "musicbrainz_recording_id"
    )

    if existing_recording_id is not None:
        recording_id = existing_recording_id

    elif recording_id_from_json:
        result = connection.execute("""
            SELECT id FROM recordings
            WHERE musicbrainz_recording_id = ?
        """, (recording_id_from_json,)).fetchone()

        if result:
            recording_id = result[0]
        else:
            cursor = connection.execute("""
                INSERT INTO recordings (title, artist_id)
                VALUES (?, ?)
            """, (track["title"], artist_id))

            recording_id = cursor.lastrowid

    else:
        cursor = connection.execute("""
            INSERT INTO recordings (title, artist_id)
            VALUES (?, ?)
        """, (track["title"], artist_id))

        recording_id = cursor.lastrowid

    update_musicbrainz_id(
        connection,
        "recordings",
        "musicbrainz_recording_id",
        recording_id,
        recording_id_from_json
    )

    return recording_id


def get_or_create_version(
    connection,
    recording_id,
    existing_version_id=None
):
    if existing_version_id is not None:
        return existing_version_id

    result = connection.execute("""
        SELECT id FROM versions
        WHERE recording_id = ?
        AND version_name = 'Album Version'
        AND version_type = 'album'
        ORDER BY id
        LIMIT 1
    """, (recording_id,)).fetchone()

    if result:
        return result[0]

    cursor = connection.execute("""
        INSERT INTO versions (
            recording_id,
            version_name,
            status,
            version_type
        )
        VALUES (?, ?, ?, ?)
    """, (
        recording_id,
        "Album Version",
        "official_release",
        "album"
    ))

    return cursor.lastrowid


def find_recording_by_musicbrainz_id(connection, musicbrainz_recording_id):
    """Recording ID for a primary or alternate MusicBrainz Recording ID, or None."""
    if not musicbrainz_recording_id:
        return None
    row = connection.execute("""
        SELECT id FROM recordings WHERE musicbrainz_recording_id = ?
        UNION
        SELECT recording_id FROM recording_musicbrainz_ids WHERE musicbrainz_recording_id = ?
    """, (musicbrainz_recording_id, musicbrainz_recording_id)).fetchone()
    return row[0] if row else None


def attach_recording_musicbrainz_id(
    connection, recording_id, musicbrainz_recording_id,
    allow_alternate=False, title=None
):
    """Make sure the MusicBrainz ID belongs to this recording.

    Accepts the primary or a known alternate ID.  Sets the primary ID when the
    recording has none.  Stores a new alternate only when the same-song rule
    linked the track (allow_alternate); otherwise a different ID is a conflict.
    """
    if not musicbrainz_recording_id:
        return
    owner = find_recording_by_musicbrainz_id(connection, musicbrainz_recording_id)
    if owner is not None and owner != recording_id:
        raise ValueError(
            f"MusicBrainz ID conflict: {musicbrainz_recording_id} already belongs "
            f"to recordings ID {owner}."
        )
    if owner == recording_id:
        return
    primary = connection.execute(
        "SELECT musicbrainz_recording_id FROM recordings WHERE id = ?", (recording_id,)
    ).fetchone()[0]
    if primary is None:
        connection.execute(
            "UPDATE recordings SET musicbrainz_recording_id = ? WHERE id = ?",
            (musicbrainz_recording_id, recording_id),
        )
    elif allow_alternate:
        connection.execute("""
            INSERT INTO recording_musicbrainz_ids (
                recording_id, musicbrainz_recording_id, source, note
            )
            VALUES (?, ?, 'same-song rule', ?)
        """, (recording_id, musicbrainz_recording_id,
              f"Linked by title and duration: {title}"))
    else:
        raise ValueError(
            f"Conflicting MusicBrainz ID for recordings ID {recording_id}: "
            f"database={primary}, JSON={musicbrainz_recording_id}"
        )


def find_version_by_musicbrainz_id(connection, musicbrainz_recording_id):
    """Return (version_id, recording_id) of a version with its own MB ID, or None.

    Also finds version-level alternate IDs (the same remix under another ID).
    """
    if not musicbrainz_recording_id:
        return None
    return connection.execute("""
        SELECT id, recording_id FROM versions
        WHERE musicbrainz_recording_id = ?
        UNION
        SELECT version_id, recording_id FROM recording_musicbrainz_ids
        WHERE musicbrainz_recording_id = ? AND version_id IS NOT NULL
    """, (musicbrainz_recording_id, musicbrainz_recording_id)).fetchone()


def get_or_create_remix_version(connection, track, artist_id=None):
    """Return (recording_id, version_id) of the remix version of the original.

    The remix becomes a version (version_type 'remix') of the recording given
    by remix_of_recording_id; no separate recording is created.  When the
    original is not catalogued yet but MusicBrainz named it (remix_of_title
    from a "remix" relationship), the original song recording is created
    without versions; its own release adds its album version later.
    """
    remix_id = track.get("musicbrainz_recording_id")
    original_id = track["remix_of_recording_id"]
    version_name = remix_version_name(track["title"])

    existing = find_version_by_musicbrainz_id(connection, remix_id)
    if existing:
        return existing[1], existing[0]

    if remix_id and find_recording_by_musicbrainz_id(connection, remix_id) is not None \
            and not find_version_by_musicbrainz_id(connection, remix_id):
        raise ValueError(
            f"Remix '{track['title']}' ({remix_id}) is already catalogued as a "
            "separate recording; resolve this manually."
        )

    original = connection.execute(
        "SELECT id FROM recordings WHERE musicbrainz_recording_id = ?", (original_id,)
    ).fetchone()
    if not original and track.get("remix_of_title") and artist_id is not None:
        cursor = connection.execute("""
            INSERT INTO recordings (title, artist_id, musicbrainz_recording_id)
            VALUES (?, ?, ?)
        """, (track["remix_of_title"], artist_id, original_id))
        original = (cursor.lastrowid,)
    if not original:
        raise ValueError(
            f"Original recording {original_id} of remix '{track['title']}' is not "
            "in the database; import the release with the original first."
        )

    # The same remix (same original, same remix name) under another
    # MusicBrainz ID is one version; the extra ID is stored as an alternate.
    same = connection.execute("""
        SELECT id FROM versions
        WHERE recording_id = ? AND version_type = 'remix'
        AND lower(version_name) = lower(?)
        ORDER BY id LIMIT 1
    """, (original[0], version_name)).fetchone()
    if same:
        if remix_id:
            connection.execute("""
                INSERT OR IGNORE INTO recording_musicbrainz_ids (
                    recording_id, musicbrainz_recording_id, source, note, version_id
                )
                VALUES (?, ?, 'same-song rule', ?, ?)
            """, (original[0], remix_id, f"Same remix: {track['title']}", same[0]))
        return original[0], same[0]

    cursor = connection.execute("""
        INSERT INTO versions (
            recording_id, version_name, status, version_type,
            musicbrainz_recording_id
        )
        VALUES (?, ?, 'official_release', 'remix', ?)
    """, (original[0], version_name, remix_id))
    return original[0], cursor.lastrowid


def get_or_create_release_track(
    connection,
    release_id,
    version_id,
    track,
    disc_number
):
    track_number = track["number"]

    existing = connection.execute("""
        SELECT id, version_id
        FROM release_tracks
        WHERE release_id = ?
        AND disc_number = ?
        AND track_number = ?
    """, (
        release_id,
        disc_number,
        track_number
    )).fetchone()

    if existing:
        release_track_id, existing_version_id = existing

        if existing_version_id != version_id:
            raise ValueError(
                f"Track relationship conflict: "
                f"disc {disc_number}, track {track_number}."
            )

        return release_track_id, False

    cursor = connection.execute("""
        INSERT INTO release_tracks (
            release_id,
            version_id,
            disc_number,
            track_number,
            track_title
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        release_id,
        version_id,
        disc_number,
        track_number,
        track["title"]
    ))

    return cursor.lastrowid, True


def check_existing_track_title(
    connection,
    release_track_id,
    stored_title,
    recording_title,
    recording_musicbrainz_id,
    track,
    disc_number
):
    """Verify a re-imported track title against the title stored for this edition.

    release_tracks.track_title holds the spelling printed on this edition, so
    another edition may spell the shared recording differently.  Rows imported
    before the column existed have NULL; they are filled in once the track is
    confirmed to be the same recording.
    """
    incoming_title = track["title"]
    position = f"disc {disc_number}, track {track['number']}"

    if stored_title is not None:
        if stored_title != incoming_title:
            raise ValueError(
                f"Track title changed at {position}: "
                f"database='{stored_title}', JSON='{incoming_title}'. "
                "Review the edition manually."
            )
        return

    incoming_recording_id = track.get("musicbrainz_recording_id")
    same_recording = bool(
        recording_musicbrainz_id
        and incoming_recording_id == recording_musicbrainz_id
    )
    same_title = recording_title.casefold() == incoming_title.casefold()

    if not (same_recording or same_title):
        raise ValueError(
            f"Track title mismatch at {position}: "
            f"database='{recording_title}', JSON='{incoming_title}'."
        )

    connection.execute("""
        UPDATE release_tracks
        SET track_title = ?
        WHERE id = ?
        AND track_title IS NULL
    """, (incoming_title, release_track_id))


def update_release_track_metadata(
    connection,
    release_track_id,
    track
):
    track_id = track.get("musicbrainz_track_id")
    duration_ms = track.get("duration_ms")

    update_musicbrainz_id(
        connection,
        "release_tracks",
        "musicbrainz_track_id",
        release_track_id,
        track_id
    )

    if duration_ms is not None:
        if not isinstance(duration_ms, int) or duration_ms < 0:
            raise ValueError(
                f"Invalid duration for track: {track['title']}"
            )

        connection.execute("""
            UPDATE release_tracks
            SET duration_ms = ?
            WHERE id = ?
            AND duration_ms IS NULL
        """, (duration_ms, release_track_id))

        current_duration = connection.execute("""
            SELECT duration_ms
            FROM release_tracks
            WHERE id = ?
        """, (release_track_id,)).fetchone()[0]

        if current_duration != duration_ms:
            print(
                f"WARNING: Duration differs for "
                f"{track['title']}: "
                f"database={current_duration}, "
                f"JSON={duration_ms}"
            )


def import_credits(connection, track, version_id):
    credits_added = 0
    credits_skipped = 0

    for credit in track.get("credits", []):
        artist_id = get_or_create_artist(
            connection,
            credit["artist"]
        )

        role = credit["role"]

        existing_credit = connection.execute("""
            SELECT id FROM credits
            WHERE artist_id = ?
            AND version_id = ?
            AND role = ?
        """, (
            artist_id,
            version_id,
            role
        )).fetchone()

        if existing_credit:
            credits_skipped += 1
            continue

        connection.execute("""
            INSERT INTO credits (
                artist_id,
                version_id,
                role
            )
            VALUES (?, ?, ?)
        """, (
            artist_id,
            version_id,
            role
        ))

        credits_added += 1

    return credits_added, credits_skipped


def import_release_data(connection, data):
    """Import validated in-memory release data using the caller's transaction."""
    release_data = data["release"]
    tracks = data["tracks"]

    # 1. Find or create main artist.
    main_artist_id = get_or_create_artist(connection, release_data["artist"])

    # 2. Find or create release.
    release_id, release_created = get_or_create_release(
        connection, release_data, main_artist_id
    )
    if release_created:
        print(f"New release created: {release_data['title']}")
    else:
        print(f"Existing release found: {release_data['title']}")

    # 3. Register sources.
    source_ids = []
    for source_data in release_data.get("sources", []):
        source_id = get_or_create_source(connection, source_data)
        source_ids.append(source_id)
        add_source_link(
            connection, source_id, release_id=release_id,
            notes="Release information source."
        )

    # 4. Import tracks.
    tracks_created = 0
    tracks_updated = 0
    credits_added = 0
    credits_skipped = 0

    catalogue = None          # loaded lazily for the same-song rule
    linked_by_rule = False

    # Originals first, so a remix can find its original on the same edition.
    for track in sorted(tracks, key=lambda item: bool(item.get("remix_of_recording_id"))):
        track_number = int(track["number"])
        disc_number = int(track.get("disc_number", 1))
        is_remix = bool(track.get("remix_of_recording_id"))

        existing_track = connection.execute("""
            SELECT
                recordings.id,
                recordings.title,
                recordings.musicbrainz_recording_id,
                versions.id,
                release_tracks.id,
                release_tracks.track_title,
                versions.musicbrainz_recording_id
            FROM release_tracks
            JOIN versions ON release_tracks.version_id = versions.id
            JOIN recordings ON versions.recording_id = recordings.id
            WHERE release_tracks.release_id = ?
            AND release_tracks.disc_number = ?
            AND release_tracks.track_number = ?
        """, (release_id, disc_number, track_number)).fetchone()

        if existing_track:
            (
                recording_id,
                recording_title,
                recording_musicbrainz_id,
                version_id,
                existing_release_track_id,
                stored_track_title,
                version_musicbrainz_id,
            ) = existing_track
            if is_remix or version_musicbrainz_id:
                # A remix version: its own Recording ID lives on the version.
                incoming_id = track.get("musicbrainz_recording_id")
                known_version = find_version_by_musicbrainz_id(connection, incoming_id)
                if (incoming_id and version_musicbrainz_id and incoming_id != version_musicbrainz_id
                        and (known_version is None or known_version[0] != version_id)):
                    raise ValueError(
                        f"Remix version conflict at disc {disc_number}, track "
                        f"{track_number}: database={version_musicbrainz_id}, "
                        f"JSON={incoming_id}."
                    )
            check_existing_track_title(
                connection,
                existing_release_track_id,
                stored_track_title,
                recording_title,
                version_musicbrainz_id or recording_musicbrainz_id,
                track,
                disc_number,
            )
            tracks_updated += 1
        elif is_remix:
            recording_id, version_id = get_or_create_remix_version(
                connection, track, main_artist_id
            )
            tracks_created += 1
        else:
            existing_version = find_version_by_musicbrainz_id(
                connection, track.get("musicbrainz_recording_id")
            )
            if existing_version:
                # This recording is already catalogued as a version (remix).
                version_id, recording_id = existing_version
            else:
                recording_id = find_recording_by_musicbrainz_id(
                    connection, track.get("musicbrainz_recording_id")
                )
                if recording_id is None:
                    # Same song under another MusicBrainz ID?  (title + duration)
                    if catalogue is None:
                        catalogue = load_catalogue_from_connection(connection)
                    outcome, matched_id = match_recording(
                        track["title"], track.get("duration_ms"),
                        catalogue["by_title"].get(normalized_title(track["title"]), {}),
                        allow_unknown_duration=release_data.get("release_type") in (
                            "single", "group_single"),
                    )
                    if outcome == REVIEW and release_data.get("discography") == "unofficial":
                        # Bootleg tracks: an ambiguous match becomes its own
                        # recording; merge_recordings.py lists possible pairs.
                        outcome = NEW
                    if outcome == REVIEW:
                        raise ValueError(
                            f"Possible duplicate of catalogued recording {matched_id}: "
                            f"'{track['title']}' (same title, duration differs 5-15 s "
                            "or is unknown); review it manually."
                        )
                    if outcome == NEW:
                        # A song known so far only from a snippet / demo /
                        # source data gets this track as its main version.
                        matched_id = song_without_main_version(track["title"], catalogue)
                        if matched_id is not None:
                            outcome = SAME
                            # It has a main version now (cache is per release).
                            catalogue["without_main"][normalized_title(track["title"])].discard(matched_id)
                    if outcome == SAME:
                        recording_id = matched_id
                        linked_by_rule = True
                if recording_id is None:
                    recording_id = get_or_create_recording(
                        connection, track, main_artist_id
                    )
                version_id = get_or_create_version(connection, recording_id)
            tracks_created += 1

        is_version_level = is_remix or bool(find_version_by_musicbrainz_id(
            connection, track.get("musicbrainz_recording_id")
        ))
        if not is_version_level:
            attach_recording_musicbrainz_id(
                connection, recording_id, track.get("musicbrainz_recording_id"),
                allow_alternate=linked_by_rule, title=track["title"],
            )
        linked_by_rule = False
        release_track_id, _ = get_or_create_release_track(
            connection, release_id, version_id, track, disc_number
        )
        update_release_track_metadata(connection, release_track_id, track)

        for source_id in source_ids:
            add_source_link(
                connection, source_id, version_id=version_id,
                notes="Track information source."
            )

        added, skipped = import_credits(connection, track, version_id)
        credits_added += added
        credits_skipped += skipped

    result = {
        "release_id": release_id,
        "release_created": release_created,
        "tracks_created": tracks_created,
        "tracks_updated": tracks_updated,
        "credits_added": credits_added,
        "credits_skipped": credits_skipped,
    }
    print("\nIMPORT COMPLETED")
    print(f"Release ID: {release_id}")
    print(f"Tracks created: {tracks_created}")
    print(f"Existing tracks updated: {tracks_updated}")
    print(f"Credits added: {credits_added}")
    print(f"Credits already existed: {credits_skipped}")
    return result
