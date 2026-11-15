"""Joining and splitting songs, versions and artists."""

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

import database
import import_release_group
from merge_recordings import find_pairs, merge
from merge_recordings import find_pairs, within_limit
import merge_artists
from split_versions import apply_move, apply_rename, find_splits
from version_types import split_version


# ---------- merge_recordings ----------


def track(number, title, recording_id, duration, track_id, **extra):
    return {"number": number, "disc_number": 1, "title": title, "musicbrainz_track_id": track_id,
            "musicbrainz_recording_id": recording_id, "duration_ms": duration, "credits": [], **extra}


def package(group_id, category, tracks):
    return {"schema_version": 2,
            "release_group": {"title": group_id, "artist": "Lil Wayne",
                              "musicbrainz_release_group_id": group_id, "catalog_category": category},
            "canonical_edition": {"edition_name": group_id, "release": {
                "title": group_id, "artist": "Lil Wayne", "release_type": category,
                "musicbrainz_release_id": f"rel-{group_id}", "musicbrainz_release_group_id": group_id},
                "tracks": tracks},
            "additional_editions": []}


class MergeTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._directory.name)
        self._paths = (database.DATABASE_PATH, import_release_group.DATABASE_PATH)
        database.DATABASE_PATH = self.directory / "test.db"
        import_release_group.DATABASE_PATH = database.DATABASE_PATH
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
        # Simulate imports from before the same-song rule: the single's
        # Lollipop (+3.04 s, other ID) became a separate recording.
        self.import_package(package("album", "studio_album", [
            track(1, "Lollipop", "rec-lolli", 299000, "a1"),
            track(2, "Intro", "rec-intro", 60000, "a2"),
            track(3, "Hot Revolver", "rec-hot", 201000, "a3"),
        ]))
        connection = self.connect()
        with connection:
            connection.execute("INSERT INTO recordings (title, artist_id, musicbrainz_recording_id) "
                               "VALUES ('Lollipop', 1, 'rec-lolli-single')")
            recording = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            connection.execute("INSERT INTO versions (recording_id, version_name, status, version_type) "
                               "VALUES (?, 'Album Version', 'official_release', 'album')", (recording,))
            version = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            connection.execute("INSERT INTO releases (title, release_type, artist_id, discography) "
                               "VALUES ('Lollipop', 'single', 1, 'official')")
            release = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            connection.execute("INSERT INTO release_tracks (release_id, version_id, track_number, duration_ms) "
                               "VALUES (?, ?, 1, 302040)", (release, version))
            connection.execute("INSERT INTO credits (artist_id, version_id, role) VALUES (1, ?, 'producer')",
                               (version,))
            connection.execute("INSERT INTO files (version_id, file_path) VALUES (?, 'C:/music/lollipop.flac')",
                               (version,))
            # A remix version of the single recording, plus a 9 s longer Hot Revolver.
            connection.execute("INSERT INTO versions (recording_id, version_name, status, version_type, "
                               "musicbrainz_recording_id) VALUES (?, 'remix', 'official_release', 'remix', 'rec-lr')",
                               (recording,))
            connection.execute("INSERT INTO recordings (title, artist_id, musicbrainz_recording_id) "
                               "VALUES ('Hot Revolver', 1, 'rec-hot-single')")
            hot = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            connection.execute("INSERT INTO versions (recording_id, version_name, status, version_type) "
                               "VALUES (?, 'Album Version', 'official_release', 'album')", (hot,))
            hot_version = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            # 30 s shorter: a different song with the same title.
            connection.execute("INSERT INTO release_tracks (release_id, version_id, track_number, duration_ms) "
                               "VALUES (?, ?, 2, 171000)", (release, hot_version))
        connection.close()

    def tearDown(self):
        database.DATABASE_PATH, import_release_group.DATABASE_PATH = self._paths
        self._directory.cleanup()

    def connect(self):
        connection = sqlite3.connect(database.DATABASE_PATH)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def import_package(self, data):
        path = self.directory / "package.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            import_release_group.import_release_group_package(path)

    def test_pairs(self):
        connection = self.connect()
        try:
            same, review = find_pairs(connection)
        finally:
            connection.close()
        self.assertEqual([title for _, _, title in same], ["Lollipop"])
        self.assertEqual(review, [])  # Hot Revolver 30 s apart: different songs

    def test_generic_titles_and_unknown_durations_are_not_merged(self):
        connection = self.connect()
        try:
            with connection:
                # On another release: two positions of one release are never merged.
                connection.execute("INSERT INTO releases (id, title, release_type, artist_id, discography) "
                                   "VALUES (99, 'Some Bootleg', 'bootleg', 1, 'unofficial')")
                for title, duration in (("Intro", 66000), ("Intro", 60500), ("Intro", None)):
                    connection.execute("INSERT INTO recordings (title, artist_id) VALUES (?, 1)", (title,))
                    rec = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
                    connection.execute("INSERT INTO versions (recording_id, version_name, version_type) "
                                       "VALUES (?, 'Album Version', 'album')", (rec,))
                    ver = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
                    connection.execute("INSERT INTO release_tracks (release_id, version_id, track_number, duration_ms) "
                                       "VALUES (99, ?, ?, ?)", (ver, 10 + rec, duration))
            same, review = find_pairs(connection)
        finally:
            connection.close()
        # Only the album Intro (60 s) and the 60.5 s Intro are within 3 s.
        self.assertEqual([title for _, _, title in same], ["Lollipop", "Intro"])
        self.assertEqual(review, [])

    def test_merge_keeps_everything_on_the_older_recording(self):
        connection = self.connect()
        try:
            (keep_id, drop_id, _), = find_pairs(connection)[0]
            with connection:
                merge(connection, keep_id, drop_id)
            q = lambda sql: connection.execute(sql).fetchall()
            self.assertEqual(q("SELECT COUNT(*) FROM recordings WHERE title = 'Lollipop'"), [(1,)])
            album_version = q(f"SELECT id FROM versions WHERE recording_id = {keep_id} "
                              "AND version_type = 'album'")[0][0]
            self.assertEqual(q("SELECT COUNT(*) FROM release_tracks WHERE version_id = "
                               f"{album_version}"), [(2,)])
            self.assertEqual(q(f"SELECT version_id FROM files"), [(album_version,)])
            self.assertEqual(q(f"SELECT version_id, role FROM credits"), [(album_version, "producer")])
            self.assertEqual(q(f"SELECT recording_id FROM versions WHERE version_type = 'remix'"),
                             [(keep_id,)])
            self.assertEqual(q("SELECT recording_id, musicbrainz_recording_id, source "
                               "FROM recording_musicbrainz_ids"),
                             [(keep_id, "rec-lolli-single", "merge")])
            self.assertEqual(q("PRAGMA foreign_key_check"), [])
            self.assertEqual(find_pairs(connection)[0], [])
        finally:
            connection.close()

        # Importing the single later finds the song through the alternate ID.
        self.import_package(package("lolli-single", "single", [
            track(1, "Lollipop", "rec-lolli-single", 302040, "s1")]))
        connection = self.connect()
        try:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM recordings WHERE title = 'Lollipop'").fetchone(), (1,))
        finally:
            connection.close()

    def test_merge_moves_streaming_links(self):
        """version_links follow the merged version; the kept version's own link of a service wins."""
        connection = self.connect()
        try:
            (keep_id, drop_id, _), = find_pairs(connection)[0]
            version = lambda rec: connection.execute(
                "SELECT id FROM versions WHERE recording_id = ? AND version_type = 'album'", (rec,)).fetchone()[0]
            keep_version, drop_version = version(keep_id), version(drop_id)
            with connection:
                connection.executemany("INSERT INTO version_links (version_id, service, url) VALUES (?, ?, ?)", [
                    (keep_version, "spotify", "https://open.spotify.com/track/kept"),
                    (drop_version, "spotify", "https://open.spotify.com/track/dropped"),
                    (drop_version, "apple_music", "https://music.apple.com/us/album/x/1?i=2")])
                merge(connection, keep_id, drop_id)
            self.assertEqual(connection.execute("SELECT version_id, service, url FROM version_links ORDER BY service").fetchall(), [
                (keep_version, "apple_music", "https://music.apple.com/us/album/x/1?i=2"),
                (keep_version, "spotify", "https://open.spotify.com/track/kept")])
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            connection.close()


