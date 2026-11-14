"""Genius as a source (official API, read-only).

The access token is read from the GENIUS_ACCESS_TOKEN environment variable
(user scope); it is never written to project files or exports.

Genius lists far more songs than the official releases: features, unreleased
and leaked songs, snippets.  That makes it the base for the future catalogue
of songs that are not on any release.

usage:
    python genius_source.py artist "Lil Wayne"     # find the Genius artist ID
    python genius_source.py songs [ARTIST_ID]      # export all songs to exports/
"""

import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path


API_URL = "https://api.genius.com"
USER_AGENT = "LilWayneTracker/0.1 (local catalog project)"
LIL_WAYNE_GENIUS_ID = 4  # https://genius.com/artists/Lil-wayne
PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPORTS_FOLDER = PROJECT_ROOT / "exports"
REQUEST_DELAY = 0.5


def access_token():
    token = os.environ.get("GENIUS_ACCESS_TOKEN")
    if not token and sys.platform == "win32":
        # A variable set for the user is visible to new processes only.
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                token = winreg.QueryValueEx(key, "GENIUS_ACCESS_TOKEN")[0]
        except OSError:
            token = None
    if not token:
        raise RuntimeError("Set the GENIUS_ACCESS_TOKEN environment variable first.")
    return token


def api_get(path, params=None, attempts=3):
    """GET an API path; returns the 'response' object or None."""
    url = f"{API_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {access_token()}",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    for attempt in range(1, attempts + 1):
        time.sleep(REQUEST_DELAY)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8")).get("response")
        except urllib.error.HTTPError as error:
            if error.code in (429, 500, 502, 503) and attempt < attempts:
                time.sleep(3 * attempt)
                continue
            print(f"Genius HTTP error {error.code} for {path}")
            return None
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, ConnectionError,
                http.client.HTTPException) as error:  # a read can time out after connecting
            if attempt < attempts:
                time.sleep(3 * attempt)
                continue
            print(f"Genius request failed for {path}: {error}")
            return None


def find_artist(name):
    """[(artist id, name, url)] of primary artists in search results for name."""
    response = api_get("/search", {"q": name}) or {}
    artists = {}
    for hit in response.get("hits", []):
        artist = hit.get("result", {}).get("primary_artist", {})
        if artist.get("id"):
            artists[artist["id"]] = (artist["id"], artist.get("name"), artist.get("url"))
    return list(artists.values())


def song_record(song):
    """The fields kept from a Genius song object."""
    return {
        "genius_id": song.get("id"),
        "title": song.get("title"),
        "full_title": song.get("full_title"),
        "primary_artist": (song.get("primary_artist") or {}).get("name"),
        "primary_artist_id": (song.get("primary_artist") or {}).get("id"),
        "release_date": song.get("release_date_for_display"),
        "url": song.get("url"),
    }


def iter_artist_songs(artist_id, per_page=50):
    """All songs Genius lists for an artist (paginated, sorted by title)."""
    page = 1
    while page:
        response = api_get(f"/artists/{artist_id}/songs",
                           {"per_page": per_page, "page": page, "sort": "title"})
        if response is None:
            raise RuntimeError(f"Genius songs page {page} could not be retrieved.")
        for song in response.get("songs", []):
            yield song_record(song)
        page = response.get("next_page")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    command = sys.argv[1]
    if command == "artist":
        for artist_id, name, url in find_artist(" ".join(sys.argv[2:]) or "Lil Wayne"):
            print(f"{artist_id:>10}  {name}  {url}")
    elif command == "songs":
        artist_id = int(sys.argv[2]) if len(sys.argv) > 2 else LIL_WAYNE_GENIUS_ID
        if not artist_id:
            print("Give the Genius artist ID (python genius_source.py artist \"Lil Wayne\").")
            return
        songs = []
        for song in iter_artist_songs(artist_id):
            songs.append(song)
            if len(songs) % 500 == 0:
                print(f"  ... {len(songs)} songs")
        EXPORTS_FOLDER.mkdir(exist_ok=True)
        out = EXPORTS_FOLDER / f"genius_songs_{artist_id}_{date.today():%Y%m%d}.json"
        out.write_text(json.dumps(songs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        primary = sum(1 for s in songs if s["primary_artist_id"] == artist_id)
        print(f"Songs: {len(songs)} (Lil Wayne as primary artist: {primary}) -> {out.relative_to(PROJECT_ROOT)}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
