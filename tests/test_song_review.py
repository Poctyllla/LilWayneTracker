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
from song_review import apply, plan


class SongReviewTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self._path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self._directory.name) / "test.db"
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
        self.c = sqlite3.connect(database.DATABASE_PATH)
        self.c.execute("PRAGMA foreign_keys = ON")
        self.c.execute("INSERT INTO releases (id, title, artist_id, release_type, discography) "
                       "VALUES (1, 'Sorry 4 the Wait', 1, 'mixtape', 'official'), "
                       "(2, 'Sorry 4 the Wait', 1, 'mixtape', 'official')")
        self.ids = {}
        for number, (title, release_id) in enumerate(
                [("Grove St. Party (Freestyle)", 1), ("Grove Party", 2), ("Miami", 1), ("Miami", 2)], start=1):
            recording = self.c.execute("INSERT INTO recordings (title, artist_id) VALUES (?, 1)", (title,)).lastrowid
            version = self.c.execute("INSERT INTO versions (recording_id, version_name, version_type) "
                                     "VALUES (?, 'Album Version', 'album')", (recording,)).lastrowid
            self.c.execute("INSERT INTO release_tracks (release_id, version_id, disc_number, track_number) "
                           "VALUES (?, ?, 1, ?)", (release_id, version, number))
            self.ids[f"{title}:{release_id}"] = recording
        self.c.commit()

    def tearDown(self):
        self.c.close()
        database.DATABASE_PATH = self._path
        self._directory.cleanup()

    def test_apply_and_rerun(self):
        grove, grove_2022 = self.ids["Grove St. Party (Freestyle):1"], self.ids["Grove Party:2"]
        miami, miami_original = self.ids["Miami:1"], self.ids["Miami:2"]
        decisions = [
            {"action": "merge", "keep": grove, "drop": grove_2022},
            {"action": "alias", "recording": miami, "title": "Miami (Blow)", "source": "LilWayneHQ"},
            {"action": "make_version", "recording": miami_original, "of": miami,
             "version_type": "original", "version_name": "Original"},
            {"action": "add_version", "of": miami, "title": "Miami [Demo]", "version_type": "demo",
             "version_name": "Demo", "hq": True, "credits": [{"artist": "Curren$y", "role": "featured_artist"}]},
            {"action": "keep_separate", "recordings": [grove, miami]},
        ]
        planned = plan(self.c, decisions)
        self.assertEqual([status for _, status in planned], ["apply"] * 4 + ["noted"])
        with self.c:
            apply(self.c, planned)
        q = lambda sql, *args: self.c.execute(sql, args).fetchall()
        self.assertEqual(q("SELECT count(*) FROM recordings WHERE id = ?", grove_2022), [(0,)])
        self.assertEqual(q("SELECT track_title FROM release_tracks WHERE release_id = 2 AND track_number = 2"),
                         [("Grove Party",)])
        self.assertIn(("Grove Party",), q("SELECT title FROM recording_aliases WHERE recording_id = ?", grove))
        self.assertEqual(sorted(q("SELECT version_type FROM versions WHERE recording_id = ?", miami)),
                         [("album",), ("demo",), ("original",)])
        self.assertEqual(q("PRAGMA foreign_key_check"), [])
        self.assertEqual([status for _, status in plan(self.c, decisions)], ["done"] * 4 + ["noted"])


class DetachVersionTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self._path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self._directory.name) / "test.db"
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
        c = self.c = sqlite3.connect(database.DATABASE_PATH)
        c.execute("PRAGMA foreign_keys = ON")
        c.execute("INSERT INTO releases (id, title, artist_id, release_type, release_date, discography) VALUES "
                  "(1, 'Funeral', 1, 'studio_album', '2020-01-31', 'official'), "
                  "(2, 'SQ2', 1, 'mixtape', '2002', 'official'), (3, 'Bootleg', 1, 'bootleg', '2009', 'unofficial')")
        self.help = c.execute("INSERT INTO recordings (title, artist_id) VALUES ('Help', 1)").lastrowid
        album = c.execute("INSERT INTO versions (recording_id, version_name, version_type) "
                          "VALUES (?, 'Album Version', 'album')", (self.help,)).lastrowid
        self.remix = c.execute("INSERT INTO versions (recording_id, version_name, version_type, musicbrainz_recording_id) "
                               "VALUES (?, 'remix', 'remix', 'mb-help-remix')", (self.help,)).lastrowid
        self.lost_remix = c.execute("INSERT INTO versions (recording_id, version_name, version_type) "
                                    "VALUES (?, 'Remix', 'remix')", (self.help,)).lastrowid
        for release_id, version, number in ((1, album, 2), (2, self.remix, 13), (3, self.lost_remix, 3)):
            c.execute("INSERT INTO release_tracks (release_id, version_id, disc_number, track_number) VALUES (?, ?, 1, ?)",
                      (release_id, version, number))
        c.execute("INSERT INTO files (version_id, file_path) VALUES (?, 'C:/x/lost.mp3')", (self.lost_remix,))
        c.execute("INSERT INTO artists (id, name) VALUES (50, 'Gorilla Zoe')")
        self.guest = c.execute("INSERT INTO recordings (title, artist_id, appearance) "
                               "VALUES ('Lost (Remix)', 50, 'guest')").lastrowid
        c.commit()

    def tearDown(self):
        self.c.close()
        database.DATABASE_PATH = self._path
        self._directory.cleanup()

    def test_detach(self):
        decisions = [
            {"action": "detach_version", "version": self.remix, "from": self.help, "title": "Help (remix)"},
            {"action": "detach_version", "version": self.lost_remix, "from": self.help, "to": self.guest},
        ]
        planned = plan(self.c, decisions)
        self.assertEqual([s for _, s in planned], ["apply", "apply"])
        with self.c:
            apply(self.c, planned)
        q = lambda sql, *a: self.c.execute(sql, a).fetchall()
        new_song = q("SELECT recording_id, version_type FROM versions WHERE id = ?", self.remix)[0]
        self.assertNotEqual(new_song[0], self.help)
        self.assertEqual(new_song[1], "album")
        self.assertEqual(q("SELECT title, musicbrainz_recording_id FROM recordings WHERE id = ?", new_song[0]),
                         [("Help (remix)", "mb-help-remix")])
        self.assertEqual(q("SELECT track_title FROM release_tracks WHERE version_id = ?", self.remix), [("Help",)])
        self.assertEqual(q("SELECT recording_id, version_type FROM versions WHERE id = ?", self.lost_remix),
                         [(self.guest, "album")])
        self.assertEqual(q("SELECT appearance FROM recordings WHERE id = ?", self.guest), [("guest",)])
        self.assertEqual(q("SELECT count(*) FROM versions WHERE recording_id = ?", self.help), [(1,)])
        self.assertEqual([s for _, s in plan(self.c, decisions)], ["done", "done"])
        self.assertEqual(q("PRAGMA foreign_key_check"), [])

    def test_split_off(self):
        album = self.c.execute("SELECT id FROM versions WHERE recording_id = ? AND version_type = 'album'",
                               (self.help,)).fetchone()[0]
        with self.c:
            self.c.execute("UPDATE release_tracks SET duration_ms = 142000 WHERE version_id = ?", (album,))
            self.c.execute("INSERT INTO release_tracks (release_id, version_id, disc_number, track_number, duration_ms) "
                           "VALUES (3, ?, 1, 9, 150000)", (album,))
            self.c.execute("INSERT INTO files (version_id, file_path, duration) VALUES (?, 'C:/x/help-2009.mp3', 150.3)",
                           (album,))
        decision = {"action": "split_off", "recording": self.help, "releases": [3], "title": "Help"}
        planned = plan(self.c, [decision])
        self.assertEqual(planned[0][1], "apply")
        with self.c:
            apply(self.c, planned)
        q = lambda sql, *a: self.c.execute(sql, a).fetchall()
        moved = q("SELECT v.recording_id FROM release_tracks rt JOIN versions v ON v.id = rt.version_id "
                  "WHERE rt.release_id = 3 AND rt.track_number = 9")[0][0]
        self.assertNotEqual(moved, self.help)
        self.assertEqual(q("SELECT v.recording_id FROM files f JOIN versions v ON v.id = f.version_id "
                           "WHERE f.file_path = 'C:/x/help-2009.mp3'"), [(moved,)])
        self.assertEqual(plan(self.c, [decision])[0][1], "done")

    def test_detach_last_version_merges_the_empty_song(self):
        lonely = self.c.execute("INSERT INTO recordings (title, artist_id) VALUES ('Someone To Love Me', 1)").lastrowid
        with self.c:
            self.c.execute("UPDATE versions SET recording_id = ? WHERE id = ?", (lonely, self.lost_remix))
        decision = {"action": "detach_version", "version": self.lost_remix, "from": lonely, "to": self.guest}
        with self.c:
            apply(self.c, plan(self.c, [decision]))
        self.assertEqual(self.c.execute("SELECT count(*) FROM recordings WHERE id = ?", (lonely,)).fetchone()[0], 0)
        self.assertIn(("Someone To Love Me",), self.c.execute(
            "SELECT title FROM recording_aliases WHERE recording_id = ?", (self.guest,)).fetchall())


if __name__ == "__main__":
    unittest.main()
