"""Release-group packages: review queue, edition selection, deltas, import, singles."""

from pathlib import Path
from unittest import mock
import contextlib
import copy
import io
import json
import sqlite3
import sys
import tempfile
import unittest

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
from edition_selection import (
    DUPLICATE,
    MANUAL_REVIEW,
    NO_TRACKLIST,
    NOT_OFFICIAL,
    OUT_OF_SCOPE,
    SAME_RECORDINGS,
    SAME_TRACKLIST,
    SELECTED,
    normalized_title,
    select_editions,
)
from review_candidates import edition_sort_key
import database
import import_release_group
from edition_delta import (
    DeltaError,
    diff_edition_tracks,
    make_delta_edition,
    resolve_edition_tracks,
)
from release_group_package import (
    get_package_validation_issues,
    iter_resolved_editions,
    upgrade_package_to_v2,
)
from hq_packages import artist_credit, build_single_package
from release_group_package import get_package_validation_issues
from release_relations import singles_of_album
from review_candidates import (
    edition_sort_key,
    empty_review_data,
    find_candidates,
    merge_candidates,
)
from review_candidates import edition_excluded_reason
from version_policy import excluded_edition_reason, excluded_track_reason
import musicbrainz_catalog
from duplicate_guard import (
    NEW, REVIEW, SAME, classify_tracks, load_catalogue, match_recording, possible_duplicates,
)


# ---------- package_paths ----------


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


# ---------- edition_selection ----------


def edition(edition_id, date="2011-08-29", country="US", status="Official",
            title="Tha Carter IV", disambiguation=""):
    return {"id": edition_id, "title": title, "date": date, "country": country,
            "status": status, "disambiguation": disambiguation}


def tracks(*items):
    """items: (title, recording_id[, disc_number]); numbered per disc."""
    result, numbers = [], {}
    for item in items:
        title, recording_id, disc = (*item, 1)[:3]
        numbers[disc] = numbers.get(disc, 0) + 1
        result.append({"number": numbers[disc], "disc_number": disc, "title": title,
                       "musicbrainz_track_id": f"{recording_id}-{disc}-{numbers[disc]}-{title}",
                       "musicbrainz_recording_id": recording_id, "duration_ms": 1000,
                       "credits": []})
    return result


STANDARD = (("Intro", "r1"), ("Blunt Blowin", "r2"), ("6 Foot 7 Foot", "r3"))


def statuses(decisions):
    return {edition["id"]: (status, detail) for edition, status, detail in decisions}


