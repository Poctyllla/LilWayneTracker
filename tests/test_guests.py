"""Guest appearances, unreleased songs, song cross-check and review decisions."""

from pathlib import Path
import contextlib
import io
import sqlite3
import sys
import tempfile
import unittest

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import database
from duplicate_guard import load_catalogue_from_connection, song_without_main_version
from guest_appearances import apply, hq_credits, musicbrainz_credits, plan, promote_released_guests
from guest_survey import artist_key, guest_song_key
from guest_matches import attach_to_catalogued, catalogue_facts, judge
from unreleased_songs import apply as apply_unreleased_songs, genius_date, hq_credits as hq_credits_unreleased_songs, plan as plan_unreleased_songs
from song_crosscheck import CatalogueIndex, classify, collect_songs, similar, song_key
from song_review import apply as apply_song_review, plan as plan_song_review


# ---------- guest_appearances ----------


def survey_song(title, artist, mb=None, genius=None, hq=None, **extra):
    return {"title": title, "main_artist": artist, "in_catalogue": [], "group_song": False,
            "musicbrainz": mb or [], "genius": genius or [], "hq": bool(hq), "hq_credit": hq,
            "hq_projects": extra.pop("hq_projects", []), **extra}


class CreditTests(unittest.TestCase):
    def test_musicbrainz_credits(self):
        self.assertEqual(musicbrainz_credits([["Drake", " feat. "], ["Lil Wayne", ""]]),
                         [{"artist": "Drake", "role": "main_artist"}, {"artist": "Lil Wayne", "role": "featured_artist"}])
        self.assertEqual(musicbrainz_credits([["2 Chainz", " & "], ["Lil Wayne", ""]]),
                         [{"artist": "2 Chainz", "role": "main_artist"}, {"artist": "Lil Wayne", "role": "main_artist"}])

    def test_hq_credits(self):
        self.assertEqual(hq_credits("Juvenile featuring Lil Wayne, Turk, and B.G."),
                         [{"artist": "Juvenile", "role": "main_artist"},
                          {"artist": "Lil Wayne", "role": "featured_artist"},
                          {"artist": "Turk", "role": "featured_artist"},
                          {"artist": "B.G.", "role": "featured_artist"}])

    def test_keys(self):
        self.assertEqual(artist_key("Baby"), artist_key("Birdman"))
        self.assertEqual(artist_key("T‐Pain"), artist_key("T-Pain"))
        self.assertEqual(guest_song_key("Rich As F#ck"), guest_song_key("Rich As F*ck"))


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self._path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self._directory.name) / "test.db"
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
        self.c = sqlite3.connect(database.DATABASE_PATH)
        self.c.execute("PRAGMA foreign_keys = ON")

    def tearDown(self):
        self.c.close()
        database.DATABASE_PATH = self._path
        self._directory.cleanup()

    def test_plan_and_apply(self):
        survey = {"songs": [
            survey_song("Forever", "Drake", mb=[
                {"id": "mb-1", "first_release_date": "2009-08-28", "credit_parts": [
                    ["Drake", ", "], ["Kanye West", ", "], ["Lil Wayne", " & "], ["Eminem", ""]]},
                {"id": "mb-2", "first_release_date": "2009-09-15", "credit_parts": [["Drake", ""]]}],
                genius=[{"id": 77, "url": "https://genius.com/x", "release_date": "August 28, 2009"}]),
            survey_song("Unknown Feature", "Rick Ross", genius=[{"id": 78, "url": "https://genius.com/y",
                                                                  "release_date": None}]),
            survey_song("Motivation", "DJ Drama", in_catalogue=[5]),
            survey_song("Neva Get Enuf", "Hot Boys", group_song=True),
        ]}
        actions, skipped = plan(survey, self.c)
        self.assertEqual([a["title"] for a in actions], ["Forever", "Unknown Feature"])
        self.assertEqual(skipped, {"already in the catalogue": 1, "group song": 1})
        with self.c:
            apply(self.c, actions)
        q = lambda sql, *a: self.c.execute(sql, a).fetchall()
        forever = q("SELECT r.id, a.name, r.appearance, r.musicbrainz_recording_id, r.recording_date "
                    "FROM recordings r JOIN artists a ON a.id = r.artist_id WHERE r.title = 'Forever'")[0]
        self.assertEqual(forever[1:], ("Drake", "guest", "mb-1", "2009-08-28"))
        self.assertEqual(q("SELECT availability FROM recording_availability WHERE recording_id = ?", forever[0]),
                         [("released",)])
        self.assertEqual(q("SELECT musicbrainz_recording_id FROM recording_musicbrainz_ids"), [("mb-2",)])
        self.assertEqual(sorted(q("SELECT a.name, c.role FROM credits c JOIN artists a ON a.id = c.artist_id "
                                  "WHERE c.recording_id = ?", forever[0])),
                         [("Drake", "main_artist"), ("Eminem", "main_artist"), ("Kanye West", "main_artist"),
                          ("Lil Wayne", "main_artist")])
        unknown = q("SELECT a.availability FROM recordings r JOIN recording_availability a "
                    "ON a.recording_id = r.id WHERE r.title = 'Unknown Feature'")
        self.assertEqual(unknown, [("unreleased",)])
        # A guest song is never taken for Lil Wayne's own song of that title.
        self.assertIsNone(song_without_main_version("Forever", load_catalogue_from_connection(self.c)))
        # Second run adds nothing.
        self.assertEqual(plan(survey, self.c)[0], [])
        # The song later appears on a release of Wayne's discography -> his song.
        self.c.execute("INSERT INTO releases (id, title, artist_id, release_type, discography) "
                       "VALUES (1, 'Collab', 1, 'collaboration_album', 'official')")
        version = self.c.execute("INSERT INTO versions (recording_id, version_name, version_type) "
                                 "VALUES (?, 'Album Version', 'album')", (forever[0],)).lastrowid
        self.c.execute("INSERT INTO release_tracks (release_id, version_id, disc_number, track_number) "
                       "VALUES (1, ?, 1, 1)", (version,))
        with self.c:
            self.assertEqual(promote_released_guests(self.c), [forever[0]])
        self.assertEqual(q("SELECT r.appearance, a.name, r.availability_override FROM recordings r "
                           "JOIN artists a ON a.id = r.artist_id WHERE r.id = ?", forever[0]),
                         [(None, "Lil Wayne", None)])
        self.assertIn(("Drake", "main_artist"), q("SELECT a.name, c.role FROM credits c JOIN artists a "
                                                  "ON a.id = c.artist_id WHERE c.recording_id = ?", forever[0]))


