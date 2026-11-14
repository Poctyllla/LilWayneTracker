import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import audio_stream
import database
import tracker_data
import tracker_server


class TrackerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._directory = tempfile.TemporaryDirectory()
        cls.path = Path(cls._directory.name) / "test.db"
        old = database.DATABASE_PATH
        database.DATABASE_PATH = cls.path
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                database.initialize_database()
        finally:
            database.DATABASE_PATH = old
        c = sqlite3.connect(cls.path)
        c.execute("INSERT INTO eras (id, name, end_date) VALUES (1, 'Tha Carter III', '2008-06-10')")
        c.execute("INSERT INTO releases (id, title, artist_id, release_type, release_date, discography, cover_file) "
                  "VALUES (1, 'Tha Carter III', 1, 'studio_album', '2008-06-10', 'official', 'release_1.jpg')")
        c.execute("INSERT INTO release_links (release_id, service, url) VALUES "
                  "(1, 'spotify', 'https://open.spotify.com/album/x'), (1, 'apple_music', 'https://music.apple.com/us/album/y')")
        song = c.execute("INSERT INTO recordings (title, artist_id, era_id) VALUES ('A Milli', 1, 1)").lastrowid
        album = c.execute("INSERT INTO versions (recording_id, version_name, version_type) "
                          "VALUES (?, 'Album Version', 'album')", (song,)).lastrowid
        c.execute("INSERT INTO versions (recording_id, version_name, version_type) VALUES (?, 'Demo', 'demo')", (song,))
        c.execute("INSERT INTO release_tracks (release_id, version_id, disc_number, track_number, duration_ms) "
                  "VALUES (1, ?, 1, 3, 221000)", (album,))
        c.execute("INSERT INTO files (version_id, file_path) VALUES (?, 'C:/music/a milli.mp3')", (album,))
        c.execute("INSERT INTO version_links (version_id, service, url) VALUES (?, 'spotify', "
                  "'https://open.spotify.com/track/amilli')", (album,))
        c.execute("INSERT INTO artists (name) VALUES ('Bangladesh'), ('Drake')")
        c.execute("INSERT INTO credits (artist_id, recording_id, role, source) "
                  "SELECT id, ?, 'producer', 'Genius' FROM artists WHERE name = 'Bangladesh'", (song,))
        c.execute("INSERT INTO recording_aliases (recording_id, title) VALUES (?, 'A Mili')", (song,))
        c.execute("INSERT INTO recordings (title, artist_id, appearance, availability_override) "
                  "SELECT 'Forever', id, 'guest', 'released' FROM artists WHERE name = 'Drake'")
        c.commit()
        c.close()
        cls.song_id = song
        tracker_server.Handler.database_path = cls.path
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), tracker_server.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls._directory.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as response:
            return response.status, response.read()

    def test_data(self):
        connection = tracker_data.connect(self.path)
        try:
            summary = tracker_data.summary(connection)
            self.assertEqual(summary["counts"]["songs"], 1)
            self.assertEqual(summary["counts"]["guest_songs"], 1)
            self.assertEqual(summary["eras"][0]["songs"], 1)
            songs = {s["title"]: s for s in tracker_data.songs(connection)}
            self.assertEqual(songs["A Milli"]["types"], ["demo"])
            self.assertTrue(songs["A Milli"]["file"])
            self.assertEqual(songs["A Milli"]["aliases"], ["A Mili"])
            self.assertTrue(songs["Forever"]["guest"])
            song = tracker_data.song(connection, self.song_id)
            self.assertEqual([v["version_type"] for v in song["versions"]], ["album", "demo"])
            self.assertEqual(song["versions"][0]["tracks"][0]["track_number"], 3)
            self.assertEqual(song["versions"][1]["availability"], "unreleased")
            self.assertEqual(song["credits"]["producer"][0]["name"], "Bangladesh")
            self.assertIsNone(tracker_data.song(connection, 99999))
            self.assertEqual(songs["A Milli"]["producers"], ["Bangladesh"])
            self.assertEqual(songs["A Milli"]["cover"], "release_1.jpg")
            self.assertIsNone(songs["Forever"]["cover"])
            self.assertEqual(song["cover_file"], "release_1.jpg")
            self.assertEqual([l["service"] for l in song["listen"]], ["spotify", "apple_music"])
            self.assertEqual(song["listen"][0]["url"], "https://open.spotify.com/track/amilli")  # track beats album
            self.assertEqual(song["versions"][0]["links"][0]["service"], "spotify")
            self.assertEqual(len(tracker_data.release(connection, 1)["listen"]), 2)
            era = tracker_data.era(connection, 1)
            self.assertEqual([r["title"] for r in era["releases"]], ["Tha Carter III"])
            self.assertEqual([s["title"] for s in era["songs"]], ["A Milli"])
            self.assertEqual(era["producers"][0]["name"], "Bangladesh")
            self.assertIsNone(tracker_data.era(connection, 99))
            stats = tracker_data.stats(connection)
            self.assertEqual(stats["by_era"][0]["released"], 1)
            self.assertEqual(stats["by_year"], [{"year": "2008", "released": 1, "leaked": 0}])
            self.assertEqual(stats["versions"], {"demo": 1})
            timeline = tracker_data.timeline(connection)
            self.assertEqual([(y["year"], [r["title"] for r in y["releases"]]) for y in timeline],
                             [("2008", ["Tha Carter III"])])
            self.assertEqual([e["name"] for e in timeline[0]["eras"]], ["Tha Carter III"])
            self.assertEqual([r["title"] for r in tracker_data.on_this_day(connection, "06-10")], ["Tha Carter III"])
            self.assertEqual(tracker_data.on_this_day(connection, "01-01"), [])
            years = {p["name"]: p["years"] for p in tracker_data.people(connection)}
            self.assertEqual(years["Bangladesh"], ["2008", "2008"])
            people = {p["name"]: p for p in tracker_data.people(connection)}
            self.assertEqual(people["Bangladesh"]["songs"], 1)
            self.assertNotIn("Lil Wayne", people)
            person = tracker_data.artist(connection, people["Bangladesh"]["id"])
            self.assertEqual([(s["title"], s["roles"]) for s in person["songs"]], [("A Milli", ["producer"])])
            drake = connection.execute("SELECT id FROM artists WHERE name = 'Drake'").fetchone()[0]
            self.assertEqual([s["title"] for s in tracker_data.artist(connection, drake)["songs"]], ["Forever"])
            self.assertEqual(song["credits"]["producer"][0]["id"], people["Bangladesh"]["id"])
            self.assertEqual(tracker_data.release(connection, 1)["tracks"][0]["title"], "A Milli")
        finally:
            connection.close()

    def test_server(self):
        status, body = self.get("/api/songs")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)), 2)
        status, body = self.get(f"/api/song/{self.song_id}")
        self.assertEqual(json.loads(body)["title"], "A Milli")
        status, body = self.get("/")
        self.assertIn(b"Lil Wayne Tracker", body)
        for path in ("/api/song/99999", "/api/nothing", "/../src/tracker_server.py", "/..%2f..%2fdata/lilwayne.db"):
            with self.subTest(path), self.assertRaises(urllib.error.HTTPError) as error:
                self.get(path)
            self.assertEqual(error.exception.code, 404)


