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
from guest_appearances import apply, hq_credits, musicbrainz_credits, plan, promote_released_guests
from guest_survey import artist_key, guest_song_key


def survey_song(title, artist, mb=None, genius=None, hq=None, **extra):
    return {"title": title, "main_artist": artist, "in_catalogue": [], "group_song": False,
            "musicbrainz": mb or [], "genius": genius or [], "hq": bool(hq), "hq_credit": hq,
            "hq_projects": extra.pop("hq_projects", []), **extra}


class CreditTests(unittest.TestCase):
    def test_musicbrainz_credits(self):
        self.assertEqual(musicbrainz_credits([["Drake", " feat. "], ["Lil Wayne", ""]]),
                         [{"artist": "Drake", "role": "main_artist"}, {"artist": "Lil Wayne", "role": "featured_artist"}])
        self.assertEqual(musicbrainz_credits([["2 Chainz", " & "], ["Lil Wayne", ""]]),
                         [{"artist": "2 Chainz", "role": "main_artist"}, {"artist": "Lil Wayne", "role": "main_artist"}])

    def test_hq_credits(self):
        self.assertEqual(hq_credits("Juvenile featuring Lil Wayne, Turk, and B.G."),
                         [{"artist": "Juvenile", "role": "main_artist"},
                          {"artist": "Lil Wayne", "role": "featured_artist"},
                          {"artist": "Turk", "role": "featured_artist"},
                          {"artist": "B.G.", "role": "featured_artist"}])

    def test_keys(self):
        self.assertEqual(artist_key("Baby"), artist_key("Birdman"))
        self.assertEqual(artist_key("T‐Pain"), artist_key("T-Pain"))
        self.assertEqual(guest_song_key("Rich As F#ck"), guest_song_key("Rich As F*ck"))


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self._path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self._directory.name) / "test.db"
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
        self.c = sqlite3.connect(database.DATABASE_PATH)
        self.c.execute("PRAGMA foreign_keys = ON")

    def tearDown(self):
        self.c.close()
        database.DATABASE_PATH = self._path
        self._directory.cleanup()

    def test_plan_and_apply(self):
        survey = {"songs": [
            survey_song("Forever", "Drake", mb=[
                {"id": "mb-1", "first_release_date": "2009-08-28", "credit_parts": [
                    ["Drake", ", "], ["Kanye West", ", "], ["Lil Wayne", " & "], ["Eminem", ""]]},
                {"id": "mb-2", "first_release_date": "2009-09-15", "credit_parts": [["Drake", ""]]}],
                genius=[{"id": 77, "url": "https://genius.com/x", "release_date": "August 28, 2009"}]),
            survey_song("Unknown Feature", "Rick Ross", genius=[{"id": 78, "url": "https://genius.com/y",
                                                                  "release_date": None}]),
            survey_song("Motivation", "DJ Drama", in_catalogue=[5]),
            survey_song("Neva Get Enuf", "Hot Boys", group_song=True),
        ]}
        actions, skipped = plan(survey, self.c)
        self.assertEqual([a["title"] for a in actions], ["Forever", "Unknown Feature"])
        self.assertEqual(skipped, {"already in the catalogue": 1, "group song": 1})
        with self.c:
            apply(self.c, actions)
        q = lambda sql, *a: self.c.execute(sql, a).fetchall()
        forever = q("SELECT r.id, a.name, r.appearance, r.musicbrainz_recording_id, r.recording_date "
                    "FROM recordings r JOIN artists a ON a.id = r.artist_id WHERE r.title = 'Forever'")[0]
        self.assertEqual(forever[1:], ("Drake", "guest", "mb-1", "2009-08-28"))
        self.assertEqual(q("SELECT availability FROM recording_availability WHERE recording_id = ?", forever[0]),
                         [("released",)])
        self.assertEqual(q("SELECT musicbrainz_recording_id FROM recording_musicbrainz_ids"), [("mb-2",)])
        self.assertEqual(sorted(q("SELECT a.name, c.role FROM credits c JOIN artists a ON a.id = c.artist_id "
                                  "WHERE c.recording_id = ?", forever[0])),
                         [("Drake", "main_artist"), ("Eminem", "main_artist"), ("Kanye West", "main_artist"),
                          ("Lil Wayne", "main_artist")])
        unknown = q("SELECT a.availability FROM recordings r JOIN recording_availability a "
                    "ON a.recording_id = r.id WHERE r.title = 'Unknown Feature'")
        self.assertEqual(unknown, [("unreleased",)])
        # A guest song is never taken for Lil Wayne's own song of that title.
        self.assertIsNone(song_without_main_version("Forever", load_catalogue_from_connection(self.c)))
        # Second run adds nothing.
        self.assertEqual(plan(survey, self.c)[0], [])
        # The song later appears on a release of Wayne's discography -> his song.
        self.c.execute("INSERT INTO releases (id, title, artist_id, release_type, discography) "
                       "VALUES (1, 'Collab', 1, 'collaboration_album', 'official')")
        version = self.c.execute("INSERT INTO versions (recording_id, version_name, version_type) "
                                 "VALUES (?, 'Album Version', 'album')", (forever[0],)).lastrowid
        self.c.execute("INSERT INTO release_tracks (release_id, version_id, disc_number, track_number) "
                       "VALUES (1, ?, 1, 1)", (version,))
        with self.c:
            self.assertEqual(promote_released_guests(self.c), [forever[0]])
        self.assertEqual(q("SELECT r.appearance, a.name, r.availability_override FROM recordings r "
                           "JOIN artists a ON a.id = r.artist_id WHERE r.id = ?", forever[0]),
                         [(None, "Lil Wayne", None)])
        self.assertIn(("Drake", "main_artist"), q("SELECT a.name, c.role FROM credits c JOIN artists a "
                                                  "ON a.id = c.artist_id WHERE c.recording_id = ?", forever[0]))


if __name__ == "__main__":
    unittest.main()
