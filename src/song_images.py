"""Original covers of the guest songs (songs of other artists featuring Lil Wayne).

A guest song is not on a release of Lil Wayne's discography, so without this it
would show one of his era covers.  For every guest song:
  1. Genius: the song's art (thumbnail, 300 px; Genius's default art skipped);
  2. MusicBrainz: the release group of a release with the recording -> the
     Cover Art Archive front (250 px).
Images go to web/covers/songs/<recording id>.jpg (local, not in git, like the
covers); data/song_images.json keeps for each song the file and its source.
Resumable: songs already in the map are skipped.  SQLite is not changed.

usage:
    python song_images.py [--limit N] [--apply]     # dry run without --apply
"""

import argparse
import json
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path

import genius_source
from import_release import DATABASE_PATH
from musicbrainz_catalog import BASE_URL, api_request
from release_media import is_image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FOLDER = PROJECT_ROOT / "web" / "covers" / "songs"
MAP_PATH = PROJECT_ROOT / "data" / "song_images.json"
USER_AGENT = "LilWayneTracker/0.1 (local catalog project)"


def genius_art(genius_id):
    """(image url, page) of a Genius song's art, or (None, None)."""
    try:
        song = (genius_source.api_get(f"/songs/{genius_id}") or {}).get("song") or {}
    except RuntimeError:  # no GENIUS_ACCESS_TOKEN
        return None, None
    url = song.get("song_art_image_thumbnail_url") or song.get("song_art_image_url") or ""
    if not url or "default" in url.rsplit("/", 1)[-1]:
        return None, None
    return url, song.get("url")


def musicbrainz_art(recording_id):
    """(Cover Art Archive url, release group page) for a release group of the recording."""
    data = None
    for attempt in range(1, 4):
        time.sleep(1 if attempt == 1 else 3 * attempt)  # rate limit; 503 = busy
        data = api_request(f"{BASE_URL}recording/{recording_id}?inc=releases+release-groups&fmt=json")
        if data is not None:
            break
    for release in (data or {}).get("releases") or []:
        group = (release.get("release-group") or {}).get("id")
        if group and release.get("status") in (None, "Official"):
            return (f"https://coverartarchive.org/release-group/{group}/front-250",
                    f"https://musicbrainz.org/release-group/{group}")
    return None, None


def download(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return None
    return data if is_image(data) else None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Original covers of the guest songs.")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    images = json.loads(MAP_PATH.read_text(encoding="utf-8")) if MAP_PATH.exists() else {}
    connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
    try:
        songs = connection.execute("""
            SELECT id, title, genius_song_id, musicbrainz_recording_id FROM recordings
            WHERE appearance = 'guest' AND (genius_song_id IS NOT NULL OR musicbrainz_recording_id IS NOT NULL)
            ORDER BY id""").fetchall()
    finally:
        connection.close()
    todo = [s for s in songs if str(s[0]) not in images][:args.limit]
    found = 0
    for index, (recording_id, title, genius_id, mbid) in enumerate(todo, start=1):
        url, page, source = None, None, None
        if genius_id:
            url, page = genius_art(genius_id)
            source = "Genius" if url else None
        if not url and mbid:
            url, page = musicbrainz_art(mbid)
            source = "Cover Art Archive" if url else None
        data = download(url) if url and args.apply else None
        if url and (data or not args.apply):
            found += 1
        if data:
            FOLDER.mkdir(parents=True, exist_ok=True)
            (FOLDER / f"{recording_id}.jpg").write_bytes(data)
            images[str(recording_id)] = {"file": f"songs/{recording_id}.jpg", "source": source, "page": page,
                                         "image": url, "fetched": time.strftime("%Y-%m-%d")}
        if index % 50 == 0 or index == len(todo):
            if args.apply:
                MAP_PATH.write_text(json.dumps(images, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            print(f"  {index}/{len(todo)}: {found} covers", flush=True)
    print(f"{found} of {len(todo)} guest songs have an original cover"
          + ("" if args.apply else " (dry run; --apply downloads them)"))


if __name__ == "__main__":
    main()
