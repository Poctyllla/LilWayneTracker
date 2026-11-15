"""Data sources: LilWayneHQ, Wikipedia, Spotify, LRCLIB, Discogs, Genius, streaming search, covers, era texts."""

from pathlib import Path
from unittest import mock
import contextlib
import email.message
import io
import sqlite3
import sys
import tempfile
import unittest
import urllib.error

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from lilwaynehq_source import compare, parse_list_page
from wikipedia_source import parse_release_table, strip_markup
import spotify_source
from spotify_source import album_id, match_tracks
import lyrics_source
from discogs_credits import artist_name, in_range, match_edition, position_number, roles, track_credits
import database
from genius_credits import apply, genius_credits, plan, role_name
from genius_details import song_details
from streaming_search import base_key, best_album
from release_media import is_image, link_rank, local_cover, service_of
from era_info import first_sentences, generated_summary


# ---------- lilwaynehq_source ----------


PAGE = """<html><head><script>var x = "<p>not this</p>";</script></head><body>
<ul><li><a href="/">Home</a></li></ul>
<h1>Lil Wayne Singles</h1>
<p>Here, you will find every single Lil Wayne has officially released since he began.</p>
<p><img src="a.jpg"></p>
<p><a href="/x">Grindin (feat. Drake)</a></p>
<p>Sucker For Pain (with Wiz Khalifa &amp; Imagine Dragons feat. Logic)</p>
<p><a>T-Wayne</a> <span>Lil Wayne &amp; T-Pain</span></p>
<p><a>Senile</a> <span>(with Young Money)</span></p>
<p>If you have any problems with the mixtape downloads, please <a>contact me</a>.</p>
<p>» <a href="https://www.lilwaynehq.com/discography/">Lil Wayne Discography</a></p>
<p>Footer paragraph</p>
<h2>Website</h2>
</body></html>"""


class ParserTests(unittest.TestCase):
    def test_list_page(self):
        heading, entries = parse_list_page(PAGE)
        self.assertEqual(heading, "Lil Wayne Singles")
        self.assertEqual(entries, [
            {"title": "Grindin", "with": None, "featuring": "Drake", "credit": None},
            {"title": "Sucker For Pain", "with": "Wiz Khalifa & Imagine Dragons feat. Logic",
             "featuring": None, "credit": None},
            {"title": "T-Wayne", "with": None, "featuring": None, "credit": "Lil Wayne & T-Pain"},
            {"title": "Senile", "with": "Young Money", "featuring": None, "credit": None},
        ])

    def test_compare(self):
        titles = {"grindin": [("Grindin’", "single", "official")],
                  "dedication 6 reloaded": [("Dedication 6: Reloaded", "mixtape", "official")]}
        entries = [{"title": "Grindin"}, {"title": "Dedication 6"}, {"title": "Glory"},
                   {"title": "D6: Reloaded"}]
        found, missing = compare(entries, titles)
        self.assertEqual([e["title"] for e, _ in found], ["Grindin", "Dedication 6", "D6: Reloaded"])
        self.assertEqual([e["title"] for e, _ in missing], ["Glory"])


# ---------- wikipedia_source ----------


SAMPLE = """==Mixtapes==
{| class="wikitable plainrowheaders"
! scope="col" | Title
|-
! scope="row"| ''SQ6: Tha Remix''<br /><span style="font-size:85%;">(with [[Sqad Up]])</span>
|
* Released: 2003<ref>{{cite web|url=http://x|title=SQ6}}</ref>
* Format: Digital download
| — || — || — || —
|-
! scope="row"| ''[[Da Drought 3]]''
|
* Released: May 11, 2007<ref name="d3"/>
* Label: [[Young Money Entertainment|Young Money]]
* Format: Digital download
| — || — || — || —
|-
! scope="row"| ''[[Trust Fund Babies]]''<br /><span>(with [[Rich the Kid]])</span>
|
* Released: October 1, 2021
* Labels: Young Money, Rostrum, [[Republic Records|Republic]]
* Formats: CD, digital download
| 31 || — || 18 || 15
|}
"""


