"""The local web tracker: data, player sources, static export."""

from http.server import ThreadingHTTPServer
from pathlib import Path
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

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import audio_stream
import database
import tracker_data
import tracker_server
import audio_providers
import export_static
from export_static import without_local_files


# ---------- tracker ----------


tracker_data.ARTIST_IMAGES_PATH = Path(tempfile.gettempdir()) / "lwt-test-no-artist-images.json"


tracker_data.SONG_IMAGES_PATH = Path(tempfile.gettempdir()) / "lwt-test-no-song-images.json"


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


class SingleEraTests(unittest.TestCase):
    """A single from a solo studio album belongs to that album's era (Mrs. Officer -> Tha Carter III)."""

    def test_single_album_eras(self):
        connection = sqlite3.connect(":memory:")
        connection.executescript("""
            CREATE TABLE releases (id INTEGER PRIMARY KEY, release_type TEXT);
            CREATE TABLE recordings (id INTEGER PRIMARY KEY, era_id INTEGER, era_source TEXT);
            CREATE TABLE versions (id INTEGER PRIMARY KEY, recording_id INTEGER, version_type TEXT);
            CREATE TABLE release_tracks (id INTEGER PRIMARY KEY, release_id INTEGER, version_id INTEGER);
            INSERT INTO releases VALUES (1, 'single'), (2, 'single'), (3, 'studio_album'), (4, 'single');
            INSERT INTO recordings VALUES (10, 6, 'album'), (11, 7, 'date');
            INSERT INTO versions VALUES (100, 10, 'album'), (101, 11, 'album'), (102, 10, 'remix');
            INSERT INTO release_tracks (release_id, version_id) VALUES (1, 100), (2, 101), (3, 100), (4, 102);
        """)
        # 2: no album song; 3: not a single; 4: a remix single (A Milli remixes, 2023) is not the album's
        self.assertEqual(tracker_data.single_album_eras(connection), {1: 6})


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


# ---------- audio_providers ----------


def fixture():
    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        CREATE TABLE versions (id INTEGER PRIMARY KEY, recording_id INTEGER, version_type TEXT);
        CREATE TABLE version_links (id INTEGER PRIMARY KEY, version_id INTEGER, service TEXT, url TEXT);
        CREATE TABLE files (id INTEGER PRIMARY KEY, version_id INTEGER, file_path TEXT, bitrate INTEGER);
        INSERT INTO versions VALUES (1, 10, 'demo'), (2, 10, 'album'), (3, 20, 'album');
        INSERT INTO version_links (version_id, service, url) VALUES
            (2, 'spotify', 'https://open.spotify.com/track/0123456789abcdefABCDEF'),
            (2, 'apple_music', 'https://music.apple.com/us/album/x/1?i=2'),
            (2, 'apple_music', 'https://music.apple.com/us/album/x/1'),      -- album page: not playable
            (3, 'youtube', 'https://www.youtube.com/watch?v=abc');
        INSERT INTO files (version_id, file_path, bitrate) VALUES (1, 'C:/m/demo.mp3', 128), (1, 'C:/m/demo.flac', 900),
                                                                (3, 'C:/m/cover.jpg', NULL);
    """)
    return connection


class AudioProviderTests(unittest.TestCase):
    def test_registry(self):
        self.assertEqual([p.name for p in audio_providers.PROVIDERS], ["spotify", "apple_music", "local_file"])
        self.assertEqual({p.name: p.public for p in audio_providers.PROVIDERS},
                         {"spotify": True, "apple_music": True, "local_file": False})
        self.assertEqual(audio_providers.private_keys(), {"file_id"})

    def test_playables_per_provider(self):
        play = audio_providers.playables(fixture())
        self.assertEqual(play, {
            1: {"file_id": 2},  # the better file (higher bitrate) first; the cover image is no audio
            2: {"spotify": "0123456789abcdefABCDEF", "apple": "https://music.apple.com/us/album/x/1?i=2"},
        })
        songs = audio_providers.song_play(fixture(), play)
        self.assertEqual(songs[10]["v"], 2)  # the album version before the demo
        self.assertNotIn(20, songs)

    def test_a_new_provider_needs_no_other_change(self):
        class YouTubeProvider(audio_providers.LinkProvider):
            name, key, service = "youtube", "youtube", "youtube"

            def value(self, url):
                return url.rsplit("v=", 1)[-1]

        providers = audio_providers.PROVIDERS + [YouTubeProvider()]
        play = audio_providers.playables(fixture(), providers)
        self.assertEqual(play[3], {"youtube": "abc"})
        self.assertEqual(audio_providers.private_keys(providers), {"file_id"})

    def test_sources_of_one_version(self):
        connection = fixture()
        self.assertEqual(audio_providers.sources(connection, 2), [
            {"provider": "spotify", "key": "spotify", "kind": "embed", "public": True, "ref": "0123456789abcdefABCDEF"},
            {"provider": "apple_music", "key": "apple", "kind": "embed", "public": True,
             "ref": "https://music.apple.com/us/album/x/1?i=2"}])
        self.assertEqual(audio_providers.sources(connection, 1), [
            {"provider": "local_file", "key": "file_id", "kind": "stream", "public": False,
             "url": "/api/v1/stream/2", "supports_range": True}])  # the better file (higher bitrate)
        self.assertEqual(audio_providers.sources(connection, 1, public_only=True), [])
        self.assertEqual(audio_providers.sources(connection, 3), [])  # only an unknown service and a cover image
        self.assertIsNone(audio_providers.sources(connection, 99))

    def test_static_copy_drops_private_sources(self):
        data = {"play": {"v": 2, "spotify": "x", "apple": "y", "file_id": 7}}
        self.assertEqual(export_static.without_local_files(data), {"play": {"v": 2, "spotify": "x", "apple": "y"}})

    def test_resolve_only_existing_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            song = Path(folder) / "a.mp3"
            song.write_bytes(b"x")
            connection = sqlite3.connect(":memory:")
            connection.executescript("CREATE TABLE files (id INTEGER PRIMARY KEY, version_id INTEGER, file_path TEXT);")
            connection.executemany("INSERT INTO files VALUES (?, 1, ?)",
                                   [(1, str(song)), (2, str(Path(folder) / "gone.mp3")), (3, str(Path(folder) / "a.txt"))])
            self.assertEqual(audio_providers.audio_file(connection, 1), (song, "audio/mpeg"))
            for missing in (2, 3, 99):
                self.assertIsNone(audio_providers.audio_file(connection, missing))


# ---------- export_static ----------


class ExportStaticTests(unittest.TestCase):
    def test_local_library_facts_are_dropped(self):
        data = {"songs": [{"id": 1, "file": True, "title": "A Milli"}],
                "versions": [{"files": 3, "tracks": [{"release": "Tha Carter III"}]}],
                "files": [{"file_path": "C:/Users/x/Downloads/a.mp3"}]}
        self.assertEqual(without_local_files(data), {
            "songs": [{"id": 1, "file": False, "title": "A Milli"}],
            "versions": [{"files": 0, "tracks": [{"release": "Tha Carter III"}]}]})

    def test_player_file_ids_are_dropped(self):
        data = {"play": {"v": 7, "spotify": "abc1234567", "file_id": 271}}
        self.assertEqual(without_local_files(data), {"play": {"v": 7, "spotify": "abc1234567"}})


if __name__ == "__main__":
    unittest.main()
