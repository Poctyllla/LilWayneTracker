import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import database
import import_release_group
from library_packages import artist_tag_credits, build_package, folder_tracks, group_folders, split_artists
from release_group_package import get_package_validation_issues


def file(n, title, duration=200000, featuring=None, position=None):
    """position = the number in the file name (the tag number may be wrong)."""
    return {"file_name": f"{position or n:02}-{title}.mp3", "title": title, "tag_title": title,
            "track_number": n, "duration_ms": duration, "featuring": featuring}


def folder(name, title, tracks, disc=None, year=2009):
    return {"folder": name, "title": title, "artist": "Lil Wayne", "kind": "bootleg",
            "disc": disc, "year": year, "tracks": tracks}


class BuildTests(unittest.TestCase):
    def test_tracks_out_of_scope_removed_and_renumbered(self):
        tracks, removed = folder_tracks(folder("f", "Libra", [
            file(1, "Pimpin'"), file(1, "Exquisite", featuring="Tyga & Gata", position=2),
            file(3, "Real Rap (Instrumental)")]), disc=1)
        self.assertEqual([(t["number"], t["title"]) for t in tracks], [(1, "Pimpin'"), (2, "Exquisite")])
        self.assertEqual(tracks[1]["credits"], [{"artist": "Tyga", "role": "featured_artist"},
                                                {"artist": "Gata", "role": "featured_artist"}])
        self.assertEqual(removed, ["Real Rap (Instrumental)"])

    def test_bonus_disc_joins_the_release(self):
        groups = group_folders([folder("Libra", "Libra", [file(1, "A")]),
                                folder("Libra bonus", "Libra", [file(1, "B")], disc=2)])
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
        tracks = [dict(file(1, "Gangsta Music"), artist="Juelz Santana"),
                  dict(file(2, "Rider"), artist="Lil Wayne")]
        package = build_package([folder("f", "Face Off", tracks)], {})
        credits = [t["credits"] for t in package["canonical_edition"]["tracks"]]
        self.assertEqual(credits, [[{"artist": "Juelz Santana", "role": "main_artist"}], []])

    def test_uniform_album_artist_tag_gives_no_credits(self):
        tracks = [dict(file(1, "Intro"), artist="DJ Drama & Lil Wayne"),
                  dict(file(2, "Motivation"), artist="DJ Drama & Lil Wayne")]
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
            file(1, "A Milli", 222000), file(2, "Exquisite", 212000, "Tyga")])], {})
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