class WikipediaParserTests(unittest.TestCase):
    def test_rows(self):
        self.assertEqual(parse_release_table(SAMPLE), [
            {"title": "SQ6: Tha Remix", "with": "Sqad Up", "released": "2003",
             "label": None, "format": "Digital download"},
            {"title": "Da Drought 3", "with": None, "released": "May 11, 2007",
             "label": "Young Money", "format": "Digital download"},
            {"title": "Trust Fund Babies", "with": "Rich the Kid", "released": "October 1, 2021",
             "label": "Young Money, Rostrum, Republic", "format": "CD, digital download"},
        ])

    def test_strip_markup(self):
        self.assertEqual(strip_markup("''[[No Ceilings (mixtape)|No Ceilings]]''<ref>x</ref>"),
                         "No Ceilings")
        self.assertEqual(parse_release_table(""), [])


# ---------- spotify_source ----------


class SpotifySourceTests(unittest.TestCase):
    def test_album_id(self):
        self.assertEqual(album_id("https://open.spotify.com/album/1Do3y8IAcbYOToYQJnGwSO"), "1Do3y8IAcbYOToYQJnGwSO")
        self.assertEqual(album_id("https://open.spotify.com/intl-de/album/ABC123"), "ABC123")
        self.assertIsNone(album_id("https://music.apple.com/us/album/x/1"))

    def test_match_tracks(self):
        theirs = [{"disc": 1, "number": 1, "title": "3 Peat", "url": "s1"},
                  {"disc": 1, "number": 2, "title": "Mr. Carter", "url": "s2"},
                  {"disc": 1, "number": 9, "title": "Mrs. Officer", "url": "s9"}]
        ours = [{"disc": 1, "number": 1, "title": "3 Peat"}, {"disc": 1, "number": 2, "title": "Mr Carter"},
                {"disc": 1, "number": 5, "title": "Mrs. Officer"}, {"disc": 1, "number": 7, "title": "Bonus"}]
        self.assertEqual([(a["title"], b["url"]) for a, b in match_tracks(ours, theirs)],
                         [("3 Peat", "s1"), ("Mr Carter", "s2"), ("Mrs. Officer", "s9")])


class RateLimitTests(unittest.TestCase):
    """A long Retry-After stops with RateLimited instead of sleeping for hours."""

    def fake_429(self, seconds):
        def urlopen(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests",
                                         email.message.Message() if seconds is None else self.headers(seconds), None)
        return urlopen

    @staticmethod
    def headers(seconds):
        message = email.message.Message()
        message["Retry-After"] = str(seconds)
        return message

    def test_long_wait_raises(self):
        sleeps = []
        with mock.patch.object(spotify_source, "access_token", return_value="t"),                 mock.patch.object(spotify_source.time, "sleep", sleeps.append),                 mock.patch.object(spotify_source.urllib.request, "urlopen", self.fake_429(36000)):
            with self.assertRaises(spotify_source.RateLimited) as error:
                spotify_source.api_get("/albums/x")
        self.assertEqual(error.exception.seconds, 36000)
        self.assertTrue(all(s < 1 for s in sleeps))  # never slept on the long wait

    def test_short_wait_retries(self):
        sleeps = []
        with mock.patch.object(spotify_source, "access_token", return_value="t"),                 mock.patch.object(spotify_source.time, "sleep", sleeps.append),                 mock.patch.object(spotify_source.urllib.request, "urlopen", self.fake_429(7)),                 contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(spotify_source.api_get("/albums/x", attempts=2))
        self.assertIn(8, sleeps)  # waited Retry-After + 1 once, then gave up


# ---------- lyrics_source ----------