# ---------- guest_matches ----------


def song(title, artist, candidates, mb=(), genius=()):
    return {"title": title, "main_artist": artist, "in_catalogue": list(candidates), "sources": "x",
            "musicbrainz": [{"id": i, "length": length, "credit": f"{artist} feat. Lil Wayne",
                             "credit_parts": [[artist, " feat. "], ["Lil Wayne", ""]]} for i, length in mb],
            "genius": [{"id": g, "url": f"https://genius.com/{g}"} for g in genius], "hq": False}


class GuestMatchTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self._path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self._directory.name) / "test.db"
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
        c = self.c = sqlite3.connect(database.DATABASE_PATH)
        c.execute("PRAGMA foreign_keys = ON")
        c.execute("INSERT INTO releases (id, title, artist_id, release_type, discography) "
                  "VALUES (1, 'Mixtape', 1, 'mixtape', 'official')")
        self.ids = {}
        for number, (title, mbid, duration) in enumerate(
                [("Motivation", None, 200000), ("Rock Star", None, 150000), ("Ya Heard Me", "mb-yhm", 240000)],
                start=1):
            recording = c.execute("INSERT INTO recordings (title, artist_id, musicbrainz_recording_id) "
                                  "VALUES (?, 1, ?)", (title, mbid)).lastrowid
            version = c.execute("INSERT INTO versions (recording_id, version_name, version_type) "
                                "VALUES (?, 'Album Version', 'album')", (recording,)).lastrowid
            c.execute("INSERT INTO release_tracks (release_id, version_id, disc_number, track_number, duration_ms) "
                      "VALUES (1, ?, 1, ?, ?)", (version, number, duration))
            self.ids[title] = recording
        c.commit()

    def tearDown(self):
        self.c.close()
        database.DATABASE_PATH = self._path
        self._directory.cleanup()

    def test_judge(self):
        facts = catalogue_facts(self.c)
        self.assertEqual(judge(song("Ya Heard Me", "B.G.", [self.ids["Ya Heard Me"]], mb=[("mb-yhm", 1)]), facts)[:2],
                         ("same", self.ids["Ya Heard Me"]))
        self.assertEqual(judge(song("Motivation", "DJ Drama", [self.ids["Motivation"]]), facts)[:2],
                         ("same", self.ids["Motivation"]))
        self.assertEqual(judge(song("Rock Star", "Chamillionaire", [self.ids["Rock Star"]],
                                    mb=[("mb-rs", 260000)]), facts)[0], "different")
        self.assertEqual(judge(song("Rock Star", "Chamillionaire", [self.ids["Rock Star"]],
                                    mb=[("mb-rs", 155000)]), facts)[0], "same")
        self.assertEqual(judge(song("Rock Star", "Chamillionaire", [self.ids["Rock Star"]], genius=[5]), facts)[0],
                         "review")
        # A credit does not outweigh lengths that differ by more than 15 s.
        facts[self.ids["Rock Star"]]["artists"].add("chamillionaire")
        self.assertEqual(judge(song("Rock Star", "Chamillionaire", [self.ids["Rock Star"]],
                                    mb=[("mb-rs", 260000)]), facts)[0], "different")

    def test_genius_album_settles_review(self):
        facts = catalogue_facts(self.c)
        review = song("Rock Star", "Chamillionaire", [self.ids["Rock Star"]], genius=[5])
        on_mixtape = {5: {"album": {"name": "Mixtape"}}}
        elsewhere = {5: {"album": {"name": "The Sound of Revenge"}}}
        self.assertEqual(judge(review, facts, on_mixtape)[:2], ("same", self.ids["Rock Star"]))
        self.assertEqual(judge(review, facts, elsewhere)[0], "different")
        self.assertEqual(judge(review, facts, {5: {"album": None}})[0], "review")

    def test_attach(self):
        recording = self.ids["Rock Star"]
        with self.c:
            attach_to_catalogued(self.c, song("Rock Star", "Chamillionaire", [recording],
                                              mb=[("mb-rs", 155000)], genius=[5]), recording)
            attach_to_catalogued(self.c, song("Motivation", "DJ Drama", [self.ids["Motivation"]], genius=[6]),
                                 self.ids["Motivation"])
        q = lambda sql, *a: self.c.execute(sql, a).fetchall()
        self.assertEqual(q("SELECT recording_id, musicbrainz_recording_id FROM recording_musicbrainz_ids"),
                         [(recording, "mb-rs")])
        self.assertEqual(q("SELECT genius_song_id FROM recordings WHERE id = ?", recording), [(5,)])
        self.assertEqual(q("SELECT a.name, c.role FROM credits c JOIN artists a ON a.id = c.artist_id "
                           "WHERE c.recording_id = ?", recording), [("Chamillionaire", "main_artist")])
        self.assertEqual(q("SELECT count(*) FROM credits WHERE recording_id = ?", self.ids["Motivation"]), [(0,)])
        self.assertEqual(q("PRAGMA foreign_key_check"), [])