class SelectionTests(unittest.TestCase):
    def test_canonical_is_earliest_standard_us_official(self):
        pairs = [
            (edition("uk", country="GB"), tracks(*STANDARD)),
            (edition("us-late", date="2011-09-01"), tracks(*STANDARD)),
            (edition("us", date="2011-08-29"), tracks(*STANDARD)),
        ]
        canonical, additional, decisions = select_editions(pairs, edition_sort_key)

        self.assertEqual(canonical[0]["id"], "us")
        self.assertEqual(additional, [])
        self.assertEqual(statuses(decisions)["uk"][0], SAME_TRACKLIST)
        self.assertEqual(statuses(decisions)["us-late"][0], SAME_TRACKLIST)

    def test_deluxe_with_bonus_tracks_is_additional(self):
        deluxe = tracks(*STANDARD, ("Mirror", "r4"), ("Novacane", "r5"))
        pairs = [
            (edition("us"), tracks(*STANDARD)),
            (edition("deluxe", disambiguation="deluxe edition"), deluxe),
            (edition("deluxe-uk", country="GB", disambiguation="deluxe edition"), deluxe),
        ]
        canonical, additional, decisions = select_editions(pairs, edition_sort_key)

        self.assertEqual([e["id"] for e, _ in additional], ["deluxe"])
        self.assertEqual(statuses(decisions)["deluxe"], (SELECTED, "additional: add 2"))
        self.assertEqual(statuses(decisions)["deluxe-uk"][0], DUPLICATE)

    def test_out_of_scope_and_unofficial_editions_are_skipped(self):
        pairs = [
            (edition("us"), tracks(*STANDARD)),
            (edition("clean", disambiguation="clean version"), tracks(*STANDARD)),
            (edition("promo", status="Promotion"), tracks(*STANDARD)),
            (edition("bootleg", status="Bootleg"), None),
            (edition("instr", title="Tha Carter IV (Instrumentals)"), tracks(*STANDARD)),
            (edition("bonus-instr"), tracks(*STANDARD, ("6 Foot 7 Foot (Instrumental)", "r9"))),
            (edition("no-tracks"), None),
        ]
        canonical, additional, decisions = select_editions(pairs, edition_sort_key)
        result = statuses(decisions)

        self.assertEqual(canonical[0]["id"], "us")
        self.assertEqual(additional, [])
        self.assertEqual(result["clean"][0], OUT_OF_SCOPE)
        self.assertEqual(result["instr"][0], OUT_OF_SCOPE)
        self.assertEqual(result["bonus-instr"][0], OUT_OF_SCOPE)
        self.assertEqual(result["promo"][0], NOT_OFFICIAL)
        self.assertEqual(result["bootleg"][0], NOT_OFFICIAL)
        self.assertEqual(result["no-tracks"][0], NO_TRACKLIST)

    def test_canonical_skips_an_out_of_scope_first_edition(self):
        pairs = [
            (edition("clean", date="2011-08-01", disambiguation="clean"), tracks(*STANDARD)),
            (edition("explicit"), tracks(*STANDARD)),
        ]
        canonical, _, _ = select_editions(pairs, edition_sort_key)
        self.assertEqual(canonical[0]["id"], "explicit")

    def test_same_songs_under_other_recording_ids_add_nothing(self):
        """Another pressing: same titles and durations, other Recording IDs."""
        duplicate_recordings = tracks(("Intro", "x1"), ("Blunt Blowin", "x2"), ("6 Foot 7 Foot", "x3"))
        reordered = tracks(("6 Foot 7 Foot", "r3"), ("Intro", "r1"), ("Blunt Blowin", "r2"))
        pairs = [
            (edition("us"), tracks(*STANDARD)),
            (edition("other-ids", country="DE"), duplicate_recordings),
            (edition("reordered", country="FR"), reordered),
        ]
        _, additional, decisions = select_editions(pairs, edition_sort_key)

        self.assertEqual(statuses(decisions)["other-ids"][0], SAME_RECORDINGS)
        # Same Recording IDs in another order add nothing to the catalogue.
        self.assertEqual(additional, [])
        self.assertEqual(statuses(decisions)["reordered"][0], SAME_RECORDINGS)

    def test_two_disc_layout_of_the_same_recordings_is_skipped(self):
        """Tha Carter V-like: a 2-CD pressing of the same 3 recordings."""
        two_discs = tracks(("Intro", "r1"), ("Blunt Blowin", "r2"), ("6 Foot 7 Foot", "r3", 2))
        pairs = [(edition("us"), tracks(*STANDARD)), (edition("2cd", country="XW"), two_discs)]
        _, additional, decisions = select_editions(pairs, edition_sort_key)
        self.assertEqual(additional, [])
        self.assertEqual(statuses(decisions)["2cd"][0], SAME_RECORDINGS)

    def test_reordered_deluxe_with_new_tracks_is_selected(self):
        """Funeral-like: deluxe puts new songs first; old recordings are reused."""
        deluxe = tracks(("New Song", "n1"), *STANDARD)
        pairs = [(edition("us"), tracks(*STANDARD)),
                 (edition("deluxe", disambiguation="deluxe"), deluxe)]
        _, additional, decisions = select_editions(pairs, edition_sort_key)
        self.assertEqual([e["id"] for e, _ in additional], ["deluxe"])
        self.assertEqual(statuses(decisions)["deluxe"], (SELECTED, "additional: add 1, replace 3"))

    def test_real_replacement_is_selected(self):
        pairs = [
            (edition("us"), tracks(*STANDARD)),
            (edition("new", date="2011-10-01"), tracks(("Intro", "r1"), ("Blunt Blowin", "r2"), ("Pussy Monster", "r9"))),
        ]
        _, additional, decisions = select_editions(pairs, edition_sort_key)
        self.assertEqual([e["id"] for e, _ in additional], ["new"])
        self.assertEqual(statuses(decisions)["new"], (SELECTED, "additional: replace 1"))

    def test_base_tracklist_becomes_canonical(self):
        """Rebirth-like: an earlier-dated edition has two extra tracks."""
        pairs = [
            (edition("bigger", date="2009"), tracks(*STANDARD, ("On Fire", "r7"), ("Ground Zero", "r8"))),
            (edition("standard", date="2010-02-02"), tracks(*STANDARD)),
        ]
        canonical, additional, decisions = select_editions(pairs, edition_sort_key)

        self.assertEqual(canonical[0]["id"], "standard")
        self.assertEqual([e["id"] for e, _ in additional], ["bigger"])
        self.assertEqual(statuses(decisions)["bigger"], (SELECTED, "additional: add 2"))

    def test_subset_edition_of_lower_rank_does_not_become_canonical(self):
        """I Am Not a Human Being II-like: a country-less edition is a subset."""
        pairs = [
            (edition("us"), tracks(*STANDARD, ("Bonus", "r9"))),
            (edition("unknown-country", country=None), tracks(*STANDARD)),
        ]
        canonical, _, _ = select_editions(pairs, edition_sort_key)
        self.assertEqual(canonical[0]["id"], "us")

    def test_worldwide_standard_beats_us_special_edition(self):
        """Tha Carter VI-like: the only US edition is a special edition."""
        pairs = [
            (edition("us-special", country="US", disambiguation="special edition, bonus tracks"),
             tracks(*STANDARD, ("Bonus", "r9"))),
            (edition("xw", country="XW", disambiguation="explicit"), tracks(*STANDARD)),
            (edition("other", country="DE", disambiguation="explicit"), tracks(*STANDARD)),
        ]
        canonical, additional, _ = select_editions(pairs, edition_sort_key)

        self.assertEqual(canonical[0]["id"], "xw")
        self.assertEqual([e["id"] for e, _ in additional], ["us-special"])

    def test_clean_edition_without_fetched_tracks_is_out_of_scope(self):
        pairs = [(edition("us"), tracks(*STANDARD)),
                 (edition("clean", disambiguation="clean"), None)]
        _, _, decisions = select_editions(pairs, edition_sort_key)
        self.assertEqual(statuses(decisions)["clean"][0], OUT_OF_SCOPE)

    def test_same_bonus_track_with_other_recording_id_in_two_editions(self):
        """Tha Carter IV-like: two deluxe editions add 'Novacane' with different IDs
        but the same duration; it is one song, so the third edition adds nothing."""
        pairs = [
            (edition("us"), tracks(*STANDARD)),
            (edition("deluxe-a", disambiguation="deluxe"), tracks(*STANDARD, ("Novacane", "n1"))),
            (edition("deluxe-b", disambiguation="deluxe", country="XW"),
             tracks(*STANDARD, ("Novacane", "n2"), ("Mirror", "m1"))),
            (edition("deluxe-c", disambiguation="deluxe", country="DE"),
             tracks(*STANDARD, ("Novacane", "n1"), ("Mirror", "m1"))),
        ]
        _, additional, decisions = select_editions(pairs, edition_sort_key)

        self.assertEqual([e["id"] for e, _ in additional], ["deluxe-a", "deluxe-b"])
        self.assertEqual(statuses(decisions)["deluxe-c"][0], SAME_RECORDINGS)

    def test_same_title_up_to_15_seconds_apart_is_the_same_song(self):
        longer = tracks(*STANDARD, ("Novacane", "n2"))
        longer[-1]["duration_ms"] = 1000 + 10000
        unknown = tracks(*STANDARD, ("Novacane", "n3"), ("Mirror", "m1"))
        unknown[-2]["duration_ms"] = None
        pairs = [
            (edition("us"), tracks(*STANDARD)),
            (edition("deluxe-a", disambiguation="deluxe"), tracks(*STANDARD, ("Novacane", "n1"))),
            (edition("deluxe-b", disambiguation="deluxe", country="XW"), longer),
            (edition("deluxe-c", disambiguation="deluxe", country="DE"), unknown),
        ]
        _, additional, decisions = select_editions(pairs, edition_sort_key)
        # deluxe-c: unknown duration, one 'Novacane' known -> the same song; it
        # adds 'Mirror', so it is a new edition.
        self.assertEqual([e["id"] for e, _ in additional], ["deluxe-a", "deluxe-c"])
        self.assertEqual(statuses(decisions)["deluxe-b"][0], SAME_RECORDINGS)

    def test_unknown_duration_with_several_candidates_needs_manual_review(self):
        album = tracks(("Grown Man", "i1"), ("Grown Man", "i2"), ("Song", "s1"))
        album[1]["duration_ms"] = 90000
        bonus = tracks(("Grown Man", "i1"), ("Grown Man", "i2"), ("Song", "s1"), ("Grown Man", "i3"))
        bonus[1]["duration_ms"] = 90000
        bonus[3]["duration_ms"] = None
        pairs = [(edition("us"), album), (edition("bonus", disambiguation="bonus"), bonus)]
        _, additional, decisions = select_editions(pairs, edition_sort_key)
        self.assertEqual(additional, [])
        self.assertEqual(statuses(decisions)["bonus"][0], MANUAL_REVIEW)

    def test_same_title_more_than_15_seconds_apart_is_another_song(self):
        other = tracks(*STANDARD, ("Intro", "i2"))
        other[-1]["duration_ms"] = 1000 + 60000
        pairs = [(edition("us"), tracks(*STANDARD)),
                 (edition("bonus", disambiguation="bonus"), other)]
        _, additional, _ = select_editions(pairs, edition_sort_key)
        self.assertEqual([e["id"] for e, _ in additional], ["bonus"])

    def test_ampersand_and_and_are_the_same_title(self):
        pairs = [
            (edition("us"), tracks(("Up Up and Away", "u1"), *STANDARD)),
            (edition("other", country="XW"), tracks(("Up Up & Away", "u2"), *STANDARD)),
        ]
        _, additional, decisions = select_editions(pairs, edition_sort_key)
        self.assertEqual(additional, [])
        self.assertEqual(statuses(decisions)["other"][0], SAME_RECORDINGS)

    def test_no_usable_edition(self):
        canonical, additional, _ = select_editions(
            [(edition("promo", status="Promotion"), tracks(*STANDARD))], edition_sort_key)
        self.assertIsNone(canonical)
        self.assertEqual(additional, [])

    def test_normalized_title(self):
        self.assertEqual(normalized_title("Mo’ Fire"), normalized_title("Mo' Fire"))
        self.assertEqual(normalized_title("Bring It Back (feat. Mannie Fresh)"),
                         normalized_title("Bring It Back"))
        self.assertNotEqual(normalized_title("Lollipop (Remix)"), normalized_title("Lollipop"))


