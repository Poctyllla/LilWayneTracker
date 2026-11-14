import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from version_types import split_version


class SplitVersionTests(unittest.TestCase):
    def test_versions(self):
        cases = {
            "A Milli (Demo)": ("A Milli", "demo", "Demo"),
            "A Milli [Demo]": ("A Milli", "demo", "Demo"),
            "Cascades (Snippet)": ("Cascades", "snippet", "Snippet"),
            "30 Minutes to New Orleans (Low Quality Snippet)":
                ("30 Minutes to New Orleans", "snippet", "Low Quality Snippet"),
            "Forever (Remix) (Snippet)": ("Forever", "snippet", "Remix (Snippet)"),
            "Heat [Original]": ("Heat", "original", "Original"),
            "A Millie(Original Version)": ("A Millie", "original", "Original Version"),
            "Mr. Carter (Alternate Version)": ("Mr. Carter", "alternate", "Alternate Version"),
            "Amillie (C3 Version)": ("Amillie", "alternate", "C3 Version"),
            "Fireman [Unreleased Verse]": ("Fireman", "alternate", "Unreleased Verse"),
            "Fireman (Live In Los Angeles)": ("Fireman", "live", "Live In Los Angeles"),
            "Hot Revolver (Extended)": ("Hot Revolver", "extended", "Extended"),
            "Lollipop (Remix)": ("Lollipop", "remix", "Remix"),
            "Pick & Roll (Unreleased Version)": ("Pick & Roll", "original", "Unreleased Version"),
        }
        for title, expected in cases.items():
            with self.subTest(title):
                self.assertEqual(split_version(title), expected)

    def test_same_song(self):
        self.assertEqual(split_version("Lollipop"), ("Lollipop", None, None))
        self.assertEqual(split_version("Dreamz (Prod. By M&D) (Bonus Track)"), ("Dreamz", None, None))
        self.assertEqual(split_version("Everybody Knows Me (Official Dirty Version)"),
                         ("Everybody Knows Me", None, None))
        self.assertEqual(split_version("Pretty Brown (Unreleased)"), ("Pretty Brown", None, None))

    def test_own_songs(self):
        for title in ("Sure Thing (Freestyle)", "Hands Up (My Last) (Freestyle)", "On tha Block #2 (skit)",
                      "Run the World (Girls) (Freestyle) / Outro", "(Snippet)"):
            with self.subTest(title):
                self.assertEqual(split_version(title), (title, None, None))
        self.assertEqual(split_version("Hands Up (My Last) (Snippet)"), ("Hands Up (My Last)", "snippet", "Snippet"))


if __name__ == "__main__":
    unittest.main()