# ---------- unreleased_songs ----------


def song_unreleased_songs(title, status, catalogue=(), genius_id=None, hq=True, role="lead"):
    return {"title": title, "status": status, "role": role,
            "catalogue": [{"recording_id": r, "availability": a} for r, a in catalogue],
            "genius": [{"id": genius_id, "url": f"https://genius.com/{genius_id}",
                        "release_date": "March 3, 2008"}] if genius_id else [],
            "hq_projects": [], "hq_artists": ["Lil Wayne featuring Gudda Gudda"] if hq else []}


class HelperTests(unittest.TestCase):
    def test_hq_credits(self):
        self.assertEqual(hq_credits_unreleased_songs(["Lil Wayne featuring Gudda Gudda & Kidd Kidd"]),
                         [{"artist": "Gudda Gudda", "role": "featured_artist"},
                          {"artist": "Kidd Kidd", "role": "featured_artist"}])
        self.assertEqual(hq_credits_unreleased_songs(["Lil Wayne and Juelz Santana"]),
                         [{"artist": "Juelz Santana", "role": "main_artist"}])
        self.assertEqual(hq_credits_unreleased_songs(["Lil Wayne"]), [])

    def test_genius_date(self):
        self.assertEqual(genius_date("March 3, 2008"), "2008-03-03")
        self.assertEqual(genius_date("March 2008"), "2008-03")
        self.assertEqual(genius_date("2008"), "2008")
        self.assertIsNone(genius_date(None))


class UnreleasedApplyTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self._path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self._directory.name) / "test.db"
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
        self.c = sqlite3.connect(database.DATABASE_PATH)
        self.c.execute("PRAGMA foreign_keys = ON")
        self.c.execute("INSERT INTO releases (id, title, artist_id, release_type, discography) "
                       "VALUES (1, 'Album', 1, 'studio_album', 'official')")
        self.milli = self.c.execute("INSERT INTO recordings (title, artist_id) VALUES ('A Milli', 1)").lastrowid
        version = self.c.execute("INSERT INTO versions (recording_id, version_name, version_type) "
                                 "VALUES (?, 'Album Version', 'album')", (self.milli,)).lastrowid
        self.c.execute("INSERT INTO release_tracks (release_id, version_id, disc_number, track_number, duration_ms) "
                       "VALUES (1, ?, 1, 1, 221000)", (version,))
        self.c.commit()

    def tearDown(self):
        self.c.close()
        database.DATABASE_PATH = self._path
        self._directory.cleanup()

    def test_plan_and_apply(self):
        crosscheck = {"songs": [
            song_unreleased_songs("A Milli", "in_catalogue", [(self.milli, "released")], genius_id=11),
            song_unreleased_songs("A Milli [Demo]", "variant", [(self.milli, "released")], genius_id=12),
            song_unreleased_songs("A Milly", "probable", [(self.milli, "released")]),
            song_unreleased_songs("Brand New Song", "missing", genius_id=13),
            song_unreleased_songs("Brand New Song (Snippet)", "missing"),
            song_unreleased_songs("Lonely Snippet (Snippet)", "missing"),
            song_unreleased_songs("Lil Wayne Buffalo Tweet", "missing"),
            song_unreleased_songs("Guest Verse", "missing", role="featured"),
        ]}
        actions, skipped = plan_unreleased_songs(crosscheck, self.c)
        self.assertEqual(sorted(a["action"] for a in actions),
                         ["alias", "genius_id", "song", "version", "version", "version"])
        self.assertEqual(skipped, [("Lil Wayne Buffalo Tweet", "not a song")])
        with self.c:
            apply_unreleased_songs(self.c, actions)
        q = lambda sql, *args: self.c.execute(sql, args).fetchall()
        self.assertEqual(q("SELECT genius_song_id FROM recordings WHERE id = ?", self.milli), [(11,)])
        self.assertEqual(q("SELECT version_type, version_name, genius_song_id, first_known_date FROM versions "
                           "WHERE recording_id = ? AND version_type = 'demo'", self.milli),
                         [("demo", "Demo", 12, "2008-03-03")])
        self.assertEqual(q("SELECT title FROM recording_aliases"), [("A Milly",)])
        new = q("SELECT r.id, a.availability FROM recordings r JOIN recording_availability a "
                "ON a.recording_id = r.id WHERE r.title = 'Brand New Song'")
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0][1], "unreleased")
        self.assertEqual(q("SELECT version_type FROM versions WHERE recording_id = ?", new[0][0]), [("snippet",)])
        self.assertEqual(q("SELECT title FROM recordings WHERE title = 'Lonely Snippet'"), [("Lonely Snippet",)])
        self.assertEqual(q("SELECT a.name, c.role FROM credits c JOIN artists a ON a.id = c.artist_id "
                           "WHERE c.recording_id = ?", new[0][0]), [("Gudda Gudda", "featured_artist")])
        self.assertEqual(q("SELECT count(*) FROM recording_sources rs JOIN sources s ON s.id = rs.source_id "
                           "WHERE rs.recording_id = ?", new[0][0]), [(2,)])  # Genius + HQ
        self.assertEqual(q("PRAGMA foreign_key_check"), [])
        # A later full track "Brand New Song" becomes the song's main version.
        catalogue = load_catalogue_from_connection(self.c)
        self.assertEqual(song_without_main_version("Brand New Song", catalogue), new[0][0])
        # The alias finds the song_unreleased_songs too.
        self.assertIn(self.milli, catalogue["by_title"]["a milly"])
        # A second run with the same (old) cross-check creates nothing new.
        actions, _ = plan_unreleased_songs(crosscheck, self.c)
        self.assertEqual([a["action"] for a in actions if a["action"] in ("song", "version")], [])


