"""Spotify track links for official releases (Spotify Web API, read-only).

Credentials: the user environment variables SPOTIFY_CLIENT_ID and
SPOTIFY_CLIENT_SECRET (client-credentials flow; never written to project
files or exports).  For every official release that already has a Spotify
album link (release_links, from MusicBrainz), the album's tracks are
fetched (cached in exports/spotify_albums.json) and matched to that
release's tracks by disc/position + title, else by a unique title.  The
track link is stored per version in version_links (service 'spotify',
source 'Spotify API'); the tracker then links a song straight to its track.

usage:
    python spotify_source.py           # dry run: albums, matched tracks
    python spotify_source.py --apply   # store the track links
"""

import argparse
import base64
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from import_release import DATABASE_PATH
from library_matching import titles_match


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = PROJECT_ROOT / "exports" / "spotify_albums.json"
API_URL = "https://api.spotify.com/v1"
USER_AGENT = "LilWayneTracker/0.1 (local catalog project)"
_TOKEN = {}


def credential(name):
    value = os.environ.get(name)
    if not value and sys.platform == "win32":
        import winreg  # a user variable is visible to new processes only
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value = winreg.QueryValueEx(key, name)[0]
        except OSError:
            value = None
    if not value:
        raise RuntimeError(f"Set the {name} environment variable first.")
    return value


def access_token():
    if _TOKEN.get("expires", 0) > time.time() + 60:
        return _TOKEN["value"]
    auth = base64.b64encode(f"{credential('SPOTIFY_CLIENT_ID')}:{credential('SPOTIFY_CLIENT_SECRET')}".encode()).decode()
    request = urllib.request.Request("https://accounts.spotify.com/api/token", data=b"grant_type=client_credentials",
                                     headers={"Authorization": f"Basic {auth}", "User-Agent": USER_AGENT,
                                              "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))
    _TOKEN.update(value=data["access_token"], expires=time.time() + data.get("expires_in", 3600))
    return _TOKEN["value"]


def api_get(path, attempts=4):
    """GET an API path (or full URL); JSON or None.  Honours 429 Retry-After."""
    url = path if path.startswith("http") else f"{API_URL}{path}"
    for attempt in range(1, attempts + 1):
        time.sleep(0.2)
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token()}",
                                                       "User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code == 429 and attempt < attempts:
                time.sleep(int(error.headers.get("Retry-After") or 5) + 1)
                continue
            if error.code in (500, 502, 503) and attempt < attempts:
                time.sleep(3 * attempt)
                continue
            print(f"Spotify HTTP error {error.code} for {url}")
            return None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            if attempt < attempts:
                time.sleep(3 * attempt)
                continue
            print(f"Spotify request failed for {url}: {error}")
            return None


def album_id(url):
    match = re.search(r"open\.spotify\.com/(?:intl-[a-z]+/)?album/([A-Za-z0-9]+)", url or "")
    return match.group(1) if match else None


def album_tracks(spotify_album_id):
    """[{disc, number, title, duration_ms, url}] of a Spotify album (all pages)."""
    tracks, url = [], f"/albums/{spotify_album_id}/tracks?limit=50"
    while url:
        data = api_get(url)
        if data is None:
            return None
        for item in data.get("items", []):
            tracks.append({"disc": item.get("disc_number") or 1, "number": item.get("track_number"),
                           "title": item.get("name"), "duration_ms": item.get("duration_ms"),
                           "url": (item.get("external_urls") or {}).get("spotify")})
        url = data.get("next")
    return tracks


def match_tracks(ours, theirs):
    """[(our track, spotify track)] by disc/position + title, else a unique title."""
    by_position = {(t["disc"], t["number"]): t for t in theirs}
    pairs = []
    for track in ours:
        candidate = by_position.get((track["disc"], track["number"]))
        if candidate and titles_match(track["title"], candidate["title"]):
            pairs.append((track, candidate))
            continue
        same = [t for t in theirs if titles_match(track["title"], t["title"])]
        if len(same) == 1:
            pairs.append((track, same[0]))
    return pairs


def load_cache(path=CACHE_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}


def save_cache(cache, path=CACHE_PATH):
    Path(path).write_text(json.dumps(cache, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def release_tracks(connection, release_id):
    return [{"disc": disc or 1, "number": number, "title": title, "version_id": version_id}
            for disc, number, title, version_id in connection.execute("""
                SELECT rt.disc_number, rt.track_number, COALESCE(rt.track_title, r.title), rt.version_id
                FROM release_tracks rt JOIN versions v ON v.id = rt.version_id JOIN recordings r ON r.id = v.recording_id
                WHERE rt.release_id = ? ORDER BY rt.disc_number, rt.track_number""", (release_id,))]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Spotify track links for official releases.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    connection = sqlite3.connect(DATABASE_PATH)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        releases = connection.execute("""
            SELECT r.id, r.title, l.url FROM releases r JOIN release_links l ON l.release_id = r.id
            WHERE r.discography = 'official' AND l.service = 'spotify' ORDER BY r.release_date, r.id""").fetchall()
        cache = load_cache()
        links, unmatched = {}, 0
        for release_id, title, url in releases:
            spotify_id = album_id(url)
            if not spotify_id:
                continue
            if spotify_id not in cache:
                cache[spotify_id] = album_tracks(spotify_id)
                save_cache(cache)
            theirs = cache[spotify_id] or []
            ours = release_tracks(connection, release_id)
            pairs = match_tracks(ours, theirs)
            unmatched += len(ours) - len(pairs)
            for track, spotify_track in pairs:
                if spotify_track.get("url"):
                    links.setdefault(track["version_id"], spotify_track["url"])  # earliest release first
        print(f"Official releases with a Spotify album: {len(releases)}; Spotify albums cached: {len(cache)}")
        print(f"Versions with a Spotify track link: {len(links)}; release tracks without a match: {unmatched}")
        if not args.apply:
            print("Dry run; use --apply to store the links (make a backup first).")
            return links
        with connection:
            for version_id, url in links.items():
                connection.execute("INSERT OR IGNORE INTO version_links (version_id, service, url, source) "
                                   "VALUES (?, 'spotify', ?, 'Spotify API')", (version_id, url))
        print(f"Stored: {len(links)}")
        return links
    finally:
        connection.close()


if __name__ == "__main__":
    main()
