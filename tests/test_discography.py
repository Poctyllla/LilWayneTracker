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
from release_group_package import (
    find_package_files,
    get_package_validation_issues,
    iter_package_files,
    iter_resolved_editions,
    package_discography,
    resolve_discography,
)
from review_candidates import load_review_data


def package(category="studio_album", discography=None, release_id="rel-1"):
    release_group = {"title": "Album", "artist": "Lil Wayne",
                     "musicbrainz_release_group_id": "g1", "catalog_category": category}
    if discography is not None:
        release_group["discography"] = discography
    return {
        "schema_version": 2,
        "release_group": release_group,
        "canonical_edition": {
            "edition_name": "Canonical",
            "release": {"title": "Album", "artist": "Lil Wayne", "release_type": category,
                        "musicbrainz_release_id": release_id,
                        "musicbrainz_release_group_id": "g1"},
            "tracks": [{"number": 1, "title": "Song", "musicbrainz_track_id": f"t-{release_id}",
                        "musicbrainz_recording_id": "r1", "credits": []}],
        },
        "additional_editions": [],
    }


class RuleTests(unittest.TestCase):
    def test_defaults_and_fixed_values(self):
        self.assertEqual(resolve_discography("studio_album"), "official")
        self.assertEqual(resolve_discography("collaboration_album"), "official")
        self.assertEqual(resolve_discography("ep"), "official")
        self.assertEqual(resolve_discography("single"), "official")
        self.assertEqual(resolve_discography("single", "unofficial"), "unofficial")
        self.assertEqual(resolve_discography("bootleg"), "unofficial")
        self.assertEqual(resolve_discography("album"), "official")  # legacy name

    def test_categories_without_default_need_a_decision(self):
        for category in ("mixtape", "remix", "compilation", "other"):
            with self.subTest(category):
                with self.assertRaisesRegex(ValueError, "must be chosen"):
                    resolve_discography(category)
                self.assertEqual(resolve_discography(category, "official"), "official")
                self.assertEqual(resolve_discography(category, "unofficial"), "unofficial")

    def test_invalid_combinations(self):
        with self.assertRaisesRegex(ValueError, "always official"):
            resolve_discography("studio_album", "unofficial")
        with self.assertRaisesRegex(ValueError, "always unofficial"):
            resolve_discography("bootleg", "official")
        with self.assertRaisesRegex(ValueError, "Unknown discography"):
            resolve_discography("single", "semi")


class PackageTests(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(get_package_validation_issues(package())[0], [])
        self.assertEqual(get_package_validation_issues(package("mixtape", "official"))[0], [])
        for bad in (package("mixtape"), package("studio_album", "unofficial"),
                    package("single", "maybe")):
            with self.subTest(bad["release_group"]):
                errors, _ = get_package_validation_issues(bad)
                self.assertTrue(any("discography" in error for error in errors))

    def test_resolved_editions_carry_the_discography(self):
        editions = list(iter_resolved_editions(package("mixtape", "unofficial")))
        self.assertEqual(editions[0][1]["release"]["discography"], "unofficial")
        self.assertEqual(package_discography(package()), "official")

    def test_package_files_are_found_in_both_parts(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for part in ("official/mixtapes", "unofficial/mixtapes"):
                (base / part).mkdir(parents=True)
                (base / part / "dedication.json").write_text("{}", encoding="utf-8")
            self.assertEqual(
                [p.relative_to(base).as_posix() for p in find_package_files("dedication.json", base)],
                ["official/mixtapes/dedication.json", "unofficial/mixtapes/dedication.json"],
            )
            self.assertEqual(len(iter_package_files(base)), 2)

    def test_old_approved_review_decisions_get_the_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review.json"
            path.write_text(json.dumps({"candidates": [
                {"release_group_id": "a", "title": "A", "decision": "approved",
                 "confirmed_category": "studio_album"},
                {"release_group_id": "b", "title": "B", "decision": "approved",
                 "confirmed_category": "mixtape"},
            ]}), encoding="utf-8")
            candidates = load_review_data(path)["candidates"]
            self.assertEqual(candidates[0]["confirmed_discography"], "official")
            self.assertNotIn("confirmed_discography", candidates[1])


class DatabaseTests(unittest.TestCase):
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

    def query(self, sql):
        connection = sqlite3.connect(database.DATABASE_PATH)
        try:
            return connection.execute(sql).fetchall()
        finally:
            connection.close()

    def test_import_stores_discography(self):
        self.import_package(package())
        self.import_package(package("mixtape", "unofficial", release_id="rel-2"))
        self.assertEqual(self.query(
            "SELECT musicbrainz_release_id, discography FROM releases ORDER BY id"),
            [("rel-1", "official"), ("rel-2", "unofficial")])

    def test_conflicting_discography_on_reimport_is_rejected(self):
        self.import_package(package("mixtape", "official"))
        with self.assertRaisesRegex(ValueError, "Conflicting discography"):
            self.import_package(package("mixtape", "unofficial"))
        self.assertEqual(self.query("SELECT discography FROM releases"), [("official",)])

    def test_migration_backfills_only_unambiguous_types(self):
        connection = sqlite3.connect(database.DATABASE_PATH)
        with connection:
            for title, release_type in (("A", "studio_album"), ("B", "mixtape"),
                                        ("C", "bootleg"), ("D", "single")):
                connection.execute(
                    "INSERT INTO releases (title, release_type, artist_id) VALUES (?, ?, 1)",
                    (title, release_type))
        connection.close()
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
        self.assertEqual(self.query("SELECT title, discography FROM releases ORDER BY title"),
                         [("A", "official"), ("B", None), ("C", "unofficial"), ("D", "official")])


if __name__ == "__main__":
    unittest.main()