# ---------- song_crosscheck ----------


CATALOGUE = [
    (1, "A Milli", "released"),
    (2, "F**k Wit Me Now", "released"),
    (3, "Black Republican", "released"),
    (4, "Lisa Marie", "leaked"),
]


TRACKS = {"da drought 3": [(3, "Black Republican", "released")],
          "the prefix": [(5, "Lucifer", "released")]}


def hq(title, project="–", artists="Lil Wayne"):
    return {"Song Title": title, "Project": project, "Artists": artists}


def genius(title, genius_id=1):
    return {"genius_id": genius_id, "title": title, "url": "u", "primary_artist_id": 4}


class KeyTests(unittest.TestCase):
    def test_keys(self):
        self.assertEqual(song_key("A Milli (Mastered)"), song_key("A Milli"))
        self.assertNotEqual(song_key("A Milli [Demo]"), song_key("A Milli"))

    def test_similar(self):
        self.assertTrue(similar("Black Republicans", "Black Republican"))
        self.assertTrue(similar("Gucci Gucci", "Gucci Gucci (Freestyle)"))
        self.assertFalse(similar("Lucifer (Remix)", "Lucifer"))
        self.assertFalse(similar("Heat [Original]", "Heat"))
        self.assertFalse(similar("On Tha Block #1", "On tha Block #2 (skit)"))


class ClassifyTests(unittest.TestCase):
    def run_classify(self, genius_songs, hq_rows):
        songs, skipped = collect_songs(genius_songs, hq_rows)
        classify(songs, CatalogueIndex(CATALOGUE), TRACKS)
        return {song["title"]: song for song in songs.values()}, skipped

    def test_statuses(self):
        songs, skipped = self.run_classify(
            [genius("A Milli"), genius("Fuck Wit Me Now"), genius("Lisa Marie (Instrumental)")],
            [hq("A Milli [Demo]"), hq("Black Republicans", "Da Drought 3"), hq("Lucifer (Remix)", "The Prefix"),
             hq("Brand New Song"), hq("Some Hit", "X", "Nicki Minaj featuring Lil Wayne")])
        self.assertEqual(skipped, 1)
        self.assertEqual(songs["A Milli"]["status"], "in_catalogue")
        self.assertEqual(songs["Fuck Wit Me Now"]["catalogue"], [{"recording_id": 2, "availability": "released"}])
        self.assertEqual(songs["A Milli [Demo]"]["status"], "variant")
        self.assertEqual(songs["Black Republicans"]["status"], "probable")
        self.assertEqual(songs["Lucifer (Remix)"]["status"], "missing")
        self.assertEqual(songs["Brand New Song"]["status"], "missing")
        self.assertEqual(songs["Brand New Song"]["role"], "lead")
        self.assertEqual(songs["Some Hit"]["role"], "featured")

    def test_sources_merge(self):
        songs, _ = self.run_classify([genius("Brand New Song")], [hq("Brand New Song")])
        self.assertEqual(songs["Brand New Song"]["sources"], "genius+hq")


# ---------- song_review ----------


class SongReviewTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self._path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self._directory.name) / "test.db"
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
        self.c = sqlite3.connect(database.DATABASE_PATH)
        self.c.execute("PRAGMA foreign_keys = ON")
        self.c.execute("INSERT INTO releases (id, title, artist_id, release_type, discography) "
                       "VALUES (1, 'Sorry 4 the Wait', 1, 'mixtape', 'official'), "
                       "(2, 'Sorry 4 the Wait', 1, 'mixtape', 'official')")
        self.ids = {}
        for number, (title, release_id) in enumerate(
                [("Grove St. Party (Freestyle)", 1), ("Grove Party", 2), ("Miami", 1), ("Miami", 2)], start=1):
            recording = self.c.execute("INSERT INTO recordings (title, artist_id) VALUES (?, 1)", (title,)).lastrowid
            version = self.c.execute("INSERT INTO versions (recording_id, version_name, version_type) "
                                     "VALUES (?, 'Album Version', 'album')", (recording,)).lastrowid
            self.c.execute("INSERT INTO release_tracks (release_id, version_id, disc_number, track_number) "
                           "VALUES (?, ?, 1, ?)", (release_id, version, number))
            self.ids[f"{title}:{release_id}"] = recording
        self.c.commit()

    def tearDown(self):
        self.c.close()
        database.DATABASE_PATH = self._path
        self._directory.cleanup()

    def test_apply_and_rerun(self):
        grove, grove_2022 = self.ids["Grove St. Party (Freestyle):1"], self.ids["Grove Party:2"]
        miami, miami_original = self.ids["Miami:1"], self.ids["Miami:2"]
        decisions = [
            {"action": "merge", "keep": grove, "drop": grove_2022},
            {"action": "alias", "recording": miami, "title": "Miami (Blow)", "source": "LilWayneHQ"},
            {"action": "make_version", "recording": miami_original, "of": miami,
             "version_type": "original", "version_name": "Original"},
            {"action": "add_version", "of": miami, "title": "Miami [Demo]", "version_type": "demo",
             "version_name": "Demo", "hq": True, "credits": [{"artist": "Curren$y", "role": "featured_artist"}]},
            {"action": "keep_separate", "recordings": [grove, miami]},
        ]
        planned = plan_song_review(self.c, decisions)
        self.assertEqual([status for _, status in planned], ["apply"] * 4 + ["noted"])
        with self.c:
            apply_song_review(self.c, planned)
        q = lambda sql, *args: self.c.execute(sql, args).fetchall()
        self.assertEqual(q("SELECT count(*) FROM recordings WHERE id = ?", grove_2022), [(0,)])
        self.assertEqual(q("SELECT track_title FROM release_tracks WHERE release_id = 2 AND track_number = 2"),
                         [("Grove Party",)])
        self.assertIn(("Grove Party",), q("SELECT title FROM recording_aliases WHERE recording_id = ?", grove))
        self.assertEqual(sorted(q("SELECT version_type FROM versions WHERE recording_id = ?", miami)),
                         [("album",), ("demo",), ("original",)])
        self.assertEqual(q("PRAGMA foreign_key_check"), [])
        self.assertEqual([status for _, status in plan_song_review(self.c, decisions)], ["done"] * 4 + ["noted"])


class DetachVersionTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self._path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self._directory.name) / "test.db"
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
        c = self.c = sqlite3.connect(database.DATABASE_PATH)
        c.execute("PRAGMA foreign_keys = ON")
        c.execute("INSERT INTO releases (id, title, artist_id, release_type, release_date, discography) VALUES "
                  "(1, 'Funeral', 1, 'studio_album', '2020-01-31', 'official'), "
                  "(2, 'SQ2', 1, 'mixtape', '2002', 'official'), (3, 'Bootleg', 1, 'bootleg', '2009', 'unofficial')")
        self.help = c.execute("INSERT INTO recordings (title, artist_id) VALUES ('Help', 1)").lastrowid
        album = c.execute("INSERT INTO versions (recording_id, version_name, version_type) "
                          "VALUES (?, 'Album Version', 'album')", (self.help,)).lastrowid
        self.remix = c.execute("INSERT INTO versions (recording_id, version_name, version_type, musicbrainz_recording_id) "
                               "VALUES (?, 'remix', 'remix', 'mb-help-remix')", (self.help,)).lastrowid
        self.lost_remix = c.execute("INSERT INTO versions (recording_id, version_name, version_type) "
                                    "VALUES (?, 'Remix', 'remix')", (self.help,)).lastrowid
        for release_id, version, number in ((1, album, 2), (2, self.remix, 13), (3, self.lost_remix, 3)):
            c.execute("INSERT INTO release_tracks (release_id, version_id, disc_number, track_number) VALUES (?, ?, 1, ?)",
                      (release_id, version, number))
        c.execute("INSERT INTO files (version_id, file_path) VALUES (?, 'C:/x/lost.mp3')", (self.lost_remix,))
        c.execute("INSERT INTO artists (id, name) VALUES (50, 'Gorilla Zoe')")
        self.guest = c.execute("INSERT INTO recordings (title, artist_id, appearance) "
                               "VALUES ('Lost (Remix)', 50, 'guest')").lastrowid
        c.commit()

    def tearDown(self):
        self.c.close()
        database.DATABASE_PATH = self._path
        self._directory.cleanup()

    def test_detach(self):
        decisions = [
            {"action": "detach_version", "version": self.remix, "from": self.help, "title": "Help (remix)"},
            {"action": "detach_version", "version": self.lost_remix, "from": self.help, "to": self.guest},
        ]
        planned = plan_song_review(self.c, decisions)
        self.assertEqual([s for _, s in planned], ["apply", "apply"])
        with self.c:
            apply_song_review(self.c, planned)
        q = lambda sql, *a: self.c.execute(sql, a).fetchall()
        new_song = q("SELECT recording_id, version_type FROM versions WHERE id = ?", self.remix)[0]
        self.assertNotEqual(new_song[0], self.help)
        self.assertEqual(new_song[1], "album")
        self.assertEqual(q("SELECT title, musicbrainz_recording_id FROM recordings WHERE id = ?", new_song[0]),
                         [("Help (remix)", "mb-help-remix")])
        self.assertEqual(q("SELECT track_title FROM release_tracks WHERE version_id = ?", self.remix), [("Help",)])
        self.assertEqual(q("SELECT recording_id, version_type FROM versions WHERE id = ?", self.lost_remix),
                         [(self.guest, "album")])
        self.assertEqual(q("SELECT appearance FROM recordings WHERE id = ?", self.guest), [("guest",)])
        self.assertEqual(q("SELECT count(*) FROM versions WHERE recording_id = ?", self.help), [(1,)])
        self.assertEqual([s for _, s in plan_song_review(self.c, decisions)], ["done", "done"])
        self.assertEqual(q("PRAGMA foreign_key_check"), [])

    def test_split_off(self):
        album = self.c.execute("SELECT id FROM versions WHERE recording_id = ? AND version_type = 'album'",
                               (self.help,)).fetchone()[0]
        with self.c:
            self.c.execute("UPDATE release_tracks SET duration_ms = 142000 WHERE version_id = ?", (album,))
            self.c.execute("INSERT INTO release_tracks (release_id, version_id, disc_number, track_number, duration_ms) "
                           "VALUES (3, ?, 1, 9, 150000)", (album,))
            self.c.execute("INSERT INTO files (version_id, file_path, duration) VALUES (?, 'C:/x/help-2009.mp3', 150.3)",
                           (album,))
        decision = {"action": "split_off", "recording": self.help, "releases": [3], "title": "Help"}
        planned = plan_song_review(self.c, [decision])
        self.assertEqual(planned[0][1], "apply")
        with self.c:
            apply_song_review(self.c, planned)
        q = lambda sql, *a: self.c.execute(sql, a).fetchall()
        moved = q("SELECT v.recording_id FROM release_tracks rt JOIN versions v ON v.id = rt.version_id "
                  "WHERE rt.release_id = 3 AND rt.track_number = 9")[0][0]
        self.assertNotEqual(moved, self.help)
        self.assertEqual(q("SELECT v.recording_id FROM files f JOIN versions v ON v.id = f.version_id "
                           "WHERE f.file_path = 'C:/x/help-2009.mp3'"), [(moved,)])
        self.assertEqual(plan_song_review(self.c, [decision])[0][1], "done")

    def test_detach_last_version_merges_the_empty_song(self):
        lonely = self.c.execute("INSERT INTO recordings (title, artist_id) VALUES ('Someone To Love Me', 1)").lastrowid
        with self.c:
            self.c.execute("UPDATE versions SET recording_id = ? WHERE id = ?", (lonely, self.lost_remix))
        decision = {"action": "detach_version", "version": self.lost_remix, "from": lonely, "to": self.guest}
        with self.c:
            apply_song_review(self.c, plan_song_review(self.c, [decision]))
        self.assertEqual(self.c.execute("SELECT count(*) FROM recordings WHERE id = ?", (lonely,)).fetchone()[0], 0)
        self.assertIn(("Someone To Love Me",), self.c.execute(
            "SELECT title FROM recording_aliases WHERE recording_id = ?", (self.guest,)).fetchall())


if __name__ == "__main__":
    unittest.main()
