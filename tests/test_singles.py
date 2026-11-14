import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import database
import import_release_group
import musicbrainz_catalog
from duplicate_guard import (
    NEW, REVIEW, SAME, classify_tracks, load_catalogue, match_recording, possible_duplicates,
)
from release_group_package import get_package_validation_issues
from release_relations import singles_of_album


def track(number, title, recording_id, duration=200000, track_id=None):
    return {"number": number, "disc_number": 1, "title": title,
            "musicbrainz_track_id": track_id or f"t-{recording_id}-{number}-{title}",
            "musicbrainz_recording_id": recording_id, "duration_ms": duration, "credits": []}


def package(group_id, title, category, tracks, relations=None, release_id=None):
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


ALBUM = package("album-g", "Tha Carter III", "studio_album", [
    track(1, "A Milli", "rec-milli", 221000, "album-1"),
    track(2, "Lollipop", "rec-lolli", 299000, "album-2"),
    track(3, "Intro", "rec-intro", 60000, "album-3"),
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
        self.assertEqual(self.classify(track(1, "Lollipop", "other-id", 302040)),
                         {"same": [("Lollipop", 2)], "review": []})

    def test_up_to_15_seconds_is_the_same_song(self):
        self.assertEqual(self.classify(track(1, "Lollipop", "other-id", 309000)),
                         {"same": [("Lollipop", 2)], "review": []})
        self.assertEqual(possible_duplicates([track(1, "Lollipop", "x", 309000)], self.catalogue), [])

    def test_more_than_15_seconds_is_another_song(self):
        """Different songs share generic titles like 'Intro'."""
        self.assertEqual(self.classify(track(1, "Intro", "new", 95000)), {"same": [], "review": []})

    def test_known_recording_is_ignored(self):
        self.assertEqual(self.classify(track(1, "A Milli", "rec-milli")), {"same": [], "review": []})

    def test_remix_and_feature_annotations(self):
        remix = dict(track(1, "Lollipop (remix)", "r", 299000), remix_of_recording_id="rec-lolli")
        self.assertEqual(self.classify(remix), {"same": [], "review": []})
        featured = track(1, "Lollipop (feat. Static Major)", "x", 299000)
        self.assertEqual(self.classify(featured)["same"], [("Lollipop (feat. Static Major)", 2)])

    def test_missing_duration_with_one_candidate_is_the_same_song(self):
        """John single: MusicBrainz has no duration, one 'John' in the catalogue."""
        self.assertEqual(self.classify(track(1, "A Milli", "x", None))["same"], [("A Milli", 1)])
        self.assertEqual(match_recording("Grown Man", None, {1: {60000}, 2: {95000}}), (REVIEW, 1))

    def test_unknown_duration_outside_singles_is_a_new_song(self):
        """SQ3 (2002) 'Feel Me' without duration is not Tha Carter II's 'Feel Me'."""
        self.assertEqual(match_recording("Feel Me", None, {98: {228933}},
                                         allow_unknown_duration=False), (NEW, None))
        self.assertEqual(classify_tracks([track(1, "A Milli", "x", None)], self.catalogue,
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
        ok = package("s", "A Milli", "single", [track(1, "A Milli", "rec-milli")],
                     [{"type": "single_from", "musicbrainz_release_group_id": "album-g"}])
        self.assertEqual(get_package_validation_issues(ok)[0], [])
        for relations in ("x", [{"type": "remix_of", "musicbrainz_release_group_id": "a"}],
                          [{"type": "single_from"}]):
            bad = package("s", "A Milli", "single", [track(1, "A Milli", "rec-milli")], relations)
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
        self.import_package(ALBUM)
        # Linked in MusicBrainz and sharing the album recording.
        self.import_package(package("milli-g", "A Milli", "single",
                                    [track(1, "A Milli", "rec-milli", 221000, "single-1")],
                                    [{"type": "single_from", "musicbrainz_release_group_id": "album-g",
                                      "title": "Tha Carter III", "source": "MusicBrainz"}]))
        # No MusicBrainz link, but the same recording as on the album.
        self.import_package(package("lolli-g", "Lollipop", "single",
                                    [track(1, "Lollipop", "rec-lolli", 299000, "single-2")]))
        # Unrelated single.
        self.import_package(package("other-g", "Other", "single",
                                    [track(1, "Other Song", "rec-other", 1000, "single-3")]))

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
        self.import_package(package("milli-g", "A Milli", "single",
                                    [track(1, "A Milli", "rec-milli", 221000, "single-1")],
                                    [{"type": "single_from", "musicbrainz_release_group_id": "album-g"}]))
        connection = sqlite3.connect(database.DATABASE_PATH)
        try:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM release_group_relations").fetchone()[0], 1)
        finally:
            connection.close()

    def test_load_catalogue(self):
        self.import_package(ALBUM)
        catalogue = load_catalogue(database.DATABASE_PATH)
        self.assertIn("rec-milli", catalogue["known_ids"])
        self.assertEqual(classify_tracks([track(1, "A Milli", "dup", 222000)], catalogue)["same"],
                         [("A Milli", 1)])

    def query(self, sql):
        connection = sqlite3.connect(database.DATABASE_PATH)
        try:
            return connection.execute(sql).fetchall()
        finally:
            connection.close()

    def test_single_under_another_recording_id_links_to_the_album_song(self):
        """A Milli single: other Recording ID, 2 s longer -> one recording."""
        self.import_package(ALBUM)
        single = package("milli-g", "A Milli", "single",
                         [track(1, "A Milli", "rec-milli-single", 223000, "single-1")])
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
        self.import_package(ALBUM)
        self.import_package(package("lolli-g", "Lollipop", "single",
                                    [track(1, "Lollipop", "rec-lolli-2", 309000, "single-2")]))
        self.import_package(package("milli-g", "A Milli", "single",
                                    [track(1, "A Milli", "rec-milli-2", None, "single-3")]))
        self.assertEqual(self.query("SELECT COUNT(*) FROM recordings"), [(3,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM recording_musicbrainz_ids"), [(2,)])

    def test_other_id_at_an_existing_position_is_still_a_conflict(self):
        self.import_package(ALBUM)
        changed = json.loads(json.dumps(ALBUM))
        changed["canonical_edition"]["tracks"][0]["musicbrainz_recording_id"] = "rec-something-else"
        with self.assertRaisesRegex(ValueError, "Conflicting MusicBrainz ID"):
            self.import_package(changed)
        self.assertEqual(self.query("SELECT COUNT(*) FROM recording_musicbrainz_ids"), [(0,)])


if __name__ == "__main__":
    unittest.main()
