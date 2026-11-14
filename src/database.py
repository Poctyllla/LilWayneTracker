
import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = PROJECT_ROOT / "data" / "lilwayne.db"

DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)


def get_connection():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database():
    with get_connection() as connection:

        # 1. Artists
        connection.execute("""
            CREATE TABLE IF NOT EXISTS artists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            )
        """)

        # 2. Releases
        connection.execute("""
            CREATE TABLE IF NOT EXISTS releases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                release_type TEXT,
                release_date TEXT,
                artist_id INTEGER,
                FOREIGN KEY (artist_id) REFERENCES artists(id)
            )
        """)

        # 3. Recordings
        connection.execute("""
            CREATE TABLE IF NOT EXISTS recordings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                artist_id INTEGER,
                FOREIGN KEY (artist_id) REFERENCES artists(id)
            )
        """)

        # 4. Versions
        connection.execute("""
            CREATE TABLE IF NOT EXISTS versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recording_id INTEGER NOT NULL,
                version_name TEXT,
                status TEXT,
                FOREIGN KEY (recording_id) REFERENCES recordings(id)
            )
        """)

        # 5. Audio files
        connection.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version_id INTEGER,
                file_path TEXT NOT NULL,
                format TEXT,
                duration REAL,
                FOREIGN KEY (version_id) REFERENCES versions(id)
            )
        """)

        # 6. Sources
        connection.execute("""
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT
            )
        """)

        # 7. Release tracklists
        connection.execute("""
            CREATE TABLE IF NOT EXISTS release_tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                release_id INTEGER NOT NULL,
                version_id INTEGER NOT NULL,
                disc_number INTEGER DEFAULT 1,
                track_number INTEGER NOT NULL,
                FOREIGN KEY (release_id) REFERENCES releases(id),
                FOREIGN KEY (version_id) REFERENCES versions(id),
                UNIQUE (release_id, disc_number, track_number)
            )
        """)

        # 8. Credits
        connection.execute("""
            CREATE TABLE IF NOT EXISTS credits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artist_id INTEGER NOT NULL,
                recording_id INTEGER,
                version_id INTEGER,
                role TEXT NOT NULL,
                credited_as TEXT,
                FOREIGN KEY (artist_id) REFERENCES artists(id),
                FOREIGN KEY (recording_id) REFERENCES recordings(id),
                FOREIGN KEY (version_id) REFERENCES versions(id),
                CHECK (
                    (recording_id IS NOT NULL AND version_id IS NULL)
                    OR
                    (recording_id IS NULL AND version_id IS NOT NULL)
                )
            )
        """)

        # 9. Recording sources
        connection.execute("""
            CREATE TABLE IF NOT EXISTS recording_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                recording_id INTEGER,
                version_id INTEGER,
                release_id INTEGER,
                notes TEXT,
                FOREIGN KEY (source_id) REFERENCES sources(id),
                FOREIGN KEY (recording_id) REFERENCES recordings(id),
                FOREIGN KEY (version_id) REFERENCES versions(id),
                FOREIGN KEY (release_id) REFERENCES releases(id),
                CHECK (
                    (recording_id IS NOT NULL) +
                    (version_id IS NOT NULL) +
                    (release_id IS NOT NULL) = 1
                )
            )
        """)

        # 9.3. Eras of Lil Wayne's career (for the tracker view), e.g.
        # "Tha Carter III era" 2007-2008.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS eras (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                start_date TEXT,
                end_date TEXT,
                description TEXT
            )
        """)

        # 9.4. Alternate MusicBrainz Recording IDs of one recording (the same
        # song under other IDs, e.g. single vs album).  The primary ID stays in
        # recordings.musicbrainz_recording_id.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS recording_musicbrainz_ids (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recording_id INTEGER NOT NULL,
                musicbrainz_recording_id TEXT NOT NULL UNIQUE,
                source TEXT,
                note TEXT,
                FOREIGN KEY (recording_id) REFERENCES recordings(id)
            )
        """)

        # 9.5. Relations between release groups, e.g. a single taken from an
        # album ('single_from').  Keyed by MusicBrainz Release Group IDs, so
        # the related album does not have to be catalogued yet.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS release_group_relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                musicbrainz_release_group_id TEXT NOT NULL,
                related_musicbrainz_release_group_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                related_title TEXT,
                source TEXT,
                UNIQUE (
                    musicbrainz_release_group_id,
                    related_musicbrainz_release_group_id,
                    relation_type
                )
            )
        """)

        # 9.6. Other spellings of a song from sources outside releases
        # (LilWayneHQ, Genius), e.g. "Black Republicans" for "Black
        # Republican".  Spellings printed on releases stay in
        # release_tracks.track_title.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS recording_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recording_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                source TEXT,
                note TEXT,
                UNIQUE (recording_id, title),
                FOREIGN KEY (recording_id) REFERENCES recordings(id)
            )
        """)

        # 9.7. Links of a release to streaming / shop pages (Spotify, Apple
        # Music ...), from the MusicBrainz release's URL relations.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS release_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                release_id INTEGER NOT NULL,
                service TEXT NOT NULL,
                url TEXT NOT NULL,
                source TEXT,
                UNIQUE (release_id, url),
                FOREIGN KEY (release_id) REFERENCES releases(id)
            )
        """)

        # 9.8. Links of one version to a streaming track page (Spotify track ...).
        connection.execute("""
            CREATE TABLE IF NOT EXISTS version_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version_id INTEGER NOT NULL,
                service TEXT NOT NULL,
                url TEXT NOT NULL,
                source TEXT,
                UNIQUE (version_id, service),
                FOREIGN KEY (version_id) REFERENCES versions(id)
            )
        """)

        # 10. Database migrations
        migrations = {
            "releases": {
                # Cover image in web/covers/ (release_media.py).
                "cover_file": "TEXT",
                "label": "TEXT",
                "description": "TEXT",
                "musicbrainz_release_id": "TEXT",
                "musicbrainz_release_group_id": "TEXT",
                # 'official' or 'unofficial' discography, independent of
                # release_type (e.g. official vs unofficial mixtapes).
                "discography": "TEXT",
            },
            "recordings": {
                "recording_date": "TEXT",
                "description": "TEXT",
                "musicbrainz_recording_id": "TEXT",
                # Tracker fields (songs outside releases).
                "era_id": "INTEGER REFERENCES eras(id)",
                "leak_date": "TEXT",
                # Manual status: 'leaked', 'snippet', 'unreleased', 'released';
                # NULL = computed in the recording_availability view.
                "availability_override": "TEXT",
                "genius_song_id": "INTEGER",
                # How era_id was set: 'album' (on that studio album), 'date'
                # (earliest known date), 'manual' (never overwritten).
                "era_source": "TEXT",
                # NULL = Lil Wayne's own song; 'guest' = another artist's song
                # with Lil Wayne as guest or co-artist (song only, no release;
                # user decision Nov 13, 2026).
                "appearance": "TEXT",
            },
            "eras": {
                # The studio album the era is named after (user decision Nov 13, 2026).
                "musicbrainz_release_group_id": "TEXT",
                # Tracker page (era_info.py; eras.py never overwrites these):
                # short text from the album's English Wikipedia article, its
                # URL, and the cover file in web/covers/ (Cover Art Archive).
                "summary": "TEXT",
                "summary_url": "TEXT",
                "cover_file": "TEXT",
            },
            "versions": {
                "version_type": "TEXT",
                "availability_status": "TEXT",
                "first_known_date": "TEXT",
                "description": "TEXT",
                # MusicBrainz Recording ID of this specific version, used for
                # remixes (the recording keeps the original's ID).
                "musicbrainz_recording_id": "TEXT",
                # Genius song of this version (a demo / snippet has its own page).
                "genius_song_id": "INTEGER",
            },
            "files": {
                "file_size": "INTEGER",
                "bitrate": "INTEGER",
                "sample_rate": "INTEGER",
                "sha256": "TEXT",
            },
            "recording_musicbrainz_ids": {
                # Set when the alternate ID belongs to one version (e.g. the
                # same remix under two MusicBrainz IDs).
                "version_id": "INTEGER REFERENCES versions(id)",
            },
            "credits": {
                # Where the credit comes from ('Genius', ...); NULL = package /
                # local library tags (before Nov 13, 2026).
                "source": "TEXT",
            },
            "release_tracks": {
                "musicbrainz_track_id": "TEXT",
                "duration_ms": "INTEGER",
                # Title as printed on this edition.  recordings.title stays the
                # main title; NULL means the row predates this column.
                "track_title": "TEXT",
            },
        }

        for table, columns in migrations.items():

            existing_columns = {
                row[1]
                for row in connection.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()
            }

            for column, data_type in columns.items():

                if column not in existing_columns:
                    connection.execute(
                        f"ALTER TABLE {table} "
                        f"ADD COLUMN {column} {data_type}"
                    )

        # 10.5. Fill the discography of existing releases where it is
        # unambiguous; other release types need a per-release decision.
        connection.execute("""
            UPDATE releases
            SET discography = 'official'
            WHERE discography IS NULL
            AND release_type IN (
                'studio_album', 'collaboration_album', 'ep', 'single'
            )
        """)
        connection.execute("""
            UPDATE releases
            SET discography = 'unofficial'
            WHERE discography IS NULL AND release_type = 'bootleg'
        """)

        # 10.6. Song status for the tracker, computed (never stale):
        # official release -> released, only unofficial releases -> leaked,
        # no release (known from sources only) -> unreleased; a manual
        # availability_override wins.
        connection.execute("DROP VIEW IF EXISTS recording_availability")
        connection.execute("""
            CREATE VIEW recording_availability AS
            SELECT
                rec.id AS recording_id,
                COALESCE(
                    rec.availability_override,
                    CASE
                        WHEN EXISTS (
                            SELECT 1 FROM versions v
                            JOIN release_tracks rt ON rt.version_id = v.id
                            JOIN releases r ON r.id = rt.release_id
                            WHERE v.recording_id = rec.id AND r.discography = 'official'
                        ) THEN 'released'
                        WHEN EXISTS (
                            SELECT 1 FROM versions v
                            JOIN release_tracks rt ON rt.version_id = v.id
                            WHERE v.recording_id = rec.id
                        ) THEN 'leaked'
                        ELSE 'unreleased'
                    END
                ) AS availability
            FROM recordings rec
        """)
        # The same per version (a released song can have an unreleased demo).
        connection.execute("DROP VIEW IF EXISTS version_availability")
        connection.execute("""
            CREATE VIEW version_availability AS
            SELECT
                v.id AS version_id,
                v.recording_id,
                COALESCE(
                    v.availability_status,
                    CASE
                        WHEN EXISTS (
                            SELECT 1 FROM release_tracks rt
                            JOIN releases r ON r.id = rt.release_id
                            WHERE rt.version_id = v.id AND r.discography = 'official'
                        ) THEN 'released'
                        WHEN EXISTS (
                            SELECT 1 FROM release_tracks rt WHERE rt.version_id = v.id
                        ) THEN 'leaked'
                        ELSE 'unreleased'
                    END
                ) AS availability
            FROM versions v
        """)

        # 11. MusicBrainz ID indexes
        connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_releases_musicbrainz_id
            ON releases(musicbrainz_release_id)
            WHERE musicbrainz_release_id IS NOT NULL
        """)

        connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_recordings_musicbrainz_id
            ON recordings(musicbrainz_recording_id)
            WHERE musicbrainz_recording_id IS NOT NULL
        """)

        connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_versions_musicbrainz_recording_id
            ON versions(musicbrainz_recording_id)
            WHERE musicbrainz_recording_id IS NOT NULL
        """)

        connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_release_tracks_musicbrainz_id
            ON release_tracks(musicbrainz_track_id)
            WHERE musicbrainz_track_id IS NOT NULL
        """)

        # 12. Add Lil Wayne if he doesn't exist
        connection.execute("""
            INSERT OR IGNORE INTO artists (name)
            VALUES ('Lil Wayne')
        """)

        # 13. Indexes for faster searches
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_recordings_title
            ON recordings(title)
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_versions_recording
            ON versions(recording_id)
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_release_tracks_release
            ON release_tracks(release_id)
        """)

    # sqlite3 connection context managers commit or roll back, but they do not
    # close the connection.  Explicitly release the handle for Windows.
    connection.close()


if __name__ == "__main__":
    initialize_database()

    print("Database initialized successfully!")
    print(f"Database location: {DATABASE_PATH}")
