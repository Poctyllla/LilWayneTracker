import contextlib
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import database
from eras import album_eras, apply, earliest, era_of, plan_assignments


class EraTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self._path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self._directory.name) / "test.db"
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
        self.c = sqlite3.connect(database.DATABASE_PATH)
        self.c.execute("PRAGMA foreign_keys = ON")
        releases = [
            (1, "Tha Carter II", "studio_album", "2005-12-06", "rg-c2", "official"),
            (2, "Tha Carter III", "studio_album", "2008-06-10", "rg-c3", "official"),
            (3, "Tha Carter III", "studio_album", "2008-08-18", "rg-c3", "official"),  # later edition
            (4, "Da Drought 3", "mixtape", "2007-04-13", "rg-dd3", "official"),
            (5, "The Leak 4", "bootleg", "2008", None, "unofficial"),
            (6, "Rebirth", "studio_album", "2009", "rg-rebirth", "official"),
            (8, "Get It How U Live!!", "group_album", "1997-10-28", "rg-hotboys", "official"),
            (9, "Rich Gang", "group_album", "2013-07-23", "rg-richgang", "official"),  # after: no era
            (7, "Rebirth", "studio_album", "2010-02-02", "rg-rebirth", "official"),
        ]
        for row in releases:
            self.c.execute("INSERT INTO releases (id, title, release_type, release_date, artist_id, "
                           "musicbrainz_release_group_id, discography) VALUES (?, ?, ?, ?, 1, ?, ?)",
                           (row[0], row[1], row[2], row[3], row[4], row[5]))
        self.songs = {}
        for title, release_ids, extra in [
                ("Hustler Musik", [1], {}), ("A Milli", [2], {}), ("Pussy Monster", [3], {}),
                ("Dough Is What I Got", [4], {}), ("Leak 2008", [5], {}),
                ("Leak dated", [5], {"leak_date": "2007-05-01"}),
                ("Demo 2009", [], {"recording_date": "2009-03-01"}), ("No date", [], {}),
                ("Manual", [], {"era_source": "manual"})]:
            recording = self.c.execute(
                "INSERT INTO recordings (title, artist_id, leak_date, recording_date, era_source) "
                "VALUES (?, 1, ?, ?, ?)", (title, extra.get("leak_date"), extra.get("recording_date"),
                                           extra.get("era_source"))).lastrowid
            version = self.c.execute("INSERT INTO versions (recording_id, version_name, version_type) "
                                     "VALUES (?, 'Album Version', 'album')", (recording,)).lastrowid
            for number, release_id in enumerate(release_ids, start=1):
                self.c.execute("INSERT INTO release_tracks (release_id, version_id, disc_number, track_number) "
                               "VALUES (?, ?, 1, ?)", (release_id, version, number + recording))
            self.songs[title] = recording
        self.c.commit()

    def tearDown(self):
        self.c.close()
        database.DATABASE_PATH = self._path
        self._directory.cleanup()

    def test_eras(self):
        eras = album_eras(self.c)
        self.assertEqual([(e["name"], e["start_date"], e["end_date"]) for e in eras], [
            ("Get It How U Live!!", None, "1997-10-28"),  # group album before the first solo album
            ("Tha Carter II", "1997-10-29", "2005-12-06"),
            ("Tha Carter III", "2005-12-07", "2008-06-10"),
            ("Rebirth", "2008-06-11", "2010-02-02"),
            ("After Rebirth", "2010-02-03", None),
        ])
        self.assertEqual(era_of(eras, "2008"), None)  # crosses the Tha Carter III release
        self.assertEqual(era_of(eras, "2009"), 3)
        self.assertEqual(earliest(["2008", "2008-06-10"]), "2008-06-10")

    def test_assignments(self):
        eras = album_eras(self.c)
        plan = plan_assignments(self.c, eras)
        by_title = {title: plan.get(recording) for title, recording in self.songs.items()}
        self.assertEqual(by_title["Hustler Musik"], (1, "album"))
        self.assertEqual(by_title["A Milli"], (2, "album"))
        self.assertEqual(by_title["Pussy Monster"], (2, "album"))  # later edition, same album
        self.assertEqual(by_title["Dough Is What I Got"], (2, "date"))
        self.assertEqual(by_title["Leak 2008"], (2, "year"))  # earlier era of 2008: Tha Carter III
        self.assertEqual(by_title["Leak dated"], (2, "date"))
        self.assertEqual(by_title["Demo 2009"], (3, "date"))
        self.assertEqual(by_title["No date"], (None, None))
        self.assertIsNone(by_title["Manual"])
        with self.c:
            apply(self.c, eras, plan)
            apply(self.c, eras, plan)  # idempotent
        self.assertEqual(self.c.execute("SELECT count(*) FROM eras").fetchone()[0], 5)
        self.assertEqual(self.c.execute("SELECT e.name, r.era_source FROM recordings r JOIN eras e "
                                        "ON e.id = r.era_id WHERE r.id = ?",
                                        (self.songs["Dough Is What I Got"],)).fetchone(),
                         ("Tha Carter III", "date"))


if __name__ == "__main__":
    unittest.main()
