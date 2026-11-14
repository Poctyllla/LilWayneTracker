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
from split_release_tracks import apply_split, find_splits


class SplitReleaseTracksTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self._path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self._directory.name) / "test.db"
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
        c = self.c = sqlite3.connect(database.DATABASE_PATH)
        c.execute("PRAGMA foreign_keys = ON")
        c.execute("INSERT INTO releases (id, title, artist_id, release_type, discography) VALUES "
                  "(1, 'Bootleg A', 1, 'bootleg', 'unofficial'), (2, 'Bootleg B', 1, 'bootleg', 'unofficial'), "
                  "(3, 'Album', 1, 'studio_album', 'official')")
        self.recording = c.execute("INSERT INTO recordings (title, artist_id) VALUES ('Wasted', 1)").lastrowid
        self.version = c.execute("INSERT INTO versions (recording_id, version_name, version_type) "
                                 "VALUES (?, 'Album Version', 'album')", (self.recording,)).lastrowid
        for release_id, number, duration in ((1, 6, 300000), (1, 9, 386000), (2, 4, 301000),
                                             (3, 1, 216000), (3, 2, 216000)):
            c.execute("INSERT INTO release_tracks (release_id, version_id, disc_number, track_number, duration_ms, "
                      "track_title) VALUES (?, ?, 1, ?, ?, 'Wasted')", (release_id, self.version, number, duration))
        for path, seconds in (("a/06.mp3", 300.2), ("a/09.mp3", 386.4), ("b/04.mp3", 301.0)):
            c.execute("INSERT INTO files (version_id, file_path, duration) VALUES (?, ?, ?)",
                      (self.version, path, seconds))
        c.commit()

    def tearDown(self):
        self.c.close()
        database.DATABASE_PATH = self._path
        self._directory.cleanup()

    def test_split(self):
        plan = find_splits(self.c)
        self.assertEqual(len(plan), 1)  # the official album is left alone
        self.assertEqual(plan[0]["keep"]["number"], 6)  # closest to the 301 s on Bootleg B
        self.assertEqual([t["number"] for t in plan[0]["move"]], [9])
        with self.c:
            apply_split(self.c, plan[0], 1)
        q = lambda sql, *a: self.c.execute(sql, a).fetchall()
        moved = q("SELECT v.recording_id FROM release_tracks rt JOIN versions v ON v.id = rt.version_id "
                  "WHERE rt.release_id = 1 AND rt.track_number = 9")[0][0]
        self.assertNotEqual(moved, self.recording)
        self.assertEqual(q("SELECT f.file_path FROM files f JOIN versions v ON v.id = f.version_id "
                           "WHERE v.recording_id = ?", moved), [("a/09.mp3",)])
        self.assertEqual(q("SELECT count(*) FROM files WHERE version_id = ?", self.version), [(2,)])
        self.assertEqual(find_splits(self.c), [])
        self.assertEqual(q("PRAGMA foreign_key_check"), [])


if __name__ == "__main__":
    unittest.main()
