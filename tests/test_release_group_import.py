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


def edition(release_id, track_id, recording_id, title):
    return {
        "edition_name": release_id,
        "release": {
            "title": "Example Album",
            "artist": "Lil Wayne",
            "release_type": "studio_album",
            "musicbrainz_release_id": release_id,
            "musicbrainz_release_group_id": "group-1",
        },
        "tracks": [{
            "number": 1,
            "title": title,
            "musicbrainz_track_id": track_id,
            "musicbrainz_recording_id": recording_id,
            "credits": [],
        }],
    }


class ReleaseGroupImportTests(unittest.TestCase):
    def test_imports_editions_together_and_reuses_recording(self):
        package = {
            "schema_version": 1,
            "release_group": {
                "title": "Example Album",
                "artist": "Lil Wayne",
                "musicbrainz_release_group_id": "group-1",
            },
            "canonical_edition": edition(
                "release-1", "track-1", "recording-1", "Original Track"
            ),
            "additional_editions": [edition(
                "release-2", "track-2", "recording-1", "Original Track"
            )],
        }

        original_database_path = database.DATABASE_PATH
        original_import_path = import_release_group.DATABASE_PATH
        try:
            with tempfile.TemporaryDirectory() as directory:
                directory = Path(directory)
                database.DATABASE_PATH = directory / "test.db"
                database.initialize_database()
                import_release_group.DATABASE_PATH = database.DATABASE_PATH
                package_path = directory / "package.json"
                package_path.write_text(json.dumps(package), encoding="utf-8")

                import_release_group.import_release_group_package(package_path)

                connection = sqlite3.connect(database.DATABASE_PATH)
                try:
                    self.assertEqual(
                        connection.execute("SELECT COUNT(*) FROM releases").fetchone()[0],
                        2,
                    )
                    self.assertEqual(
                        connection.execute("SELECT COUNT(*) FROM recordings").fetchone()[0],
                        1,
                    )
                    self.assertEqual(
                        connection.execute("SELECT COUNT(*) FROM release_tracks").fetchone()[0],
                        2,
                    )
                finally:
                    connection.close()
        finally:
            database.DATABASE_PATH = original_database_path
            import_release_group.DATABASE_PATH = original_import_path


if __name__ == "__main__":
    unittest.main()
