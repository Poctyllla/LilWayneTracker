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


def track(number, title, track_id, recording_id, disc_number=1):
    return {
        "number": number,
        "disc_number": disc_number,
        "title": title,
        "musicbrainz_track_id": track_id,
        "musicbrainz_recording_id": recording_id,
        "credits": [],
    }


def edition(release_id, tracks):
    return {
        "edition_name": release_id,
        "release": {
            "title": "Example Album",
            "artist": "Lil Wayne",
            "release_type": "studio_album",
            "musicbrainz_release_id": release_id,
            "musicbrainz_release_group_id": "group-1",
        },
        "tracks": tracks,
    }


def example_package():
    """Canonical + an edition that spells the shared recording differently
    and adds a bonus disc (modelled on Tha Carter III / Target edition)."""
    return {
        "schema_version": 1,
        "release_group": {
            "title": "Example Album",
            "artist": "Lil Wayne",
            "musicbrainz_release_group_id": "group-1",
            "catalog_category": "studio_album",
        },
        "canonical_edition": edition("release-1", [
            track(1, "Playing With Fire", "track-1", "recording-1"),
        ]),
        "additional_editions": [edition("release-2", [
            track(1, "Playing with Fire", "track-2", "recording-1"),
            track(1, "Bonus Track", "track-3", "recording-2", disc_number=2),
        ])],
    }


class TemporaryDatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._directory.name)
        self._original_paths = (
            database.DATABASE_PATH, import_release_group.DATABASE_PATH
        )
        database.DATABASE_PATH = self.directory / "test.db"
        import_release_group.DATABASE_PATH = database.DATABASE_PATH
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()

    def tearDown(self):
        database.DATABASE_PATH, import_release_group.DATABASE_PATH = self._original_paths
        self._directory.cleanup()

    def import_package(self, package):
        path = self.directory / "package.json"
        path.write_text(json.dumps(package), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            return import_release_group.import_release_group_package(path)

    def query(self, sql, params=()):
        connection = sqlite3.connect(database.DATABASE_PATH)
        try:
            return connection.execute(sql, params).fetchall()
        finally:
            connection.close()

    def counts(self):
        return {
            table: self.query(f"SELECT COUNT(*) FROM {table}")[0][0]
            for table in ("releases", "recordings", "versions", "release_tracks")
        }

    def edition_titles(self):
        return self.query("""
            SELECT releases.musicbrainz_release_id, rt.disc_number,
                   rt.track_number, rt.track_title, recordings.title
            FROM release_tracks AS rt
            JOIN releases ON releases.id = rt.release_id
            JOIN versions ON versions.id = rt.version_id
            JOIN recordings ON recordings.id = versions.recording_id
            ORDER BY 1, 2, 3
        """)


class EditionTitleTests(TemporaryDatabaseTestCase):
    def test_edition_spelling_is_stored_without_a_new_recording(self):
        self.import_package(example_package())

        self.assertEqual(
            self.counts(),
            {"releases": 2, "recordings": 2, "versions": 2, "release_tracks": 3},
        )
        self.assertEqual(self.edition_titles(), [
            ("release-1", 1, 1, "Playing With Fire", "Playing With Fire"),
            ("release-2", 1, 1, "Playing with Fire", "Playing With Fire"),
            ("release-2", 2, 1, "Bonus Track", "Bonus Track"),
        ])

    def test_reimport_of_the_same_package_succeeds_without_duplicates(self):
        self.import_package(example_package())
        before = (self.counts(), self.edition_titles())

        self.import_package(example_package())

        self.assertEqual((self.counts(), self.edition_titles()), before)

    def test_changed_title_on_reimport_is_rejected_and_rolled_back(self):
        self.import_package(example_package())
        before = (self.counts(), self.edition_titles())

        changed = example_package()
        # A valid new canonical track proves the whole package is rolled back.
        changed["canonical_edition"]["tracks"].append(
            track(2, "New Track", "track-9", "recording-9")
        )
        changed["additional_editions"][0]["tracks"][0]["title"] = "Fire"

        with self.assertRaisesRegex(ValueError, "Track title changed"):
            self.import_package(changed)

        self.assertEqual((self.counts(), self.edition_titles()), before)

    def test_error_in_second_edition_rolls_back_the_whole_package(self):
        broken = example_package()
        # Reusing the canonical Track ID is detected as a MusicBrainz ID conflict.
        broken["additional_editions"][0]["tracks"][1]["musicbrainz_track_id"] = "track-1"

        with self.assertRaisesRegex(ValueError, "MusicBrainz ID conflict"):
            self.import_package(broken)

        self.assertEqual(
            self.counts(),
            {"releases": 0, "recordings": 0, "versions": 0, "release_tracks": 0},
        )


class LegacyRowTests(TemporaryDatabaseTestCase):
    def test_rows_without_track_title_are_filled_on_reimport(self):
        self.import_package(example_package())
        connection = sqlite3.connect(database.DATABASE_PATH)
        with connection:
            connection.execute("UPDATE release_tracks SET track_title = NULL")
        connection.close()

        # release-2 track 1 differs from the main title only by case and has
        # the same Recording ID, so it is accepted and its spelling is stored.
        self.import_package(example_package())

        self.assertEqual(
            [row[3] for row in self.edition_titles()],
            ["Playing With Fire", "Playing with Fire", "Bonus Track"],
        )

    def test_legacy_row_with_a_different_recording_is_rejected(self):
        package = example_package()
        for item in package["canonical_edition"]["tracks"]:
            item.pop("musicbrainz_recording_id")
            item.pop("musicbrainz_track_id")
        package["additional_editions"] = []
        self.import_package(package)
        connection = sqlite3.connect(database.DATABASE_PATH)
        with connection:
            connection.execute("UPDATE release_tracks SET track_title = NULL")
        connection.close()

        package["canonical_edition"]["tracks"][0]["title"] = "Another Song"
        with self.assertRaisesRegex(ValueError, "Track title mismatch"):
            self.import_package(package)


class MigrationTests(TemporaryDatabaseTestCase):
    def test_migration_adds_track_title_and_keeps_existing_rows(self):
        self.import_package(example_package())
        connection = sqlite3.connect(database.DATABASE_PATH)
        with connection:
            connection.execute("ALTER TABLE release_tracks DROP COLUMN track_title")
        connection.close()
        before = self.query(
            "SELECT id, release_id, version_id, disc_number, track_number, "
            "musicbrainz_track_id FROM release_tracks ORDER BY id"
        )

        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
            database.initialize_database()  # Must be safe to run repeatedly.

        columns = [row[1] for row in self.query("PRAGMA table_info(release_tracks)")]
        self.assertIn("track_title", columns)
        self.assertEqual(
            self.query(
                "SELECT id, release_id, version_id, disc_number, track_number, "
                "musicbrainz_track_id FROM release_tracks ORDER BY id"
            ),
            before,
        )
        self.assertEqual(
            self.query("SELECT COUNT(*) FROM release_tracks WHERE track_title IS NULL")[0][0],
            3,
        )


if __name__ == "__main__":
    unittest.main()
