"""Discogs as a source (official API, read-only).

The personal access token is read from the DISCOGS_TOKEN environment variable
(user scope); it is never written to project files or exports.  Requests
carry a descriptive User-Agent and stay under the 60 requests/minute limit.

For every official release group of the catalogue the Discogs master is
found through the MusicBrainz release group's Discogs URL relation, or else
by a Discogs search (artist + title); its main release (track list with
per-track credits and release credits with track ranges) is cached in
exports/discogs_releases.json.

usage:
    python discogs_source.py fetch     # official release groups -> cache (resumable)
"""

import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from import_release import DATABASE_PATH
from library_matching import release_key
from musicbrainz_catalog import BASE_URL as MUSICBRAINZ_URL, api_request as musicbrainz_request


API_URL = "https://api.discogs.com"
USER_AGENT = "LilWayneTracker/0.1 (local catalog project)"
REQUEST_DELAY = 1.1
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = PROJECT_ROOT / "exports" / "discogs_releases.json"


def access_token():
    token = os.environ.get("DISCOGS_TOKEN")
    if not token and sys.platform == "win32":
        import winreg  # a user variable is visible to new processes only
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                token = winreg.QueryValueEx(key, "DISCOGS_TOKEN")[0]
        except OSError:
            token = None
    if not token:
        raise RuntimeError("Set the DISCOGS_TOKEN environment variable first.")
    return token


def api_get(path, params=None, attempts=3):
    """GET a Discogs API path; JSON or None."""
    url = f"{API_URL}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Authorization": f"Discogs token={access_token()}"})
    for attempt in range(1, attempts + 1):
        time.sleep(REQUEST_DELAY)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code in (429, 500, 502, 503) and attempt < attempts:
                time.sleep(5 * attempt)
                continue
            print(f"Discogs HTTP error {error.code} for {path}")
            return None
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as error:
            if attempt < attempts:
                time.sleep(5 * attempt)
                continue
            print(f"Discogs request failed for {path}: {error}")
            return None


def discogs_master_from_musicbrainz(release_group_id):
    """('master'|'release', id) from the MusicBrainz release group's Discogs link, or None."""
    for attempt in range(3):
        time.sleep(1 + 2 * attempt)
        data = musicbrainz_request(f"{MUSICBRAINZ_URL}release-group/{release_group_id}?inc=url-rels&fmt=json")
        if data:
            break
    else:
        return None
    for relation in data.get("relations", []):
        url = (relation.get("url") or {}).get("resource", "")
        match = re.search(r"discogs\.com/(master|release)/(\d+)", url)
        if match:
            return match.group(1), int(match.group(2))
    return None


def search_master(artist, title):
    """('master', id) of the best Discogs search hit (same comparable title), or None."""
    data = api_get("/database/search", {"artist": artist, "release_title": title, "type": "master"}) or {}
    for result in data.get("results", []):
        hit_title = result.get("title", "").split(" - ", 1)[-1]
        if release_key(hit_title) == release_key(title):
            return "master", result["id"]
    return None


def fetch_release(kind, discogs_id):
    """Main release JSON of a master (or the release itself)."""
    if kind == "master":
        master = api_get(f"/masters/{discogs_id}") or {}
        if not master.get("main_release"):
            return None
        discogs_id = master["main_release"]
    return api_get(f"/releases/{discogs_id}")


def keep_release(release):
    """The fields used for credits."""
    def people(items):
        return [{"name": p.get("name"), "role": p.get("role"), "tracks": p.get("tracks", "")} for p in items or []]
    return {
        "id": release.get("id"), "title": release.get("title"), "uri": release.get("uri"),
        "year": release.get("year"), "master_id": release.get("master_id"),
        "extraartists": people(release.get("extraartists")),
        "tracklist": [{"position": t.get("position"), "title": t.get("title"), "duration": t.get("duration"),
                       "type": t.get("type_"), "artists": people(t.get("artists")),
                       "extraartists": people(t.get("extraartists"))} for t in release.get("tracklist", [])],
    }


def official_release_groups(connection):
    """[(release group key, title, MusicBrainz release group ID or None, artist credit)]."""
    groups = {}
    for release_id, title, group_id, release_type in connection.execute("""
        SELECT id, title, musicbrainz_release_group_id, release_type FROM releases
        WHERE discography = 'official' ORDER BY id
    """):
        key = group_id or f"release:{release_id}"
        groups.setdefault(key, (key, title, group_id, release_type))
    return list(groups.values())


def load_cache(path=CACHE_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}


def save_cache(cache, path=CACHE_PATH):
    Path(path).write_text(json.dumps(cache, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def fetch_all():
    connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
    try:
        groups = official_release_groups(connection)
    finally:
        connection.close()
    cache = load_cache()
    todo = [g for g in groups if g[0] not in cache]
    print(f"Official release groups: {len(groups)}; cached {len(groups) - len(todo)}; to fetch {len(todo)}")
    for index, (key, title, group_id, release_type) in enumerate(todo, start=1):
        link = discogs_master_from_musicbrainz(group_id) if group_id else None
        how = "musicbrainz link"
        if link is None:
            link, how = search_master("Lil Wayne", title), "discogs search"
        release = fetch_release(*link) if link else None
        cache[key] = {"title": title, "found_by": how if link else None,
                      "discogs": keep_release(release) if release else None}
        if index % 10 == 0:
            save_cache(cache)
            print(f"  ... {index}/{len(todo)}", flush=True)
    save_cache(cache)
    found = sum(1 for v in cache.values() if v.get("discogs"))
    print(f"Done: {len(cache)} release groups; with Discogs data {found}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "fetch":
        fetch_all()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
