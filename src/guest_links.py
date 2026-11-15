"""Apple Music track links for guest appearances (songs by other artists with Lil Wayne).

The release-based search (streaming_search.py) covers Lil Wayne's own releases;
most guest songs are on no catalogued release, so they had no player.  For every
released guest song whose Genius / MusicBrainz data names Lil Wayne as a performer
(main or featured -- not only as a writer) the iTunes Lookup API (no key, US store)
is used.  The iTunes *search* hides explicit editions, *lookup* does not, so:
  1. the main artist's Apple id (a search for the artist, exact name);
  2. that artist's songs (lookup, up to 200);
  3. if the song is not among them: the album Genius names for the song, found in
     the artist's albums (lookup), and that album's tracks (lookup).
A track is accepted only when its title is the song's title (featuring notes ignored),
the artist is the song's main artist, Lil Wayne is in its artist or title, and it is
neither a censored ("cleaned") edition nor a karaoke / instrumental / sped up ...
version.  The song gets a main version (if it has none) with the link in
version_links (service apple_music, source 'iTunes API').
Answers are cached in exports/guest_links.json.

usage:
    python guest_links.py            # dry run (looks up, prints the matches)
    python guest_links.py --apply    # store the links (back up first: python database.py --backup <label>)
"""

import argparse
import json
import re
import sqlite3
import urllib.parse
from pathlib import Path

from import_release import DATABASE_PATH
from streaming_search import itunes_get, letters

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = PROJECT_ROOT / "exports" / "guest_links.json"
GENIUS_PATH = PROJECT_ROOT / "exports" / "genius_song_details.json"
WAYNE = re.compile(r"\blil'? wayne\b", re.IGNORECASE)
NOTES = re.compile(r"\s*[(\[][^)\]]*[)\]]")
UNWANTED = re.compile(r"karaoke|instrumental|clean|sped up|slowed|acapella|a cappella|tribute|8d audio", re.IGNORECASE)
TRACK_FIELDS = ("trackName", "artistName", "trackViewUrl", "trackExplicitness", "collectionName", "releaseDate")


def performer_songs(connection):
    """[(recording id, title, main artist, availability, genius album)] of released guest
    songs with Lil Wayne as a performer on Genius or MusicBrainz."""
    genius = json.loads(GENIUS_PATH.read_text(encoding="utf-8")) if GENIUS_PATH.exists() else {}
    survey = sorted((PROJECT_ROOT / "exports").glob("musicbrainz_guest_recordings_*.json"))
    musicbrainz = {r["id"]: r for r in json.loads(survey[-1].read_text(encoding="utf-8"))} if survey else {}
    ids = {}
    for recording_id, mbid in connection.execute("""
            SELECT id, musicbrainz_recording_id FROM recordings WHERE musicbrainz_recording_id IS NOT NULL
            UNION SELECT recording_id, musicbrainz_recording_id FROM recording_musicbrainz_ids"""):
        ids.setdefault(recording_id, set()).add(mbid)
    songs = []
    for recording_id, title, artist, genius_id, availability in connection.execute("""
            SELECT r.id, r.title, a.name, r.genius_song_id, av.availability FROM recordings r
            JOIN artists a ON a.id = r.artist_id JOIN recording_availability av ON av.recording_id = r.id
            WHERE r.appearance = 'guest' ORDER BY r.id"""):
        if availability != "released":
            continue
        details = genius.get(str(genius_id)) or {}
        on_genius = bool(WAYNE.fullmatch(details.get("primary_artist") or "")) or any(
            WAYNE.fullmatch(name) for name in details.get("featured_artists") or [])
        on_musicbrainz = any(any(WAYNE.fullmatch(c.get("name") or "") for c in (musicbrainz.get(m) or {}).get("credit") or [])
                             for m in ids.get(recording_id, ()))
        if on_genius or on_musicbrainz:
            songs.append((recording_id, title, artist, availability, (details.get("album") or {}).get("name")))
    return songs


def plain(text):
    return letters(NOTES.sub("", re.sub(r"\s+-\s+.*$", "", str(text or ""))))


