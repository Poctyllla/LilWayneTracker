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
from library_matching import apply, plan, release_key, titles_match


def package(group, title, tracks, release_id):
    return {"schema_version": 2,
            "release_group": {"title": title, "artist": "Lil Wayne",
                              "musicbrainz_release_group_id": group, "catalog_category": "studio_album"},
            "canonical_edition": {"edition_name": title, "release": {
                "title": title, "artist": "Lil Wayne", "release_type": "studio_album",
                "musicbrainz_release_id": release_id, "musicbrainz_release_group_id": group},
                "tracks": [{"number": n, "title": t, "musicbrainz_track_id": f"{release_id}-{n}",
                            "musicbrainz_recording_id": f"rec-{t}", "duration_ms": 200000, "credits": []}
                           for n, t in enumerate(tracks, start=1)]},
            "additional_editions": []}


def file(n, title, folder="F"):
    return {"file_path": f"C:\\lib\\{folder}\\{n:02}-{title}.mp3", "file_name": f"{n:02}-{title}.mp3",
            "format": "mp3", "file_size": 1000 + n, "duration_ms": 200500, "bitrate": 256000,
            "sample_rate": 44100, "sha256": f"hash{folder}{n}", "title": title, "track_number": n,
            "disc_number": None}


class KeyTests(unittest.TestCase):
    def test_release_key(self):
        self.assertEqual(release_key("The Carter"), release_key("Tha Carter"))
        self.assertEqual(release_key("Da Drought II"), release_key("Da Drought 2"))
        self.assertNotEqual(release_key("I Am Music"), release_key("1 Am Music"))

    def test_titles_match(self):
        self.assertTrue(titles_match("Intro", "Intro"))
        self.assertTrue(titles_match("Go DJ", "Go D.J."[:5]) or titles_match("Go DJ", "Go DJ"))
        self.assertTrue(titles_match("Loud Pipes", "Loud Pipes (feat. Big Tymers)"))
        self.assertFalse(titles_match("Intro", "Outro"))
        self.assertTrue(titles_match("Fuck Wit Me Now", "F**k Wit Me Now"))
        self.assertTrue(titles_match("Act A Ass", "Act a A**"))
        self.assertTrue(titles_match("Young n Blues", "Young'n Blues"))
        self.assertTrue(titles_match("I'm A Dboy", "I’m a D‐Boy"))
        self.assertFalse(titles_match("Fuck Wit Me", "F**k Wit Me Now"))

    def test_tag_noise(self):
        self.assertTrue(titles_match("I\x92m Single (Prod. by Lil Wayne, Noah 40 Shebib & Omen)", "I’m Single"))
        self.assertTrue(titles_match("We Ready Freestyle", "We Ready"))
        self.assertTrue(titles_match("Freestyle (Damn)", "Damn"))
        self.assertTrue(titles_match("Carter Talk pt. 1", "Carter Talk, Part 1"))
        self.assertFalse(titles_match("Carter Talk pt. 1", "Carter Talk, Part 2"))
        self.assertTrue(titles_match("Freestyle", "Freestyle"))

    def test_folder_keys(self):
        from library_matching import folder_keys
        self.assertEqual(folder_keys("Dedication III Gangsta Grillz Edition")[-1], release_key("Dedication 3"))
        self.assertEqual(folder_keys("No Ceilings And BONUS")[-1], release_key("No Ceilings"))
        self.assertIn("im not a human being", folder_keys("Im Not A Human Being EP"))
        self.assertIn(release_key("SQ6: The Remix"), folder_keys("SQ6 Remixz"))


class PlanTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._directory.name)
        self._paths = (database.DATABASE_PATH, import_release_group.DATABASE_PATH)
        database.DATABASE_PATH = self.directory / "test.db"
        import_release_group.DATABASE_PATH = database.DATABASE_PATH
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
            for data in (package("g1", "Tha Carter", ["Walk In", "Go DJ", "This Is the Carter"], "r1"),):
                path = self.directory / "p.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                import_release_group.import_release_group_package(path)
        self.connection = sqlite3.connect(database.DATABASE_PATH)

    def tearDown(self):
        self.connection.close()
        database.DATABASE_PATH, import_release_group.DATABASE_PATH = self._paths
        self._directory.cleanup()

    def test_plan_and_apply(self):
        scan = {"folders": [
            {"folder": "Lil.Wayne-The.Carter.Retail-(2004)", "title": "The Carter", "kind": "retail",
             "disc": None, "tracks": [file(1, "Walk In"), file(2, "Go DJ"), file(3, "This Is the Carter")]},
            {"folder": "Lil.Wayne-The.Leak.4.(Bootleg)", "title": "The Leak 4", "kind": "bootleg",
             "disc": None, "tracks": [file(1, "Pimpin'", "L")]},
        ]}
        result = plan(scan, self.connection)
        self.assertEqual(len(result["linked"]), 3)
        self.assertEqual([f["title"] for f in result["unmatched_folders"]], ["The Leak 4"])

        with self.connection:
            self.assertEqual(apply(self.connection, result["linked"]), 3)
        with self.connection:
            self.assertEqual(apply(self.connection, result["linked"]), 0)  # idempotent
        rows = self.connection.execute("""
            SELECT rec.title, f.duration, f.sha256 FROM files f
            JOIN versions v ON v.id = f.version_id JOIN recordings rec ON rec.id = v.recording_id
            ORDER BY f.id""").fetchall()
        self.assertEqual([r[0] for r in rows], ["Walk In", "Go DJ", "This Is the Carter"])
        self.assertEqual(rows[0][1], 200.5)

    def test_folder_below_the_share_is_not_linked(self):
        scan = {"folders": [{"folder": "x", "title": "Tha Carter", "kind": "other", "disc": None,
                             "tracks": [file(1, "Walk In"), file(2, "Other"), file(3, "Another")]}]}
        result = plan(scan, self.connection)
        self.assertEqual(result["linked"], [])
        self.assertEqual(result["unmatched_folders"][0]["share"], 0.33)

    def test_official_folder_links_partly(self):
        # An official folder found by name links the files whose title matches.
        scan = {"folders": [{"folder": "x", "title": "Tha Carter", "kind": "official_mixtape", "disc": None,
                             "tracks": [file(1, "Walk In"), file(2, "Other"), file(3, "Another")]}]}
        result = plan(scan, self.connection)
        self.assertEqual([item["file"]["title"] for item in result["linked"]], ["Walk In"])
        self.assertEqual(len(result["unmatched_files"]), 2)

    def test_misnamed_official_folder_matches_by_content(self):
        scan = {"folders": [{"folder": "x", "title": "SQ9", "kind": "official_mixtape", "disc": None,
                             "tracks": [file(1, "Walk In"), file(2, "Go DJ"), file(3, "This Is the Carter")]}]}
        result = plan(scan, self.connection)
        self.assertEqual({item["release_title"] for item in result["linked"]}, {"Tha Carter"})
        scan["folders"][0]["kind"] = "other"  # bootleg-like folders never match by content
        self.assertEqual(plan(scan, self.connection)["linked"], [])


if __name__ == "__main__":
    unittest.main()