class LyricsSourceTests(unittest.TestCase):
    def setUp(self):
        lyrics_source.CACHE.clear()

    def test_parse_lrc(self):
        text = "[ar:Lil Wayne]\n[00:06.09] Young Money!\n[01:02.5][02:10.00]Hook\nno stamp"
        self.assertEqual(lyrics_source.parse_lrc(text),
                         [[6.09, "Young Money!"], [62.5, "Hook"], [130.0, "Hook"]])
        self.assertEqual(lyrics_source.parse_lrc(None), [])

    def test_choose_prefers_synced_within_the_length(self):
        results = [
            {"id": 1, "duration": 222, "plainLyrics": "a"},
            {"id": 2, "duration": 228, "plainLyrics": "a", "syncedLyrics": "[00:01.00]a"},
            {"id": 3, "duration": 300, "plainLyrics": "a", "syncedLyrics": "[00:01.00]a"},  # other song
            {"id": 4, "duration": 221, "instrumental": True},
        ]
        self.assertEqual(lyrics_source.choose(results, 221)["id"], 2)
        self.assertIsNone(lyrics_source.choose(results[2:], 221))
        self.assertEqual(lyrics_source.choose(results[:1], None)["id"], 1)  # length unknown

    def test_lookup_uses_the_catalogue_and_caches(self):
        connection = sqlite3.connect(":memory:")
        connection.executescript("""
            CREATE TABLE artists (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE recordings (id INTEGER PRIMARY KEY, title TEXT, artist_id INTEGER);
            CREATE TABLE versions (id INTEGER PRIMARY KEY, recording_id INTEGER, version_type TEXT);
            CREATE TABLE release_tracks (id INTEGER PRIMARY KEY, version_id INTEGER, duration_ms INTEGER);
            CREATE TABLE files (id INTEGER PRIMARY KEY, version_id INTEGER, duration REAL);
            INSERT INTO artists VALUES (1, 'Lil Wayne');
            INSERT INTO recordings VALUES (1, 'Mrs. Officer (feat. Bobby V. & Kidd Kidd)', 1);
            INSERT INTO versions VALUES (5, 1, 'album');
            INSERT INTO release_tracks VALUES (1, 5, 286000);
        """)
        calls = []

        def fetcher(title, artist):
            calls.append((title, artist))
            return [{"id": 9, "duration": 287, "plainLyrics": "x", "syncedLyrics": "[00:12.00] Woo"}]

        result = lyrics_source.lookup(connection, 5, fetcher)
        self.assertEqual(calls, [("Mrs. Officer", "Lil Wayne")])  # featuring dropped from the query
        self.assertEqual(result["synced"], [[12.0, "Woo"]])
        lyrics_source.lookup(connection, 5, fetcher)
        self.assertEqual(len(calls), 1)  # memory cache
        self.assertIsNone(lyrics_source.lookup(connection, 99, fetcher))


# ---------- discogs_credits ----------


RELEASE = {
    "extraartists": [
        {"name": "Dwayne Carter", "role": "Executive-Producer", "tracks": ""},
        {"name": "Mr. Scott Design", "role": "Art Direction", "tracks": ""},
        {"name": "Vlado Meller", "role": "Mastered By", "tracks": ""},
        {"name": "Fabian Marasciullo", "role": "Mixed By", "tracks": "1 to 3, 7"},
        {"name": "Dwayne Carter", "role": "Written-By", "tracks": "2"},
    ],
    "tracklist": [
        {"position": "1", "title": "3 Peat", "type": "track", "extraartists": [
            {"name": "Maestro (3)", "role": "Producer", "tracks": ""}]},
        {"position": "2", "title": "Mr. Carter", "type": "track", "extraartists": [
            {"name": "Jay-Z", "role": "Featuring", "tracks": ""},
            {"name": "Infamous", "role": "Producer [Additional], Co-producer", "tracks": ""}]},
        {"position": "", "title": "Bonus", "type": "heading", "extraartists": []},
    ],
}


class DiscogsCreditTests(unittest.TestCase):
    def test_helpers(self):
        self.assertEqual(artist_name("Bryan Williams (2)"), "Birdman")  # Birdman's real name
        self.assertEqual(artist_name("Baby"), "Birdman")
        self.assertEqual(artist_name("Mannie Fresh (3)"), "Mannie Fresh")
        self.assertEqual(artist_name("Dwayne Carter"), "Lil Wayne")
        self.assertEqual(roles("Producer [Additional], Mixed By"), ["producer", "mixing_engineer"])
        self.assertEqual(roles("A&R [Direction]"), [])
        self.assertEqual(position_number("2-5"), (2, 5))
        self.assertEqual(position_number("7"), (1, 7))
        self.assertIsNone(position_number("B2"))
        self.assertTrue(in_range("2", "1 to 3, 7"))
        self.assertTrue(in_range("7", "1 to 3, 7"))
        self.assertFalse(in_range("5", "1 to 3, 7"))
        self.assertTrue(in_range("A2", "A1 to B2"))

    def test_track_credits(self):
        self.assertEqual(track_credits(RELEASE["tracklist"][1], RELEASE), [
            ("Jay-Z", "featured_artist"), ("Infamous", "producer"), ("Infamous", "co_producer"),
            ("Vlado Meller", "mastering_engineer"), ("Fabian Marasciullo", "mixing_engineer"),
            ("Lil Wayne", "writer")])

    def test_match_edition(self):
        ours = [{"disc": 1, "number": 1, "title": "3 Peat"}, {"disc": 1, "number": 2, "title": "Mr. Carter"},
                {"disc": 1, "number": 9, "title": "Something Else"}]
        pairs = match_edition(ours, RELEASE)
        self.assertEqual([(a["title"], b["title"]) for a, b in pairs], [("3 Peat", "3 Peat"), ("Mr. Carter", "Mr. Carter")])


