import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import database
import import_release_group
from merge_recordings import find_pairs, merge


def track(number, title, recording_id, duration, track_id, **extra):
    return {"number": number, "disc_number": 1, "title": title, "musicbrainz_track_id": track_id,
            "musicbrainz_recording_id": recording_id, "duration_ms": duration, "credits": [], **extra}


def package(group_id, category, tracks):
    return {"schema_version": 2,
            "release_group": {"title": group_id, "artist": "Lil Wayne",
                              "musicbrainz_release_group_id": group_id, "catalog_category": category},
            "canonical_edition": {"edition_name": group_id, "release": {
                "title": group_id, "artist": "Lil Wayne", "release_type": category,
                "musicbrainz_release_id": f"rel-{group_id}", "musicbrainz_release_group_id": group_id},
                "tracks": tracks},
            "additional_editions": []}


class MergeTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._directory.name)
        self._paths = (database.DATABASE_PATH, import_release_group.DATABASE_PATH)
        database.DATABASE_PATH = self.directory / "test.db"
        import_release_group.DATABASE_PATH = database.DATABASE_PATH
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
        # Simulate imports from before the same-song rule: the single's
        # Lollipop (+3.04 s, other ID) became a separate recording.
        self.import_package(package("album", "studio_album", [
            track(1, "Lollipop", "rec-lolli", 299000, "a1"),
            track(2, "Intro", "rec-intro", 60000, "a2"),
            track(3, "Hot Revolver", "rec-hot", 201000, "a3"),
        ]))
        connection = self.connect()
        with connection:
            connection.execute("INSERT INTO recordings (title, artist_id, musicbrainz_recording_id) "
                               "VALUES ('Lollipop', 1, 'rec-lolli-single')")
            recording = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            connection.execute("INSERT INTO versions (recording_id, version_name, status, version_type) "
                               "VALUES (?, 'Album Version', 'official_release', 'album')", (recording,))
            version = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            connection.execute("INSERT INTO releases (title, release_type, artist_id, discography) "
                               "VALUES ('Lollipop', 'single', 1, 'official')")
            release = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            connection.execute("INSERT INTO release_tracks (release_id, version_id, track_number, duration_ms) "
                               "VALUES (?, ?, 1, 302040)", (release, version))
            connection.execute("INSERT INTO credits (artist_id, version_id, role) VALUES (1, ?, 'producer')",
                               (version,))
            connection.execute("INSERT INTO files (version_id, file_path) VALUES (?, 'C:/music/lollipop.flac')",
                               (version,))
            # A remix version of the single recording, plus a 9 s longer Hot Revolver.
            connection.execute("INSERT INTO versions (recording_id, version_name, status, version_type, "
                               "musicbrainz_recording_id) VALUES (?, 'remix', 'official_release', 'remix', 'rec-lr')",
                               (recording,))
            connection.execute("INSERT INTO recordings (title, artist_id, musicbrainz_recording_id) "
                               "VALUES ('Hot Revolver', 1, 'rec-hot-single')")
            hot = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            connection.execute("INSERT INTO versions (recording_id, version_name, status, version_type) "
                               "VALUES (?, 'Album Version', 'official_release', 'album')", (hot,))
            hot_version = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            # 30 s shorter: a different song with the same title.
            connection.execute("INSERT INTO release_tracks (release_id, version_id, track_number, duration_ms) "
                               "VALUES (?, ?, 2, 171000)", (release, hot_version))
        connection.close()

    def tearDown(self):
        database.DATABASE_PATH, import_release_group.DATABASE_PATH = self._paths
        self._directory.cleanup()

    def connect(self):
        connection = sqlite3.connect(database.DATABASE_PATH)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def import_package(self, data):
        path = self.directory / "package.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            import_release_group.import_release_group_package(path)

    def test_pairs(self):
        connection = self.connect()
        try:
            same, review = find_pairs(connection)
        finally:
            connection.close()
        self.assertEqual([title for _, _, title in same], ["Lollipop"])
        self.assertEqual(review, [])  # Hot Revolver 30 s apart: different songs

    def test_generic_titles_and_unknown_durations_are_not_merged(self):
        connection = self.connect()
        try:
            with connection:
                # On another release: two positions of one release are never merged.
                connection.execute("INSERT INTO releases (id, title, release_type, artist_id, discography) "
                                   "VALUES (99, 'Some Bootleg', 'bootleg', 1, 'unofficial')")
                for title, duration in (("Intro", 66000), ("Intro", 60500), ("Intro", None)):
                    connection.execute("INSERT INTO recordings (title, artist_id) VALUES (?, 1)", (title,))
                    rec = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
                    connection.execute("INSERT INTO versions (recording_id, version_name, version_type) "
                                       "VALUES (?, 'Album Version', 'album')", (rec,))
                    ver = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
                    connection.execute("INSERT INTO release_tracks (release_id, version_id, track_number, duration_ms) "
                                       "VALUES (99, ?, ?, ?)", (ver, 10 + rec, duration))
            same, review = find_pairs(connection)
        finally:
            connection.close()
        # Only the album Intro (60 s) and the 60.5 s Intro are within 3 s.
        self.assertEqual([title for _, _, title in same], ["Lollipop", "Intro"])
        self.assertEqual(review, [])

    def test_merge_keeps_everything_on_the_older_recording(self):
        connection = self.connect()
        try:
            (keep_id, drop_id, _), = find_pairs(connection)[0]
            with connection:
                merge(connection, keep_id, drop_id)
            q = lambda sql: connection.execute(sql).fetchall()
            self.assertEqual(q("SELECT COUNT(*) FROM recordings WHERE title = 'Lollipop'"), [(1,)])
            album_version = q(f"SELECT id FROM versions WHERE recording_id = {keep_id} "
                              "AND version_type = 'album'")[0][0]
            self.assertEqual(q("SELECT COUNT(*) FROM release_tracks WHERE version_id = "
                               f"{album_version}"), [(2,)])
            self.assertEqual(q(f"SELECT version_id FROM files"), [(album_version,)])
            self.assertEqual(q(f"SELECT version_id, role FROM credits"), [(album_version, "producer")])
            self.assertEqual(q(f"SELECT recording_id FROM versions WHERE version_type = 'remix'"),
                             [(keep_id,)])
            self.assertEqual(q("SELECT recording_id, musicbrainz_recording_id, source "
                               "FROM recording_musicbrainz_ids"),
                             [(keep_id, "rec-lolli-single", "merge")])
            self.assertEqual(q("PRAGMA foreign_key_check"), [])
            self.assertEqual(find_pairs(connection)[0], [])
        finally:
            connection.close()

        # Importing the single later finds the song through the alternate ID.
        self.import_package(package("lolli-single", "single", [
            track(1, "Lollipop", "rec-lolli-single", 302040, "s1")]))
        connection = self.connect()
        try:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM recordings WHERE title = 'Lollipop'").fetchone(), (1,))
        finally:
            connection.close()

    def test_merge_moves_streaming_links(self):
        """version_links follow the merged version; the kept version's own link of a service wins."""
        connection = self.connect()
        try:
            (keep_id, drop_id, _), = find_pairs(connection)[0]
            version = lambda rec: connection.execute(
                "SELECT id FROM versions WHERE recording_id = ? AND version_type = 'album'", (rec,)).fetchone()[0]
            keep_version, drop_version = version(keep_id), version(drop_id)
            with connection:
                connection.executemany("INSERT INTO version_links (version_id, service, url) VALUES (?, ?, ?)", [
                    (keep_version, "spotify", "https://open.spotify.com/track/kept"),
                    (drop_version, "spotify", "https://open.spotify.com/track/dropped"),
                    (drop_version, "apple_music", "https://music.apple.com/us/album/x/1?i=2")])
                merge(connection, keep_id, drop_id)
            self.assertEqual(connection.execute("SELECT version_id, service, url FROM version_links ORDER BY service").fetchall(), [
                (keep_version, "apple_music", "https://music.apple.com/us/album/x/1?i=2"),
                (keep_version, "spotify", "https://open.spotify.com/track/kept")])
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
