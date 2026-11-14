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
from hq_packages import artist_credit, build_single_package
from release_group_package import get_package_validation_issues
from release_relations import singles_of_album


ALBUM = {
    "schema_version": 2,
    "release_group": {"title": "Tha Carter", "artist": "Lil Wayne",
                      "musicbrainz_release_group_id": "carter-g", "catalog_category": "studio_album"},
    "canonical_edition": {
        "edition_name": "Canonical",
        "release": {"title": "Tha Carter", "artist": "Lil Wayne", "release_type": "studio_album",
                    "musicbrainz_release_id": "carter-1", "musicbrainz_release_group_id": "carter-g"},
        "tracks": [{"number": 1, "title": "Go DJ", "musicbrainz_track_id": "t1",
                    "musicbrainz_recording_id": "rec-godj", "duration_ms": 241000, "credits": []},
                   {"number": 2, "title": "Bring It Back", "musicbrainz_track_id": "t2",
                    "musicbrainz_recording_id": "rec-bib", "duration_ms": 300000, "credits": []}],
    },
    "additional_editions": [],
}


class BuildTests(unittest.TestCase):
    def test_package_is_valid_without_musicbrainz_ids(self):
        package = build_single_package({"title": "Go DJ", "with": None, "featuring": None})
        errors, _ = get_package_validation_issues(package)
        self.assertEqual(errors, [])
        self.assertEqual(package["release_group"]["source"], "LilWayneHQ")
        self.assertIsNone(package["canonical_edition"]["release"]["musicbrainz_release_id"])

    def test_missing_ids_are_still_an_error_without_a_source(self):
        package = build_single_package({"title": "Go DJ", "with": None, "featuring": None})
        del package["release_group"]["source"]
        self.assertTrue(get_package_validation_issues(package)[0])
        package["release_group"]["source"] = "Somewhere"
        self.assertTrue(any("Unknown release group source" in e
                            for e in get_package_validation_issues(package)[0]))

    def test_artist_credit(self):
        self.assertEqual(artist_credit({"title": "x", "with": None, "featuring": "Mannie Fresh"}),
                         "Lil Wayne feat. Mannie Fresh")
        self.assertEqual(artist_credit({"title": "x", "with": "Birdman", "featuring": None}),
                         "Lil Wayne & Birdman")
        self.assertEqual(artist_credit({"title": "x", "with": "Young Money feat. Lloyd", "featuring": None}),
                         "Young Money feat. Lloyd")


class ImportTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._directory.name)
        self._paths = (database.DATABASE_PATH, import_release_group.DATABASE_PATH)
        database.DATABASE_PATH = self.directory / "test.db"
        import_release_group.DATABASE_PATH = database.DATABASE_PATH
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()

    def tearDown(self):
        database.DATABASE_PATH, import_release_group.DATABASE_PATH = self._paths
        self._directory.cleanup()

    def import_package(self, data):
        path = self.directory / "package.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            import_release_group.import_release_group_package(path)

    def test_hq_single_links_to_the_album_song(self):
        self.import_package(ALBUM)
        single = build_single_package({"title": "Go DJ", "with": None, "featuring": None})
        self.import_package(single)
        self.import_package(single)  # re-import is safe

        connection = sqlite3.connect(database.DATABASE_PATH)
        try:
            q = lambda sql: connection.execute(sql).fetchall()
            self.assertEqual(q("SELECT COUNT(*) FROM recordings"), [(2,)])
            self.assertEqual(q("SELECT COUNT(*) FROM releases"), [(2,)])
            self.assertEqual(q("SELECT title, release_type, discography, musicbrainz_release_id "
                               "FROM releases WHERE release_type = 'single'"),
                             [("Go DJ", "single", "official", None)])
            self.assertEqual(q("SELECT s.name, s.url FROM sources s JOIN recording_sources rs "
                               "ON rs.source_id = s.id WHERE rs.release_id IS NOT NULL "
                               "AND s.name = 'LilWayneHQ'"),
                             [("LilWayneHQ", "https://www.lilwaynehq.com/discography/singles/")])
            self.assertEqual(singles_of_album(connection, "carter-g"),
                             [("Go DJ", singles_of_album(connection, "carter-g")[0][1], "LilWayneHQ")])
        finally:
            connection.close()

    def test_hq_single_without_album_song_becomes_a_new_recording(self):
        self.import_package(ALBUM)
        self.import_package(build_single_package({"title": "Glory", "with": None, "featuring": None}))
        connection = sqlite3.connect(database.DATABASE_PATH)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM recordings").fetchone(), (3,))
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
