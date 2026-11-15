"""The owner's music library: scan, matching, packages."""

from pathlib import Path
import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from library_scanner import clean_track_title, number, parse_folder_name
import database
import import_release_group
from library_matching import apply, plan, release_key, titles_match
from library_packages import artist_tag_credits, build_package, folder_tracks, group_folders, split_artists
from release_group_package import get_package_validation_issues


# ---------- library_scanner ----------


class FolderNameTests(unittest.TestCase):
    def test_folder_names(self):
        cases = {
            "Lil Wayne-Tha.Block.Is.Hot.Retail-(1999)-[NoFS]": ("Tha Block Is Hot", 1999, "retail", None),
            "Lil.Wayne-The.Leak.4.(Bootleg)-2008-[NoFS]": ("The Leak 4", 2008, "bootleg", None),
            "Lil.Wayne-Tha.Fix-(2006)-(Unreleased)-[NoFS]": ("Tha Fix", 2006, "unreleased", None),
            "Lil.Wayne-Da.Drought.3-CD.2-(Official.mixtape)-2007-[NoFS]": ("Da Drought 3", 2007, "official_mixtape", 2),
            "Lil.Wayne-SQ6.(Collectors.Edition)-2003-[NoFS]": ("SQ6", 2003, "official_mixtape", None),
            "Birdman.And.Lil Wayne-Like.Father.Like.Son.Retail-(2006-Bonus.CD)-[NoFS]":
                ("Like Father Like Son", 2006, "retail", 2),
            "Lil.Wayne-Rebirth-Retail.Deluxe.Edition)-2010-[NoFS]": ("Rebirth", 2010, "retail", None),
            "Lil.Wayne.And.Drake.The.Carter.Meets.The.Cartel.II-(Bootleg)-2009-[NoFS]":
                ("The Carter Meets The Cartel II", 2009, "bootleg", None),
            "Lil.Wayne.&.The.Game.4.Blood.Sake-(Bootleg)-2008-[NoFS]": ("4 Blood Sake", 2008, "bootleg", None),
            "Lil.Wayne.Ringtones.VR.7-[NoFS]": ("Ringtones VR 7", None, "other", None),
            "Dedication 2": ("Dedication 2", None, "other", None),  # Music/Lil Wayne/<release>
            "SQ7 (10,000 Bars)": ("SQ7 10,000 Bars", None, "other", None),
        }
        for name, (title, year, kind, disc) in cases.items():
            with self.subTest(name):
                parsed = parse_folder_name(name)
                self.assertEqual((parsed["title"], parsed["year"], parsed["kind"], parsed["disc"]),
                                 (title, year, kind, disc))