# ---------- genius_credits ----------


GENIUS_SONG = {
    "id": 10, "title": "A Milli", "url": "u", "release_date": "2008-04-29",
    "album": {"id": 1, "name": "Tha Carter III", "artist": {"name": "Lil Wayne"}},
    "primary_artist": {"name": "Lil Wayne"}, "featured_artists": [],
    "producer_artists": [{"name": "Bangladesh"}], "writer_artists": [{"name": "Lil Wayne"}, {"name": "Bangladesh"}],
    "custom_performances": [{"label": "Mixing Engineer", "artists": [{"name": "Fabian Marasciullo"}]},
                            {"label": "Label", "artists": [{"name": "Cash Money Records"}]},
                            {"label": "Phonographic Copyright ℗", "artists": [{"name": "Cash Money"}]}],
}


class GeniusCreditTests(unittest.TestCase):
    def test_song_details_and_roles(self):
        details = song_details(GENIUS_SONG)
        self.assertEqual(details["album"]["name"], "Tha Carter III")
        self.assertEqual(role_name("Mixing Engineer"), "mixing_engineer")
        self.assertEqual(genius_credits(details, own_song=True), [
            ("Bangladesh", "producer"), ("Lil Wayne", "writer"), ("Bangladesh", "writer"),
            ("Fabian Marasciullo", "mixing_engineer")])
        guest = dict(details, primary_artist="Drake", featured_artists=["Lil Wayne"])
        self.assertEqual(genius_credits(guest, own_song=False)[:2],
                         [("Drake", "main_artist"), ("Lil Wayne", "featured_artist")])

    def test_apply_once(self):
        directory = tempfile.TemporaryDirectory()
        old = database.DATABASE_PATH
        database.DATABASE_PATH = Path(directory.name) / "test.db"
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                database.initialize_database()
            c = sqlite3.connect(database.DATABASE_PATH)
            c.execute("PRAGMA foreign_keys = ON")
            recording = c.execute("INSERT INTO recordings (title, artist_id, genius_song_id) "
                                  "VALUES ('A Milli', 1, 10)").lastrowid
            c.execute("INSERT INTO artists (name) VALUES ('Bangladesh')")
            c.execute("INSERT INTO credits (artist_id, recording_id, role) "
                      "SELECT id, ?, 'producer' FROM artists WHERE name = 'Bangladesh'", (recording,))
            c.commit()
            cache = {10: song_details(GENIUS_SONG), 11: None}
            additions, missing = plan(c, cache)
            self.assertEqual(missing, 0)
            self.assertEqual([(name, role) for _, _, name, role in additions],
                             [("Lil Wayne", "writer"), ("Bangladesh", "writer"), ("Fabian Marasciullo", "mixing_engineer")])
            with c:
                apply(c, additions)
            self.assertEqual(plan(c, cache)[0], [])
            self.assertEqual(c.execute("SELECT count(*) FROM credits WHERE source = 'Genius'").fetchone()[0], 3)
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
            c.close()
        finally:
            database.DATABASE_PATH = old
            directory.cleanup()


# ---------- streaming_search ----------


def track(n, title, url=None):
    return {"disc": 1, "number": n, "title": title, "url": url}


class StreamingSearchTests(unittest.TestCase):
    def test_base_key(self):
        self.assertEqual(base_key("Tha Carter V (Deluxe)"), base_key("Tha Carter V"))
        self.assertEqual(base_key("A Milli - Single"), base_key("A Milli"))
        self.assertEqual(base_key("Guerrilla Warfare (Explicit Version)"), base_key("Guerrilla Warfare"))
        self.assertNotEqual(base_key("Tha Carter IV"), base_key("Tha Carter V"))

    def test_best_album(self):
        ours = [track(1, "Intro"), track(2, "We On Fire"), track(3, "Respect My Mind"), track(4, "Help")]
        right = {"title": "Guerrilla Warfare", "url": "a", "tracks": [
            track(1, "Intro", "t1"), track(2, "We On Fire", "t2"), track(3, "Respect My Mind", "t3")]}
        wrong_title = {"title": "Let 'Em Burn", "url": "b", "tracks": right["tracks"]}
        thin = {"title": "Guerrilla Warfare", "url": "c", "tracks": [track(1, "Intro", "x")]}
        album, pairs = best_album("Guerrilla Warfare", ours, [wrong_title, thin, right])
        self.assertEqual(album["url"], "a")
        self.assertEqual(len(pairs), 3)
        self.assertEqual(best_album("Guerrilla Warfare", ours, [thin, wrong_title]), (None, []))  # < 60 %