# ---------- edition_delta ----------


def track(number, title, track_id, recording_id, duration=200000, disc_number=1):
    return {
        "number": number,
        "disc_number": disc_number,
        "title": title,
        "musicbrainz_track_id": track_id,
        "musicbrainz_recording_id": recording_id,
        "duration_ms": duration,
        "credits": [],
    }


def canonical_tracks():
    """Shaped like Tha Carter III: positions 14 and 16 differ between editions."""
    return [
        track(13, "La La", "c-13", "rec-13", 261000),
        track(14, "Playing With Fire", "c-14", "rec-14", 261000),
        track(15, "You Ain't Got Nuthin", "c-15", "rec-15", 200000),
        track(16, "DontGetIt", "c-16", "rec-16", 592000),
    ]


def target_tracks():
    """Same songs with own Track IDs, a duration and a spelling difference,
    plus a bonus disc."""
    return [
        track(13, "La La", "t-13", "rec-13", 263000),
        track(14, "Playing with Fire", "t-14", "rec-14", 259000),
        track(15, "You Ain't Got Nuthin", "t-15", "rec-15", 200000),
        track(16, "DontGetIt", "t-16", "rec-16", 592000),
        track(1, "Action", "t-21", "rec-21", 180000, disc_number=2),
        track(2, "Whip It", "t-22", "rec-22", 190000, disc_number=2),
    ]


def new_version_tracks():
    """Pussy Monster replaces Playing With Fire; 'Dontgetit' spelling."""
    return [
        track(13, "La La", "n-13", "rec-13", 263000),
        track(14, "Pussy Monster", "n-14", "rec-pm", 314000),
        track(15, "You Ain't Got Nuthin", "n-15", "rec-15", 200000),
        track(16, "Dontgetit", "n-16", "rec-16", 592000),
    ]


