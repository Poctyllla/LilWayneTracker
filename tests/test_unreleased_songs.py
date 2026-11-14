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
from duplicate_guard import load_catalogue_from_connection, song_without_main_version
from unreleased_songs import apply, genius_date, hq_credits, plan


def song(title, status, catalogue=(), genius_id=None, hq=True, role="lead"):
    return {"title": title, "status": status, "role": role,
            "catalogue": [{"recording_id": r, "availability": a} for r, a in catalogue],
            "genius": [{"id": genius_id, "url": f"https://genius.com/{genius_id}",
                        "release_date": "March 3, 2008"}] if genius_id else [],
            "hq_projects": [], "hq_artists": ["Lil Wayne featuring Gudda Gudda"] if hq else []}


class HelperTests(unittest.TestCase):
    def test_hq_credits(self):
        self.assertEqual(hq_credits(["Lil Wayne featuring Gudda Gudda & Kidd Kidd"]),
                         [{"artist": "Gudda Gudda", "role": "featured_artist"},
                          {"artist": "Kidd Kidd", "role": "featured_artist"}])
        self.assertEqual(hq_credits(["Lil Wayne and Juelz Santana"]),
                         [{"artist": "Juelz Santana", "role": "main_artist"}])
        self.assertEqual(hq_credits(["Lil Wayne"]), [])

    def test_genius_date(self):
        self.assertEqual(genius_date("March 3, 2008"), "2008-03-03")
        self.assertEqual(genius_date("March 2008"), "2008-03")
        self.assertEqual(genius_date("2008"), "2008")
        self.assertIsNone(genius_date(None))


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self._path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self._directory.name) / "test.db"
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
        self.c = sqlite3.connect(database.DATABASE_PATH)
        self.c.execute("PRAGMA foreign_keys = ON")
        self.c.execute("INSERT INTO releases (id, title, artist_id, release_type, discography) "
                       "VALUES (1, 'Album', 1, 'studio_album', 'official')")
        self.milli = self.c.execute("INSERT INTO recordings (title, artist_id) VALUES ('A Milli', 1)").lastrowid
        version = self.c.execute("INSERT INTO versions (recording_id, version_name, version_type) "
                                 "VALUES (?, 'Album Version', 'album')", (self.milli,)).lastrowid
        self.c.execute("INSERT INTO release_tracks (release_id, version_id, disc_number, track_number, duration_ms) "
                       "VALUES (1, ?, 1, 1, 221000)", (version,))
        self.c.commit()

    def tearDown(self):
        self.c.close()
        database.DATABASE_PATH = self._path
        self._directory.cleanup()

    def test_plan_and_apply(self):
        crosscheck = {"songs": [
            song("A Milli", "in_catalogue", [(self.milli, "released")], genius_id=11),
            song("A Milli [Demo]", "variant", [(self.milli, "released")], genius_id=12),
            song("A Milly", "probable", [(self.milli, "released")]),
            song("Brand New Song", "missing", genius_id=13),
            song("Brand New Song (Snippet)", "missing"),
            song("Lonely Snippet (Snippet)", "missing"),
            song("Lil Wayne Buffalo Tweet", "missing"),
            song("Guest Verse", "missing", role="featured"),
        ]}
        actions, skipped = plan(crosscheck, self.c)
        self.assertEqual(sorted(a["action"] for a in actions),
                         ["alias", "genius_id", "song", "version", "version", "version"])
        self.assertEqual(skipped, [("Lil Wayne Buffalo Tweet", "not a song")])
        with self.c:
            apply(self.c, actions)
        q = lambda sql, *args: self.c.execute(sql, args).fetchall()
        self.assertEqual(q("SELECT genius_song_id FROM recordings WHERE id = ?", self.milli), [(11,)])
        self.assertEqual(q("SELECT version_type, version_name, genius_song_id, first_known_date FROM versions "
                           "WHERE recording_id = ? AND version_type = 'demo'", self.milli),
                         [("demo", "Demo", 12, "2008-03-03")])
        self.assertEqual(q("SELECT title FROM recording_aliases"), [("A Milly",)])
        new = q("SELECT r.id, a.availability FROM recordings r JOIN recording_availability a "
                "ON a.recording_id = r.id WHERE r.title = 'Brand New Song'")
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0][1], "unreleased")
        self.assertEqual(q("SELECT version_type FROM versions WHERE recording_id = ?", new[0][0]), [("snippet",)])
        self.assertEqual(q("SELECT title FROM recordings WHERE title = 'Lonely Snippet'"), [("Lonely Snippet",)])
        self.assertEqual(q("SELECT a.name, c.role FROM credits c JOIN artists a ON a.id = c.artist_id "
                           "WHERE c.recording_id = ?", new[0][0]), [("Gudda Gudda", "featured_artist")])
        self.assertEqual(q("SELECT count(*) FROM recording_sources rs JOIN sources s ON s.id = rs.source_id "
                           "WHERE rs.recording_id = ?", new[0][0]), [(2,)])  # Genius + HQ
        self.assertEqual(q("PRAGMA foreign_key_check"), [])
        # A later full track "Brand New Song" becomes the song's main version.
        catalogue = load_catalogue_from_connection(self.c)
        self.assertEqual(song_without_main_version("Brand New Song", catalogue), new[0][0])
        # The alias finds the song too.
        self.assertIn(self.milli, catalogue["by_title"]["a milly"])
        # A second run with the same (old) cross-check creates nothing new.
        actions, _ = plan(crosscheck, self.c)
        self.assertEqual([a["action"] for a in actions if a["action"] in ("song", "version")], [])


if __name__ == "__main__":
    unittest.main()
