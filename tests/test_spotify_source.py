import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from spotify_source import album_id, match_tracks


class SpotifySourceTests(unittest.TestCase):
    def test_album_id(self):
        self.assertEqual(album_id("https://open.spotify.com/album/1Do3y8IAcbYOToYQJnGwSO"), "1Do3y8IAcbYOToYQJnGwSO")
        self.assertEqual(album_id("https://open.spotify.com/intl-de/album/ABC123"), "ABC123")
        self.assertIsNone(album_id("https://music.apple.com/us/album/x/1"))

    def test_match_tracks(self):
        theirs = [{"disc": 1, "number": 1, "title": "3 Peat", "url": "s1"},
                  {"disc": 1, "number": 2, "title": "Mr. Carter", "url": "s2"},
                  {"disc": 1, "number": 9, "title": "Mrs. Officer", "url": "s9"}]
        ours = [{"disc": 1, "number": 1, "title": "3 Peat"}, {"disc": 1, "number": 2, "title": "Mr Carter"},
                {"disc": 1, "number": 5, "title": "Mrs. Officer"}, {"disc": 1, "number": 7, "title": "Bonus"}]
        self.assertEqual([(a["title"], b["url"]) for a, b in match_tracks(ours, theirs)],
                         [("3 Peat", "s1"), ("Mr Carter", "s2"), ("Mrs. Officer", "s9")])


if __name__ == "__main__":
    unittest.main()
