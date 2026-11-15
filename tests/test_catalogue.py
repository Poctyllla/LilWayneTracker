"""The catalogue: discography import, removal, remixes, track titles, eras."""

from pathlib import Path
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

import database
import import_release_group
from release_group_package import (
    find_package_files,
    get_package_validation_issues,
    iter_package_files,
    iter_resolved_editions,
    package_discography,
    resolve_discography,
)
from review_candidates import load_review_data
from remove_release import plan_release_removal, remove_release
from musicbrainz_catalog import remix_of_recording_id
from release_group_package import get_package_validation_issues, upgrade_package_to_v2
from remix_links import is_remix_title, link_remixes, remix_issues, remix_parts
from eras import album_eras, apply, earliest, earliest_era_of, era_of, plan_assignments


# ---------- discography ----------


def package(category="studio_album", discography=None, release_id="rel-1"):
    release_group = {"title": "Album", "artist": "Lil Wayne",
                     "musicbrainz_release_group_id": "g1", "catalog_category": category}
    if discography is not None:
        release_group["discography"] = discography
    return {
        "schema_version": 2,
        "release_group": release_group,
        "canonical_edition": {
            "edition_name": "Canonical",
            "release": {"title": "Album", "artist": "Lil Wayne", "release_type": category,
                        "musicbrainz_release_id": release_id,
                        "musicbrainz_release_group_id": "g1"},
            "tracks": [{"number": 1, "title": "Song", "musicbrainz_track_id": f"t-{release_id}",
                        "musicbrainz_recording_id": "r1", "credits": []}],
        },
        "additional_editions": [],
    }


class RuleTests(unittest.TestCase):
    def test_defaults_and_fixed_values(self):
        self.assertEqual(resolve_discography("studio_album"), "official")
        self.assertEqual(resolve_discography("collaboration_album"), "official")
        self.assertEqual(resolve_discography("ep"), "official")
        self.assertEqual(resolve_discography("single"), "official")
        self.assertEqual(resolve_discography("single", "unofficial"), "unofficial")
        self.assertEqual(resolve_discography("bootleg"), "unofficial")
        self.assertEqual(resolve_discography("album"), "official")  # legacy name

    def test_categories_without_default_need_a_decision(self):
        for category in ("mixtape", "remix", "compilation", "other"):
            with self.subTest(category):
                with self.assertRaisesRegex(ValueError, "must be chosen"):
                    resolve_discography(category)
                self.assertEqual(resolve_discography(category, "official"), "official")
                self.assertEqual(resolve_discography(category, "unofficial"), "unofficial")

    def test_invalid_combinations(self):
        with self.assertRaisesRegex(ValueError, "always official"):
            resolve_discography("studio_album", "unofficial")
        with self.assertRaisesRegex(ValueError, "always unofficial"):
            resolve_discography("bootleg", "official")
        with self.assertRaisesRegex(ValueError, "Unknown discography"):
            resolve_discography("single", "semi")


