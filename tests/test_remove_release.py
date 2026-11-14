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
from remove_release import plan_release_removal, remove_release


def track(number, title, track_id, recording_id, disc_number=1):
    return {"number": number, "disc_number": disc_number, "title": title,
            "musicbrainz_track_id": track_id, "musicbrainz_recording_id": recording_id,
            "duration_ms": 200000, "credits": []}


def edition(release_id, tracks):
    return {
        "edition_name": release_id,
        "release": {
            "title": "Example Album", "artist": "Lil Wayne", "release_type": "studio_album",
            "musicbrainz_release_id": release_id, "musicbrainz_release_group_id": "group-1",
            "sources": [{"name": "MusicBrainz",
                         "url": f"https://musicbrainz.org/release/{release_id}"}],
        },
        "tracks": tracks,
    }


def package():
    """Standard edition + deluxe edition with the same songs and a bonus disc."""
    return {
        "schema_version": 1,
        "release_group": {"title": "Example Album", "artist": "Lil Wayne",
                          "musicbrainz_release_group_id": "group-1",
                          "catalog_category": "studio_album"},
        "canonical_edition": edition("standard", [
            track(1, "Song A", "s-1", "rec-a"),
            track(2, "Song B", "s-2", "rec-b"),
        ]),
        "additional_editions": [edition("deluxe", [
            track(1, "Song A", "d-1", "rec-a"),
            track(2, "Song B", "d-2", "rec-b"),
            track(1, "Bonus Song", "d-3", "rec-sc", disc_number=2),
        ])],
    }


class RemoveReleaseTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        directory = Path(self._directory.name)
        self._paths = (database.DATABASE_PATH, import_release_group.DATABASE_PATH)
        database.DATABASE_PATH = directory / "test.db"
        import_release_group.DATABASE_PATH = database.DATABASE_PATH
        path = directory / "package.json"
        path.write_text(json.dumps(package()), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
            import_release_group.import_release_group_package(path)
        self.connection = sqlite3.connect(database.DATABASE_PATH)
        self.connection.execute("PRAGMA foreign_keys = ON")

    def tearDown(self):
        self.connection.close()
        database.DATABASE_PATH, import_release_group.DATABASE_PATH = self._paths
        self._directory.cleanup()

    def scalar(self, sql, params=()):
        return self.connection.execute(sql, params).fetchone()[0]

    def snapshot_standard(self):
        return self.connection.execute("""
            SELECT rt.disc_number, rt.track_number, rt.track_title, rt.musicbrainz_track_id,
                   v.id, rec.id, rec.musicbrainz_recording_id
            FROM release_tracks rt JOIN releases r ON r.id = rt.release_id
            JOIN versions v ON v.id = rt.version_id JOIN recordings rec ON rec.id = v.recording_id
            WHERE r.musicbrainz_release_id = 'standard' ORDER BY 1, 2
        """).fetchall()

    def test_plan_only_lists_exclusive_data(self):
        plan = plan_release_removal(self.connection, "deluxe")

        self.assertEqual(plan["release_tracks"], 3)
        self.assertEqual(len(plan["versions"]), 1)
        self.assertEqual(len(plan["recordings"]), 1)
        self.assertEqual(plan["shared_versions_kept"], 2)
        self.assertEqual(len(plan["sources"]), 1)

    def test_removal_keeps_shared_data_and_other_edition(self):
        standard_before = self.snapshot_standard()

        with self.connection:
            remove_release(self.connection, "deluxe")

        self.assertEqual(self.scalar("SELECT COUNT(*) FROM releases"), 1)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM recordings"), 2)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM versions"), 2)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM release_tracks"), 2)
        self.assertEqual(self.scalar(
            "SELECT COUNT(*) FROM recordings WHERE musicbrainz_recording_id = 'rec-sc'"), 0)
        self.assertEqual(self.snapshot_standard(), standard_before)
        # Only the standard edition's MusicBrainz source and its links remain.
        self.assertEqual(self.connection.execute("SELECT url FROM sources").fetchall(),
                         [("https://musicbrainz.org/release/standard",)])
        self.assertEqual(self.scalar(
            "SELECT COUNT(*) FROM recording_sources WHERE source_id NOT IN (SELECT id FROM sources)"), 0)
        self.assertEqual(self.connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_linked_local_file_blocks_removal(self):
        version_id = self.scalar(
            "SELECT v.id FROM versions v JOIN recordings r ON r.id = v.recording_id "
            "WHERE r.musicbrainz_recording_id = 'rec-sc'")
        with self.connection:
            self.connection.execute(
                "INSERT INTO files (version_id, file_path) VALUES (?, 'C:/music/sc.flac')",
                (version_id,))

        with self.assertRaisesRegex(ValueError, "local file"):
            plan_release_removal(self.connection, "deluxe")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM releases"), 2)

    def test_unknown_release_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not found"):
            plan_release_removal(self.connection, "missing")


if __name__ == "__main__":
    unittest.main()
