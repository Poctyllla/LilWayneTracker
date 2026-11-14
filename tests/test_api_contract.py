"""Contract of the local web API (stage 0 of the architecture migration).

Every route of api_routes.ROUTES is called on a small fixture database and
the *shape* of its answer (keys and value types, recursively; list items
merged) is compared with tests/api_contract.json.  Later refactorings
(api_routes.py, audio_providers.py, /api/v1) must keep these shapes.

After an intended change of the API, refresh the snapshot and review the diff:
    set LWT_UPDATE_CONTRACT=1 && python -m unittest tests.test_api_contract
"""

import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock


TESTS_DIR = Path(__file__).resolve().parent
SRC_DIR = TESTS_DIR.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import database
import genius_details
import lyrics_source
import review_queue
import api_routes
import tracker_data
import tracker_server

SNAPSHOT = TESTS_DIR / "api_contract.json"


def scalar(value):
    return "null" if value is None else type(value).__name__


def shape(value):
    """Keys and types of a JSON value; the items of a list are merged into one shape."""
    if isinstance(value, dict):
        return {key: shape(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        merged = None
        for item in value:
            merged = merge(merged, shape(item))
        return [merged] if merged is not None else []
    return scalar(value)


def merge(a, b):
    if a is None:
        return b
    if isinstance(a, dict) and isinstance(b, dict):
        return {key: merge(a.get(key), b.get(key)) if key in a and key in b else (a.get(key) if key in a else b[key])
                for key in sorted(set(a) | set(b))}
    if isinstance(a, list) and isinstance(b, list):
        inner = merge(a[0] if a else None, b[0] if b else None)
        return [inner] if inner is not None else []
    if isinstance(a, str) and isinstance(b, str):
        return "|".join(sorted(set(a.split("|")) | set(b.split("|"))))
    return f"{json.dumps(a, sort_keys=True)}|{json.dumps(b, sort_keys=True)}"  # mixed kinds: visible in the diff


def fake_lrclib(title, artist):
    return [{"id": 7, "duration": 221, "plainLyrics": "Young Money", "syncedLyrics": "[00:06.09] Young Money"}]


def build_fixture(folder):
    path = folder / "test.db"
    old = database.DATABASE_PATH
    database.DATABASE_PATH = path
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
    finally:
        database.DATABASE_PATH = old
    audio = folder / "a milli.mp3"
    audio.write_bytes(bytes(range(256)) * 4)
    c = sqlite3.connect(path)
    c.execute("INSERT INTO eras (id, name, start_date, end_date, summary, summary_url, cover_file) VALUES "
              "(1, 'Tha Carter III', '2005-12-07', '2008-06-10', 'Sixth album.', 'https://en.wikipedia.org/wiki/x', 'era_1.jpg')")
    c.execute("INSERT INTO releases (id, title, artist_id, release_type, release_date, discography, cover_file, "
              "musicbrainz_release_id, label) VALUES (1, 'Tha Carter III', 1, 'studio_album', '2008-06-10', 'official', "
              "'release_1.jpg', 'mb-release-1', 'Cash Money')")
    c.execute("INSERT INTO releases (id, title, artist_id, release_type, release_date, discography) "
              "VALUES (2, 'Old Bootleg', 1, 'bootleg', '2001-03-01', 'unofficial')")
    c.execute("INSERT INTO release_links (release_id, service, url) VALUES "
              "(1, 'spotify', 'https://open.spotify.com/album/x'), (1, 'apple_music', 'https://music.apple.com/us/album/y')")
    song = c.execute("INSERT INTO recordings (title, artist_id, era_id, genius_song_id) VALUES ('A Milli', 1, 1, 123)").lastrowid
    album = c.execute("INSERT INTO versions (recording_id, version_name, version_type) VALUES (?, 'Album Version', 'album')",
                      (song,)).lastrowid
    demo = c.execute("INSERT INTO versions (recording_id, version_name, version_type, first_known_date) "
                     "VALUES (?, 'Demo', 'demo', '2007-05-01')", (song,)).lastrowid
    c.execute("INSERT INTO release_tracks (release_id, version_id, disc_number, track_number, duration_ms) "
              "VALUES (1, ?, 1, 3, 221000)", (album,))
    c.execute("INSERT INTO release_tracks (release_id, version_id, disc_number, track_number, duration_ms, track_title) "
              "VALUES (2, ?, 1, 1, 222000, 'A Mili')", (album,))  # 7 years before the album: a "spread" question
    file_id = c.execute("INSERT INTO files (version_id, file_path, duration) VALUES (?, ?, 221.0)",
                        (album, str(audio))).lastrowid
    c.execute("INSERT INTO version_links (version_id, service, url) VALUES "
              "(?, 'spotify', 'https://open.spotify.com/track/0123456789abcdefABCDEF'), "
              "(?, 'apple_music', 'https://music.apple.com/us/album/tha-carter-iii/1?i=2')", (album, album))
    producer = c.execute("INSERT INTO artists (name) VALUES ('Bangladesh')").lastrowid
    c.execute("INSERT INTO artists (name) VALUES ('Drake')")
    c.execute("INSERT INTO credits (artist_id, recording_id, role, source) "
              "SELECT id, ?, 'producer', 'Genius' FROM artists WHERE name = 'Bangladesh'", (song,))
    c.execute("INSERT INTO credits (artist_id, version_id, role, source) "
              "SELECT id, ?, 'featured_artist', 'Discogs' FROM artists WHERE name = 'Drake'", (demo,))
    c.execute("INSERT INTO recording_aliases (recording_id, title) VALUES (?, 'A Mili')", (song,))
    source = c.execute("INSERT INTO sources (name, url) VALUES ('Genius', 'https://genius.com/Lil-wayne-a-milli-lyrics')").lastrowid
    c.execute("INSERT INTO recording_sources (recording_id, source_id) VALUES (?, ?)", (song, source))
    c.execute("INSERT INTO recordings (title, artist_id, appearance, availability_override) "
              "SELECT 'Forever', id, 'guest', 'released' FROM artists WHERE name = 'Drake'")
    c.commit()
    c.close()
    return path, {"song": song, "album": album, "file": file_id, "producer": producer}


class ApiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._directory = tempfile.TemporaryDirectory()
        folder = Path(cls._directory.name)
        cls.path, cls.ids = build_fixture(folder)
        profile = folder / "artist_profile.json"
        profile.write_text(json.dumps({"name": "Lil Wayne", "summary": "Rapper.", "summary_url": "https://en.wikipedia.org/x",
                                       "image": {"file": "artist.jpg", "credit": "x"},
                                       "links": [{"group": "Social", "service": "instagram", "url": "https://instagram.com/x"}]}),
                           encoding="utf-8")
        (folder / "exports").mkdir()
        cls.patches = [
            mock.patch.object(tracker_server, "PROFILE_PATH", profile),
            mock.patch.object(review_queue, "EXPORTS_FOLDER", folder / "exports"),
            mock.patch.object(genius_details, "load_cache", return_value={}),
            # LRCLIB answer without the network (urllib.request itself is shared with this test's requests)
            mock.patch.object(lyrics_source, "lookup", lambda connection, version_id, _real=lyrics_source.lookup:
                              _real(connection, version_id, fake_lrclib)),
        ]
        for patch in cls.patches:
            patch.start()
        lyrics_source.CACHE.clear()
        tracker_server.Handler.database_path = cls.path
        tracker_server.Handler.decisions_path = folder / "decisions.json"
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), tracker_server.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for patch in cls.patches:
            patch.stop()
        lyrics_source.CACHE.clear()
        tracker_server.Handler.decisions_path = None
        cls._directory.cleanup()

    def request(self, path, data=None, headers=None):
        request = urllib.request.Request(self.base + path, data=data, headers=headers or {})
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()

    def get_json(self, path):
        status, _headers, body = self.request(path)
        self.assertEqual(status, 200, path)
        return json.loads(body)

    def json_paths(self):
        ids = self.ids
        return {
            "/api/summary": None, "/api/songs": None, f"/api/song/{ids['song']}": "/api/song/<id>",
            "/api/releases": None, "/api/release/1": "/api/release/<id>", f"/api/artist/{ids['producer']}": "/api/artist/<id>",
            "/api/people": None, "/api/era/1": "/api/era/<id>", "/api/stats": None, "/api/profile": None,
            "/api/timeline": None, "/api/on-this-day/06-10": "/api/on-this-day/<MM-DD>",
            f"/api/lyrics/{ids['album']}": "/api/lyrics/<version id>", "/api/review": None,
        }

    def v1_paths(self):
        """{/api/v1 path: the old /api path it must answer exactly like}."""
        ids = self.ids
        return {
            "/api/v1/summary": "/api/summary", "/api/v1/songs": "/api/songs",
            f"/api/v1/songs/{ids['song']}": f"/api/song/{ids['song']}", "/api/v1/releases/": "/api/releases",
            "/api/v1/releases/1": "/api/release/1", "/api/v1/artists": "/api/people",
            f"/api/v1/artists/{ids['producer']}": f"/api/artist/{ids['producer']}", "/api/v1/eras/1": "/api/era/1",
            "/api/v1/stats": "/api/stats", "/api/v1/profile": "/api/profile", "/api/v1/timeline": "/api/timeline",
            "/api/v1/on-this-day/06-10": "/api/on-this-day/06-10",
            f"/api/v1/lyrics/{ids['album']}": f"/api/lyrics/{ids['album']}", "/api/v1/review": "/api/review",
        }

    def current_contract(self):
        contract = {}
        for path, name in self.json_paths().items():
            contract[f"GET {name or path}"] = shape(self.get_json(path))
        items = self.get_json("/api/review")
        decision = {"key": items[0]["key"], "action": "skip", "note": "contract test"}
        status, _headers, body = self.request("/api/review", json.dumps(decision).encode(),
                                              {"Content-Type": "application/json", "Origin": self.base})
        contract["POST /api/review"] = shape(json.loads(body))
        contract["GET /api/v1/"] = shape(self.get_json("/api/v1/"))
        contract["GET /api/v1/eras"] = shape(self.get_json("/api/v1/eras"))
        status, headers, body = self.request(f"/api/audio/{self.ids['file']}", headers={"Range": "bytes=0-99"})
        contract["GET /api/audio/<file id> (Range)"] = {
            "status": status, "Content-Type": headers["Content-Type"], "Accept-Ranges": headers["Accept-Ranges"],
            "Content-Range": headers["Content-Range"], "length": len(body)}
        return contract

    def test_every_route_is_covered(self):
        paths = list(self.json_paths()) + list(self.v1_paths()) + [
            "/api/v1/", "/api/v1", "/api/v1/eras", f"/api/audio/{self.ids['file']}", f"/api/v1/stream/{self.ids['file']}"]
        for route in api_routes.ROUTES:
            for pattern in route.patterns:
                self.assertTrue(any(pattern.match(path) for path in paths), f"no contract for {pattern.pattern}")

    def test_v1_answers_like_the_old_paths(self):
        for new, old in self.v1_paths().items():
            with self.subTest(new):
                self.assertEqual(self.get_json(new), self.get_json(old))
        new = self.request(f"/api/v1/stream/{self.ids['file']}", headers={"Range": "bytes=0-99"})
        old = self.request(f"/api/audio/{self.ids['file']}", headers={"Range": "bytes=0-99"})
        self.assertEqual((new[0], new[2], new[1]["Content-Range"]), (old[0], old[2], old[1]["Content-Range"]))

    def test_overview(self):
        overview = self.get_json("/api/v1")
        self.assertEqual((overview["version"], overview["mode"]), ("1", "local"))
        self.assertEqual(overview["features"], {"stream": True, "lyrics": True, "review": True})
        self.assertEqual({e["name"] for e in overview["endpoints"]}, set(api_routes.BY_NAME))
        connection = tracker_data.connect(self.path)
        try:
            static = api_routes.overview(connection, {"mode": "static"})
        finally:
            connection.close()
        self.assertEqual(static["features"], {"stream": False, "lyrics": False, "review": False})
        self.assertFalse(any(e["local_only"] for e in static["endpoints"]))

    def test_static_file_names(self):
        """export_static writes the v1 paths that web/api.js asks for (apiUrl)."""
        names = {name: route.file_name(5 if route.param == "id" else "06-10" if route.param else None)
                 for name, route in api_routes.BY_NAME.items()}
        self.assertEqual(names["overview"], "v1/index")
        self.assertEqual(names["song"], "v1/songs/5")
        self.assertEqual(names["artists"], "v1/artists")
        self.assertEqual(names["on_this_day"], "v1/on-this-day/06-10")
        for route in api_routes.ROUTES:  # every file name is the route's own v1 path
            self.assertTrue(route.patterns[0].match("/api/" + route.file_name(5 if route.param == "id" else "06-10")
                                                    .replace("v1/index", "v1/")), route.name)

    def test_post_review_on_v1(self):
        items = self.get_json("/api/v1/review")
        with self.assertRaises(urllib.error.HTTPError) as error:  # same checks as the old path
            self.request("/api/v1/review", b"{}", {"Content-Type": "text/plain"})
        self.assertEqual(error.exception.code, 415)
        self.assertIsInstance(items, list)

    def test_contract(self):
        contract = self.current_contract()
        if os.environ.get("LWT_UPDATE_CONTRACT") == "1" or not SNAPSHOT.exists():
            SNAPSHOT.write_text(json.dumps(contract, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            self.skipTest(f"snapshot written: {SNAPSHOT.name} -- review it")
        expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        self.assertEqual(sorted(contract), sorted(expected))
        for name in expected:
            with self.subTest(name):
                self.assertEqual(contract[name], expected[name])

    def test_errors(self):
        for path in ("/api/song/99999", "/api/nothing", "/api/audio/99999"):
            with self.subTest(path), self.assertRaises(urllib.error.HTTPError) as error:
                self.request(path)
            self.assertEqual(error.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(f"/api/audio/{self.ids['file']}", headers={"Range": "bytes=5000-"})
        self.assertEqual(error.exception.code, 416)


if __name__ == "__main__":
    unittest.main()