class ResolveTests(unittest.TestCase):
    def test_no_changes_inherits_tracks_without_canonical_ids(self):
        resolved = resolve_edition_tracks(canonical_tracks(), [], [])

        self.assertEqual([t["title"] for t in resolved],
                         ["La La", "Playing With Fire", "You Ain't Got Nuthin", "DontGetIt"])
        for item in resolved:
            self.assertNotIn("musicbrainz_track_id", item)
            self.assertNotIn("duration_ms", item)
            self.assertTrue(item["musicbrainz_recording_id"].startswith("rec-"))

    def test_replace_add_remove_and_metadata(self):
        changes = [
            {"operation": "replace", "target": {"disc_number": 1, "number": 14},
             "track": track(14, "Pussy Monster", "n-14", "rec-pm", 314000)},
            {"operation": "remove", "target": {"number": 15}},
            {"operation": "add", "track": track(1, "Action", "t-21", "rec-21", disc_number=2)},
        ]
        metadata = [{"disc_number": 1, "number": 16, "musicbrainz_track_id": "n-16",
                     "duration_ms": 592000, "title": "Dontgetit"}]

        resolved = resolve_edition_tracks(canonical_tracks(), changes, metadata)

        self.assertEqual(
            [(t["disc_number"], t["number"], t["title"], t.get("musicbrainz_track_id"))
             for t in resolved],
            [(1, 13, "La La", None),
             (1, 14, "Pussy Monster", "n-14"),
             (1, 16, "Dontgetit", "n-16"),
             (2, 1, "Action", "t-21")],
        )

    def test_replace_without_position_uses_target(self):
        replacement = track(14, "Pussy Monster", "n-14", "rec-pm")
        del replacement["number"], replacement["disc_number"]
        resolved = resolve_edition_tracks(canonical_tracks(), [
            {"operation": "replace", "target": {"number": 14}, "track": replacement}
        ], [])
        self.assertEqual(resolved[1]["title"], "Pussy Monster")
        self.assertEqual((resolved[1]["disc_number"], resolved[1]["number"]), (1, 14))

    def test_operations_are_applied_in_order(self):
        resolved = resolve_edition_tracks(canonical_tracks(), [
            {"operation": "remove", "target": {"number": 14}},
            {"operation": "add", "track": track(14, "New Song", "x", "rec-x")},
        ], [])
        self.assertEqual(resolved[1]["title"], "New Song")

    def test_inputs_are_not_modified(self):
        canonical = canonical_tracks()
        changes = [{"operation": "add", "track": track(1, "Action", "t", "r", disc_number=2)}]
        metadata = [{"number": 13, "musicbrainz_track_id": "m"}]
        snapshot = copy.deepcopy((canonical, changes, metadata))

        resolve_edition_tracks(canonical, changes, metadata)

        self.assertEqual((canonical, changes, metadata), snapshot)

    def test_invalid_deltas_are_rejected(self):
        cases = {
            "replace missing target": [{"operation": "replace", "target": {"number": 99},
                                        "track": track(99, "X", "x", "x")}],
            "replace moves position": [{"operation": "replace", "target": {"number": 14},
                                        "track": track(15, "X", "x", "x")}],
            "replace without track": [{"operation": "replace", "target": {"number": 14}}],
            "add occupied": [{"operation": "add", "track": track(14, "X", "x", "x")}],
            "add without number": [{"operation": "add", "track": {"title": "X"}}],
            "add with target": [{"operation": "add", "target": {"number": 20},
                                 "track": track(20, "X", "x", "x")}],
            "remove missing": [{"operation": "remove", "target": {"number": 99}}],
            "remove twice": [{"operation": "remove", "target": {"number": 14}},
                             {"operation": "remove", "target": {"number": 14}}],
            "unknown operation": [{"operation": "move", "target": {"number": 14}}],
            "bad disc number": [{"operation": "remove", "target": {"disc_number": 0, "number": 14}}],
            "not a list": {"operation": "remove"},
        }
        for name, changes in cases.items():
            with self.subTest(name), self.assertRaises(DeltaError):
                resolve_edition_tracks(canonical_tracks(), changes, [])

    def test_invalid_metadata_is_rejected(self):
        replaced = [{"operation": "replace", "target": {"number": 14},
                     "track": track(14, "Pussy Monster", "n-14", "rec-pm")}]
        cases = {
            "missing position": ([], [{"number": 99, "musicbrainz_track_id": "x"}]),
            "duplicate": ([], [{"number": 13}, {"number": 13}]),
            "unknown field": ([], [{"number": 13, "musicbrainz_recording_id": "other"}]),
            "replaced track": (replaced, [{"number": 14, "musicbrainz_track_id": "x"}]),
        }
        for name, (changes, metadata) in cases.items():
            with self.subTest(name), self.assertRaises(DeltaError):
                resolve_edition_tracks(canonical_tracks(), changes, metadata)


class DiffTests(unittest.TestCase):
    def test_target_edition_becomes_adds_and_metadata(self):
        changes, metadata = diff_edition_tracks(canonical_tracks(), target_tracks())

        self.assertEqual([c["operation"] for c in changes], ["add", "add"])
        self.assertEqual(len(metadata), 4)
        self.assertEqual(metadata[1], {"disc_number": 1, "number": 14,
                                       "musicbrainz_track_id": "t-14",
                                       "duration_ms": 259000,
                                       "title": "Playing with Fire"})
        self.assertNotIn("title", metadata[0])

    def test_new_version_becomes_one_replace(self):
        changes, metadata = diff_edition_tracks(canonical_tracks(), new_version_tracks())

        self.assertEqual(changes, [{
            "operation": "replace",
            "target": {"disc_number": 1, "number": 14},
            "track": track(14, "Pussy Monster", "n-14", "rec-pm", 314000),
        }])
        self.assertEqual([m["number"] for m in metadata], [13, 15, 16])
        self.assertEqual(metadata[2]["title"], "Dontgetit")

    def test_missing_position_becomes_remove(self):
        changes, _ = diff_edition_tracks(canonical_tracks(), new_version_tracks()[:3])
        self.assertIn({"operation": "remove", "target": {"disc_number": 1, "number": 16}},
                      changes)

    def test_round_trip_reproduces_full_edition(self):
        for edition_tracks in (target_tracks(), new_version_tracks(), canonical_tracks(), []):
            with self.subTest(len(edition_tracks)):
                changes, metadata = diff_edition_tracks(canonical_tracks(), edition_tracks)
                resolved = resolve_edition_tracks(canonical_tracks(), changes, metadata)
                key = lambda t: (t["disc_number"], t["number"])
                self.assertEqual(sorted(resolved, key=key), sorted(edition_tracks, key=key))

    def test_different_credits_are_not_inherited(self):
        edition = canonical_tracks()
        edition[0]["credits"] = [{"artist": "Someone", "role": "featured_artist"}]
        changes, _ = diff_edition_tracks(canonical_tracks(), edition)
        self.assertEqual([c["operation"] for c in changes], ["replace"])

    def test_tracks_without_recording_id_need_matching_title(self):
        canonical = [{"number": 1, "title": "Intro", "credits": []}]
        same, _ = diff_edition_tracks(canonical, [{"number": 1, "title": "INTRO", "credits": []}])
        different, _ = diff_edition_tracks(canonical, [{"number": 1, "title": "Outro", "credits": []}])
        self.assertEqual(same, [])
        self.assertEqual([c["operation"] for c in different], ["replace"])