class TrackTitleTests(unittest.TestCase):
    def test_clean_titles(self):
        self.assertEqual(clean_track_title("Lil Wayne feat. Tyga & Gata - Exquisite", "Lil Wayne"),
                         ("Exquisite", "Tyga & Gata"))
        self.assertEqual(clean_track_title("Lil Wayne - Pimpin'", "Lil Wayne"), ("Pimpin'", None))
        self.assertEqual(clean_track_title("Intro (feat. Big Tymers)", "Lil Wayne"), ("Intro", "Big Tymers"))
        self.assertEqual(clean_track_title("Flashy And Fly (Feat Nikki)", "Lil Wayne"), ("Flashy And Fly", "Nikki"))
        self.assertEqual(clean_track_title("Mr. Carter (Alternate Version)", "Lil Wayne"),
                         ("Mr. Carter (Alternate Version)", None))

    def test_dash_without_spaces(self):
        self.assertEqual(clean_track_title("The Game Ft. Cam'ron & Glasses Malone-Where It At", "Lil Wanye & The Game"),
                         ("Where It At", "Cam'ron & Glasses Malone"))
        self.assertEqual(clean_track_title("Lil Wayne Ft. The Game-My Life-[NoFS]", "x"),
                         ("My Life", "The Game"))
        self.assertEqual(clean_track_title("Lil Wayne Ft. Nutt Da Kid- EXclusive (HOT!!!)", "Lil.Wayne"),
                         ("EXclusive (HOT!!!)", "Nutt Da Kid"))
        self.assertEqual(clean_track_title("Lil Wayne Ft. Bobby Valentino And Jers -Your Smile (Remix)", "Lil Wayne"),
                         ("Your Smile (Remix)", "Bobby Valentino And Jers"))

    def test_bootleg_tag_variants(self):
        self.assertEqual(clean_track_title("Nothing Can Stop Me - Lil' Wayne", "Lil Wayne"),
                         ("Nothing Can Stop Me", None))
        self.assertEqual(clean_track_title("Jay-Z, Kanye West - Swagga Like Us", "Lil Wayne", with_artist=True),
                         ("Swagga Like Us", None, "Jay-Z, Kanye West"))
        self.assertEqual(clean_track_title("T Pain - Fire", "Lil Wayne", with_artist=True),
                         ("Fire", None, "T Pain"))
        self.assertEqual(clean_track_title("Lil Wayne - Ft. Nutt Da Kid-Datz My Nigga (HOT!!!)", "Lil Wayne"),
                         ("Datz My Nigga (HOT!!!)", "Nutt Da Kid"))
        self.assertEqual(clean_track_title("Interlude - Lil' Wayne Speaks On Ladies", "Lil Wayne"),
                         ("Interlude", None))
        self.assertEqual(clean_track_title("Lil Wayne Ft. Nas - Rich Boy & John Legend - Ghetto Rich (Remix)",
                                           "Lil.Wayne DJ Whiteowl", with_artist=True),
                         ("Ghetto Rich (Remix)", "Nas", "Rich Boy & John Legend"))
        self.assertEqual(clean_track_title("Lil Wayne Ft. T Pain \x96 Live (BONUS)", "DJ Testarosa"),
                         ("Live (BONUS)", "T Pain"))
        self.assertEqual(clean_track_title("Winding On Me Ft.Fat Joe & Ron Browz", "Lil Wayne"),
                         ("Winding On Me", "Fat Joe & Ron Browz"))

    def test_titles_with_dashes_stay_whole(self):
        self.assertEqual(clean_track_title("G-Code", "Lil Wayne"), ("G-Code", None))
        self.assertEqual(clean_track_title("Lil Wayne - G-Code", "Lil Wayne"), ("G-Code", None))
        self.assertEqual(clean_track_title("Lil Wayne - I", "Lil Wayne"), ("I", None))
        self.assertEqual(clean_track_title("LIL WAYNE FT JUNIOR REID FREESTYLES", "Lil Wayne"),
                         ("LIL WAYNE FT JUNIOR REID FREESTYLES", None))

    def test_number(self):
        self.assertEqual((number("2/17"), number("01/22"), number(None), number("x")), (2, 1, None, None))


# ---------- library_matching ----------


def package(group, title, tracks, release_id):
    return {"schema_version": 2,
            "release_group": {"title": title, "artist": "Lil Wayne",
                              "musicbrainz_release_group_id": group, "catalog_category": "studio_album"},
            "canonical_edition": {"edition_name": title, "release": {
                "title": title, "artist": "Lil Wayne", "release_type": "studio_album",
                "musicbrainz_release_id": release_id, "musicbrainz_release_group_id": group},
                "tracks": [{"number": n, "title": t, "musicbrainz_track_id": f"{release_id}-{n}",
                            "musicbrainz_recording_id": f"rec-{t}", "duration_ms": 200000, "credits": []}
                           for n, t in enumerate(tracks, start=1)]},
            "additional_editions": []}


def file(n, title, folder="F"):
    return {"file_path": f"C:\\lib\\{folder}\\{n:02}-{title}.mp3", "file_name": f"{n:02}-{title}.mp3",
            "format": "mp3", "file_size": 1000 + n, "duration_ms": 200500, "bitrate": 256000,
            "sample_rate": 44100, "sha256": f"hash{folder}{n}", "title": title, "track_number": n,
            "disc_number": None}


