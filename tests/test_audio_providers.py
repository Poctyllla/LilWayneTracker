import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import audio_providers
import export_static


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


if __name__ == "__main__":
    unittest.main()