class FirstEditionTests(unittest.TestCase):
    """An album belongs to the era of its first edition, not of a later reissue."""

    def test_first_editions(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript("""
            CREATE TABLE releases (id INTEGER PRIMARY KEY, title TEXT, release_type TEXT, release_date TEXT,
                                   discography TEXT, cover_file TEXT, musicbrainz_release_group_id TEXT);
            INSERT INTO releases VALUES
              (1, 'Tha Carter V', 'studio_album', '2018-09-28', 'official', NULL, 'g5'),
              (2, 'Tha Carter V (deluxe edition)', 'studio_album', '2020-09-25', 'official', NULL, 'g5'),
              (3, 'Rich Gang', 'group_album', '2013', 'official', NULL, 'rg'),
              (4, 'Rich Gang', 'group_album', '2013-07-23', 'official', NULL, 'rg'),
              (5, 'Glory', 'single', NULL, 'official', NULL, NULL),
              (6, 'Bootleg', 'bootleg', '2008-01-01', 'unofficial', NULL, NULL),
              (7, 'Single', 'single', '2007', 'official', NULL, NULL);
        """)
        firsts = {r["id"]: r["release_date"] for r in tracker_data.first_editions(connection)}
        self.assertEqual(firsts, {1: "2018-09-28", 4: "2013-07-23", 7: "2007"})


class PlayerTests(unittest.TestCase):
    """Player data and the local audio route (owner's library, 127.0.0.1 only)."""

    @classmethod
    def setUpClass(cls):
        cls._directory = tempfile.TemporaryDirectory()
        folder = Path(cls._directory.name)
        cls.path = folder / "test.db"
        cls.audio = folder / "a milli.mp3"
        cls.audio.write_bytes(bytes(range(256)) * 4)  # 1024 bytes
        (folder / "cover.jpg").write_bytes(b"jpg")
        old = database.DATABASE_PATH
        database.DATABASE_PATH = cls.path
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                database.initialize_database()
        finally:
            database.DATABASE_PATH = old
        c = sqlite3.connect(cls.path)
        song = c.execute("INSERT INTO recordings (title, artist_id) VALUES ('A Milli', 1)").lastrowid
        demo = c.execute("INSERT INTO versions (recording_id, version_name, version_type) VALUES (?, 'Demo', 'demo')",
                         (song,)).lastrowid
        album = c.execute("INSERT INTO versions (recording_id, version_name, version_type) VALUES (?, 'Album', 'album')",
                          (song,)).lastrowid
        leak = c.execute("INSERT INTO recordings (title, artist_id) VALUES ('Leak', 1)").lastrowid
        leak_version = c.execute("INSERT INTO versions (recording_id, version_type) VALUES (?, 'album')", (leak,)).lastrowid
        c.execute("INSERT INTO version_links (version_id, service, url) VALUES "
                  "(?, 'spotify', 'https://open.spotify.com/track/0123456789abcdefABCDEF'), "
                  "(?, 'apple_music', 'https://music.apple.com/us/album/tha-carter-iii/1?i=2&uo=4')", (album, album))
        cls.file_id = c.execute("INSERT INTO files (version_id, file_path) VALUES (?, ?)",
                                (demo, str(cls.audio))).lastrowid
        cls.cover_id = c.execute("INSERT INTO files (version_id, file_path) VALUES (?, ?)",
                                 (leak_version, str(folder / "cover.jpg"))).lastrowid
        c.commit()
        c.close()
        cls.song, cls.album, cls.demo, cls.leak = song, album, demo, leak
        tracker_server.Handler.database_path = cls.path
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), tracker_server.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls._directory.cleanup()

    def test_playables(self):
        connection = tracker_data.connect(self.path)
        try:
            play = tracker_data.playables(connection)
            self.assertEqual(play[self.album], {"spotify": "0123456789abcdefABCDEF",
                                                "apple": "https://music.apple.com/us/album/tha-carter-iii/1?i=2&uo=4"})
            self.assertEqual(play[self.demo], {"file_id": self.file_id})
            songs = tracker_data.song_play(connection, play)
            self.assertEqual(songs[self.song]["v"], self.album)  # the album version first
            self.assertNotIn(self.leak, songs)  # a cover image is not audio
        finally:
            connection.close()

    def test_parse_range(self):
        self.assertIsNone(audio_stream.parse_range(None, 100))
        self.assertEqual(audio_stream.parse_range("bytes=0-", 100), (0, 99))
        self.assertEqual(audio_stream.parse_range("bytes=10-19", 100), (10, 19))
        self.assertEqual(audio_stream.parse_range("bytes=-30", 100), (70, 99))
        self.assertEqual(audio_stream.parse_range("bytes=90-500", 100), (90, 99))
        for bad in ("bytes=100-", "bytes=5-2", "items=0-1", "bytes=-"):
            with self.assertRaises(ValueError):
                audio_stream.parse_range(bad, 100)

    def test_audio_route(self):
        status, body = self.get(f"/api/audio/{self.file_id}")
        self.assertEqual((status, body), (200, self.audio.read_bytes()))
        request = urllib.request.Request(f"{self.base}/api/audio/{self.file_id}", headers={"Range": "bytes=100-199"})
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.headers["Content-Range"], "bytes 100-199/1024")
            self.assertEqual(response.headers["Content-Type"], "audio/mpeg")
            self.assertEqual(response.read(), self.audio.read_bytes()[100:200])
        for missing in (self.cover_id, 9999):  # not audio / unknown id
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.get(f"/api/audio/{missing}")
            self.assertEqual(error.exception.code, 404)

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as response:
            return response.status, response.read()


if __name__ == "__main__":
    unittest.main()
