import contextlib
import copy
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


if __name__ == "__main__":
    unittest.main()
