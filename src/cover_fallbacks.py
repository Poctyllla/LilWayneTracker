"""Covers for official releases the Cover Art Archive has no art for (mostly singles).

release_media.py takes covers from the Cover Art Archive.  For the releases
still without a cover this tool asks, in this order:
  1. the release's own links (release_links): Apple Music (iTunes Lookup API),
     Deezer (public API), Spotify (oEmbed -- the public page preview, not the
     Web API), Discogs (API, DISCOGS_TOKEN);
  2. an iTunes search for "Lil Wayne <title>": only an album / single whose
     title is exactly the release title (" - Single" / " - EP" ignored) and
     whose artist includes Lil Wayne.
The image goes to web/covers/release_<id>.jpg and releases.cover_file; where
each cover came from is kept in exports/release_cover_sources.json.

usage:
    python cover_fallbacks.py            # dry run: what would be used
    python cover_fallbacks.py --apply    # download and store (make a backup first)
"""

import argparse
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import discogs_source
import genius_source
from import_release import DATABASE_PATH
from release_media import COVERS_FOLDER, is_image
from streaming_search import base_key, itunes_get

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCES_PATH = PROJECT_ROOT / "exports" / "release_cover_sources.json"
USER_AGENT = "LilWayneTracker/0.1 (local catalog project)"


def get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        time.sleep(0.5)
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
        return None


def bigger_itunes_art(url):
    return re.sub(r"/\d+x\d+bb\.(jpg|png)$", r"/600x600bb.\1", url or "") or None


def from_apple(url):
    match = re.search(r"music\.apple\.com/[^?]*/(\d+)(?:\?|$)", url)
    found = itunes_get(f"lookup?id={match.group(1)}&country=US") if match else None
    results = (found or {}).get("results") or []
    return bigger_itunes_art(results[0].get("artworkUrl100")) if results else None


def from_deezer(url):
    match = re.search(r"deezer\.com/(?:[a-z]{2}/)?album/(\d+)", url)
    data = get_json(f"https://api.deezer.com/album/{match.group(1)}") if match else None
    return (data or {}).get("cover_xl")


def from_spotify(url):
    if "open.spotify.com/album/" not in url:
        return None
    data = get_json("https://open.spotify.com/oembed?" + urllib.parse.urlencode({"url": url}))
    return (data or {}).get("thumbnail_url")


def from_discogs(url):
    match = re.search(r"discogs\.com/(?:[^/]+/)?(release|master)/(\d+)", url)
    if not match:
        return None
    try:
        data = discogs_source.api_get(f"/{match.group(1)}s/{match.group(2)}")
    except RuntimeError:  # no DISCOGS_TOKEN
        return None
    images = (data or {}).get("images") or []
    primary = [i for i in images if i.get("type") == "primary"] or images
    return primary[0].get("uri") if primary else None


GROUPS = ("Young Money", "Hot Boys", "Rich Gang")  # groups Lil Wayne belongs to (group_* releases)
LINK_SOURCES = (("apple_music", from_apple), ("deezer", from_deezer), ("spotify", from_spotify), ("discogs", from_discogs))


FEATURING = re.compile(r"\s*[(\[](?:feat|ft|with)\.?\s[^)\]]*[)\]]", re.IGNORECASE)


def title_key(title):
    """Comparable title: " - Single", "(feat. X)" and spaces ignored ("BedRock" = "Bed Rock")."""
    name = re.sub(r"\s+-\s+(single|ep)$", "", str(title or ""), flags=re.IGNORECASE)
    return base_key(FEATURING.sub("", name)).replace(" ", "")


def matches_title(result, title, artist="Lil Wayne"):
    credit = result.get("artistName", "").casefold()
    return (title_key(result.get("collectionName", "")) == title_key(title)
            and any(name.casefold() in credit for name in {"Lil Wayne", artist}))


def from_itunes_search(title, artist="Lil Wayne"):
    found = itunes_get("search?" + urllib.parse.urlencode(
        {"term": f"{artist} {title}", "entity": "album", "limit": 25, "country": "US"})) or {}
    for result in found.get("results", []):
        if matches_title(result, title, artist):
            return bigger_itunes_art(result.get("artworkUrl100")), result.get("collectionViewUrl")
    return None, None


