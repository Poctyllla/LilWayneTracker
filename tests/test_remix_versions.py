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
from musicbrainz_catalog import remix_of_recording_id
from release_group_package import get_package_validation_issues, upgrade_package_to_v2
from remix_links import is_remix_title, link_remixes, remix_issues, remix_parts


def track(number, title, recording_id, disc_number=1, **extra):
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
        canonical = [track(1, "Stuntin' Like My Daddy", "rec-s")]
        bonus = [track(1, "Stuntin’ Like My Daddy (Rock remix)", "rec-r", disc_number=2)]
        linked = link_remixes([canonical, bonus])
        self.assertEqual(linked[1][0]["remix_of_recording_id"], "rec-s")
        self.assertNotIn("remix_of_recording_id", bonus[0])  # input unchanged

    def test_catalogue_original_is_used_only_when_unique(self):
        """I'm Single (remix): the original is a single already in the catalogue."""
        remix = [track(1, "I’m Single (remix)", "rec-isr")]
        linked = link_remixes([remix], {"im single": {"rec-is"}})
        self.assertEqual(linked[0][0]["remix_of_recording_id"], "rec-is")
        ambiguous = link_remixes([remix], {"im single": {"a", "b"}})
        self.assertNotIn("remix_of_recording_id", ambiguous[0][0])
        # An original in the package wins over the catalogue.
        in_package = link_remixes([[track(2, "I'm Single", "rec-pkg")] + remix],
                                  {"im single": {"rec-is"}})
        self.assertEqual(in_package[0][1]["remix_of_recording_id"], "rec-pkg")

    def test_marker_on_the_same_recording_is_only_a_title_variant(self):
        """MusicBrainz names one recording 'I'm Single (remix)' and 'I'm Single'."""
        canonical = [track(1, "I’m Single (remix)", "rec-6569")]
        later = [track(1, "I'm Single", "rec-6569")]
        linked = link_remixes([canonical, later], {"im single": {"rec-single"}})
        self.assertNotIn("remix_of_recording_id", linked[0][0])
        self.assertTrue(linked[0][0]["remix_marker_ignored"])
        self.assertEqual(remix_issues(linked[0][0]), [])

    def test_marker_on_a_catalogued_recording_is_only_a_title_variant(self):
        """'F**k Today (Rebirth mix)' single = the Rebirth album recording."""
        linked = link_remixes([[track(1, "F**k Today (Rebirth mix)", "rec-ft")]],
                              {"f k today": {"rec-ft"}})
        self.assertNotIn("remix_of_recording_id", linked[0][0])
        self.assertTrue(linked[0][0]["remix_marker_ignored"])

    def test_single_without_any_original_is_the_song(self):
        from remix_links import mark_unlinked_as_songs
        marked = mark_unlinked_as_songs([[track(1, "F**k Today (Rebirth mix)", "rec-ft"),
                                          track(2, "Lollipop (remix)", "r", remix_of_recording_id="x")]])[0]
        self.assertTrue(marked[0]["remix_marker_ignored"])
        self.assertNotIn("remix_marker_ignored", marked[1])
        self.assertEqual(remix_issues(marked[0]), [])

    def test_extended_mix_is_a_remix(self):
        self.assertEqual(remix_parts("A Milli (SIDEPIECE extended mix)"),
                         ("A Milli", "SIDEPIECE extended mix"))
        linked = link_remixes([[track(1, "A Milli (SIDEPIECE extended mix)", "rec-x")]],
                              {"a milli": {"rec-milli"}})
        self.assertEqual(linked[0][0]["remix_of_recording_id"], "rec-milli")
        self.assertFalse(is_remix_title("Mixtape Intro"))

    def test_mixtape_remix_of_another_artists_song(self):
        from remix_links import mark_external_remixes
        marked = mark_external_remixes([[track(1, "Cannon (AMG remix)", "rec-c"),
                                         track(2, "Song (Remix)", "rec-r", remix_of_recording_id="x"),
                                         track(3, "Normal", "rec-n")]])[0]
        self.assertEqual([t.get("remix_of_external") for t in marked], [True, None, None])
        self.assertEqual(remix_issues(marked[0]), [])

    def test_musicbrainz_link_is_kept(self):
        remix = track(1, "Song (Remix)", "rec-r", remix_of_recording_id="from-mb")
        linked = link_remixes([[track(2, "Song", "rec-s"), remix]])
        self.assertEqual(linked[0][1]["remix_of_recording_id"], "from-mb")

    def test_missing_or_ambiguous_original_stays_unlinked(self):
        alone = link_remixes([[track(1, "Song (Remix)", "rec-r")]])
        ambiguous = link_remixes([[track(1, "Song", "a"), track(2, "Song", "b"),
                                   track(3, "Song (Remix)", "rec-r")]])
        self.assertNotIn("remix_of_recording_id", alone[0][0])
        self.assertNotIn("remix_of_recording_id", ambiguous[0][2])

    def test_remix_issues(self):
        self.assertTrue(remix_issues(track(1, "Song (Remix)", "r")))
        self.assertTrue(remix_issues(track(1, "Song", "r", remix_of_recording_id="r")))
        self.assertEqual(remix_issues(track(1, "Song (Remix)", "r", remix_of_recording_id="s")), [])
        self.assertEqual(remix_issues(track(1, "Song", "r")), [])


