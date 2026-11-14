import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from song_crosscheck import CatalogueIndex, classify, collect_songs, similar, song_key


CATALOGUE = [
    (1, "A Milli", "released"),
    (2, "F**k Wit Me Now", "released"),
    (3, "Black Republican", "released"),
    (4, "Lisa Marie", "leaked"),
]
TRACKS = {"da drought 3": [(3, "Black Republican", "released")],
          "the prefix": [(5, "Lucifer", "released")]}


def hq(title, project="–", artists="Lil Wayne"):
    return {"Song Title": title, "Project": project, "Artists": artists}


def genius(title, genius_id=1):
    return {"genius_id": genius_id, "title": title, "url": "u", "primary_artist_id": 4}


class KeyTests(unittest.TestCase):
    def test_keys(self):
        self.assertEqual(song_key("A Milli (Mastered)"), song_key("A Milli"))
        self.assertNotEqual(song_key("A Milli [Demo]"), song_key("A Milli"))

    def test_similar(self):
        self.assertTrue(similar("Black Republicans", "Black Republican"))
        self.assertTrue(similar("Gucci Gucci", "Gucci Gucci (Freestyle)"))
        self.assertFalse(similar("Lucifer (Remix)", "Lucifer"))
        self.assertFalse(similar("Heat [Original]", "Heat"))
        self.assertFalse(similar("On Tha Block #1", "On tha Block #2 (skit)"))


class ClassifyTests(unittest.TestCase):
    def run_classify(self, genius_songs, hq_rows):
        songs, skipped = collect_songs(genius_songs, hq_rows)
        classify(songs, CatalogueIndex(CATALOGUE), TRACKS)
        return {song["title"]: song for song in songs.values()}, skipped

    def test_statuses(self):
        songs, skipped = self.run_classify(
            [genius("A Milli"), genius("Fuck Wit Me Now"), genius("Lisa Marie (Instrumental)")],
            [hq("A Milli [Demo]"), hq("Black Republicans", "Da Drought 3"), hq("Lucifer (Remix)", "The Prefix"),
             hq("Brand New Song"), hq("Some Hit", "X", "Nicki Minaj featuring Lil Wayne")])
        self.assertEqual(skipped, 1)
        self.assertEqual(songs["A Milli"]["status"], "in_catalogue")
        self.assertEqual(songs["Fuck Wit Me Now"]["catalogue"], [{"recording_id": 2, "availability": "released"}])
        self.assertEqual(songs["A Milli [Demo]"]["status"], "variant")
        self.assertEqual(songs["Black Republicans"]["status"], "probable")
        self.assertEqual(songs["Lucifer (Remix)"]["status"], "missing")
        self.assertEqual(songs["Brand New Song"]["status"], "missing")
        self.assertEqual(songs["Brand New Song"]["role"], "lead")
        self.assertEqual(songs["Some Hit"]["role"], "featured")

    def test_sources_merge(self):
        songs, _ = self.run_classify([genius("Brand New Song")], [hq("Brand New Song")])
        self.assertEqual(songs["Brand New Song"]["sources"], "genius+hq")


if __name__ == "__main__":
    unittest.main()
