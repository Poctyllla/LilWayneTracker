import contextlib
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import database
from split_versions import apply_move, apply_rename, find_splits


class SplitVersionsTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self._path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self._directory.name) / "test.db"
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
        self.connection = sqlite3.connect(database.DATABASE_PATH)
        self.connection.execute("PRAGMA foreign_keys = ON")
        c = self.connection
        c.execute("INSERT OR IGNORE INTO artists (id, name) VALUES (1, 'Lil Wayne')")
        c.execute("INSERT INTO releases (id, title, artist_id, release_type, discography) "
                  "VALUES (1, 'Album', 1, 'studio_album', 'official'), (2, 'Leak', 1, 'bootleg', 'unofficial')")
        self.songs = {}
        for number, (title, release_id, mbid) in enumerate([
                ("Fireman", 1, "mb-fire"), ("Fireman (Live In Los Angeles)", 2, None),
                ("Cascades (Snippet)", 2, None), ("Hustler Musik (radio edit)", 1, None),
                ("Hustler Musik", 1, None), ("Hustler Musik (Studio Acapella)", 2, None)], start=1):
            recording = c.execute("INSERT INTO recordings (title, artist_id, musicbrainz_recording_id) "
                                  "VALUES (?, 1, ?)", (title, mbid)).lastrowid
            version = c.execute("INSERT INTO versions (recording_id, version_name, version_type) "
                                "VALUES (?, 'Album Version', 'album')", (recording,)).lastrowid
            c.execute("INSERT INTO release_tracks (release_id, version_id, disc_number, track_number) "
                      "VALUES (?, ?, 1, ?)", (release_id, version, number))
            self.songs[title] = (recording, version)
        c.commit()

    def tearDown(self):
        self.connection.close()
        database.DATABASE_PATH = self._path
        self._directory.cleanup()

    def test_plan_and_apply(self):
        plan = find_splits(self.connection)
        moves = {item["title"]: item for item in plan["move"]}
        self.assertEqual(moves["Fireman (Live In Los Angeles)"]["base_id"], self.songs["Fireman"][0])
        self.assertEqual(moves["Hustler Musik (Studio Acapella)"]["base_id"], self.songs["Hustler Musik"][0])
        self.assertEqual([item["title"] for item in plan["rename"]], ["Cascades (Snippet)"])
        self.assertEqual(plan["review"], [])

        with self.connection:
            for item in plan["move"]:
                apply_move(self.connection, item)
            for item in plan["rename"]:
                apply_rename(self.connection, item)
        c = self.connection
        live_version = self.songs["Fireman (Live In Los Angeles)"][1]
        self.assertEqual(c.execute("SELECT recording_id, version_type, version_name FROM versions WHERE id = ?",
                                   (live_version,)).fetchone(),
                         (self.songs["Fireman"][0], "live", "Live In Los Angeles"))
        self.assertIsNone(c.execute("SELECT 1 FROM recordings WHERE id = ?",
                                    (self.songs["Fireman (Live In Los Angeles)"][0],)).fetchone())
        self.assertEqual(c.execute("SELECT track_title FROM release_tracks WHERE version_id = ?",
                                   (live_version,)).fetchone()[0], "Fireman (Live In Los Angeles)")
        cascades = self.songs["Cascades (Snippet)"]
        self.assertEqual(c.execute("SELECT title FROM recordings WHERE id = ?", (cascades[0],)).fetchone()[0],
                         "Cascades")
        self.assertEqual(c.execute("SELECT version_type FROM versions WHERE id = ?", (cascades[1],)).fetchone()[0],
                         "snippet")
        self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(find_splits(c), {"move": [], "rename": [], "review": []})


if __name__ == "__main__":
    unittest.main()
