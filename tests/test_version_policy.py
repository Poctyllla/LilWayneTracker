import copy
import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from release_group_package import get_package_validation_issues
from review_candidates import edition_excluded_reason
from version_policy import excluded_edition_reason, excluded_track_reason


class TrackTitleTests(unittest.TestCase):
    def test_excluded_variants_in_brackets(self):
        cases = {
            "The Block Is Hot (Screwed & Chopped mix)": "screwed_and_chopped",
            "Bring It Back (Chopped Not Slopped)": "screwed_and_chopped",
            "A Milli [Instrumental]": "instrumental",
            "Lollipop (Clean)": "clean",
            "Got Money (Radio Edit)": "clean",
            "Fireman (edited version)": "clean",
        }
        for title, reason in cases.items():
            with self.subTest(title):
                self.assertEqual(excluded_track_reason(title), reason)

    def test_normal_titles_are_allowed(self):
        for title in ("Fuck Tha World", "F*** tha World", "Lollipop (remix)",
                      "Mr. Carter", "Clean Up My Mess", "Intro (feat. Big Tymers)",
                      "Playing with Fire", "Tha Mobb", "", None):
            with self.subTest(title):
                self.assertIsNone(excluded_track_reason(title))


class EditionTests(unittest.TestCase):
    def test_excluded_editions_from_musicbrainz(self):
        self.assertEqual(excluded_edition_reason("Tha Carter II: Chopped & Screwed"),
                         "screwed_and_chopped")
        self.assertEqual(excluded_edition_reason("Tha Block Is Hot", "clean version"), "clean")
        self.assertEqual(excluded_edition_reason("Tha Carter II", "clean edit"), "clean")
        self.assertEqual(edition_excluded_reason(
            {"title": "Tha Carter II", "disambiguation": "clean edit"}), "clean")

    def test_standard_editions_are_allowed(self):
        for texts in (("Tha Carter III", "Target limited edition"),
                      ("Tha Carter II", "deluxe edition"),
                      ("Tha Carter", "16 bit - 44.1 kHz, explicit reissue"),
                      ("Additional: US — Official — 2008-08-18 — new version", None)):
            with self.subTest(texts):
                self.assertIsNone(excluded_edition_reason(*texts))


class FilterTracksTests(unittest.TestCase):
    def test_out_of_scope_tracks_are_removed_and_positions_kept(self):
        from version_policy import without_excluded_tracks
        details = {"status": "Promotion", "media": [
            {"position": 1, "tracks": [
                {"position": 1, "title": "Fireman"},
                {"position": 2, "title": "Fireman (radio edit)"},
                {"position": 3, "title": "Fireman (instrumental)"},
            ]},
            {"position": 2, "tracks": [{"position": 1, "title": "Fireman [Clean]"}]},
        ]}
        filtered, removed = without_excluded_tracks(details)

        self.assertEqual(removed, ["Fireman (radio edit)", "Fireman (instrumental)", "Fireman [Clean]"])
        self.assertEqual([(m["position"], [t["position"] for t in m["tracks"]])
                          for m in filtered["media"]], [(1, [1])])
        self.assertEqual(filtered["_removed_out_of_scope_tracks"], removed)
        self.assertEqual(len(details["media"][0]["tracks"]), 3)  # input unchanged

    def test_clean_tracks_can_be_kept_for_clean_only_singles(self):
        from version_policy import without_excluded_tracks
        details = {"media": [{"tracks": [{"title": "Hustler Musik (radio edit)"},
                                         {"title": "Hustler Musik (instrumental)"}]}]}
        filtered, removed = without_excluded_tracks(details, keep_reasons=("clean",))
        self.assertEqual([t["title"] for t in filtered["media"][0]["tracks"]],
                         ["Hustler Musik (radio edit)"])
        self.assertEqual(removed, ["Hustler Musik (instrumental)"])

    def test_clean_markers_are_ignored_when_comparing_titles(self):
        from titles import normalized_title
        self.assertEqual(normalized_title("Hustler Musik (radio edit)"), "hustler musik")
        self.assertEqual(normalized_title("Lollipop [Clean]"), normalized_title("Lollipop"))
        self.assertNotEqual(normalized_title("Lollipop (Remix)"), normalized_title("Lollipop"))
        self.assertNotEqual(normalized_title("A Milli (Instrumental)"), normalized_title("A Milli"))

    def test_nothing_to_remove(self):
        from version_policy import without_excluded_tracks
        filtered, removed = without_excluded_tracks({"media": [{"tracks": [{"title": "A Milli"}]}]})
        self.assertEqual(removed, [])
        self.assertNotIn("_removed_out_of_scope_tracks", filtered)


def package():
    track = {"number": 1, "title": "Song", "musicbrainz_track_id": "t1",
             "musicbrainz_recording_id": "r1", "credits": []}
    return {
        "schema_version": 2,
        "release_group": {"title": "Album", "artist": "Lil Wayne",
                          "musicbrainz_release_group_id": "g1",
                          "catalog_category": "studio_album"},
        "canonical_edition": {
            "edition_name": "Canonical: US — Official — 2005-12-06",
            "release": {"title": "Album", "artist": "Lil Wayne", "release_type": "studio_album",
                        "musicbrainz_release_id": "rel-1", "musicbrainz_release_group_id": "g1"},
            "tracks": [track],
        },
        "additional_editions": [{
            "edition_name": "Additional: US — Official — 2005-12-06 — deluxe edition",
            "release": {"title": "Album", "artist": "Lil Wayne", "release_type": "studio_album",
                        "musicbrainz_release_id": "rel-2", "musicbrainz_release_group_id": "g1"},
            "track_changes": [],
            "track_metadata": [{"number": 1, "musicbrainz_track_id": "t2"}],
        }],
    }


class PackageValidationTests(unittest.TestCase):
    def test_clean_package_passes(self):
        self.assertEqual(get_package_validation_issues(package())[0], [])

    def test_screwed_and_chopped_track_is_an_error(self):
        bad = package()
        bad["additional_editions"][0]["track_changes"].append({"operation": "add", "track": {
            "number": 1, "disc_number": 2, "title": "Song (Screwed & Chopped mix)",
            "musicbrainz_track_id": "t3", "musicbrainz_recording_id": "r3", "credits": []}})
        errors, _ = get_package_validation_issues(bad)
        self.assertTrue(any("screwed_and_chopped" in error for error in errors))

    def test_clean_track_is_allowed_on_a_clean_only_single(self):
        data = package()
        data["canonical_edition"]["tracks"][0]["title"] = "Song (radio edit)"
        self.assertTrue(get_package_validation_issues(data)[0])
        for edition in [data["canonical_edition"], *data["additional_editions"]]:
            edition["release"]["clean_only"] = True
        self.assertEqual(get_package_validation_issues(data)[0], [])
        data["canonical_edition"]["tracks"][0]["title"] = "Song (instrumental)"
        self.assertTrue(get_package_validation_issues(data)[0])

    def test_clean_edition_is_an_error(self):
        bad = package()
        bad["additional_editions"][0]["edition_name"] = "Additional: US — Official — clean version"
        errors, _ = get_package_validation_issues(bad)
        self.assertTrue(any("clean edition" in error for error in errors))

    def test_input_is_not_modified(self):
        original = package()
        snapshot = copy.deepcopy(original)
        get_package_validation_issues(original)
        self.assertEqual(original, snapshot)


if __name__ == "__main__":
    unittest.main()
