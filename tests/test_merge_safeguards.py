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
from merge_recordings import find_pairs, within_limit


class MergeSafeguardTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self._path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self._directory.name) / "test.db"
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
        self.c = sqlite3.connect(database.DATABASE_PATH)
        self.position = 0

    def tearDown(self):
        self.c.close()
        database.DATABASE_PATH = self._path
        self._directory.cleanup()

    def add(self, title, release_id, *durations):
        if not self.c.execute("SELECT 1 FROM releases WHERE id = ?", (release_id,)).fetchone():
            self.c.execute("INSERT INTO releases (id, title, artist_id, release_type, discography) "
                           "VALUES (?, ?, 1, 'bootleg', 'unofficial')", (release_id, f"Bootleg {release_id}"))
        recording = self.c.execute("INSERT INTO recordings (title, artist_id) VALUES (?, 1)", (title,)).lastrowid
        version = self.c.execute("INSERT INTO versions (recording_id, version_name, version_type) "
                                 "VALUES (?, 'Album Version', 'album')", (recording,)).lastrowid
        for duration in durations:
            self.position += 1
            self.c.execute("INSERT INTO release_tracks (release_id, version_id, disc_number, track_number, "
                           "duration_ms) VALUES (?, ?, 1, ?, ?)", (release_id, version, self.position, duration))
        return recording

    def test_within_limit(self):
        self.assertTrue(within_limit("Red Magic", {313000, 308000}, {300000}))
        self.assertFalse(within_limit("Red Magic", {313000, 308000}, {297000}))  # no drift along a chain
        self.assertFalse(within_limit("Intro", {33000, 34000}, {37000}))

    def test_same_release_is_never_merged(self):
        first = self.add("Freestyle", 1, 60000)
        self.add("Freestyle", 1, 60000)
        other = self.add("Freestyle", 2, 60500)
        same, _ = find_pairs(self.c, separate=set())
        self.assertEqual([(keep, drop) for keep, drop, _ in same], [(first, other)])

    def test_keep_separate_is_respected(self):
        a = self.add("Red Magic", 1, 313000)
        b = self.add("Red Magic", 2, 305000)
        self.assertEqual(len(find_pairs(self.c, separate=set())[0]), 1)
        self.assertEqual(find_pairs(self.c, separate={frozenset((a, b))})[0], [])


if __name__ == "__main__":
    unittest.main()
