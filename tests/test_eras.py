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
from eras import album_eras, apply, earliest, earliest_era_of, era_of, plan_assignments


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
            (10, "A Milli", "single", "2008-04-28", "rg-amilli", "official"),  # first single of Tha Carter III
            (11, "Hot Revolver", "single", "2007-03-14", "rg-hotrevolver", "official"),  # before Tha Carter III
        ]
        for row in releases:
            self.c.execute("INSERT INTO releases (id, title, release_type, release_date, artist_id, "
                           "musicbrainz_release_group_id, discography) VALUES (?, ?, ?, ?, 1, ?, ?)",
                           (row[0], row[1], row[2], row[3], row[4], row[5]))
        self.songs = {}
        for title, release_ids, extra in [
                ("Hustler Musik", [1], {}), ("A Milli", [2, 10], {}), ("Pussy Monster", [3], {}),
                ("Hot Revolver", [7, 11], {}),  # on Rebirth (2010), but a single back in 2007
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
        # An era runs from its album (or the album's first single) until the next
        # project's first single; the latest era stays open (owner's decision, Nov 15, 2026).
        self.assertEqual([(e["name"], e["start_date"], e["end_date"]) for e in eras], [
            ("Before Tha Carter II", None, "2005-12-05"),  # one era before the first solo album (Hot Boys years)
            ("Tha Carter II", "2005-12-06", "2008-04-27"),
            ("Tha Carter III", "2008-04-28", "2010-02-01"),  # opened by its first single A Milli
            ("Rebirth", "2010-02-02", None),  # Hot Revolver (2007) predates Tha Carter III: not Rebirth's first single
        ])
        self.assertEqual(era_of(eras, "2008"), None)  # crosses the Tha Carter III start
        self.assertEqual(earliest_era_of(eras, "2008"), 2)  # most of 2008 is Tha Carter III (the owner's example)
        self.assertEqual(era_of(eras, "2009"), 2)
        self.assertEqual(earliest(["2008", "2008-06-10"]), "2008-06-10")

    def test_assignments(self):
        eras = album_eras(self.c)
        plan = plan_assignments(self.c, eras)
        by_title = {title: plan.get(recording) for title, recording in self.songs.items()}
        self.assertEqual(by_title["Hustler Musik"], (1, "album"))
        self.assertEqual(by_title["A Milli"], (2, "album"))
        self.assertEqual(by_title["Pussy Monster"], (2, "album"))  # later edition, same album
        self.assertEqual(by_title["Dough Is What I Got"], (1, "date"))  # 2007: still the Tha Carter II era
        self.assertEqual(by_title["Leak 2008"], (2, "year"))  # most of 2008: Tha Carter III
        self.assertEqual(by_title["Leak dated"], (1, "date"))
        self.assertEqual(by_title["Demo 2009"], (2, "date"))
        self.assertEqual(by_title["Hot Revolver"], (1, "date"))  # released officially before its album's era
        self.assertEqual(by_title["No date"], (None, None))
        self.assertIsNone(by_title["Manual"])
        with self.c:
            apply(self.c, eras, plan)
            apply(self.c, eras, plan)  # idempotent
        self.assertEqual(self.c.execute("SELECT count(*) FROM eras").fetchone()[0], 4)
        self.assertEqual(self.c.execute("SELECT e.name, r.era_source FROM recordings r JOIN eras e "
                                        "ON e.id = r.era_id WHERE r.id = ?",
                                        (self.songs["Dough Is What I Got"],)).fetchone(),
                         ("Tha Carter II", "date"))


if __name__ == "__main__":
    unittest.main()