def best_track(tracks, title, artist):
    """The accepted Apple track for a guest song, or None (explicit editions first)."""
    wanted_title, wanted_artist = plain(title), letters(artist)
    good = []
    for track in tracks:
        name, by = track.get("trackName") or "", track.get("artistName") or ""
        if plain(name) != wanted_title or wanted_artist not in letters(by):
            continue
        if not (WAYNE.search(by) or WAYNE.search(name)) or track.get("trackExplicitness") == "cleaned":
            continue
        if UNWANTED.search(name) and not UNWANTED.search(title):
            continue
        good.append(track)
    good.sort(key=lambda t: (t.get("trackExplicitness") != "explicit", t.get("releaseDate") or ""))
    return good[0] if good else None


class Apple:
    """iTunes lookups, cached (exports/guest_links.json)."""

    def __init__(self):
        self.cache = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
        self.calls = 0

    def get(self, key, params):
        if key not in self.cache:
            self.cache[key] = itunes_get(params) or {"results": []}
            self.calls += 1
            if self.calls % 20 == 0:
                self.save()
        return self.cache[key]

    def save(self):
        CACHE_PATH.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")

    def artist_id(self, name):
        data = self.get(f"artist|{name}", "search?" + urllib.parse.urlencode(
            {"term": name, "entity": "musicArtist", "limit": 10, "country": "US"}))
        exact = [r for r in data.get("results", []) if letters(r.get("artistName") or "") == letters(name)]
        return exact[0]["artistId"] if exact else None

    def tracks(self, lookup_id, kind):
        data = self.get(f"{kind}|{lookup_id}", f"lookup?id={lookup_id}&entity={kind}&limit=200&country=US")
        return [r for r in data.get("results", []) if r.get("wrapperType") == ("track" if kind == "song" else "collection")]

    def find(self, title, artist, album):
        artist_id = self.artist_id(artist)
        if artist_id is None:
            return None
        found = best_track(self.tracks(artist_id, "song"), title, artist)
        if found or not album:
            return found
        albums = [a for a in self.tracks(artist_id, "album") if plain(a.get("collectionName")) == plain(album)
                  and a.get("collectionExplicitness") != "cleaned"]
        for collection in sorted(albums, key=lambda a: a.get("collectionExplicitness") != "explicit")[:2]:
            found = best_track(self.tracks(collection["collectionId"], "song"), title, artist)
            if found:
                return found
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Apple Music track links for guest appearances.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.execute("PRAGMA foreign_keys = ON")
    apple = Apple()
    try:
        songs = performer_songs(connection)
        linked = {row[0] for row in connection.execute("""
            SELECT v.recording_id FROM versions v JOIN version_links l ON l.version_id = v.id
            WHERE l.service = 'apple_music'""")}
        todo = [s for s in songs if s[0] not in linked]
        print(f"Released guest songs with Lil Wayne as a performer: {len(songs)}; without an Apple link: {len(todo)}")
        found = []
        for index, (recording_id, title, artist, availability, album) in enumerate(todo, start=1):
            track = apple.find(title, artist, album)
            if track:
                found.append((recording_id, title, artist, availability, track))
            if index % 50 == 0:
                print(f"  ... {index}/{len(todo)}: {len(found)} found")
        apple.save()
        for _id, title, artist, _a, track in found[:15]:
            print(f"  {artist} - {title}  ->  {track['artistName']} - {track['trackName']} "
                  f"({track['collectionName']}, {track['trackExplicitness']})")
        print(f"Matches: {len(found)} of {len(todo)}")
        if not args.apply:
            print("Dry run; --apply stores the links (back up first).")
            return
        with connection:
            for recording_id, _title, _artist, availability, track in found:
                version = connection.execute("SELECT id FROM versions WHERE recording_id = ? "
                                             "ORDER BY version_type != 'album', id", (recording_id,)).fetchone()
                if version is None:
                    version_id = connection.execute(
                        "INSERT INTO versions (recording_id, version_name, version_type, availability_status) "
                        "VALUES (?, 'Album Version', 'album', ?)", (recording_id, availability)).lastrowid
                else:
                    version_id = version[0]
                connection.execute("INSERT OR IGNORE INTO version_links (version_id, service, url, source) "
                                   "VALUES (?, 'apple_music', ?, 'iTunes API')", (version_id, track["trackViewUrl"]))
        print(f"Stored. Foreign keys: {connection.execute('PRAGMA foreign_key_check').fetchall()[:3]}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