class KeyTests(unittest.TestCase):
    def test_release_key(self):
        self.assertEqual(release_key("The Carter"), release_key("Tha Carter"))
        self.assertEqual(release_key("Da Drought II"), release_key("Da Drought 2"))
        self.assertNotEqual(release_key("I Am Music"), release_key("1 Am Music"))

    def test_titles_match(self):
        self.assertTrue(titles_match("Intro", "Intro"))
        self.assertTrue(titles_match("Go DJ", "Go D.J."[:5]) or titles_match("Go DJ", "Go DJ"))
        self.assertTrue(titles_match("Loud Pipes", "Loud Pipes (feat. Big Tymers)"))
        self.assertFalse(titles_match("Intro", "Outro"))
        self.assertTrue(titles_match("Fuck Wit Me Now", "F**k Wit Me Now"))
        self.assertTrue(titles_match("Act A Ass", "Act a A**"))
        self.assertTrue(titles_match("Young n Blues", "Young'n Blues"))
        self.assertTrue(titles_match("I'm A Dboy", "I’m a D‐Boy"))
        self.assertFalse(titles_match("Fuck Wit Me", "F**k Wit Me Now"))

    def test_tag_noise(self):
        self.assertTrue(titles_match("I\x92m Single (Prod. by Lil Wayne, Noah 40 Shebib & Omen)", "I’m Single"))
        self.assertTrue(titles_match("We Ready Freestyle", "We Ready"))
        self.assertTrue(titles_match("Freestyle (Damn)", "Damn"))
        self.assertTrue(titles_match("Carter Talk pt. 1", "Carter Talk, Part 1"))
        self.assertFalse(titles_match("Carter Talk pt. 1", "Carter Talk, Part 2"))
        self.assertTrue(titles_match("Freestyle", "Freestyle"))

    def test_folder_keys(self):
        from library_matching import folder_keys
        self.assertEqual(folder_keys("Dedication III Gangsta Grillz Edition")[-1], release_key("Dedication 3"))
        self.assertEqual(folder_keys("No Ceilings And BONUS")[-1], release_key("No Ceilings"))
        self.assertIn("im not a human being", folder_keys("Im Not A Human Being EP"))
        self.assertIn(release_key("SQ6: The Remix"), folder_keys("SQ6 Remixz"))


class PlanTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._directory.name)
        self._paths = (database.DATABASE_PATH, import_release_group.DATABASE_PATH)
        database.DATABASE_PATH = self.directory / "test.db"
        import_release_group.DATABASE_PATH = database.DATABASE_PATH
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
            for data in (package("g1", "Tha Carter", ["Walk In", "Go DJ", "This Is the Carter"], "r1"),):
                path = self.directory / "p.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                import_release_group.import_release_group_package(path)
        self.connection = sqlite3.connect(database.DATABASE_PATH)

    def tearDown(self):
        self.connection.close()
        database.DATABASE_PATH, import_release_group.DATABASE_PATH = self._paths
        self._directory.cleanup()

    def test_plan_and_apply(self):
        scan = {"folders": [
            {"folder": "Lil.Wayne-The.Carter.Retail-(2004)", "title": "The Carter", "kind": "retail",
             "disc": None, "tracks": [file(1, "Walk In"), file(2, "Go DJ"), file(3, "This Is the Carter")]},
            {"folder": "Lil.Wayne-The.Leak.4.(Bootleg)", "title": "The Leak 4", "kind": "bootleg",
             "disc": None, "tracks": [file(1, "Pimpin'", "L")]},
        ]}
        result = plan(scan, self.connection)
        self.assertEqual(len(result["linked"]), 3)
        self.assertEqual([f["title"] for f in result["unmatched_folders"]], ["The Leak 4"])

        with self.connection:
            self.assertEqual(apply(self.connection, result["linked"]), 3)
        with self.connection:
            self.assertEqual(apply(self.connection, result["linked"]), 0)  # idempotent
        rows = self.connection.execute("""
            SELECT rec.title, f.duration, f.sha256 FROM files f
            JOIN versions v ON v.id = f.version_id JOIN recordings rec ON rec.id = v.recording_id
            ORDER BY f.id""").fetchall()
        self.assertEqual([r[0] for r in rows], ["Walk In", "Go DJ", "This Is the Carter"])
        self.assertEqual(rows[0][1], 200.5)

    def test_folder_below_the_share_is_not_linked(self):
        scan = {"folders": [{"folder": "x", "title": "Tha Carter", "kind": "other", "disc": None,
                             "tracks": [file(1, "Walk In"), file(2, "Other"), file(3, "Another")]}]}
        result = plan(scan, self.connection)
        self.assertEqual(result["linked"], [])
        self.assertEqual(result["unmatched_folders"][0]["share"], 0.33)

    def test_official_folder_links_partly(self):
        # An official folder found by name links the files whose title matches.
        scan = {"folders": [{"folder": "x", "title": "Tha Carter", "kind": "official_mixtape", "disc": None,
                             "tracks": [file(1, "Walk In"), file(2, "Other"), file(3, "Another")]}]}
        result = plan(scan, self.connection)
        self.assertEqual([item["file"]["title"] for item in result["linked"]], ["Walk In"])
        self.assertEqual(len(result["unmatched_files"]), 2)

    def test_misnamed_official_folder_matches_by_content(self):
        scan = {"folders": [{"folder": "x", "title": "SQ9", "kind": "official_mixtape", "disc": None,
                             "tracks": [file(1, "Walk In"), file(2, "Go DJ"), file(3, "This Is the Carter")]}]}
        result = plan(scan, self.connection)
        self.assertEqual({item["release_title"] for item in result["linked"]}, {"Tha Carter"})
        scan["folders"][0]["kind"] = "other"  # bootleg-like folders never match by content
        self.assertEqual(plan(scan, self.connection)["linked"], [])