def package(remix_first=False):
    canonical = [track(1, "Song A", "rec-a"), track(2, "Song B", "rec-b")]
    # Every edition has its own MusicBrainz Track IDs.
    bonus_edition = [dict(item, musicbrainz_track_id="bonus-" + item["musicbrainz_track_id"])
                     for item in canonical] + [
        track(1, "Song A (Rock remix)", "rec-ar", disc_number=2, remix_of_recording_id="rec-a")]
    if remix_first:
        canonical = [track(1, "Song A (Rock remix)", "rec-ar", remix_of_recording_id="rec-a"),
                     track(2, "Song A", "rec-a")]
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
        self.import_package(package())
        self.import_package(package())  # re-import is safe

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
        self.import_package(package(remix_first=True))
        self.assertEqual(self.query(
            "SELECT COUNT(*) FROM versions WHERE version_type = 'remix'")[0][0], 1)

    def test_missing_original_rolls_back(self):
        data = package()
        data["additional_editions"][0]["track_changes"][0]["track"]["remix_of_recording_id"] = "unknown"
        with self.assertRaisesRegex(ValueError, "not in the database"):
            self.import_package(data)
        self.assertEqual(self.query("SELECT COUNT(*) FROM releases")[0][0], 0)

    def test_unlinked_remix_fails_validation(self):
        data = package()
        del data["additional_editions"][0]["track_changes"][0]["track"]["remix_of_recording_id"]
        errors, _ = get_package_validation_issues(data)
        self.assertTrue(any("not linked to its original" in error for error in errors))

    def test_original_named_by_musicbrainz_is_created_once(self):
        data = package()
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
                      "tracks": [track(1, "Banned From NO", "rec-bn")]},
                  "additional_editions": []}
        self.import_package(single)
        self.assertEqual(self.query("""
            SELECT v.version_type FROM versions v JOIN recordings rec ON rec.id = v.recording_id
            WHERE rec.musicbrainz_recording_id = 'rec-bn' ORDER BY v.id"""),
            [("remix",), ("album",)])
        self.assertEqual(self.query(
            "SELECT COUNT(*) FROM recordings WHERE musicbrainz_recording_id = 'rec-bn'")[0][0], 1)

    def test_relink_existing_separate_remix_recording(self):
        from relink_remixes import find_relinks, relink_remix

        # Simulate an import from before the remix model: a separate recording.
        old = package()
        bonus = old["additional_editions"][0]["track_changes"][0]["track"]
        bonus.update(title="Song A Rock Mix")
        del bonus["remix_of_recording_id"]
        self.import_package(old)
        connection = sqlite3.connect(database.DATABASE_PATH)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            version_before = connection.execute("""
                SELECT rt.version_id FROM release_tracks rt WHERE rt.disc_number = 2""").fetchone()[0]

            items = find_relinks(connection, [package()])
            self.assertEqual(len(items), 1)
            with connection:
                relink_remix(connection, items[0], artist_id=1)

            self.assertEqual(connection.execute("SELECT COUNT(*) FROM recordings").fetchone()[0], 2)
            self.assertEqual(connection.execute("""
                SELECT v.id, rec.musicbrainz_recording_id, v.version_type, v.musicbrainz_recording_id
                FROM versions v JOIN recordings rec ON rec.id = v.recording_id
                WHERE v.version_type = 'remix'""").fetchall(),
                [(version_before, "rec-a", "remix", "rec-ar")])
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(find_relinks(connection, [package()]), [])
        finally:
            connection.close()

        # The package now re-imports cleanly on the relinked data.
        relinked = package()
        relinked["additional_editions"][0]["track_changes"][0]["track"]["title"] = "Song A Rock Mix"
        self.import_package(relinked)

    def test_same_remix_under_another_id_is_one_version(self):
        """Like Father, Like Son XW: the Rock remix has another MusicBrainz ID."""
        self.import_package(package())
        other = package()
        other["canonical_edition"]["release"]["musicbrainz_release_id"] = "xw"
        other["additional_editions"] = []
        other["canonical_edition"]["tracks"] = [
            dict(t, musicbrainz_track_id="xw-" + t["musicbrainz_track_id"])
            for t in other["canonical_edition"]["tracks"]
        ] + [track(3, "Song A (Rock remix)", "rec-ar-xw", remix_of_recording_id="rec-a",
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

    def test_unlink_remix_version_that_is_the_same_song(self):
        from relink_remixes import unlink_remix_version
        self.import_package(package())
        connection = sqlite3.connect(database.DATABASE_PATH)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                recording_id, album_id = unlink_remix_version(connection, "rec-ar")
            q = lambda sql: connection.execute(sql).fetchall()
            self.assertEqual(q("SELECT COUNT(*) FROM versions WHERE version_type = 'remix'"), [(0,)])
            self.assertEqual(q("SELECT COUNT(*) FROM release_tracks WHERE version_id = "
                               f"{album_id}"), [(3,)])  # 2 editions of Song A + former remix
            self.assertEqual(q("SELECT musicbrainz_recording_id FROM recording_musicbrainz_ids"),
                             [("rec-ar",)])
            self.assertEqual(q("PRAGMA foreign_key_check"), [])
        finally:
            connection.close()

    def test_versions_musicbrainz_index_exists(self):
        indexes = [row[1] for row in self.query("PRAGMA index_list(versions)")]
        self.assertIn("idx_versions_musicbrainz_recording_id", indexes)


if __name__ == "__main__":
    unittest.main()