def package_v1():
    def edition(release_id, tracks):
        return {
            "edition_name": release_id,
            "release": {
                "title": "Example Album", "artist": "Lil Wayne",
                "release_type": "studio_album",
                "musicbrainz_release_id": release_id,
                "musicbrainz_release_group_id": "group-1",
            },
            "tracks": tracks,
        }
    return {
        "schema_version": 1,
        "release_group": {"title": "Example Album", "artist": "Lil Wayne",
                          "musicbrainz_release_group_id": "group-1",
                          "catalog_category": "studio_album"},
        "canonical_edition": edition("release-1", canonical_tracks()),
        "additional_editions": [edition("release-2", target_tracks()),
                                edition("release-3", new_version_tracks())],
    }


class PackageV2Tests(unittest.TestCase):
    def test_upgrade_keeps_every_resolved_track_list(self):
        v1 = package_v1()
        v2 = upgrade_package_to_v2(v1)

        self.assertEqual(v2["schema_version"], 2)
        self.assertEqual(v1["schema_version"], 1)  # input unchanged
        for edition in v2["additional_editions"]:
            self.assertNotIn("tracks", edition)
        # Warnings only: the sample numbering starts at 13 (not continuous).
        errors, _ = get_package_validation_issues(v2)
        self.assertEqual(errors, [])

        key = lambda t: (t["disc_number"], t["number"])
        for (_, old), (_, new) in zip(iter_resolved_editions(v1), iter_resolved_editions(v2)):
            self.assertEqual(sorted(old["tracks"], key=key), sorted(new["tracks"], key=key))

    def test_v2_validation_errors(self):
        v2 = upgrade_package_to_v2(package_v1())

        with_tracks = copy.deepcopy(v2)
        with_tracks["additional_editions"][0]["tracks"] = []
        bad_delta = copy.deepcopy(v2)
        bad_delta["additional_editions"][0]["track_changes"].append(
            {"operation": "remove", "target": {"number": 99}})
        missing_changes = copy.deepcopy(v2)
        del missing_changes["additional_editions"][0]["track_changes"]

        for name, package in (("tracks in additional", with_tracks),
                              ("bad delta", bad_delta),
                              ("missing track_changes", missing_changes)):
            with self.subTest(name):
                errors, _ = get_package_validation_issues(package)
                self.assertTrue(errors)

    def test_inherited_track_without_track_id_is_a_warning(self):
        v2 = upgrade_package_to_v2(package_v1())
        del v2["additional_editions"][0]["track_metadata"][0]["musicbrainz_track_id"]
        errors, warnings = get_package_validation_issues(v2)
        self.assertEqual(errors, [])
        self.assertTrue(any("no MusicBrainz Track ID" in w for w in warnings))

    def test_make_delta_edition_shape(self):
        full = package_v1()["additional_editions"][1]
        delta = make_delta_edition(canonical_tracks(), full)
        self.assertEqual(set(delta), {"edition_name", "release", "track_changes", "track_metadata"})


