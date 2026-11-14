import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from discogs_credits import artist_name, in_range, match_edition, position_number, roles, track_credits


RELEASE = {
    "extraartists": [
        {"name": "Dwayne Carter", "role": "Executive-Producer", "tracks": ""},
        {"name": "Mr. Scott Design", "role": "Art Direction", "tracks": ""},
        {"name": "Vlado Meller", "role": "Mastered By", "tracks": ""},
        {"name": "Fabian Marasciullo", "role": "Mixed By", "tracks": "1 to 3, 7"},
        {"name": "Dwayne Carter", "role": "Written-By", "tracks": "2"},
    ],
    "tracklist": [
        {"position": "1", "title": "3 Peat", "type": "track", "extraartists": [
            {"name": "Maestro (3)", "role": "Producer", "tracks": ""}]},
        {"position": "2", "title": "Mr. Carter", "type": "track", "extraartists": [
            {"name": "Jay-Z", "role": "Featuring", "tracks": ""},
            {"name": "Infamous", "role": "Producer [Additional], Co-producer", "tracks": ""}]},
        {"position": "", "title": "Bonus", "type": "heading", "extraartists": []},
    ],
}


class DiscogsCreditTests(unittest.TestCase):
    def test_helpers(self):
        self.assertEqual(artist_name("Bryan Williams (2)"), "Birdman")  # Birdman's real name
        self.assertEqual(artist_name("Baby"), "Birdman")
        self.assertEqual(artist_name("Mannie Fresh (3)"), "Mannie Fresh")
        self.assertEqual(artist_name("Dwayne Carter"), "Lil Wayne")
        self.assertEqual(roles("Producer [Additional], Mixed By"), ["producer", "mixing_engineer"])
        self.assertEqual(roles("A&R [Direction]"), [])
        self.assertEqual(position_number("2-5"), (2, 5))
        self.assertEqual(position_number("7"), (1, 7))
        self.assertIsNone(position_number("B2"))
        self.assertTrue(in_range("2", "1 to 3, 7"))
        self.assertTrue(in_range("7", "1 to 3, 7"))
        self.assertFalse(in_range("5", "1 to 3, 7"))
        self.assertTrue(in_range("A2", "A1 to B2"))

    def test_track_credits(self):
        self.assertEqual(track_credits(RELEASE["tracklist"][1], RELEASE), [
            ("Jay-Z", "featured_artist"), ("Infamous", "producer"), ("Infamous", "co_producer"),
            ("Vlado Meller", "mastering_engineer"), ("Fabian Marasciullo", "mixing_engineer"),
            ("Lil Wayne", "writer")])

    def test_match_edition(self):
        ours = [{"disc": 1, "number": 1, "title": "3 Peat"}, {"disc": 1, "number": 2, "title": "Mr. Carter"},
                {"disc": 1, "number": 9, "title": "Something Else"}]
        pairs = match_edition(ours, RELEASE)
        self.assertEqual([(a["title"], b["title"]) for a, b in pairs], [("3 Peat", "3 Peat"), ("Mr. Carter", "Mr. Carter")])


if __name__ == "__main__":
    unittest.main()
