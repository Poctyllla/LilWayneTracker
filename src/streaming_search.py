"""Spotify / Apple Music pages of official releases that MusicBrainz does not link.

MusicBrainz URL relations give streaming links for only part of the
discography (none for the Hot Boys albums, for example).  This tool reads
the complete discographies of the artists behind the catalogue (Lil Wayne,
Hot Boys, Young Money, Rich Gang, Birdman ...) from
  * Spotify   (Web API: artist albums + album tracks; credentials as in spotify_source.py)
  * Apple Music (iTunes Search / Lookup API, no key; US store)
and accepts a streaming album for an official release group only when the
title matches and at least 60% of the release's tracks are found on it.
Stores release_links (source 'Spotify API' / 'iTunes API') for every
edition of the group and the track links per version in version_links.
Cached in exports/streaming_catalog.json.

usage:
    python streaming_search.py           # dry run
    python streaming_search.py --apply
"""

import argparse
import difflib
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import spotify_source
from import_release import DATABASE_PATH
from duplicate_guard import GENERIC_TITLE_WORDS
from library_matching import release_key, titles_match


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = PROJECT_ROOT / "exports" / "streaming_catalog.json"
ARTISTS = ("Lil Wayne", "Hot Boys", "Young Money", "Rich Gang", "Birdman", "Birdman & Lil Wayne", "T-Wayne",
           "2 Chainz", "Big Tymers", "Juvenile", "B.G.")
MIN_OVERLAP = 0.6
EDITION_NOISE = re.compile(r"\s*[(\[][^)\]]*(?:edition|deluxe|version|remaster|explicit|clean|expanded|"
                           r"anniversary|bonus|single)[^)\]]*[)\]]|\s+-\s+(?:single|ep)$", re.IGNORECASE)


def base_key(title):
    return release_key(EDITION_NOISE.sub("", str(title or "")))


# ---------- Apple Music (iTunes Search API) ----------

