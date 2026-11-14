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
from guest_matches import attach_to_catalogued, catalogue_facts, judge


def song(title, artist, candidates, mb=(), genius=()):
    return {"title": title, "main_artist": artist, "in_catalogue": list(candidates), "sources": "x",
            "musicbrainz": [{"id": i, "length": length, "credit": f"{artist} feat. Lil Wayne",
                             "credit_parts": [[artist, " feat. "], ["Lil Wayne", ""]]} for i, length in mb],
            "genius": [{"id": g, "url": f"https://genius.com/{g}"} for g in genius], "hq": False}


class GuestMatchTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self._path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self._directory.name) / "test.db"
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
        c = self.c = sqlite3.connect(database.DATABASE_PATH)
        c.execute("PRAGMA foreign_keys = ON")
        c.execute("INSERT INTO releases (id, title, artist_id, release_type, discography) "
                  "VALUES (1, 'Mixtape', 1, 'mixtape', 'official')")
        self.ids = {}
        for number, (title, mbid, duration) in enumerate(
                [("Motivation", None, 200000), ("Rock Star", None, 150000), ("Ya Heard Me", "mb-yhm", 240000)],
                start=1):
            recording = c.execute("INSERT INTO recordings (title, artist_id, musicbrainz_recording_id) "
                                  "VALUES (?, 1, ?)", (title, mbid)).lastrowid
            version = c.execute("INSERT INTO versions (recording_id, version_name, version_type) "
                                "VALUES (?, 'Album Version', 'album')", (recording,)).lastrowid
            c.execute("INSERT INTO release_tracks (release_id, version_id, disc_number, track_number, duration_ms) "
                      "VALUES (1, ?, 1, ?, ?)", (version, number, duration))
            self.ids[title] = recording
        c.commit()

    def tearDown(self):
        self.c.close()
        database.DATABASE_PATH = self._path
        self._directory.cleanup()

    def test_judge(self):
        facts = catalogue_facts(self.c)
        self.assertEqual(judge(song("Ya Heard Me", "B.G.", [self.ids["Ya Heard Me"]], mb=[("mb-yhm", 1)]), facts)[:2],
                         ("same", self.ids["Ya Heard Me"]))
        self.assertEqual(judge(song("Motivation", "DJ Drama", [self.ids["Motivation"]]), facts)[:2],
                         ("same", self.ids["Motivation"]))
        self.assertEqual(judge(song("Rock Star", "Chamillionaire", [self.ids["Rock Star"]],
                                    mb=[("mb-rs", 260000)]), facts)[0], "different")
        self.assertEqual(judge(song("Rock Star", "Chamillionaire", [self.ids["Rock Star"]],
                                    mb=[("mb-rs", 155000)]), facts)[0], "same")
        self.assertEqual(judge(song("Rock Star", "Chamillionaire", [self.ids["Rock Star"]], genius=[5]), facts)[0],
                         "review")
        # A credit does not outweigh lengths that differ by more than 15 s.
        facts[self.ids["Rock Star"]]["artists"].add("chamillionaire")
        self.assertEqual(judge(song("Rock Star", "Chamillionaire", [self.ids["Rock Star"]],
                                    mb=[("mb-rs", 260000)]), facts)[0], "different")

    def test_genius_album_settles_review(self):
        facts = catalogue_facts(self.c)
        review = song("Rock Star", "Chamillionaire", [self.ids["Rock Star"]], genius=[5])
        on_mixtape = {5: {"album": {"name": "Mixtape"}}}
        elsewhere = {5: {"album": {"name": "The Sound of Revenge"}}}
        self.assertEqual(judge(review, facts, on_mixtape)[:2], ("same", self.ids["Rock Star"]))
        self.assertEqual(judge(review, facts, elsewhere)[0], "different")
        self.assertEqual(judge(review, facts, {5: {"album": None}})[0], "review")

    def test_attach(self):
        recording = self.ids["Rock Star"]
        with self.c:
            attach_to_catalogued(self.c, song("Rock Star", "Chamillionaire", [recording],
                                              mb=[("mb-rs", 155000)], genius=[5]), recording)
            attach_to_catalogued(self.c, song("Motivation", "DJ Drama", [self.ids["Motivation"]], genius=[6]),
                                 self.ids["Motivation"])
        q = lambda sql, *a: self.c.execute(sql, a).fetchall()
        self.assertEqual(q("SELECT recording_id, musicbrainz_recording_id FROM recording_musicbrainz_ids"),
                         [(recording, "mb-rs")])
        self.assertEqual(q("SELECT genius_song_id FROM recordings WHERE id = ?", recording), [(5,)])
        self.assertEqual(q("SELECT a.name, c.role FROM credits c JOIN artists a ON a.id = c.artist_id "
                           "WHERE c.recording_id = ?", recording), [("Chamillionaire", "main_artist")])
        self.assertEqual(q("SELECT count(*) FROM credits WHERE recording_id = ?", self.ids["Motivation"]), [(0,)])
        self.assertEqual(q("PRAGMA foreign_key_check"), [])


if __name__ == "__main__":
    unittest.main()
