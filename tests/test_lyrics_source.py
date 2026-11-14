import sqlite3
import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import lyrics_source


class LyricsSourceTests(unittest.TestCase):
    def setUp(self):
        lyrics_source.CACHE.clear()

    def test_parse_lrc(self):
        text = "[ar:Lil Wayne]\n[00:06.09] Young Money!\n[01:02.5][02:10.00]Hook\nno stamp"
        self.assertEqual(lyrics_source.parse_lrc(text),
                         [[6.09, "Young Money!"], [62.5, "Hook"], [130.0, "Hook"]])
        self.assertEqual(lyrics_source.parse_lrc(None), [])

    def test_choose_prefers_synced_within_the_length(self):
        results = [
            {"id": 1, "duration": 222, "plainLyrics": "a"},
            {"id": 2, "duration": 228, "plainLyrics": "a", "syncedLyrics": "[00:01.00]a"},
            {"id": 3, "duration": 300, "plainLyrics": "a", "syncedLyrics": "[00:01.00]a"},  # other song
            {"id": 4, "duration": 221, "instrumental": True},
        ]
        self.assertEqual(lyrics_source.choose(results, 221)["id"], 2)
        self.assertIsNone(lyrics_source.choose(results[2:], 221))
        self.assertEqual(lyrics_source.choose(results[:1], None)["id"], 1)  # length unknown

    def test_lookup_uses_the_catalogue_and_caches(self):
        connection = sqlite3.connect(":memory:")
        connection.executescript("""
            CREATE TABLE artists (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE recordings (id INTEGER PRIMARY KEY, title TEXT, artist_id INTEGER);
            CREATE TABLE versions (id INTEGER PRIMARY KEY, recording_id INTEGER, version_type TEXT);
            CREATE TABLE release_tracks (id INTEGER PRIMARY KEY, version_id INTEGER, duration_ms INTEGER);
            CREATE TABLE files (id INTEGER PRIMARY KEY, version_id INTEGER, duration REAL);
            INSERT INTO artists VALUES (1, 'Lil Wayne');
            INSERT INTO recordings VALUES (1, 'Mrs. Officer (feat. Bobby V. & Kidd Kidd)', 1);
            INSERT INTO versions VALUES (5, 1, 'album');
            INSERT INTO release_tracks VALUES (1, 5, 286000);
        """)
        calls = []

        def fetcher(title, artist):
            calls.append((title, artist))
            return [{"id": 9, "duration": 287, "plainLyrics": "x", "syncedLyrics": "[00:12.00] Woo"}]

        result = lyrics_source.lookup(connection, 5, fetcher)
        self.assertEqual(calls, [("Mrs. Officer", "Lil Wayne")])  # featuring dropped from the query
        self.assertEqual(result["synced"], [[12.0, "Woo"]])
        lyrics_source.lookup(connection, 5, fetcher)
        self.assertEqual(len(calls), 1)  # memory cache
        self.assertIsNone(lyrics_source.lookup(connection, 99, fetcher))


if __name__ == "__main__":
    unittest.main()
