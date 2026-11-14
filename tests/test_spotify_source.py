import contextlib
import email.message
import io
import sys
import unittest
import urllib.error
from unittest import mock
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import spotify_source
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


class RateLimitTests(unittest.TestCase):
    """A long Retry-After stops with RateLimited instead of sleeping for hours."""

    def fake_429(self, seconds):
        def urlopen(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests",
                                         email.message.Message() if seconds is None else self.headers(seconds), None)
        return urlopen

    @staticmethod
    def headers(seconds):
        message = email.message.Message()
        message["Retry-After"] = str(seconds)
        return message

    def test_long_wait_raises(self):
        sleeps = []
        with mock.patch.object(spotify_source, "access_token", return_value="t"),                 mock.patch.object(spotify_source.time, "sleep", sleeps.append),                 mock.patch.object(spotify_source.urllib.request, "urlopen", self.fake_429(36000)):
            with self.assertRaises(spotify_source.RateLimited) as error:
                spotify_source.api_get("/albums/x")
        self.assertEqual(error.exception.seconds, 36000)
        self.assertTrue(all(s < 1 for s in sleeps))  # never slept on the long wait

    def test_short_wait_retries(self):
        sleeps = []
        with mock.patch.object(spotify_source, "access_token", return_value="t"),                 mock.patch.object(spotify_source.time, "sleep", sleeps.append),                 mock.patch.object(spotify_source.urllib.request, "urlopen", self.fake_429(7)),                 contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(spotify_source.api_get("/albums/x", attempts=2))
        self.assertIn(8, sleeps)  # waited Retry-After + 1 once, then gave up


if __name__ == "__main__":
    unittest.main()
