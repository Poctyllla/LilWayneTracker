import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from release_group_package import get_package_validation_issues
from review_candidates import (
    edition_sort_key,
    empty_review_data,
    find_candidates,
    merge_candidates,
)


class ReviewCandidatesTests(unittest.TestCase):
    def test_merge_preserves_a_manual_decision(self):
        data = empty_review_data()
        first = {
            "release_group_id": "group-1",
            "title": "Original title",
            "date": "2008-01-01",
            "musicbrainz_primary_type": "Album",
            "musicbrainz_secondary_types": [],
            "classification": "album_candidate",
            "reasons": ["Initial reason."],
        }
        merge_candidates(data, [first], timestamp="2026-11-10T00:00:00+00:00")
        data["candidates"][0]["decision"] = "approved"
        data["candidates"][0]["review_note"] = "Keep this release."

        updated = dict(first, title="Corrected title", reasons=["New reason."])
        added, changed = merge_candidates(
            data, [updated], timestamp="2026-11-11T00:00:00+00:00"
        )

        candidate = data["candidates"][0]
        self.assertEqual((added, changed), (0, 1))
        self.assertEqual(candidate["title"], "Corrected title")
        self.assertEqual(candidate["decision"], "approved")
        self.assertEqual(candidate["review_note"], "Keep this release.")

    def test_standard_official_us_edition_is_ranked_first(self):
        editions = [
            {"id": "deluxe", "title": "Album (Deluxe)", "country": "US", "status": "Official", "date": "2008-01-02"},
            {"id": "uk", "title": "Album", "country": "GB", "status": "Official", "date": "2008-01-01"},
            {"id": "original", "title": "Album", "country": "US", "status": "Official", "date": "2008-01-01"},
        ]

        sorted_editions = sorted(editions, key=edition_sort_key)

        self.assertEqual(sorted_editions[0]["id"], "original")

    def test_package_keeps_canonical_and_additional_tracks_separate(self):
        def edition(release_id, track_id, recording_id, title):
            return {
                "edition_name": title,
                "release": {
                    "title": "Example Album",
                    "artist": "Lil Wayne",
                    "musicbrainz_release_id": release_id,
                    "musicbrainz_release_group_id": "group-1",
                },
                "tracks": [{
                    "number": 1,
                    "title": title,
                    "musicbrainz_track_id": track_id,
                    "musicbrainz_recording_id": recording_id,
                    "credits": [],
                }],
            }

        package = {
            "schema_version": 1,
            "release_group": {
                "title": "Example Album",
                "artist": "Lil Wayne",
                "musicbrainz_release_group_id": "group-1",
            },
            "canonical_edition": edition("release-1", "track-1", "recording-1", "Original Track"),
            "additional_editions": [
                edition("release-2", "track-2", "recording-2", "Bonus Track")
            ],
        }

        errors, warnings = get_package_validation_issues(package)

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        self.assertEqual(
            package["canonical_edition"]["tracks"][0]["title"],
            "Original Track",
        )
        self.assertEqual(
            package["additional_editions"][0]["tracks"][0]["title"],
            "Bonus Track",
        )

    def test_find_candidates_matches_a_title_case_insensitively(self):
        data = empty_review_data()
        merge_candidates(data, [{
            "release_group_id": "carter-group",
            "title": "Tha Carter III",
            "date": "2008-06-10",
            "musicbrainz_primary_type": "Album",
            "musicbrainz_secondary_types": [],
            "classification": "album_candidate",
            "reasons": [],
        }])

        matches = find_candidates(data, "carter iii")

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["release_group_id"], "carter-group")


if __name__ == "__main__":
    unittest.main()
