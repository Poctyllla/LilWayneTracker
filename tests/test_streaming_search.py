import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from streaming_search import base_key, best_album


def track(n, title, url=None):
    return {"disc": 1, "number": n, "title": title, "url": url}


class StreamingSearchTests(unittest.TestCase):
    def test_base_key(self):
        self.assertEqual(base_key("Tha Carter V (Deluxe)"), base_key("Tha Carter V"))
        self.assertEqual(base_key("A Milli - Single"), base_key("A Milli"))
        self.assertEqual(base_key("Guerrilla Warfare (Explicit Version)"), base_key("Guerrilla Warfare"))
        self.assertNotEqual(base_key("Tha Carter IV"), base_key("Tha Carter V"))

    def test_best_album(self):
        ours = [track(1, "Intro"), track(2, "We On Fire"), track(3, "Respect My Mind"), track(4, "Help")]
        right = {"title": "Guerrilla Warfare", "url": "a", "tracks": [
            track(1, "Intro", "t1"), track(2, "We On Fire", "t2"), track(3, "Respect My Mind", "t3")]}
        wrong_title = {"title": "Let 'Em Burn", "url": "b", "tracks": right["tracks"]}
        thin = {"title": "Guerrilla Warfare", "url": "c", "tracks": [track(1, "Intro", "x")]}
        album, pairs = best_album("Guerrilla Warfare", ours, [wrong_title, thin, right])
        self.assertEqual(album["url"], "a")
        self.assertEqual(len(pairs), 3)
        self.assertEqual(best_album("Guerrilla Warfare", ours, [thin, wrong_title]), (None, []))  # < 60 %


if __name__ == "__main__":
    unittest.main()