# ---------- release_media ----------


class ReleaseMediaTests(unittest.TestCase):
    def test_services(self):
        self.assertEqual(service_of("https://open.spotify.com/album/1Do3y8IAcbYOToYQJnGwSO"), "spotify")
        self.assertEqual(service_of("https://music.apple.com/us/album/tha-carter-iii/1440737099"), "apple_music")
        self.assertEqual(service_of("https://itunes.apple.com/gb/album/id287553396"), "apple_music")
        self.assertEqual(service_of("https://music.youtube.com/playlist?list=X"), "youtube_music")
        self.assertEqual(service_of("https://www.deezer.com/album/213327"), "deezer")
        self.assertIsNone(service_of("https://example.com"))
        self.assertLess(link_rank("apple_music", "https://music.apple.com/us/album/x"),
                        link_rank("apple_music", "https://itunes.apple.com/gb/album/x"))

    def test_local_cover(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            jpeg = b"\xff\xd8\xff" + b"0" * 100
            (folder / "01-song.mp3").write_bytes(b"x")
            (folder / "back.jpg").write_bytes(jpeg * 3)
            (folder / "00-Release-COVER.jpg").write_bytes(jpeg)
            (folder / "inside.jpg").write_bytes(jpeg * 5)
            self.assertEqual(local_cover([str(folder / "01-song.mp3")]).name, "00-Release-COVER.jpg")
            self.assertIsNone(local_cover([str(folder / "missing" / "x.mp3")]))
        self.assertTrue(is_image(b"\x89PNG\r\n\x1a\nrest"))
        self.assertFalse(is_image(b"<html>"))


# ---------- era_info ----------


class EraInfoTests(unittest.TestCase):
    def test_first_sentences(self):
        text = ("Tha Carter III is the sixth studio album by Lil Wayne, released on June 10, 2008.[1] "
                "It follows a string of mixtapes. It sold 1 million copies. A fourth sentence.")
        self.assertEqual(first_sentences(text),
                         "Tha Carter III is the sixth studio album by Lil Wayne, released on June 10, 2008. "
                         "It follows a string of mixtapes. It sold 1 million copies.")
        self.assertEqual(first_sentences("No end mark"), "No end mark")
        self.assertEqual(first_sentences(""), "")

    def test_generated_summary(self):
        eras = [{"name": "Tha Carter V", "end_date": "2018-09-28", "release_group_id": "a"},
                {"name": "Tha Carter VI", "end_date": "2025-06-06", "release_group_id": "b"},
                {"name": "After Tha Carter VI", "end_date": None, "release_group_id": None}]
        self.assertIn("after Tha Carter VI", generated_summary(eras[2], eras))


if __name__ == "__main__":
    unittest.main()


class AudioServiceStorageTests(unittest.TestCase):
    """Library audio is played from the Audio Service's storage; the old path is the fallback."""

    def test_service_copy_first_then_original(self):
        import hashlib
        import audio_providers
        data = b"ID3" + bytes(500)
        sha = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = root / "library" / "song.mp3"
            original.parent.mkdir()
            original.write_bytes(data)
            connection = sqlite3.connect(":memory:")
            connection.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, file_path TEXT, sha256 TEXT)")
            connection.execute("INSERT INTO files VALUES (1, ?, ?)", (str(original), sha))
            with mock.patch.dict("os.environ", {"AUDIO_SERVICE_DATA": str(root / "service")}):
                self.assertEqual(audio_providers.audio_file(connection, 1), (original, "audio/mpeg"))
                stored = root / "service" / "objects" / sha[:2] / sha[2:4] / sha
                stored.parent.mkdir(parents=True)
                stored.write_bytes(data)
                original.unlink()  # moved: only the service copy is left
                self.assertEqual(audio_providers.audio_file(connection, 1), (stored, "audio/mpeg"))
                connection.execute("UPDATE files SET sha256 = '../../x'")
                self.assertIsNone(audio_providers.audio_file(connection, 1))
