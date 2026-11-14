import sys
import tempfile
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from release_media import is_image, link_rank, local_cover, service_of


class ReleaseMediaTests(unittest.TestCase):
    def test_services(self):
        self.assertEqual(service_of("https://open.spotify.com/album/1Do3y8IAcbYOToYQJnGwSO"), "spotify")
        self.assertEqual(service_of("https://music.apple.com/us/album/tha-carter-iii/1440737099"), "apple_music")
        self.assertEqual(service_of("https://itunes.apple.com/gb/album/id287553396"), "apple_music")
        self.assertEqual(service_of("https://music.youtube.com/playlist?list=X"), "youtube_music")
        self.assertEqual(service_of("https://www.deezer.com/album/213327"), "deezer")
        self.assertIsNone(service_of("https://example.com"))
        self.assertLess(link_rank("apple_music", "https://music.apple.com/us/album/x"),
                        link_rank("apple_music", "https://itunes.apple.com/gb/album/x"))

    def test_local_cover(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            jpeg = b"\xff\xd8\xff" + b"0" * 100
            (folder / "01-song.mp3").write_bytes(b"x")
            (folder / "back.jpg").write_bytes(jpeg * 3)
            (folder / "00-Release-COVER.jpg").write_bytes(jpeg)
            (folder / "inside.jpg").write_bytes(jpeg * 5)
            self.assertEqual(local_cover([str(folder / "01-song.mp3")]).name, "00-Release-COVER.jpg")
            self.assertIsNone(local_cover([str(folder / "missing" / "x.mp3")]))
        self.assertTrue(is_image(b"\x89PNG\r\n\x1a\nrest"))
        self.assertFalse(is_image(b"<html>"))


if __name__ == "__main__":
    unittest.main()
