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
from genius_credits import apply, genius_credits, plan, role_name
from genius_details import song_details


GENIUS_SONG = {
    "id": 10, "title": "A Milli", "url": "u", "release_date": "2008-04-29",
    "album": {"id": 1, "name": "Tha Carter III", "artist": {"name": "Lil Wayne"}},
    "primary_artist": {"name": "Lil Wayne"}, "featured_artists": [],
    "producer_artists": [{"name": "Bangladesh"}], "writer_artists": [{"name": "Lil Wayne"}, {"name": "Bangladesh"}],
    "custom_performances": [{"label": "Mixing Engineer", "artists": [{"name": "Fabian Marasciullo"}]},
                            {"label": "Label", "artists": [{"name": "Cash Money Records"}]},
                            {"label": "Phonographic Copyright ℗", "artists": [{"name": "Cash Money"}]}],
}


class GeniusCreditTests(unittest.TestCase):
    def test_song_details_and_roles(self):
        details = song_details(GENIUS_SONG)
        self.assertEqual(details["album"]["name"], "Tha Carter III")
        self.assertEqual(role_name("Mixing Engineer"), "mixing_engineer")
        self.assertEqual(genius_credits(details, own_song=True), [
            ("Bangladesh", "producer"), ("Lil Wayne", "writer"), ("Bangladesh", "writer"),
            ("Fabian Marasciullo", "mixing_engineer")])
        guest = dict(details, primary_artist="Drake", featured_artists=["Lil Wayne"])
        self.assertEqual(genius_credits(guest, own_song=False)[:2],
                         [("Drake", "main_artist"), ("Lil Wayne", "featured_artist")])

    def test_apply_once(self):
        directory = tempfile.TemporaryDirectory()
        old = database.DATABASE_PATH
        database.DATABASE_PATH = Path(directory.name) / "test.db"
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                database.initialize_database()
            c = sqlite3.connect(database.DATABASE_PATH)
            c.execute("PRAGMA foreign_keys = ON")
            recording = c.execute("INSERT INTO recordings (title, artist_id, genius_song_id) "
                                  "VALUES ('A Milli', 1, 10)").lastrowid
            c.execute("INSERT INTO artists (name) VALUES ('Bangladesh')")
            c.execute("INSERT INTO credits (artist_id, recording_id, role) "
                      "SELECT id, ?, 'producer' FROM artists WHERE name = 'Bangladesh'", (recording,))
            c.commit()
            cache = {10: song_details(GENIUS_SONG), 11: None}
            additions, missing = plan(c, cache)
            self.assertEqual(missing, 0)
            self.assertEqual([(name, role) for _, _, name, role in additions],
                             [("Lil Wayne", "writer"), ("Bangladesh", "writer"), ("Fabian Marasciullo", "mixing_engineer")])
            with c:
                apply(c, additions)
            self.assertEqual(plan(c, cache)[0], [])
            self.assertEqual(c.execute("SELECT count(*) FROM credits WHERE source = 'Genius'").fetchone()[0], 3)
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
            c.close()
        finally:
            database.DATABASE_PATH = old
            directory.cleanup()


if __name__ == "__main__":
    unittest.main()