def from_genius_song(connection, release_id):
    """Genius song art of a single's song (singles released outside the stores, e.g. on
    SoundCloud, have none elsewhere); Genius's default art is skipped."""
    if connection.execute("SELECT release_type FROM releases WHERE id = ?", (release_id,)).fetchone()[0] not in ("single", "group_single"):
        return None, None
    # the song's Genius page for its main version, the version's own page for a remix
    # (the original's art is not the remix single's cover)
    credit = connection.execute("SELECT a.name FROM releases r JOIN artists a ON a.id = r.artist_id WHERE r.id = ?",
                                (release_id,)).fetchone()[0]
    for (genius_id,) in connection.execute("""
            SELECT DISTINCT CASE WHEN v.version_type = 'album' THEN COALESCE(v.genius_song_id, r.genius_song_id)
                                 ELSE v.genius_song_id END AS genius_id
            FROM release_tracks rt JOIN versions v ON v.id = rt.version_id JOIN recordings r ON r.id = v.recording_id
            WHERE rt.release_id = ? ORDER BY rt.track_number""", (release_id,)):
        if genius_id is None:
            continue
        try:
            song = (genius_source.api_get(f"/songs/{genius_id}") or {}).get("song") or {}
        except RuntimeError:  # no GENIUS_ACCESS_TOKEN
            return None, None
        artist = ((song.get("primary_artist") or {}).get("name") or "").casefold()
        if artist not in {"lil wayne", credit.casefold(), *(g.casefold() for g in GROUPS)}:
            continue  # another artist's song: a wrong Genius link, not this single's art
        art = song.get("song_art_image_url") or song.get("header_image_url") or ""
        if art and "default" not in art.rsplit("/", 1)[-1]:
            return art, song.get("url")
    return None, None


def find_cover(connection, release_id, title):
    """(image url, source name, page url) or (None, None, None)."""
    links = connection.execute("SELECT service, url FROM release_links WHERE release_id = ?", (release_id,)).fetchall()
    for service, fetch in LINK_SOURCES:
        for link_service, url in links:
            if link_service == service:
                image = fetch(url)
                if image:
                    return image, service, url
    artist, kind = connection.execute("SELECT a.name, r.release_type FROM releases r JOIN artists a ON a.id = r.artist_id "
                                      "WHERE r.id = ?", (release_id,)).fetchone()
    # group releases are linked to Lil Wayne in SQLite; the stores credit the group
    for name in [artist] + (list(GROUPS) if kind.startswith("group_") else []):
        image, page = from_itunes_search(title, name)
        if image:
            return image, "itunes_search", page
    image, page = from_genius_song(connection, release_id)
    return (image, "genius_song_art", page) if image else (None, None, None)


def download(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return None
    return data if is_image(data) else None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Covers for official releases without Cover Art Archive art.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    connection = sqlite3.connect(DATABASE_PATH)
    try:
        missing = connection.execute("SELECT id, title FROM releases WHERE discography = 'official' "
                                     "AND cover_file IS NULL ORDER BY release_date, id").fetchall()
        sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8")) if SOURCES_PATH.exists() else {}
        found = 0
        for release_id, title in missing:
            image, source, page = find_cover(connection, release_id, title)
            print(f"{release_id:>4}  {title[:45]:<45}  {source or 'not found'}  {page or ''}")
            if not image:
                continue
            found += 1
            if args.apply:
                data = download(image)
                if not data:
                    print("      download failed")
                    continue
                name = f"release_{release_id}.jpg"
                (COVERS_FOLDER / name).write_bytes(data)
                connection.execute("UPDATE releases SET cover_file = ? WHERE id = ?", (name, release_id))
                connection.commit()
                sources[str(release_id)] = {"source": source, "page": page, "image": image,
                                            "fetched": time.strftime("%Y-%m-%d")}
                SOURCES_PATH.write_text(json.dumps(sources, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"{found} of {len(missing)} covers found" + ("" if args.apply else " (dry run; --apply stores them)"))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
