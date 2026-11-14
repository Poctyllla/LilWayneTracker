import json
import sys
import tempfile
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from build_release_group_package import output_path_status
from release_group_package import (
    CATEGORIES,
    CATEGORY_FOLDERS,
    find_package_files,
    get_package_validation_issues,
    normalize_category,
    package_filename,
    package_path,
)
from review_candidates import DEFAULT_CATEGORIES, load_review_data


def minimal_package(release_group_id, category="studio_album", release_type="studio_album"):
    return {
        "schema_version": 1,
        "release_group": {
            "title": "Example Album",
            "artist": "Lil Wayne",
            "musicbrainz_release_group_id": release_group_id,
            "catalog_category": category,
        },
        "canonical_edition": {
            "edition_name": "Canonical",
            "release": {
                "title": "Example Album",
                "artist": "Lil Wayne",
                "release_type": release_type,
                "musicbrainz_release_id": "release-1",
                "musicbrainz_release_group_id": release_group_id,
            },
            "tracks": [{"number": 1, "title": "Track", "credits": []}],
        },
        "additional_editions": [],
    }


class PackageFilenameTests(unittest.TestCase):
    def test_filename_uses_only_the_title(self):
        self.assertEqual(package_filename("Tha Carter III"), "tha_carter_iii.json")

    def test_filename_drops_apostrophes_and_collapses_separators(self):
        self.assertEqual(package_filename("I'm Me"), "im_me.json")
        self.assertEqual(package_filename("Sorry 4 the Wait"), "sorry_4_the_wait.json")
        self.assertEqual(package_filename("  Da Drought -- 3!! "), "da_drought_3.json")
        self.assertEqual(package_filename("No Ceilings ’"), "no_ceilings.json")

    def test_filename_rejects_empty_result(self):
        with self.assertRaises(ValueError):
            package_filename("!!!")


class CategoryFolderTests(unittest.TestCase):
    def test_every_category_has_a_folder(self):
        self.assertEqual(set(CATEGORIES), set(CATEGORY_FOLDERS))

    def test_package_path_uses_category_folder(self):
        base = Path("base")
        self.assertEqual(
            package_path("studio_album", "Tha Carter III", base),
            base / "official" / "studio_albums" / "tha_carter_iii.json",
        )
        self.assertEqual(
            package_path("ep", "Example", base), base / "official" / "eps" / "example.json"
        )
        self.assertEqual(
            package_path("mixtape", "Da Drought 3", base, discography="official"),
            base / "official" / "mixtapes" / "da_drought_3.json",
        )
        self.assertEqual(
            package_path("bootleg", "The Squad", base),
            base / "unofficial" / "bootlegs" / "the_squad.json",
        )

    def test_legacy_album_category_maps_to_studio_album(self):
        self.assertEqual(normalize_category("album"), "studio_album")
        self.assertEqual(
            package_path("album", "Tha Carter III", Path("base")),
            Path("base") / "official" / "studio_albums" / "tha_carter_iii.json",
        )
        self.assertEqual(DEFAULT_CATEGORIES["album_candidate"], "studio_album")

    def test_unknown_category_is_rejected(self):
        with self.assertRaises(ValueError):
            package_path("lp", "Example", Path("base"))
        with self.assertRaises(ValueError):
            package_path(None, "Example", Path("base"))


class CollisionTests(unittest.TestCase):
    def test_output_path_status(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "studio_albums" / "example_album.json"
            self.assertEqual(output_path_status(path, "group-1"), "new")

            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(minimal_package("group-1")), encoding="utf-8")
            self.assertEqual(output_path_status(path, "group-1"), "same_release_group")
            self.assertEqual(output_path_status(path, "group-2"), "conflict")

            path.write_text("not json", encoding="utf-8")
            self.assertEqual(output_path_status(path, "group-1"), "conflict")

    def test_find_package_files_searches_category_folders(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for folder in ("studio_albums", "mixtapes"):
                (base / folder).mkdir()
                (base / folder / "example.json").write_text("{}", encoding="utf-8")

            matches = find_package_files("example.json", base)

            self.assertEqual(
                [path.relative_to(base).as_posix() for path in matches],
                ["mixtapes/example.json", "studio_albums/example.json"],
            )
            self.assertEqual(find_package_files("missing.json", base), [])


class CategoryValidationTests(unittest.TestCase):
    def test_valid_categories_pass(self):
        errors, _ = get_package_validation_issues(minimal_package("group-1"))
        self.assertEqual(errors, [])

    def test_unknown_categories_fail(self):
        errors, _ = get_package_validation_issues(
            minimal_package("group-1", category="album")
        )
        self.assertTrue(any("catalog_category" in error for error in errors))

        errors, _ = get_package_validation_issues(
            minimal_package("group-1", release_type="album")
        )
        self.assertTrue(any("release_type" in error for error in errors))


class CollaborationCategoryTests(unittest.TestCase):
    def test_collaboration_folder(self):
        self.assertEqual(
            package_path("collaboration_album", "Like Father, Like Son", Path("base")),
            Path("base") / "official" / "collaboration_albums" / "like_father_like_son.json",
        )

    def test_format_artist_credit(self):
        from musicbrainz_catalog import format_artist_credit
        self.assertEqual(format_artist_credit([
            {"name": "Birdman", "joinphrase": " & "}, {"name": "Lil Wayne"}]),
            "Birdman & Lil Wayne")
        self.assertEqual(format_artist_credit([
            {"artist": {"name": "2 Chainz"}, "joinphrase": ", "}, {"name": "Lil Wayne"}]),
            "2 Chainz, Lil Wayne")
        self.assertIsNone(format_artist_credit([]))
        self.assertIsNone(format_artist_credit(None))

    def check(self, category, credit):
        package = minimal_package("group-1", category=category, release_type=category)
        package["canonical_edition"]["release"]["artist_credit"] = credit
        return get_package_validation_issues(package)

    def test_category_and_credit_mismatch_warns(self):
        errors, warnings = self.check("studio_album", "Birdman & Lil Wayne")
        self.assertEqual(errors, [])
        self.assertTrue(any("consider collaboration_album" in w for w in warnings))

        errors, warnings = self.check("collaboration_album", "Lil Wayne")
        self.assertEqual(errors, [])
        self.assertTrue(any("catalogued as collaboration_album" in w for w in warnings))

    def test_matching_category_and_credit_do_not_warn(self):
        for category, credit in (("studio_album", "Lil Wayne"), ("studio_album", None),
                                 ("collaboration_album", "Birdman & Lil Wayne")):
            with self.subTest(category=category, credit=credit):
                _, warnings = self.check(category, credit)
                self.assertFalse(any("collaboration" in w for w in warnings))


class ReviewFileUpgradeTests(unittest.TestCase):
    def test_load_upgrades_legacy_category_in_memory_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review.json"
            original = json.dumps({"candidates": [
                {"release_group_id": "g1", "title": "A", "confirmed_category": "album"},
                {"release_group_id": "g2", "title": "B", "confirmed_category": None},
            ]})
            path.write_text(original, encoding="utf-8")

            data = load_review_data(path)

            self.assertEqual(data["candidates"][0]["confirmed_category"], "studio_album")
            self.assertIsNone(data["candidates"][1]["confirmed_category"])
            self.assertEqual(path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
