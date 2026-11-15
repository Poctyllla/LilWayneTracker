"""Download Genius song details (album and credits) for catalogued songs.

Read-only for SQLite.  For every Genius song ID of the catalogue (recordings
and versions) and of the guest-match review list, GET /songs/:id (official
API, token from GENIUS_ACCESS_TOKEN) and keep: title, release date, album
(name, release date), primary / featured artists, producers, writers and the
other credited roles ("custom performances": Mixing Engineer, Label ...)
and the song relationships (samples, sampled in, interpolates, cover of,
remix of ...).
Results are cached in exports/genius_song_details.json and saved every 50
songs, so an interrupted run continues where it stopped.

usage:
    python genius_details.py [--guest-matches exports/guest_matches_<date>.json
                              --guest-survey exports/guest_survey_<date>.json]
    python genius_details.py --relationships   # also refresh cached songs without relationships
    python genius_details.py --albums          # page, date and cover of the albums of cached songs
                                               # (exports/genius_albums.json)
"""

import argparse
import json
import sqlite3
from pathlib import Path

from genius_source import api_get
from import_release import DATABASE_PATH


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = PROJECT_ROOT / "exports" / "genius_song_details.json"
ALBUMS_PATH = PROJECT_ROOT / "exports" / "genius_albums.json"
SAVE_EVERY = 50
RELATIONSHIPS = ("samples", "sampled_in", "interpolates", "interpolated_by", "cover_of", "covered_by",
                 "remix_of", "remixed_by")


def artist_names(items):
    return [item.get("name") for item in items or [] if item.get("name")]


def song_details(song):
    """The kept fields of a Genius song object."""
    album = song.get("album") or {}
    return {
        "id": song.get("id"),
        "title": song.get("title"),
        "url": song.get("url"),
        "release_date": song.get("release_date"),
        "release_date_for_display": song.get("release_date_for_display"),
        "album": {"id": album.get("id"), "name": album.get("name"),
                  "release_date": album.get("release_date_for_display"),
                  "artist": (album.get("artist") or {}).get("name")} if album else None,
        "primary_artist": (song.get("primary_artist") or {}).get("name"),
        "featured_artists": artist_names(song.get("featured_artists")),
        "producers": artist_names(song.get("producer_artists")),
        "writers": artist_names(song.get("writer_artists")),
        "custom_performances": [{"label": p.get("label"), "artists": artist_names(p.get("artists"))}
                                for p in song.get("custom_performances") or []],
        "relationships": [{"type": r.get("relationship_type") or r.get("type"),
                           "songs": [{"id": s.get("id"), "title": s.get("title"), "url": s.get("url"),
                                      "artist": (s.get("primary_artist") or {}).get("name")}
                                     for s in r.get("songs") or []]}
                          for r in song.get("song_relationships") or []
                          if r.get("songs") and (r.get("relationship_type") or r.get("type")) in RELATIONSHIPS],
    }


def album_details(album):
    """The kept fields of a Genius album object."""
    return {"id": album.get("id"), "name": album.get("name"), "url": album.get("url"),
            "release_date": album.get("release_date_for_display"),
            "artist": (album.get("artist") or {}).get("name"), "cover_art_url": album.get("cover_art_url")}


def load_albums(path=ALBUMS_PATH):
    if Path(path).exists():
        return {int(k): v for k, v in json.loads(Path(path).read_text(encoding="utf-8")).items()}
    return {}


def download_albums(cache):
    """GET /albums/:id for every album of the cached songs that is not known yet."""
    albums = load_albums()
    todo = sorted({(song.get("album") or {}).get("id") for song in cache.values()} - set(albums) - {None})
    print(f"Genius albums to download: {len(todo)}")
    for index, album_id in enumerate(todo, start=1):
        response = api_get(f"/albums/{album_id}", {"text_format": "plain"})
        albums[album_id] = album_details(response["album"]) if response and response.get("album") else None
        if index % SAVE_EVERY == 0 or index == len(todo):
            save_cache(albums, ALBUMS_PATH)
            print(f"  ... {index}/{len(todo)}")
    return albums


def wanted_ids(connection, guest_matches=None, guest_survey=None):
    ids = {row[0] for row in connection.execute("""
        SELECT genius_song_id FROM recordings WHERE genius_song_id IS NOT NULL
        UNION SELECT genius_song_id FROM versions WHERE genius_song_id IS NOT NULL
    """)}
    if guest_matches and guest_survey:
        survey = {(s["main_artist"], s["title"]): s
                  for s in json.loads(Path(guest_survey).read_text(encoding="utf-8"))["songs"]}
        for item in json.loads(Path(guest_matches).read_text(encoding="utf-8"))["songs"]:
            if item["verdict"] == "review":
                ids.update(g["id"] for g in survey[(item["main_artist"], item["title"])]["genius"])
    return sorted(ids)


def load_cache(path=CACHE_PATH):
    if Path(path).exists():
        return {int(k): v for k, v in json.loads(Path(path).read_text(encoding="utf-8")).items()}
    return {}


def save_cache(cache, path=CACHE_PATH):
    Path(path).write_text(json.dumps({str(k): v for k, v in sorted(cache.items())}, ensure_ascii=False, indent=1)
                          + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Download Genius song details (album and credits).")
    parser.add_argument("--guest-matches")
    parser.add_argument("--guest-survey")
    parser.add_argument("--albums", action="store_true", help="download the albums of the cached songs")
    parser.add_argument("--relationships", action="store_true",
                        help="download again the cached songs stored before relationships were kept")
    args = parser.parse_args(argv)
    if args.albums:
        download_albums(load_cache())
        return
    connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
    try:
        ids = wanted_ids(connection, args.guest_matches, args.guest_survey)
    finally:
        connection.close()
    cache = load_cache()
    todo = [song_id for song_id in ids if song_id not in cache
            or (args.relationships and "relationships" not in cache[song_id])]
    print(f"Genius songs: {len(ids)}; cached {len(ids) - len(todo)}; to download {len(todo)}")
    failed = []
    for index, song_id in enumerate(todo, start=1):
        response = api_get(f"/songs/{song_id}", {"text_format": "plain"})
        if response and response.get("song"):
            cache[song_id] = song_details(response["song"])
        else:
            failed.append(song_id)
        if index % SAVE_EVERY == 0:
            save_cache(cache)
            print(f"  ... {index}/{len(todo)}")
    save_cache(cache)
    print(f"Done: {len(cache)} songs cached; failed {len(failed)} {failed[:10]}")
    print(f"Saved: {CACHE_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