# ---------- merge_safeguards ----------


class MergeSafeguardTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self._path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self._directory.name) / "test.db"
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
        self.c = sqlite3.connect(database.DATABASE_PATH)
        self.position = 0

    def tearDown(self):
        self.c.close()
        database.DATABASE_PATH = self._path
        self._directory.cleanup()

    def add(self, title, release_id, *durations):
        if not self.c.execute("SELECT 1 FROM releases WHERE id = ?", (release_id,)).fetchone():
            self.c.execute("INSERT INTO releases (id, title, artist_id, release_type, discography) "
                           "VALUES (?, ?, 1, 'bootleg', 'unofficial')", (release_id, f"Bootleg {release_id}"))
        recording = self.c.execute("INSERT INTO recordings (title, artist_id) VALUES (?, 1)", (title,)).lastrowid
        version = self.c.execute("INSERT INTO versions (recording_id, version_name, version_type) "
                                 "VALUES (?, 'Album Version', 'album')", (recording,)).lastrowid
        for duration in durations:
            self.position += 1
            self.c.execute("INSERT INTO release_tracks (release_id, version_id, disc_number, track_number, "
                           "duration_ms) VALUES (?, ?, 1, ?, ?)", (release_id, version, self.position, duration))
        return recording

    def test_within_limit(self):
        self.assertTrue(within_limit("Red Magic", {313000, 308000}, {300000}))
        self.assertFalse(within_limit("Red Magic", {313000, 308000}, {297000}))  # no drift along a chain
        self.assertFalse(within_limit("Intro", {33000, 34000}, {37000}))

    def test_same_release_is_never_merged(self):
        first = self.add("Freestyle", 1, 60000)
        self.add("Freestyle", 1, 60000)
        other = self.add("Freestyle", 2, 60500)
        same, _ = find_pairs(self.c, separate=set())
        self.assertEqual([(keep, drop) for keep, drop, _ in same], [(first, other)])

    def test_keep_separate_is_respected(self):
        a = self.add("Red Magic", 1, 313000)
        b = self.add("Red Magic", 2, 305000)
        self.assertEqual(len(find_pairs(self.c, separate=set())[0]), 1)
        self.assertEqual(find_pairs(self.c, separate={frozenset((a, b))})[0], [])