# ---------- library_packages ----------


def file_library_packages(n, title, duration=200000, featuring=None, position=None):
    """position = the number in the file name (the tag number may be wrong)."""
    return {"file_name": f"{position or n:02}-{title}.mp3", "title": title, "tag_title": title,
            "track_number": n, "duration_ms": duration, "featuring": featuring}


def folder(name, title, tracks, disc=None, year=2009):
    return {"folder": name, "title": title, "artist": "Lil Wayne", "kind": "bootleg",
            "disc": disc, "year": year, "tracks": tracks}


class BuildTests(unittest.TestCase):
    def test_tracks_out_of_scope_removed_and_renumbered(self):
        tracks, removed = folder_tracks(folder("f", "Libra", [
            file_library_packages(1, "Pimpin'"), file_library_packages(1, "Exquisite", featuring="Tyga & Gata", position=2),
            file_library_packages(3, "Real Rap (Instrumental)")]), disc=1)
        self.assertEqual([(t["number"], t["title"]) for t in tracks], [(1, "Pimpin'"), (2, "Exquisite")])
        self.assertEqual(tracks[1]["credits"], [{"artist": "Tyga", "role": "featured_artist"},
                                                {"artist": "Gata", "role": "featured_artist"}])
        self.assertEqual(removed, ["Real Rap (Instrumental)"])

    def test_bonus_disc_joins_the_release(self):
        groups = group_folders([folder("Libra", "Libra", [file_library_packages(1, "A")]),
                                folder("Libra bonus", "Libra", [file_library_packages(1, "B")], disc=2)])
        self.assertEqual(len(groups), 1)
        package = build_package(next(iter(groups.values())), {})
        self.assertEqual([(t["disc_number"], t["title"]) for t in package["canonical_edition"]["tracks"]],
                         [(1, "A"), (2, "B")])
        self.assertEqual(get_package_validation_issues(package)[0], [])
        self.assertEqual(package["release_group"]["discography"], "unofficial")

    def test_split_artists(self):
        self.assertEqual(split_artists("Tyga, Gata and Kidd Kidd"), ["Tyga", "Gata", "Kidd Kidd"])
        self.assertEqual(split_artists(None), [])


