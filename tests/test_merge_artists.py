import sqlite3
import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import merge_artists


def fixture():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE artists (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE recordings (id INTEGER PRIMARY KEY, artist_id INTEGER);
        CREATE TABLE releases (id INTEGER PRIMARY KEY, artist_id INTEGER);
        CREATE TABLE credits (id INTEGER PRIMARY KEY, artist_id INTEGER, recording_id INTEGER, version_id INTEGER,
                              role TEXT, credited_as TEXT, source TEXT);
        INSERT INTO artists VALUES (1, 'T-Pain'), (2, 't-pain'), (3, 'T Pain'), (4, 'MIKE DEAN'), (5, 'Mike Dean'),
                                   (6, 'Bangladesh'), (7, 'Shondrae Crawford'), (8, 'Curren$y'), (9, 'Currensy'),
                                   (10, 'Drake');
        INSERT INTO credits (artist_id, recording_id, role, source) VALUES
            (1, 100, 'featured_artist', 'Genius'), (2, 100, 'featured_artist', 'Genius'),   -- same credit twice
            (3, 101, 'featured_artist', 'Discogs'),
            (4, 100, 'mixing_engineer', 'Discogs'), (4, 101, 'mixing_engineer', 'Discogs'), (5, 102, 'mixing_engineer', 'Genius'),
            (6, 100, 'producer', 'Genius'), (7, 100, 'producer', 'Discogs'), (7, 101, 'producer', 'Discogs'),
            (8, 103, 'featured_artist', 'Discogs'), (8, 104, 'featured_artist', 'Discogs'),
            (9, 103, 'featured_artist', 'Genius'), (10, 103, 'featured_artist', 'Genius');
        INSERT INTO recordings VALUES (200, 2);
    """)
    return c


class MergeArtistsTests(unittest.TestCase):
    def test_plan(self):
        merges = {name: [n for _i, n in drops] for _keep, name, drops in
                  merge_artists.plan(fixture(), [["Bangladesh", "Shondrae Crawford", "Discogs"]])}
        self.assertEqual(merges, {
            "T-Pain": ["t-pain", "T Pain"],            # tidy (not all lower case), then most used
            "Mike Dean": ["MIKE DEAN"],                # tidy over ALL CAPS although used less
            "Bangladesh": ["Shondrae Crawford"],       # the stage name stays
            "Curren$y": ["Currensy"],                  # "$" read as "s"
        })

    def test_apply(self):
        c = fixture()
        merge_artists.apply(c, merge_artists.plan(c, []))
        self.assertEqual(c.execute("SELECT count(*) FROM artists").fetchone()[0], 6)  # 10 - 2 T-Pain - 1 Mike Dean - 1 Currensy
        # the two identical T-Pain credits are one; the other spelling is kept in credited_as
        self.assertEqual(c.execute("SELECT recording_id, credited_as FROM credits WHERE artist_id = 1 ORDER BY recording_id").fetchall(),
                         [(100, None), (101, "T Pain")])  # the t-pain duplicate of recording 100 is gone
        self.assertEqual(c.execute("SELECT artist_id FROM recordings").fetchone()[0], 1)
        self.assertEqual(c.execute("SELECT DISTINCT artist_id FROM credits WHERE role = 'mixing_engineer'").fetchall(), [(5,)])


if __name__ == "__main__":
    unittest.main()