# ---------- merge_artists ----------


def fixture():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE artists (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE recordings (id INTEGER PRIMARY KEY, artist_id INTEGER);
        CREATE TABLE releases (id INTEGER PRIMARY KEY, artist_id INTEGER);
        CREATE TABLE credits (id INTEGER PRIMARY KEY, artist_id INTEGER, recording_id INTEGER, version_id INTEGER,
                              role TEXT, credited_as TEXT, source TEXT);
        INSERT INTO artists VALUES (1, 'T-Pain'), (2, 't-pain'), (3, 'T Pain'), (4, 'MIKE DEAN'), (5, 'Mike Dean'),
                                   (6, 'Bangladesh'), (7, 'Shondrae Crawford'), (8, 'Curren$y'), (9, 'Currensy'),
                                   (10, 'Drake');
        INSERT INTO credits (artist_id, recording_id, role, source) VALUES
            (1, 100, 'featured_artist', 'Genius'), (2, 100, 'featured_artist', 'Genius'),   -- same credit twice
            (3, 101, 'featured_artist', 'Discogs'),
            (4, 100, 'mixing_engineer', 'Discogs'), (4, 101, 'mixing_engineer', 'Discogs'), (5, 102, 'mixing_engineer', 'Genius'),
            (6, 100, 'producer', 'Genius'), (7, 100, 'producer', 'Discogs'), (7, 101, 'producer', 'Discogs'),
            (8, 103, 'featured_artist', 'Discogs'), (8, 104, 'featured_artist', 'Discogs'),
            (9, 103, 'featured_artist', 'Genius'), (10, 103, 'featured_artist', 'Genius');
        INSERT INTO recordings VALUES (200, 2);
    """)
    return c


class MergeArtistsTests(unittest.TestCase):
    def test_plan(self):
        merges = {name: [n for _i, n in drops] for _keep, name, drops in
                  merge_artists.plan(fixture(), [["Bangladesh", "Shondrae Crawford", "Discogs"]])}
        self.assertEqual(merges, {
            "T-Pain": ["t-pain", "T Pain"],            # tidy (not all lower case), then most used
            "Mike Dean": ["MIKE DEAN"],                # tidy over ALL CAPS although used less
            "Bangladesh": ["Shondrae Crawford"],       # the stage name stays
            "Curren$y": ["Currensy"],                  # "$" read as "s"
        })

    def test_apply(self):
        c = fixture()
        merge_artists.apply(c, merge_artists.plan(c, []))
        self.assertEqual(c.execute("SELECT count(*) FROM artists").fetchone()[0], 6)  # 10 - 2 T-Pain - 1 Mike Dean - 1 Currensy
        # the two identical T-Pain credits are one; the other spelling is kept in credited_as
        self.assertEqual(c.execute("SELECT recording_id, credited_as FROM credits WHERE artist_id = 1 ORDER BY recording_id").fetchall(),
                         [(100, None), (101, "T Pain")])  # the t-pain duplicate of recording 100 is gone
        self.assertEqual(c.execute("SELECT artist_id FROM recordings").fetchone()[0], 1)
        self.assertEqual(c.execute("SELECT DISTINCT artist_id FROM credits WHERE role = 'mixing_engineer'").fetchall(), [(5,)])


# ---------- split_versions ----------


class SplitVersionsTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self._path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self._directory.name) / "test.db"
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
        self.connection = sqlite3.connect(database.DATABASE_PATH)
        self.connection.execute("PRAGMA foreign_keys = ON")
        c = self.connection
        c.execute("INSERT OR IGNORE INTO artists (id, name) VALUES (1, 'Lil Wayne')")
        c.execute("INSERT INTO releases (id, title, artist_id, release_type, discography) "
                  "VALUES (1, 'Album', 1, 'studio_album', 'official'), (2, 'Leak', 1, 'bootleg', 'unofficial')")
        self.songs = {}
        for number, (title, release_id, mbid) in enumerate([
                ("Fireman", 1, "mb-fire"), ("Fireman (Live In Los Angeles)", 2, None),
                ("Cascades (Snippet)", 2, None), ("Hustler Musik (radio edit)", 1, None),
                ("Hustler Musik", 1, None), ("Hustler Musik (Studio Acapella)", 2, None)], start=1):
            recording = c.execute("INSERT INTO recordings (title, artist_id, musicbrainz_recording_id) "
                                  "VALUES (?, 1, ?)", (title, mbid)).lastrowid
            version = c.execute("INSERT INTO versions (recording_id, version_name, version_type) "
                                "VALUES (?, 'Album Version', 'album')", (recording,)).lastrowid
            c.execute("INSERT INTO release_tracks (release_id, version_id, disc_number, track_number) "
                      "VALUES (?, ?, 1, ?)", (release_id, version, number))
            self.songs[title] = (recording, version)
        c.commit()

    def tearDown(self):
        self.connection.close()
        database.DATABASE_PATH = self._path
        self._directory.cleanup()

    def test_plan_and_apply(self):
        plan = find_splits(self.connection)
        moves = {item["title"]: item for item in plan["move"]}
        self.assertEqual(moves["Fireman (Live In Los Angeles)"]["base_id"], self.songs["Fireman"][0])
        self.assertEqual(moves["Hustler Musik (Studio Acapella)"]["base_id"], self.songs["Hustler Musik"][0])
        self.assertEqual([item["title"] for item in plan["rename"]], ["Cascades (Snippet)"])
        self.assertEqual(plan["review"], [])

        with self.connection:
            for item in plan["move"]:
                apply_move(self.connection, item)
            for item in plan["rename"]:
                apply_rename(self.connection, item)
        c = self.connection
        live_version = self.songs["Fireman (Live In Los Angeles)"][1]
        self.assertEqual(c.execute("SELECT recording_id, version_type, version_name FROM versions WHERE id = ?",
                                   (live_version,)).fetchone(),
                         (self.songs["Fireman"][0], "live", "Live In Los Angeles"))
        self.assertIsNone(c.execute("SELECT 1 FROM recordings WHERE id = ?",
                                    (self.songs["Fireman (Live In Los Angeles)"][0],)).fetchone())
        self.assertEqual(c.execute("SELECT track_title FROM release_tracks WHERE version_id = ?",
                                   (live_version,)).fetchone()[0], "Fireman (Live In Los Angeles)")
        cascades = self.songs["Cascades (Snippet)"]
        self.assertEqual(c.execute("SELECT title FROM recordings WHERE id = ?", (cascades[0],)).fetchone()[0],
                         "Cascades")
        self.assertEqual(c.execute("SELECT version_type FROM versions WHERE id = ?", (cascades[1],)).fetchone()[0],
                         "snippet")
        self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(find_splits(c), {"move": [], "rename": [], "review": []})


# ---------- version_types ----------


class SplitVersionTests(unittest.TestCase):
    def test_versions(self):
        cases = {
            "A Milli (Demo)": ("A Milli", "demo", "Demo"),
            "A Milli [Demo]": ("A Milli", "demo", "Demo"),
            "Cascades (Snippet)": ("Cascades", "snippet", "Snippet"),
            "30 Minutes to New Orleans (Low Quality Snippet)":
                ("30 Minutes to New Orleans", "snippet", "Low Quality Snippet"),
            "Forever (Remix) (Snippet)": ("Forever", "snippet", "Remix (Snippet)"),
            "Heat [Original]": ("Heat", "original", "Original"),
            "A Millie(Original Version)": ("A Millie", "original", "Original Version"),
            "Mr. Carter (Alternate Version)": ("Mr. Carter", "alternate", "Alternate Version"),
            "Amillie (C3 Version)": ("Amillie", "alternate", "C3 Version"),
            "Fireman [Unreleased Verse]": ("Fireman", "alternate", "Unreleased Verse"),
            "Fireman (Live In Los Angeles)": ("Fireman", "live", "Live In Los Angeles"),
            "Hot Revolver (Extended)": ("Hot Revolver", "extended", "Extended"),
            "Lollipop (Remix)": ("Lollipop", "remix", "Remix"),
            "Pick & Roll (Unreleased Version)": ("Pick & Roll", "original", "Unreleased Version"),
        }
        for title, expected in cases.items():
            with self.subTest(title):
                self.assertEqual(split_version(title), expected)

    def test_same_song(self):
        self.assertEqual(split_version("Lollipop"), ("Lollipop", None, None))
        self.assertEqual(split_version("Dreamz (Prod. By M&D) (Bonus Track)"), ("Dreamz", None, None))
        self.assertEqual(split_version("Everybody Knows Me (Official Dirty Version)"),
                         ("Everybody Knows Me", None, None))
        self.assertEqual(split_version("Pretty Brown (Unreleased)"), ("Pretty Brown", None, None))

    def test_own_songs(self):
        for title in ("Sure Thing (Freestyle)", "Hands Up (My Last) (Freestyle)", "On tha Block #2 (skit)",
                      "Run the World (Girls) (Freestyle) / Outro", "(Snippet)"):
            with self.subTest(title):
                self.assertEqual(split_version(title), (title, None, None))
        self.assertEqual(split_version("Hands Up (My Last) (Snippet)"), ("Hands Up (My Last)", "snippet", "Snippet"))


if __name__ == "__main__":
    unittest.main()