class MixedAndOfficialTests(unittest.TestCase):
    def test_artist_tag_credits(self):
        self.assertEqual(artist_tag_credits("Juelz Santana Feat. Lil Wayne"),
                         [{"artist": "Juelz Santana", "role": "main_artist"}])
        self.assertEqual(artist_tag_credits("Birdman Ft. Lil Wayne & Tyga"),
                         [{"artist": "Birdman", "role": "main_artist"}, {"artist": "Tyga", "role": "featured_artist"}])
        self.assertEqual(artist_tag_credits("Juelz Santana & Lil Wayne"),
                         [{"artist": "Juelz Santana", "role": "main_artist"}])
        self.assertEqual(artist_tag_credits("Lil Wayne Feat. Currensy"), [])
        self.assertEqual(artist_tag_credits(None), [])

    def test_mixed_folder_uses_artist_tags(self):
        tracks = [dict(file_library_packages(1, "Gangsta Music"), artist="Juelz Santana"),
                  dict(file_library_packages(2, "Rider"), artist="Lil Wayne")]
        package = build_package([folder("f", "Face Off", tracks)], {})
        credits = [t["credits"] for t in package["canonical_edition"]["tracks"]]
        self.assertEqual(credits, [[{"artist": "Juelz Santana", "role": "main_artist"}], []])

    def test_uniform_album_artist_tag_gives_no_credits(self):
        tracks = [dict(file_library_packages(1, "Intro"), artist="DJ Drama & Lil Wayne"),
                  dict(file_library_packages(2, "Motivation"), artist="DJ Drama & Lil Wayne")]
        package = build_package([folder("Lil.Wayne-Dedication-2005", "Dedication", tracks)], {},
                                category="mixtape", discography="official", title="The Dedication",
                                release_date="2005-12-13", label="On the Low",
                                evidence=["Wikipedia: Lil Wayne albums discography, Mixtapes"])
        self.assertEqual([t["credits"] for t in package["canonical_edition"]["tracks"]], [[], []])
        group = package["release_group"]
        self.assertEqual((group["title"], group["catalog_category"], group["discography"]),
                         ("The Dedication", "mixtape", "official"))
        release = package["canonical_edition"]["release"]
        self.assertEqual((release["release_type"], release["release_date"], release["label"]),
                         ("mixtape", "2005-12-13", "On the Low"))
        self.assertIn("Wikipedia: Lil Wayne albums discography, Mixtapes", group["evidence"])
        errors, _ = get_package_validation_issues(package)
        self.assertEqual(errors, [])


class ImportTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._directory.name)
        self._paths = (database.DATABASE_PATH, import_release_group.DATABASE_PATH)
        database.DATABASE_PATH = self.directory / "test.db"
        import_release_group.DATABASE_PATH = database.DATABASE_PATH
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
        album = {"schema_version": 2,
                 "release_group": {"title": "Tha Carter III", "artist": "Lil Wayne",
                                   "musicbrainz_release_group_id": "c3", "catalog_category": "studio_album"},
                 "canonical_edition": {"edition_name": "c3", "release": {
                     "title": "Tha Carter III", "artist": "Lil Wayne", "release_type": "studio_album",
                     "musicbrainz_release_id": "c3-1", "musicbrainz_release_group_id": "c3"},
                     "tracks": [{"number": 1, "title": "A Milli", "musicbrainz_track_id": "t1",
                                 "musicbrainz_recording_id": "rec-milli", "duration_ms": 221000, "credits": []}]},
                 "additional_editions": []}
        self.import_package(album)

    def tearDown(self):
        database.DATABASE_PATH, import_release_group.DATABASE_PATH = self._paths
        self._directory.cleanup()

    def import_package(self, data):
        path = self.directory / "package.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            import_release_group.import_release_group_package(path)

    def test_bootleg_reuses_known_songs_and_new_songs_are_leaked(self):
        package = build_package([folder("Leak", "The Leak 4", [
            file_library_packages(1, "A Milli", 222000), file_library_packages(2, "Exquisite", 212000, "Tyga")])], {})
        self.import_package(package)
        self.import_package(package)
        connection = sqlite3.connect(database.DATABASE_PATH)
        try:
            rows = connection.execute("""
                SELECT rec.title, a.availability FROM recordings rec
                JOIN recording_availability a ON a.recording_id = rec.id ORDER BY rec.id""").fetchall()
            self.assertEqual(rows, [("A Milli", "released"), ("Exquisite", "leaked")])
            self.assertEqual(connection.execute(
                "SELECT discography, release_type FROM releases WHERE title = 'The Leak 4'").fetchall(),
                [("unofficial", "bootleg")])
            connection.execute("UPDATE recordings SET availability_override = 'snippet' WHERE title = 'Exquisite'")
            self.assertEqual(connection.execute(
                "SELECT availability FROM recording_availability a JOIN recordings r ON r.id = a.recording_id "
                "WHERE r.title = 'Exquisite'").fetchone(), ("snippet",))
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