class PackageTests(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(get_package_validation_issues(package())[0], [])
        self.assertEqual(get_package_validation_issues(package("mixtape", "official"))[0], [])
        for bad in (package("mixtape"), package("studio_album", "unofficial"),
                    package("single", "maybe")):
            with self.subTest(bad["release_group"]):
                errors, _ = get_package_validation_issues(bad)
                self.assertTrue(any("discography" in error for error in errors))

    def test_resolved_editions_carry_the_discography(self):
        editions = list(iter_resolved_editions(package("mixtape", "unofficial")))
        self.assertEqual(editions[0][1]["release"]["discography"], "unofficial")
        self.assertEqual(package_discography(package()), "official")

    def test_package_files_are_found_in_both_parts(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for part in ("official/mixtapes", "unofficial/mixtapes"):
                (base / part).mkdir(parents=True)
                (base / part / "dedication.json").write_text("{}", encoding="utf-8")
            self.assertEqual(
                [p.relative_to(base).as_posix() for p in find_package_files("dedication.json", base)],
                ["official/mixtapes/dedication.json", "unofficial/mixtapes/dedication.json"],
            )
            self.assertEqual(len(iter_package_files(base)), 2)

    def test_old_approved_review_decisions_get_the_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review.json"
            path.write_text(json.dumps({"candidates": [
                {"release_group_id": "a", "title": "A", "decision": "approved",
                 "confirmed_category": "studio_album"},
                {"release_group_id": "b", "title": "B", "decision": "approved",
                 "confirmed_category": "mixtape"},
            ]}), encoding="utf-8")
            candidates = load_review_data(path)["candidates"]
            self.assertEqual(candidates[0]["confirmed_discography"], "official")
            self.assertNotIn("confirmed_discography", candidates[1])


class DatabaseTests(unittest.TestCase):
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

    def query(self, sql):
        connection = sqlite3.connect(database.DATABASE_PATH)
        try:
            return connection.execute(sql).fetchall()
        finally:
            connection.close()

    def test_import_stores_discography(self):
        self.import_package(package())
        self.import_package(package("mixtape", "unofficial", release_id="rel-2"))
        self.assertEqual(self.query(
            "SELECT musicbrainz_release_id, discography FROM releases ORDER BY id"),
            [("rel-1", "official"), ("rel-2", "unofficial")])

    def test_conflicting_discography_on_reimport_is_rejected(self):
        self.import_package(package("mixtape", "official"))
        with self.assertRaisesRegex(ValueError, "Conflicting discography"):
            self.import_package(package("mixtape", "unofficial"))
        self.assertEqual(self.query("SELECT discography FROM releases"), [("official",)])

    def test_migration_backfills_only_unambiguous_types(self):
        connection = sqlite3.connect(database.DATABASE_PATH)
        with connection:
            for title, release_type in (("A", "studio_album"), ("B", "mixtape"),
                                        ("C", "bootleg"), ("D", "single")):
                connection.execute(
                    "INSERT INTO releases (title, release_type, artist_id) VALUES (?, ?, 1)",
                    (title, release_type))
        connection.close()
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
        self.assertEqual(self.query("SELECT title, discography FROM releases ORDER BY title"),
                         [("A", "official"), ("B", None), ("C", "unofficial"), ("D", "official")])


# ---------- remove_release ----------


def track(number, title, track_id, recording_id, disc_number=1):
    return {"number": number, "disc_number": disc_number, "title": title,
            "musicbrainz_track_id": track_id, "musicbrainz_recording_id": recording_id,
            "duration_ms": 200000, "credits": []}


def edition(release_id, tracks):
    return {
        "edition_name": release_id,
        "release": {
            "title": "Example Album", "artist": "Lil Wayne", "release_type": "studio_album",
            "musicbrainz_release_id": release_id, "musicbrainz_release_group_id": "group-1",
            "sources": [{"name": "MusicBrainz",
                         "url": f"https://musicbrainz.org/release/{release_id}"}],
        },
        "tracks": tracks,
    }


def package_remove_release():
    """Standard edition + deluxe edition with the same songs and a bonus disc."""
    return {
        "schema_version": 1,
        "release_group": {"title": "Example Album", "artist": "Lil Wayne",
                          "musicbrainz_release_group_id": "group-1",
                          "catalog_category": "studio_album"},
        "canonical_edition": edition("standard", [
            track(1, "Song A", "s-1", "rec-a"),
            track(2, "Song B", "s-2", "rec-b"),
        ]),
        "additional_editions": [edition("deluxe", [
            track(1, "Song A", "d-1", "rec-a"),
            track(2, "Song B", "d-2", "rec-b"),
            track(1, "Bonus Song", "d-3", "rec-sc", disc_number=2),
        ])],
    }


class RemoveReleaseTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        directory = Path(self._directory.name)
        self._paths = (database.DATABASE_PATH, import_release_group.DATABASE_PATH)
        database.DATABASE_PATH = directory / "test.db"
        import_release_group.DATABASE_PATH = database.DATABASE_PATH
        path = directory / "package.json"
        path.write_text(json.dumps(package_remove_release()), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
            import_release_group.import_release_group_package(path)
        self.connection = sqlite3.connect(database.DATABASE_PATH)
        self.connection.execute("PRAGMA foreign_keys = ON")

    def tearDown(self):
        self.connection.close()
        database.DATABASE_PATH, import_release_group.DATABASE_PATH = self._paths
        self._directory.cleanup()

    def scalar(self, sql, params=()):
        return self.connection.execute(sql, params).fetchone()[0]

    def snapshot_standard(self):
        return self.connection.execute("""
            SELECT rt.disc_number, rt.track_number, rt.track_title, rt.musicbrainz_track_id,
                   v.id, rec.id, rec.musicbrainz_recording_id
            FROM release_tracks rt JOIN releases r ON r.id = rt.release_id
            JOIN versions v ON v.id = rt.version_id JOIN recordings rec ON rec.id = v.recording_id
            WHERE r.musicbrainz_release_id = 'standard' ORDER BY 1, 2
        """).fetchall()

    def test_plan_only_lists_exclusive_data(self):
        plan = plan_release_removal(self.connection, "deluxe")

        self.assertEqual(plan["release_tracks"], 3)
        self.assertEqual(len(plan["versions"]), 1)
        self.assertEqual(len(plan["recordings"]), 1)
        self.assertEqual(plan["shared_versions_kept"], 2)
        self.assertEqual(len(plan["sources"]), 1)

    def test_removal_keeps_shared_data_and_other_edition(self):
        standard_before = self.snapshot_standard()

        with self.connection:
            remove_release(self.connection, "deluxe")

        self.assertEqual(self.scalar("SELECT COUNT(*) FROM releases"), 1)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM recordings"), 2)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM versions"), 2)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM release_tracks"), 2)
        self.assertEqual(self.scalar(
            "SELECT COUNT(*) FROM recordings WHERE musicbrainz_recording_id = 'rec-sc'"), 0)
        self.assertEqual(self.snapshot_standard(), standard_before)
        # Only the standard edition's MusicBrainz source and its links remain.
        self.assertEqual(self.connection.execute("SELECT url FROM sources").fetchall(),
                         [("https://musicbrainz.org/release/standard",)])
        self.assertEqual(self.scalar(
            "SELECT COUNT(*) FROM recording_sources WHERE source_id NOT IN (SELECT id FROM sources)"), 0)
        self.assertEqual(self.connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_linked_local_file_blocks_removal(self):
        version_id = self.scalar(
            "SELECT v.id FROM versions v JOIN recordings r ON r.id = v.recording_id "
            "WHERE r.musicbrainz_recording_id = 'rec-sc'")
        with self.connection:
            self.connection.execute(
                "INSERT INTO files (version_id, file_path) VALUES (?, 'C:/music/sc.flac')",
                (version_id,))

        with self.assertRaisesRegex(ValueError, "local file"):
            plan_release_removal(self.connection, "deluxe")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM releases"), 2)

    def test_unknown_release_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not found"):
            plan_release_removal(self.connection, "missing")


# ---------- remix_versions ----------


def track_remix_versions(number, title, recording_id, disc_number=1, **extra):
    return {"number": number, "disc_number": disc_number, "title": title,
            "musicbrainz_track_id": f"t-{recording_id}-{disc_number}-{number}",
            "musicbrainz_recording_id": recording_id, "duration_ms": 200000,
            "credits": [], **extra}


class RemixTitleTests(unittest.TestCase):
    def test_remix_parts(self):
        self.assertEqual(remix_parts("Stuntin' Like My Daddy (Rock remix)"),
                         ("Stuntin' Like My Daddy", "Rock remix"))
        self.assertEqual(remix_parts("Lollipop [Remix]"), ("Lollipop", "Remix"))
        self.assertEqual(remix_parts("Lollipop (Remix) (feat. Kanye West)"), None)
        for title in ("Lollipop", "Remix Me", "(Remix)", "", None):
            with self.subTest(title):
                self.assertFalse(is_remix_title(title) and remix_parts(title)[0])

    def test_musicbrainz_remix_relationship_direction(self):
        remix = {"relations": [{"type": "remix", "direction": "forward",
                                "target-type": "recording", "recording": {"id": "orig"}}]}
        original = {"relations": [{"type": "remix", "direction": "backward",
                                   "target-type": "recording", "recording": {"id": "blend"}}]}
        samples = {"relations": [{"type": "samples material", "direction": "forward",
                                  "target-type": "recording", "recording": {"id": "x"}}]}
        self.assertEqual(remix_of_recording_id(remix), "orig")
        self.assertIsNone(remix_of_recording_id(original))
        self.assertIsNone(remix_of_recording_id(samples))
        self.assertIsNone(remix_of_recording_id({}))


class LinkTests(unittest.TestCase):
    def test_title_fallback_links_within_package(self):
        canonical = [track_remix_versions(1, "Stuntin' Like My Daddy", "rec-s")]
        bonus = [track_remix_versions(1, "Stuntin’ Like My Daddy (Rock remix)", "rec-r", disc_number=2)]
        linked = link_remixes([canonical, bonus])
        self.assertEqual(linked[1][0]["remix_of_recording_id"], "rec-s")
        self.assertNotIn("remix_of_recording_id", bonus[0])  # input unchanged

    def test_catalogue_original_is_used_only_when_unique(self):
        """I'm Single (remix): the original is a single already in the catalogue."""
        remix = [track_remix_versions(1, "I’m Single (remix)", "rec-isr")]
        linked = link_remixes([remix], {"im single": {"rec-is"}})
        self.assertEqual(linked[0][0]["remix_of_recording_id"], "rec-is")
        ambiguous = link_remixes([remix], {"im single": {"a", "b"}})
        self.assertNotIn("remix_of_recording_id", ambiguous[0][0])
        # An original in the package_remix_versions wins over the catalogue.
        in_package = link_remixes([[track_remix_versions(2, "I'm Single", "rec-pkg")] + remix],
                                  {"im single": {"rec-is"}})
        self.assertEqual(in_package[0][1]["remix_of_recording_id"], "rec-pkg")

    def test_marker_on_the_same_recording_is_only_a_title_variant(self):
        """MusicBrainz names one recording 'I'm Single (remix)' and 'I'm Single'."""
        canonical = [track_remix_versions(1, "I’m Single (remix)", "rec-6569")]
        later = [track_remix_versions(1, "I'm Single", "rec-6569")]
        linked = link_remixes([canonical, later], {"im single": {"rec-single"}})
        self.assertNotIn("remix_of_recording_id", linked[0][0])
        self.assertTrue(linked[0][0]["remix_marker_ignored"])
        self.assertEqual(remix_issues(linked[0][0]), [])

    def test_marker_on_a_catalogued_recording_is_only_a_title_variant(self):
        """'F**k Today (Rebirth mix)' single = the Rebirth album recording."""
        linked = link_remixes([[track_remix_versions(1, "F**k Today (Rebirth mix)", "rec-ft")]],
                              {"f k today": {"rec-ft"}})
        self.assertNotIn("remix_of_recording_id", linked[0][0])
        self.assertTrue(linked[0][0]["remix_marker_ignored"])

    def test_single_without_any_original_is_the_song(self):
        from remix_links import mark_unlinked_as_songs
        marked = mark_unlinked_as_songs([[track_remix_versions(1, "F**k Today (Rebirth mix)", "rec-ft"),
                                          track_remix_versions(2, "Lollipop (remix)", "r", remix_of_recording_id="x")]])[0]
        self.assertTrue(marked[0]["remix_marker_ignored"])
        self.assertNotIn("remix_marker_ignored", marked[1])
        self.assertEqual(remix_issues(marked[0]), [])

    def test_extended_mix_is_a_remix(self):
        self.assertEqual(remix_parts("A Milli (SIDEPIECE extended mix)"),
                         ("A Milli", "SIDEPIECE extended mix"))
        linked = link_remixes([[track_remix_versions(1, "A Milli (SIDEPIECE extended mix)", "rec-x")]],
                              {"a milli": {"rec-milli"}})
        self.assertEqual(linked[0][0]["remix_of_recording_id"], "rec-milli")
        self.assertFalse(is_remix_title("Mixtape Intro"))

    def test_mixtape_remix_of_another_artists_song(self):
        from remix_links import mark_external_remixes
        marked = mark_external_remixes([[track_remix_versions(1, "Cannon (AMG remix)", "rec-c"),
                                         track_remix_versions(2, "Song (Remix)", "rec-r", remix_of_recording_id="x"),
                                         track_remix_versions(3, "Normal", "rec-n")]])[0]
        self.assertEqual([t.get("remix_of_external") for t in marked], [True, None, None])
        self.assertEqual(remix_issues(marked[0]), [])

    def test_musicbrainz_link_is_kept(self):
        remix = track_remix_versions(1, "Song (Remix)", "rec-r", remix_of_recording_id="from-mb")
        linked = link_remixes([[track_remix_versions(2, "Song", "rec-s"), remix]])
        self.assertEqual(linked[0][1]["remix_of_recording_id"], "from-mb")

    def test_missing_or_ambiguous_original_stays_unlinked(self):
        alone = link_remixes([[track_remix_versions(1, "Song (Remix)", "rec-r")]])
        ambiguous = link_remixes([[track_remix_versions(1, "Song", "a"), track_remix_versions(2, "Song", "b"),
                                   track_remix_versions(3, "Song (Remix)", "rec-r")]])
        self.assertNotIn("remix_of_recording_id", alone[0][0])
        self.assertNotIn("remix_of_recording_id", ambiguous[0][2])

    def test_remix_issues(self):
        self.assertTrue(remix_issues(track_remix_versions(1, "Song (Remix)", "r")))
        self.assertTrue(remix_issues(track_remix_versions(1, "Song", "r", remix_of_recording_id="r")))
        self.assertEqual(remix_issues(track_remix_versions(1, "Song (Remix)", "r", remix_of_recording_id="s")), [])
        self.assertEqual(remix_issues(track_remix_versions(1, "Song", "r")), [])


def package_remix_versions(remix_first=False):
    canonical = [track_remix_versions(1, "Song A", "rec-a"), track_remix_versions(2, "Song B", "rec-b")]
    # Every edition has its own MusicBrainz Track IDs.
    bonus_edition = [dict(item, musicbrainz_track_id="bonus-" + item["musicbrainz_track_id"])
                     for item in canonical] + [
        track_remix_versions(1, "Song A (Rock remix)", "rec-ar", disc_number=2, remix_of_recording_id="rec-a")]
    if remix_first:
        canonical = [track_remix_versions(1, "Song A (Rock remix)", "rec-ar", remix_of_recording_id="rec-a"),
                     track_remix_versions(2, "Song A", "rec-a")]
        bonus_edition = copy.deepcopy(canonical)

    def edition(release_id, tracks):
        return {"edition_name": release_id, "release": {
            "title": "Album", "artist": "Lil Wayne", "release_type": "studio_album",
            "musicbrainz_release_id": release_id, "musicbrainz_release_group_id": "g1"},
            "tracks": copy.deepcopy(tracks)}

    v1 = {"schema_version": 1,
          "release_group": {"title": "Album", "artist": "Lil Wayne",
                            "musicbrainz_release_group_id": "g1",
                            "catalog_category": "studio_album"},
          "canonical_edition": edition("standard", canonical),
          "additional_editions": [] if remix_first else [edition("bonus", bonus_edition)]}
    return upgrade_package_to_v2(v1)


class RemixImportTests(unittest.TestCase):
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

    def query(self, sql):
        connection = sqlite3.connect(database.DATABASE_PATH)
        try:
            return connection.execute(sql).fetchall()
        finally:
            connection.close()

    def test_remix_becomes_a_version_of_the_original(self):
        self.import_package(package_remix_versions())
        self.import_package(package_remix_versions())  # re-import is safe

        self.assertEqual(self.query("SELECT COUNT(*) FROM recordings")[0][0], 2)
        self.assertEqual(self.query(
            "SELECT COUNT(*) FROM recordings WHERE musicbrainz_recording_id = 'rec-ar'")[0][0], 0)
        self.assertEqual(self.query("""
            SELECT rec.musicbrainz_recording_id, v.version_name, v.version_type,
                   v.musicbrainz_recording_id
            FROM versions v JOIN recordings rec ON rec.id = v.recording_id
            WHERE v.version_type = 'remix'"""),
            [("rec-a", "Rock remix", "remix", "rec-ar")])
        self.assertEqual(self.query("""
            SELECT rt.track_title, v.version_name FROM release_tracks rt
            JOIN versions v ON v.id = rt.version_id WHERE rt.disc_number = 2"""),
            [("Song A (Rock remix)", "Rock remix")])
        self.assertEqual(self.query("SELECT COUNT(*) FROM release_tracks")[0][0], 5)

    def test_remix_before_original_on_the_same_edition(self):
        self.import_package(package_remix_versions(remix_first=True))
        self.assertEqual(self.query(
            "SELECT COUNT(*) FROM versions WHERE version_type = 'remix'")[0][0], 1)

    def test_missing_original_rolls_back(self):
        data = package_remix_versions()
        data["additional_editions"][0]["track_changes"][0]["track"]["remix_of_recording_id"] = "unknown"
        with self.assertRaisesRegex(ValueError, "not in the database"):
            self.import_package(data)
        self.assertEqual(self.query("SELECT COUNT(*) FROM releases")[0][0], 0)

    def test_unlinked_remix_fails_validation(self):
        data = package_remix_versions()
        del data["additional_editions"][0]["track_changes"][0]["track"]["remix_of_recording_id"]
        errors, _ = get_package_validation_issues(data)
        self.assertTrue(any("not linked to its original" in error for error in errors))

    def test_original_named_by_musicbrainz_is_created_once(self):
        data = package_remix_versions()
        bonus = data["additional_editions"][0]["track_changes"][0]["track"]
        bonus.update(title="Banned From NO (remix)", musicbrainz_recording_id="rec-bnr",
                     remix_of_recording_id="rec-bn", remix_of_title="Banned From NO")
        self.import_package(data)
        self.import_package(data)

        self.assertEqual(self.query("""
            SELECT rec.title, rec.musicbrainz_recording_id, v.version_name, v.musicbrainz_recording_id
            FROM versions v JOIN recordings rec ON rec.id = v.recording_id
            WHERE v.version_type = 'remix'"""),
            [("Banned From NO", "rec-bn", "remix", "rec-bnr")])

        # Importing the original single later reuses the same song recording.
        single = {"schema_version": 2,
                  "release_group": {"title": "Banned From NO", "artist": "Lil Wayne",
                                    "musicbrainz_release_group_id": "g2", "catalog_category": "single"},
                  "canonical_edition": {"edition_name": "single", "release": {
                      "title": "Banned From NO", "artist": "Lil Wayne", "release_type": "single",
                      "musicbrainz_release_id": "single-1", "musicbrainz_release_group_id": "g2"},
                      "tracks": [track_remix_versions(1, "Banned From NO", "rec-bn")]},
                  "additional_editions": []}
        self.import_package(single)
        self.assertEqual(self.query("""
            SELECT v.version_type FROM versions v JOIN recordings rec ON rec.id = v.recording_id
            WHERE rec.musicbrainz_recording_id = 'rec-bn' ORDER BY v.id"""),
            [("remix",), ("album",)])
        self.assertEqual(self.query(
            "SELECT COUNT(*) FROM recordings WHERE musicbrainz_recording_id = 'rec-bn'")[0][0], 1)

    def test_same_remix_under_another_id_is_one_version(self):
        """Like Father, Like Son XW: the Rock remix has another MusicBrainz ID."""
        self.import_package(package_remix_versions())
        other = package_remix_versions()
        other["canonical_edition"]["release"]["musicbrainz_release_id"] = "xw"
        other["additional_editions"] = []
        other["canonical_edition"]["tracks"] = [
            dict(t, musicbrainz_track_id="xw-" + t["musicbrainz_track_id"])
            for t in other["canonical_edition"]["tracks"]
        ] + [track_remix_versions(3, "Song A (Rock remix)", "rec-ar-xw", remix_of_recording_id="rec-a",
                   musicbrainz_track_id="xw-remix")]
        self.import_package(other)
        self.import_package(other)

        self.assertEqual(self.query(
            "SELECT version_name FROM versions WHERE version_type = 'remix'"), [("Rock remix",)])
        self.assertEqual(self.query("""
            SELECT alt.musicbrainz_recording_id, v.version_name FROM recording_musicbrainz_ids alt
            JOIN versions v ON v.id = alt.version_id"""), [("rec-ar-xw", "Rock remix")])
        self.assertEqual(self.query("""
            SELECT COUNT(*) FROM release_tracks rt JOIN versions v ON v.id = rt.version_id
            WHERE v.version_type = 'remix'"""), [(2,)])

    def test_versions_musicbrainz_index_exists(self):
        indexes = [row[1] for row in self.query("PRAGMA index_list(versions)")]
        self.assertIn("idx_versions_musicbrainz_recording_id", indexes)


# ---------- track_titles ----------


def track_track_titles(number, title, track_id, recording_id, disc_number=1):
    return {
        "number": number,
        "disc_number": disc_number,
        "title": title,
        "musicbrainz_track_id": track_id,
        "musicbrainz_recording_id": recording_id,
        "credits": [],
    }


def edition_track_titles(release_id, tracks):
    return {
        "edition_name": release_id,
        "release": {
            "title": "Example Album",
            "artist": "Lil Wayne",
            "release_type": "studio_album",
            "musicbrainz_release_id": release_id,
            "musicbrainz_release_group_id": "group-1",
        },
        "tracks": tracks,
    }


def example_package():
    """Canonical + an edition_track_titles that spells the shared recording differently
    and adds a bonus disc (modelled on Tha Carter III / Target edition_track_titles)."""
    return {
        "schema_version": 1,
        "release_group": {
            "title": "Example Album",
            "artist": "Lil Wayne",
            "musicbrainz_release_group_id": "group-1",
            "catalog_category": "studio_album",
        },
        "canonical_edition": edition_track_titles("release-1", [
            track_track_titles(1, "Playing With Fire", "track-1", "recording-1"),
        ]),
        "additional_editions": [edition_track_titles("release-2", [
            track_track_titles(1, "Playing with Fire", "track-2", "recording-1"),
            track_track_titles(1, "Bonus Track", "track-3", "recording-2", disc_number=2),
        ])],
    }


class TemporaryDatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._directory.name)
        self._original_paths = (
            database.DATABASE_PATH, import_release_group.DATABASE_PATH
        )
        database.DATABASE_PATH = self.directory / "test.db"
        import_release_group.DATABASE_PATH = database.DATABASE_PATH
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()

    def tearDown(self):
        database.DATABASE_PATH, import_release_group.DATABASE_PATH = self._original_paths
        self._directory.cleanup()

    def import_package(self, package):
        path = self.directory / "package.json"
        path.write_text(json.dumps(package), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            return import_release_group.import_release_group_package(path)

    def query(self, sql, params=()):
        connection = sqlite3.connect(database.DATABASE_PATH)
        try:
            return connection.execute(sql, params).fetchall()
        finally:
            connection.close()

    def counts(self):
        return {
            table: self.query(f"SELECT COUNT(*) FROM {table}")[0][0]
            for table in ("releases", "recordings", "versions", "release_tracks")
        }

    def edition_titles(self):
        return self.query("""
            SELECT releases.musicbrainz_release_id, rt.disc_number,
                   rt.track_number, rt.track_title, recordings.title
            FROM release_tracks AS rt
            JOIN releases ON releases.id = rt.release_id
            JOIN versions ON versions.id = rt.version_id
            JOIN recordings ON recordings.id = versions.recording_id
            ORDER BY 1, 2, 3
        """)


class EditionTitleTests(TemporaryDatabaseTestCase):
    def test_edition_spelling_is_stored_without_a_new_recording(self):
        self.import_package(example_package())

        self.assertEqual(
            self.counts(),
            {"releases": 2, "recordings": 2, "versions": 2, "release_tracks": 3},
        )
        self.assertEqual(self.edition_titles(), [
            ("release-1", 1, 1, "Playing With Fire", "Playing With Fire"),
            ("release-2", 1, 1, "Playing with Fire", "Playing With Fire"),
            ("release-2", 2, 1, "Bonus Track", "Bonus Track"),
        ])

    def test_reimport_of_the_same_package_succeeds_without_duplicates(self):
        self.import_package(example_package())
        before = (self.counts(), self.edition_titles())

        self.import_package(example_package())

        self.assertEqual((self.counts(), self.edition_titles()), before)

    def test_changed_title_on_reimport_is_rejected_and_rolled_back(self):
        self.import_package(example_package())
        before = (self.counts(), self.edition_titles())

        changed = example_package()
        # A valid new canonical track_track_titles proves the whole package is rolled back.
        changed["canonical_edition"]["tracks"].append(
            track_track_titles(2, "New Track", "track-9", "recording-9")
        )
        changed["additional_editions"][0]["tracks"][0]["title"] = "Fire"

        with self.assertRaisesRegex(ValueError, "Track title changed"):
            self.import_package(changed)

        self.assertEqual((self.counts(), self.edition_titles()), before)

    def test_error_in_second_edition_rolls_back_the_whole_package(self):
        broken = example_package()
        # Reusing the canonical Track ID is detected as a MusicBrainz ID conflict.
        broken["additional_editions"][0]["tracks"][1]["musicbrainz_track_id"] = "track-1"

        with self.assertRaisesRegex(ValueError, "MusicBrainz ID conflict"):
            self.import_package(broken)

        self.assertEqual(
            self.counts(),
            {"releases": 0, "recordings": 0, "versions": 0, "release_tracks": 0},
        )


class LegacyRowTests(TemporaryDatabaseTestCase):
    def test_rows_without_track_title_are_filled_on_reimport(self):
        self.import_package(example_package())
        connection = sqlite3.connect(database.DATABASE_PATH)
        with connection:
            connection.execute("UPDATE release_tracks SET track_title = NULL")
        connection.close()

        # release-2 track_track_titles 1 differs from the main title only by case and has
        # the same Recording ID, so it is accepted and its spelling is stored.
        self.import_package(example_package())

        self.assertEqual(
            [row[3] for row in self.edition_titles()],
            ["Playing With Fire", "Playing with Fire", "Bonus Track"],
        )

    def test_legacy_row_with_a_different_recording_is_rejected(self):
        package = example_package()
        for item in package["canonical_edition"]["tracks"]:
            item.pop("musicbrainz_recording_id")
            item.pop("musicbrainz_track_id")
        package["additional_editions"] = []
        self.import_package(package)
        connection = sqlite3.connect(database.DATABASE_PATH)
        with connection:
            connection.execute("UPDATE release_tracks SET track_title = NULL")
        connection.close()

        package["canonical_edition"]["tracks"][0]["title"] = "Another Song"
        with self.assertRaisesRegex(ValueError, "Track title mismatch"):
            self.import_package(package)


class MigrationTests(TemporaryDatabaseTestCase):
    def test_migration_adds_track_title_and_keeps_existing_rows(self):
        self.import_package(example_package())
        connection = sqlite3.connect(database.DATABASE_PATH)
        with connection:
            connection.execute("ALTER TABLE release_tracks DROP COLUMN track_title")
        connection.close()
        before = self.query(
            "SELECT id, release_id, version_id, disc_number, track_number, "
            "musicbrainz_track_id FROM release_tracks ORDER BY id"
        )

        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
            database.initialize_database()  # Must be safe to run repeatedly.

        columns = [row[1] for row in self.query("PRAGMA table_info(release_tracks)")]
        self.assertIn("track_title", columns)
        self.assertEqual(
            self.query(
                "SELECT id, release_id, version_id, disc_number, track_number, "
                "musicbrainz_track_id FROM release_tracks ORDER BY id"
            ),
            before,
        )
        self.assertEqual(
            self.query("SELECT COUNT(*) FROM release_tracks WHERE track_title IS NULL")[0][0],
            3,
        )


# ---------- eras ----------


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


class BackupTests(unittest.TestCase):
    def test_backup_is_checked_and_only_the_newest_are_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "db.sqlite"
            connection = sqlite3.connect(source)
            connection.execute("CREATE TABLE t (x)")
            connection.execute("INSERT INTO t VALUES (1)")
            connection.commit()
            connection.close()
            backups = Path(folder) / "backups"
            for n, stamp in enumerate(["20260101_000000", "20260102_000000", "20260103_000000"]):
                (backups.mkdir(exist_ok=True), (backups / f"lilwayne_{stamp}_old.db").write_bytes(b""))
            made = database.backup("before test/apply", source, backups, keep=3)
            self.assertTrue(made.name.endswith("_before_test_apply.db"))
            copy = sqlite3.connect(made)
            try:
                self.assertEqual(copy.execute("SELECT x FROM t").fetchall(), [(1,)])
            finally:
                copy.close()
            names = sorted(f.name for f in backups.glob("*.db"))
            self.assertEqual(len(names), 3)
            self.assertNotIn("lilwayne_20260101_000000_old.db", names)


if __name__ == "__main__":
    unittest.main()