class PackageV2ImportTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._directory.name)
        self._paths = (database.DATABASE_PATH, import_release_group.DATABASE_PATH)
        database.DATABASE_PATH = self.directory / "test.db"
        import_release_group.DATABASE_PATH = database.DATABASE_PATH
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()

    def tearDown(self):
        database.DATABASE_PATH, import_release_group.DATABASE_PATH = self._paths
        self._directory.cleanup()

    def import_package(self, package):
        path = self.directory / "package.json"
        path.write_text(json.dumps(package), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            import_release_group.import_release_group_package(path)

    def query(self, sql):
        connection = sqlite3.connect(database.DATABASE_PATH)
        try:
            return connection.execute(sql).fetchall()
        finally:
            connection.close()

    def test_v2_package_imports_all_editions_and_reimports_safely(self):
        package = upgrade_package_to_v2(package_v1())
        self.import_package(package)
        self.import_package(package)

        counts = {t: self.query(f"SELECT COUNT(*) FROM {t}")[0][0]
                  for t in ("releases", "recordings", "release_tracks")}
        # 4 canonical + 2 bonus + Pussy Monster recordings; 4 + 6 + 4 tracks.
        self.assertEqual(counts, {"releases": 3, "recordings": 7, "release_tracks": 14})
        self.assertEqual(self.query(
            "SELECT COUNT(*) FROM release_tracks WHERE musicbrainz_track_id IS NULL")[0][0], 0)
        self.assertEqual(self.query("""
            SELECT r.musicbrainz_release_id, rt.track_title, rec.title, rt.duration_ms
            FROM release_tracks rt JOIN releases r ON r.id = rt.release_id
            JOIN versions v ON v.id = rt.version_id
            JOIN recordings rec ON rec.id = v.recording_id
            WHERE rt.disc_number = 1 AND rt.track_number = 14 ORDER BY 1"""), [
            ("release-1", "Playing With Fire", "Playing With Fire", 261000),
            ("release-2", "Playing with Fire", "Playing With Fire", 259000),
            ("release-3", "Pussy Monster", "Pussy Monster", 314000),
        ])

    def test_bad_delta_imports_nothing(self):
        package = upgrade_package_to_v2(package_v1())
        package["additional_editions"][1]["track_changes"].append(
            {"operation": "add", "track": track(13, "Clash", "x", "rec-x")})

        with self.assertRaises(ValueError):
            self.import_package(package)

        self.assertEqual(self.query("SELECT COUNT(*) FROM releases")[0][0], 0)


# ---------- release_group_import ----------


def edition_release_group_import(release_id, track_id, recording_id, title):
    return {
        "edition_name": release_id,
        "release": {
            "title": "Example Album",
            "artist": "Lil Wayne",
            "release_type": "studio_album",
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


class ReleaseGroupImportTests(unittest.TestCase):
    def test_imports_editions_together_and_reuses_recording(self):
        package = {
            "schema_version": 1,
            "release_group": {
                "title": "Example Album",
                "artist": "Lil Wayne",
                "musicbrainz_release_group_id": "group-1",
            },
            "canonical_edition": edition_release_group_import(
                "release-1", "track-1", "recording-1", "Original Track"
            ),
            "additional_editions": [edition_release_group_import(
                "release-2", "track-2", "recording-1", "Original Track"
            )],
        }

        original_database_path = database.DATABASE_PATH
        original_import_path = import_release_group.DATABASE_PATH
        try:
            with tempfile.TemporaryDirectory() as directory:
                directory = Path(directory)
                database.DATABASE_PATH = directory / "test.db"
                database.initialize_database()
                import_release_group.DATABASE_PATH = database.DATABASE_PATH
                package_path = directory / "package.json"
                package_path.write_text(json.dumps(package), encoding="utf-8")

                import_release_group.import_release_group_package(package_path)

                connection = sqlite3.connect(database.DATABASE_PATH)
                try:
                    self.assertEqual(
                        connection.execute("SELECT COUNT(*) FROM releases").fetchone()[0],
                        2,
                    )
                    self.assertEqual(
                        connection.execute("SELECT COUNT(*) FROM recordings").fetchone()[0],
                        1,
                    )
                    self.assertEqual(
                        connection.execute("SELECT COUNT(*) FROM release_tracks").fetchone()[0],
                        2,
                    )
                finally:
                    connection.close()
        finally:
            database.DATABASE_PATH = original_database_path
            import_release_group.DATABASE_PATH = original_import_path


# ---------- hq_packages ----------


ALBUM = {
    "schema_version": 2,
    "release_group": {"title": "Tha Carter", "artist": "Lil Wayne",
                      "musicbrainz_release_group_id": "carter-g", "catalog_category": "studio_album"},
    "canonical_edition": {
        "edition_name": "Canonical",
        "release": {"title": "Tha Carter", "artist": "Lil Wayne", "release_type": "studio_album",
                    "musicbrainz_release_id": "carter-1", "musicbrainz_release_group_id": "carter-g"},
        "tracks": [{"number": 1, "title": "Go DJ", "musicbrainz_track_id": "t1",
                    "musicbrainz_recording_id": "rec-godj", "duration_ms": 241000, "credits": []},
                   {"number": 2, "title": "Bring It Back", "musicbrainz_track_id": "t2",
                    "musicbrainz_recording_id": "rec-bib", "duration_ms": 300000, "credits": []}],
    },
    "additional_editions": [],
}


class BuildTests(unittest.TestCase):
    def test_package_is_valid_without_musicbrainz_ids(self):
        package = build_single_package({"title": "Go DJ", "with": None, "featuring": None})
        errors, _ = get_package_validation_issues(package)
        self.assertEqual(errors, [])
        self.assertEqual(package["release_group"]["source"], "LilWayneHQ")
        self.assertIsNone(package["canonical_edition"]["release"]["musicbrainz_release_id"])

    def test_missing_ids_are_still_an_error_without_a_source(self):
        package = build_single_package({"title": "Go DJ", "with": None, "featuring": None})
        del package["release_group"]["source"]
        self.assertTrue(get_package_validation_issues(package)[0])
        package["release_group"]["source"] = "Somewhere"
        self.assertTrue(any("Unknown release group source" in e
                            for e in get_package_validation_issues(package)[0]))

    def test_artist_credit(self):
        self.assertEqual(artist_credit({"title": "x", "with": None, "featuring": "Mannie Fresh"}),
                         "Lil Wayne feat. Mannie Fresh")
        self.assertEqual(artist_credit({"title": "x", "with": "Birdman", "featuring": None}),
                         "Lil Wayne & Birdman")
        self.assertEqual(artist_credit({"title": "x", "with": "Young Money feat. Lloyd", "featuring": None}),
                         "Young Money feat. Lloyd")


class ImportTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._directory.name)
        self._paths = (database.DATABASE_PATH, import_release_group.DATABASE_PATH)
        database.DATABASE_PATH = self.directory / "test.db"
        import_release_group.DATABASE_PATH = database.DATABASE_PATH
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()

    def tearDown(self):
        database.DATABASE_PATH, import_release_group.DATABASE_PATH = self._paths
        self._directory.cleanup()

    def import_package(self, data):
        path = self.directory / "package.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            import_release_group.import_release_group_package(path)

    def test_hq_single_links_to_the_album_song(self):
        self.import_package(ALBUM)
        single = build_single_package({"title": "Go DJ", "with": None, "featuring": None})
        self.import_package(single)
        self.import_package(single)  # re-import is safe

        connection = sqlite3.connect(database.DATABASE_PATH)
        try:
            q = lambda sql: connection.execute(sql).fetchall()
            self.assertEqual(q("SELECT COUNT(*) FROM recordings"), [(2,)])
            self.assertEqual(q("SELECT COUNT(*) FROM releases"), [(2,)])
            self.assertEqual(q("SELECT title, release_type, discography, musicbrainz_release_id "
                               "FROM releases WHERE release_type = 'single'"),
                             [("Go DJ", "single", "official", None)])
            self.assertEqual(q("SELECT s.name, s.url FROM sources s JOIN recording_sources rs "
                               "ON rs.source_id = s.id WHERE rs.release_id IS NOT NULL "
                               "AND s.name = 'LilWayneHQ'"),
                             [("LilWayneHQ", "https://www.lilwaynehq.com/discography/singles/")])
            self.assertEqual(singles_of_album(connection, "carter-g"),
                             [("Go DJ", singles_of_album(connection, "carter-g")[0][1], "LilWayneHQ")])
        finally:
            connection.close()

    def test_hq_single_without_album_song_becomes_a_new_recording(self):
        self.import_package(ALBUM)
        self.import_package(build_single_package({"title": "Glory", "with": None, "featuring": None}))
        connection = sqlite3.connect(database.DATABASE_PATH)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM recordings").fetchone(), (3,))
        finally:
            connection.close()


# ---------- review_candidates ----------


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


# ---------- version_policy ----------


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


# ---------- singles ----------


def track_singles(number, title, recording_id, duration=200000, track_id=None):
    return {"number": number, "disc_number": 1, "title": title,
            "musicbrainz_track_id": track_id or f"t-{recording_id}-{number}-{title}",
            "musicbrainz_recording_id": recording_id, "duration_ms": duration, "credits": []}


def package_singles(group_id, title, category, tracks, relations=None, release_id=None):
    data = {
        "schema_version": 2,
        "release_group": {"title": title, "artist": "Lil Wayne",
                          "musicbrainz_release_group_id": group_id,
                          "catalog_category": category},
        "canonical_edition": {
            "edition_name": title,
            "release": {"title": title, "artist": "Lil Wayne", "release_type": category,
                        "musicbrainz_release_id": release_id or f"rel-{group_id}",
                        "musicbrainz_release_group_id": group_id},
            "tracks": tracks,
        },
        "additional_editions": [],
    }
    if relations is not None:
        data["release_group"]["relations"] = relations
    return data


ALBUM_singles = package_singles("album-g", "Tha Carter III", "studio_album", [
    track_singles(1, "A Milli", "rec-milli", 221000, "album-1"),
    track_singles(2, "Lollipop", "rec-lolli", 299000, "album-2"),
    track_singles(3, "Intro", "rec-intro", 60000, "album-3"),
])


class DuplicateGuardTests(unittest.TestCase):
    catalogue = {
        "known_ids": {"rec-milli", "rec-lolli"},
        "by_title": {"a milli": {1: {221000}}, "lollipop": {2: {299000}},
                     "intro": {3: {60000}}},
    }

    def classify(self, *tracks):
        return classify_tracks(list(tracks), self.catalogue)

    def test_within_5_seconds_is_the_same_song(self):
        self.assertEqual(self.classify(track_singles(1, "Lollipop", "other-id", 302040)),
                         {"same": [("Lollipop", 2)], "review": []})

    def test_up_to_15_seconds_is_the_same_song(self):
        self.assertEqual(self.classify(track_singles(1, "Lollipop", "other-id", 309000)),
                         {"same": [("Lollipop", 2)], "review": []})
        self.assertEqual(possible_duplicates([track_singles(1, "Lollipop", "x", 309000)], self.catalogue), [])

    def test_more_than_15_seconds_is_another_song(self):
        """Different songs share generic titles like 'Intro'."""
        self.assertEqual(self.classify(track_singles(1, "Intro", "new", 95000)), {"same": [], "review": []})

    def test_known_recording_is_ignored(self):
        self.assertEqual(self.classify(track_singles(1, "A Milli", "rec-milli")), {"same": [], "review": []})

    def test_remix_and_feature_annotations(self):
        remix = dict(track_singles(1, "Lollipop (remix)", "r", 299000), remix_of_recording_id="rec-lolli")
        self.assertEqual(self.classify(remix), {"same": [], "review": []})
        featured = track_singles(1, "Lollipop (feat. Static Major)", "x", 299000)
        self.assertEqual(self.classify(featured)["same"], [("Lollipop (feat. Static Major)", 2)])

    def test_missing_duration_with_one_candidate_is_the_same_song(self):
        """John single: MusicBrainz has no duration, one 'John' in the catalogue."""
        self.assertEqual(self.classify(track_singles(1, "A Milli", "x", None))["same"], [("A Milli", 1)])
        self.assertEqual(match_recording("Grown Man", None, {1: {60000}, 2: {95000}}), (REVIEW, 1))

    def test_unknown_duration_outside_singles_is_a_new_song(self):
        """SQ3 (2002) 'Feel Me' without duration is not Tha Carter II's 'Feel Me'."""
        self.assertEqual(match_recording("Feel Me", None, {98: {228933}},
                                         allow_unknown_duration=False), (NEW, None))
        self.assertEqual(classify_tracks([track_singles(1, "A Milli", "x", None)], self.catalogue,
                                         allow_unknown_duration=False),
                         {"same": [], "review": []})

    def test_candidates_without_duration_do_not_block(self):
        """'Roll On': one catalogued version has no duration at all."""
        self.assertEqual(match_recording("Roll On", 200000, {846: {201000}, 900: {None}}), (SAME, 846))
        self.assertEqual(match_recording("Roll On", 200000, {900: {None}}), (NEW, None))

    def test_generic_titles_are_stricter(self):
        """Mixtapes have many different intros and skits."""
        self.assertEqual(match_recording("Intro", 60000, {3: {60000}}), (SAME, 3))
        self.assertEqual(match_recording("Intro", 65000, {3: {60000}}), (NEW, None))
        self.assertEqual(match_recording("Intro", None, {3: {60000}}), (NEW, None))
        self.assertEqual(match_recording("Loyalty (skit)", 30000, {9: {31000}}), (SAME, 9))
        self.assertEqual(match_recording("Freestyle", 200000, {4: {205000}}), (NEW, None))
        self.assertEqual(match_recording("A Milli", 225000, {1: {221000}}), (SAME, 1))

    def test_match_recording_with_several_candidates(self):
        self.assertEqual(match_recording("Grown Man", 60000, {1: {60000}, 2: {61000}}), (REVIEW, 1))
        self.assertEqual(match_recording("Grown Man", 60000, {1: {60000}, 2: {200000}}), (SAME, 1))
        self.assertEqual(match_recording("Grown Man", 60000, {}), (NEW, None))


class RelationFormatTests(unittest.TestCase):
    def test_validation(self):
        ok = package_singles("s", "A Milli", "single", [track_singles(1, "A Milli", "rec-milli")],
                     [{"type": "single_from", "musicbrainz_release_group_id": "album-g"}])
        self.assertEqual(get_package_validation_issues(ok)[0], [])
        for relations in ("x", [{"type": "remix_of", "musicbrainz_release_group_id": "a"}],
                          [{"type": "single_from"}]):
            bad = package_singles("s", "A Milli", "single", [track_singles(1, "A Milli", "rec-milli")], relations)
            with self.subTest(relations):
                self.assertTrue(get_package_validation_issues(bad)[0])

    def test_musicbrainz_single_from_direction(self):
        response = {"relations": [
            {"type": "single from", "direction": "forward", "target-type": "release_group",
             "release_group": {"id": "album-g", "title": "Tha Carter III"}},
            {"type": "single from", "direction": "backward", "target-type": "release_group",
             "release_group": {"id": "other-single", "title": "Other"}},
        ]}
        with mock.patch.object(musicbrainz_catalog, "api_request", return_value=response), \
                mock.patch.object(musicbrainz_catalog.time, "sleep"):
            relations = musicbrainz_catalog.get_single_from_relations("single-g")
        self.assertEqual(relations, [{"type": "single_from", "musicbrainz_release_group_id": "album-g",
                                      "title": "Tha Carter III", "source": "MusicBrainz"}])
        with mock.patch.object(musicbrainz_catalog, "api_request", return_value=None), \
                mock.patch.object(musicbrainz_catalog.time, "sleep"):
            self.assertIsNone(musicbrainz_catalog.get_single_from_relations("single-g"))


class SinglesOfAlbumTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._directory.name)
        self._paths = (database.DATABASE_PATH, import_release_group.DATABASE_PATH)
        database.DATABASE_PATH = self.directory / "test.db"
        import_release_group.DATABASE_PATH = database.DATABASE_PATH
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()

    def tearDown(self):
        database.DATABASE_PATH, import_release_group.DATABASE_PATH = self._paths
        self._directory.cleanup()

    def import_package(self, data):
        path = self.directory / "package.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            import_release_group.import_release_group_package(path)

    def test_stored_and_inferred_links(self):
        self.import_package(ALBUM_singles)
        # Linked in MusicBrainz and sharing the album recording.
        self.import_package(package_singles("milli-g", "A Milli", "single",
                                    [track_singles(1, "A Milli", "rec-milli", 221000, "single-1")],
                                    [{"type": "single_from", "musicbrainz_release_group_id": "album-g",
                                      "title": "Tha Carter III", "source": "MusicBrainz"}]))
        # No MusicBrainz link, but the same recording as on the album.
        self.import_package(package_singles("lolli-g", "Lollipop", "single",
                                    [track_singles(1, "Lollipop", "rec-lolli", 299000, "single-2")]))
        # Unrelated single.
        self.import_package(package_singles("other-g", "Other", "single",
                                    [track_singles(1, "Other Song", "rec-other", 1000, "single-3")]))

        connection = sqlite3.connect(database.DATABASE_PATH)
        try:
            self.assertEqual(singles_of_album(connection, "album-g"), [
                ("A Milli", "milli-g", "MusicBrainz, shared recording"),
                ("Lollipop", "lolli-g", "shared recording"),
            ])
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM recordings").fetchone()[0], 4)
        finally:
            connection.close()

        # Relations are stored once even after a re-import.
        self.import_package(package_singles("milli-g", "A Milli", "single",
                                    [track_singles(1, "A Milli", "rec-milli", 221000, "single-1")],
                                    [{"type": "single_from", "musicbrainz_release_group_id": "album-g"}]))
        connection = sqlite3.connect(database.DATABASE_PATH)
        try:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM release_group_relations").fetchone()[0], 1)
        finally:
            connection.close()

    def test_load_catalogue(self):
        self.import_package(ALBUM_singles)
        catalogue = load_catalogue(database.DATABASE_PATH)
        self.assertIn("rec-milli", catalogue["known_ids"])
        self.assertEqual(classify_tracks([track_singles(1, "A Milli", "dup", 222000)], catalogue)["same"],
                         [("A Milli", 1)])

    def query(self, sql):
        connection = sqlite3.connect(database.DATABASE_PATH)
        try:
            return connection.execute(sql).fetchall()
        finally:
            connection.close()

    def test_single_under_another_recording_id_links_to_the_album_song(self):
        """A Milli single: other Recording ID, 2 s longer -> one recording."""
        self.import_package(ALBUM_singles)
        single = package_singles("milli-g", "A Milli", "single",
                         [track_singles(1, "A Milli", "rec-milli-single", 223000, "single-1")])
        self.import_package(single)
        self.import_package(single)  # re-import is safe

        self.assertEqual(self.query("SELECT COUNT(*) FROM recordings"), [(3,)])
        self.assertEqual(self.query("""
            SELECT rec.musicbrainz_recording_id, alt.musicbrainz_recording_id, alt.source
            FROM recording_musicbrainz_ids alt JOIN recordings rec ON rec.id = alt.recording_id"""),
            [("rec-milli", "rec-milli-single", "same-song rule")])
        connection = sqlite3.connect(database.DATABASE_PATH)
        try:
            self.assertEqual([row[0] for row in singles_of_album(connection, "album-g")], ["A Milli"])
        finally:
            connection.close()

    def test_single_10_seconds_apart_or_without_duration_links_to_the_album_song(self):
        self.import_package(ALBUM_singles)
        self.import_package(package_singles("lolli-g", "Lollipop", "single",
                                    [track_singles(1, "Lollipop", "rec-lolli-2", 309000, "single-2")]))
        self.import_package(package_singles("milli-g", "A Milli", "single",
                                    [track_singles(1, "A Milli", "rec-milli-2", None, "single-3")]))
        self.assertEqual(self.query("SELECT COUNT(*) FROM recordings"), [(3,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM recording_musicbrainz_ids"), [(2,)])

    def test_other_id_at_an_existing_position_is_still_a_conflict(self):
        self.import_package(ALBUM_singles)
        changed = json.loads(json.dumps(ALBUM_singles))
        changed["canonical_edition"]["tracks"][0]["musicbrainz_recording_id"] = "rec-something-else"
        with self.assertRaisesRegex(ValueError, "Conflicting MusicBrainz ID"):
            self.import_package(changed)
        self.assertEqual(self.query("SELECT COUNT(*) FROM recording_musicbrainz_ids"), [(0,)])


if __name__ == "__main__":
    unittest.main()