def itunes_get(params):
    url = "https://itunes.apple.com/" + params
    for attempt in range(1, 4):
        time.sleep(3.1)  # ~20 requests per minute
        request = urllib.request.Request(url, headers={"User-Agent": spotify_source.USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as error:
            if attempt == 3:
                print(f"iTunes request failed: {error}")
                return None
            time.sleep(10 * attempt)


def itunes_albums(artist):
    """[{id, title, url, tracks: [...]}] of an iTunes artist (exact artist name)."""
    found = itunes_get("search?" + urllib.parse.urlencode(
        {"term": artist, "entity": "musicArtist", "limit": 5, "country": "US"})) or {}
    ids = [r["artistId"] for r in found.get("results", []) if r.get("artistName", "").casefold() == artist.casefold()]
    albums = []
    for artist_id in ids[:1]:
        data = itunes_get(f"lookup?id={artist_id}&entity=album&limit=200&country=US") or {}
        for item in data.get("results", []):
            if item.get("wrapperType") != "collection":
                continue
            songs = itunes_get(f"lookup?id={item['collectionId']}&entity=song&country=US") or {}
            albums.append({"id": item["collectionId"], "title": item.get("collectionName"),
                           "url": item.get("collectionViewUrl", "").split("?")[0],
                           "tracks": [{"disc": s.get("discNumber") or 1, "number": s.get("trackNumber"),
                                       "title": s.get("trackName"), "url": (s.get("trackViewUrl") or "").split("&")[0]}
                                      for s in songs.get("results", []) if s.get("wrapperType") == "track"]})
    return albums


# ---------- Spotify ----------

def spotify_albums(artist):
    found = spotify_source.api_get("/search?" + urllib.parse.urlencode({"q": artist, "type": "artist", "limit": 5})) or {}
    ids = [a["id"] for a in found.get("artists", {}).get("items", []) if a.get("name", "").casefold() == artist.casefold()]
    albums = []
    for artist_id in ids[:1]:
        # The endpoint accepts at most 10 per page now ("Invalid limit" for 50).
        url = f"/artists/{artist_id}/albums?include_groups=album,single,compilation&limit=10&market=US"
        while url:
            data = spotify_source.api_get(url)
            if not data:
                break
            for item in data.get("items", []):
                tracks = spotify_source.album_tracks(item["id"]) or []
                albums.append({"id": item["id"], "title": item.get("name"),
                               "url": (item.get("external_urls") or {}).get("spotify"), "tracks": tracks})
            url = data.get("next")
    return albums


# ---------- matching ----------

def load_cache(path=CACHE_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {"spotify": {}, "apple": {}}


def save_cache(cache, path=CACHE_PATH):
    Path(path).write_text(json.dumps(cache, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


SIMILAR = 0.6  # title similarity for a same-position track inside an accepted album
SAME_LENGTH_MS = 1000  # same position and length within 1 s: the same track under another title
NOTES = re.compile(r"\s*[(\[][^)\]]*[)\]]")  # "(Lil Wayne solo)", "(feat. X)", "[Explicit]"
# store suffixes: " - Album Version (Edited)", " - Radio Edit", " - Main Clean", " - Street", " - 2008 Remaster"
SUFFIX = re.compile(r"\s+-\s+[^-]*\b(?:version|edit|edited|explicit|clean|main|street|remaster(?:ed)?)\b[^-]*$",
                    re.IGNORECASE)
FEATURING = re.compile(r"\s*[(\[](?:feat|ft|with)\.?\s[^)\]]*[)\]]", re.IGNORECASE)


def letters(text):
    return re.sub(r"[^0-9a-z]", "", text.casefold().replace("&", "and"))


def bare(title):
    """The title without bracketed notes and store suffixes, letters and digits only."""
    return letters(NOTES.sub("", SUFFIX.sub("", str(title or ""))))


def shared_word(a, b):
    """At least one real word in common (not "the", "a", generic words): a same-length
    track with a wholly different title is another song ("Earthquake" vs "Shine")."""
    def words(title):
        return {w for w in re.findall(r"[a-z0-9]+", NOTES.sub("", str(title or "")).casefold())
                if len(w) >= 3 and w not in ("the", "tha", "and") and w not in GENERIC_TITLE_WORDS}
    return bool(words(a) & words(b))


def generic(title):
    """Intro / outro / skit ...: such titles match only exactly (duplicate_guard rule)."""
    return any(word in re.findall(r"[a-z]+", str(title or "").casefold()) for word in GENERIC_TITLE_WORDS)


def similarity(a, b):
    return difflib.SequenceMatcher(None, bare(a), bare(b)).ratio()


def position_pairs(ours, theirs, pairs):
    """Tracks the title match missed, inside an album already accepted: the store's
    track at the same disc / position when the titles are similar
    ("50 Shots Set's It Off" = "Shots Set's It Off", "Infrared Dot" = "Infared Dot")
    or the lengths agree within 1 s ("Sick Uncle" = "Uncle Automatic Skit")."""
    used = {id(t) for _, t in pairs}
    done = {id(o) for o, _ in pairs}
    by_position = {(t["disc"], t["number"]): t for t in theirs}
    extra = []
    for track in ours:
        candidate = by_position.get((track["disc"], track["number"]))
        if id(track) in done or candidate is None or id(candidate) in used:
            continue
        same_length = (track.get("duration_ms") and candidate.get("duration_ms")
                       and abs(track["duration_ms"] - candidate["duration_ms"]) <= SAME_LENGTH_MS
                       and shared_word(track["title"], candidate["title"]))
        if same_length:
            pass  # same position and the same length: the store's name for it ("Sick Uncle" = "Uncle Automatic Skit")
        elif generic(track["title"]) or generic(candidate["title"]):
            if bare(track["title"]) != bare(candidate["title"]):
                continue
        elif similarity(track["title"], candidate["title"]) < SIMILAR:
            continue
        extra.append((track, candidate))
        used.add(id(candidate))
    return extra


def alias_key(title):
    """Key for "is this another name": featuring and store suffixes ignored, other brackets kept."""
    return letters(FEATURING.sub("", SUFFIX.sub("", str(title or ""))))


def other_name(track, streaming_title):
    """The store's title when it is a different spelling of the song (not only the
    same title without a note such as "(B.G. solo)"), else None."""
    if "*" in streaming_title:  # a censored spelling is not another name (search ignores stars)
        return None
    ours = {bare(track["recording_title"]), bare(track["title"]), alias_key(track["recording_title"]), alias_key(track["title"])}
    if alias_key(streaming_title) in ours or bare(streaming_title) in ours:
        return None
    return SUFFIX.sub("", FEATURING.sub("", streaming_title)).strip()


def best_album(release_title, our_tracks, albums):
    """(album, pairs) of the streaming album that is this release, or (None, [])."""
    key = base_key(release_title)
    best, best_pairs = None, []
    for album in albums:
        if base_key(album["title"]) != key or not album["tracks"]:
            continue
        pairs = spotify_source.match_tracks(our_tracks, album["tracks"])
        if len(pairs) > len(best_pairs):
            best, best_pairs = album, pairs
    if best is None or len(best_pairs) < MIN_OVERLAP * len(our_tracks):
        return None, []
    return best, best_pairs + position_pairs(our_tracks, best["tracks"], best_pairs)


def release_groups(connection):
    """{group key: [(release_id, title, tracks)]} of official releases."""
    groups = {}
    for release_id, title, group_id in connection.execute(
            "SELECT id, title, musicbrainz_release_group_id FROM releases WHERE discography = 'official' ORDER BY id"):
        groups.setdefault(group_id or f"release:{release_id}", []).append(
            (release_id, title, spotify_source.release_tracks(connection, release_id)))
    return groups


def main(argv=None):
    parser = argparse.ArgumentParser(description="Spotify / Apple Music links for official releases.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    cache = load_cache()
    for service, fetch in (("spotify", spotify_albums), ("apple", itunes_albums)):
        for artist in ARTISTS:
            if artist not in cache[service]:
                print(f"{service}: reading {artist} ...", flush=True)
                try:
                    cache[service][artist] = fetch(artist)
                except spotify_source.RateLimited as error:  # the rest of this service waits for the next run
                    until = time.strftime("%H:%M", time.localtime(time.time() + error.seconds))
                    print(f"{error}; skipping the remaining {service} artists (run again after {until}).")
                    break
                save_cache(cache)
    connection = sqlite3.connect(DATABASE_PATH)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        existing = {(r, s) for r, s in connection.execute("SELECT release_id, service FROM release_links")}
        release_links, track_links, matched = [], {}, {"spotify": 0, "apple_music": 0}
        aliases, positional = {}, []
        known = {(r, a.casefold()) for r, a in connection.execute("SELECT recording_id, title FROM recording_aliases")}
        for group in release_groups(connection).values():
            for service, name, source in (("spotify", "spotify", "Spotify API"), ("apple", "apple_music", "iTunes API")):
                albums = [a for artist_albums in cache[service].values() for a in artist_albums]
                for release_id, title, tracks in group:
                    album, pairs = best_album(title, tracks, albums)
                    if album is None:
                        continue
                    matched[name] += 1
                    if (release_id, name) not in existing and album.get("url"):
                        release_links.append((release_id, name, album["url"], source))
                    by_title = {id(o) for o, t in pairs if titles_match(o["title"], t["title"])}
                    for track, streaming_track in pairs:
                        if streaming_track.get("url"):
                            if (track["version_id"], name) not in track_links and id(track) not in by_title:
                                positional.append((title, track["title"], streaming_track["title"], name))
                            track_links.setdefault((track["version_id"], name), (streaming_track["url"], source))
                        alias = other_name(track, streaming_track["title"])
                        if alias and (track["recording_id"], alias.casefold()) not in known:
                            aliases.setdefault((track["recording_id"], alias.casefold()),
                                               (track["recording_id"], alias, "Spotify" if name == "spotify" else "Apple Music",
                                                f"track title on {album['title']}", track["recording_title"]))
        print(f"Releases matched: {matched}; new release links: {len(release_links)}; track links: {len(track_links)}")
        print(f"\nTracks matched by position + similar title ({len(positional)}):")
        for release, ours, theirs, service in positional:
            print(f"  {release}: {ours!r} = {theirs!r} ({service})")
        print(f"\nOther names from the stores -> recording_aliases ({len(aliases)}):")
        for recording_id, alias, source, note, recording_title in aliases.values():
            print(f"  {recording_title!r}: also {alias!r} ({source})")
        if not args.apply:
            print("Dry run; use --apply to store the links (make a backup first).")
            return
        with connection:
            for release_id, name, url, source in release_links:
                connection.execute("INSERT OR IGNORE INTO release_links (release_id, service, url, source) VALUES (?, ?, ?, ?)",
                                   (release_id, name, url, source))
            for (version_id, name), (url, source) in track_links.items():
                connection.execute("INSERT OR IGNORE INTO version_links (version_id, service, url, source) VALUES (?, ?, ?, ?)",
                                   (version_id, name, url, source))
            for recording_id, alias, source, note, _title in aliases.values():
                connection.execute("INSERT OR IGNORE INTO recording_aliases (recording_id, title, source, note) VALUES (?, ?, ?, ?)",
                                   (recording_id, alias, source, note))
        print("Stored.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
