"""Cover art and streaming links of releases (web tracker).

Official releases with a MusicBrainz Release ID:
  * links: URL relations of every edition in the release group (Spotify,
    Apple Music, Deezer, Tidal, YouTube Music, Amazon ...; one link per
    service, the US Apple Music page preferred) -> table release_links of
    each catalogued edition (source 'MusicBrainz');
  * cover: Cover Art Archive front image of the release (250 px), else of
    its release group -> web/covers/release_<id>.jpg.
Releases built from the user's library (bootlegs, The Dedication): the
cover image found next to the release's own audio files (a name with
"front" / "cover" / "folder" first) is copied to web/covers/ -- the original
file is not touched.
Already stored covers / links are kept (re-running only fills gaps).

usage:
    python release_media.py           # dry run: counts
    python release_media.py --apply   # download / copy covers, store links
"""

import argparse
import glob
import json
import re
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path

from import_release import DATABASE_PATH
from musicbrainz_catalog import BASE_URL as MUSICBRAINZ_URL, HEADERS, api_request as musicbrainz_request


PROJECT_ROOT = Path(__file__).resolve().parent.parent
COVERS_FOLDER = PROJECT_ROOT / "web" / "covers"
COVER_URLS = ("https://coverartarchive.org/release/{}/front-250",
              "https://coverartarchive.org/release-group/{}/front-250")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")
SERVICES = (
    ("spotify", r"open\.spotify\.com|spotify\.com/album"),
    ("apple_music", r"music\.apple\.com|itunes\.apple\.com"),
    ("deezer", r"deezer\.com"),
    ("tidal", r"tidal\.com"),
    ("youtube_music", r"music\.youtube\.com"),
    ("youtube", r"youtube\.com|youtu\.be"),
    ("amazon_music", r"music\.amazon\.|amazon\.[a-z.]+/(?:music|dp)"),
    ("soundcloud", r"soundcloud\.com"),
    ("bandcamp", r"bandcamp\.com"),
    ("discogs", r"discogs\.com"),
)


def service_of(url):
    for name, pattern in SERVICES:
        if re.search(pattern, url or "", re.IGNORECASE):
            return name
    return None


def link_rank(service, url):
    """Lower is better: the US Apple Music page over iTunes / other stores."""
    if service == "apple_music":
        return (0 if "music.apple.com/us" in url else 1 if "music.apple.com" in url else 2)
    return 0


def group_links(release_group_id):
    """{service: url} from the URL relations of every edition in a MusicBrainz release
    group (streaming links usually sit on the digital edition); None if unreachable."""
    for attempt in range(3):
        time.sleep(1 + 2 * attempt)
        data = musicbrainz_request(f"{MUSICBRAINZ_URL}release?release-group={release_group_id}"
                                   f"&inc=url-rels&limit=100&fmt=json")
        if data:
            break
    else:
        return None
    best = {}
    for release in data.get("releases", []):
        for relation in release.get("relations", []):
            url = (relation.get("url") or {}).get("resource")
            service = service_of(url)
            if service and (service not in best or link_rank(service, url) < link_rank(service, best[service])):
                best[service] = url
    return best


def is_image(data):
    return data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"


def download_cover(release_id, group_id, target):
    for template, mbid in zip(COVER_URLS, (release_id, group_id)):
        if not mbid:
            continue
        request = urllib.request.Request(template.format(mbid), headers=HEADERS)
        try:
            time.sleep(0.3)
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
        except (urllib.error.URLError, TimeoutError):
            continue
        if is_image(data):
            target.write_bytes(data)
            return True
    return False


def local_cover(file_paths):
    """Best cover image next to a release's audio files, or None.  The release's own
    folder is the one holding most of its files: its songs may also have files in
    other releases' folders (The Dedication's tracks are on From 9/11 to Katrina too),
    whose covers must not win."""
    counts = {}
    for path in file_paths:
        counts[Path(path).parent] = counts.get(Path(path).parent, 0) + 1
    images = []
    for folder in sorted(counts, key=lambda f: -counts[f]):  # own folder first, others only as a fallback
        if folder.is_dir():
            images = [p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS]
        if images:
            break
    if not images:
        return None
    rank = lambda p: (0 if re.search(r"front|cover|folder", p.name, re.I) else 1,
                      1 if re.search(r"back|cd|inside|tray", p.name, re.I) else 0, -p.stat().st_size)
    return sorted(images, key=rank)[0]


PACKAGES_FOLDER = PROJECT_ROOT / "data" / "release_groups"
SCANS = str(PROJECT_ROOT / "exports" / "library_scan_*.json")


def cover_rank(path):
    """Front covers first, then the largest image (back / CD / tray scans last)."""
    return (0 if re.search(r"front|cover|folder", path.name, re.I) else 1,
            1 if re.search(r"back|cd|inside|tray", path.name, re.I) else 0, -path.stat().st_size)


def library_folder_covers(connection, scan_path=None, packages_folder=PACKAGES_FOLDER):
    """{release_id: (folder name, cover path)} for releases built from the local library
    (packages with source LocalLibrary).  The release's own folder is the scanned folder
    holding most of its tracks' file names; its songs can have files in other folders
    too, so the files table alone does not tell which folder the release is."""
    scans = sorted(glob.glob(SCANS)) if scan_path is None else [scan_path]
    if not scans:
        return {}
    folders = json.loads(Path(scans[-1]).read_text(encoding="utf-8"))["folders"]
    names = [{t["file_name"] for t in f.get("tracks", [])} for f in folders]
    result = {}
    for path in Path(packages_folder).glob("**/*.json"):
        package = json.loads(path.read_text(encoding="utf-8"))
        if (package.get("release_group") or {}).get("source") != "LocalLibrary":
            continue
        for edition in [package.get("canonical_edition") or {}] + list(package.get("additional_editions") or []):
            release = edition.get("release") or {}
            files = {t.get("local_file") for t in edition.get("tracks") or [] if t.get("local_file")}
            rows = connection.execute("SELECT id FROM releases WHERE title = ? AND COALESCE(release_date, '') = ?",
                                      (release.get("title"), release.get("release_date") or "")).fetchall()
            if len(rows) != 1 or not files:
                continue
            overlap = [len(files & n) for n in names]
            best = max(range(len(folders)), key=lambda i: overlap[i])
            covers = [Path(c) for c in folders[best].get("covers") or [] if Path(c).is_file()]
            if overlap[best] and covers:
                result[rows[0][0]] = (folders[best]["folder"], sorted(covers, key=cover_rank)[0])
    return result


def plan(connection):
    """(official releases to look up, library releases with audio files)."""
    official = connection.execute("""
        SELECT id, title, musicbrainz_release_id, musicbrainz_release_group_id, cover_file,
               (SELECT count(*) FROM release_links l WHERE l.release_id = releases.id)
        FROM releases WHERE musicbrainz_release_id IS NOT NULL ORDER BY id""").fetchall()
    library = {}
    for release_id, path in connection.execute("""
        SELECT DISTINCT rt.release_id, f.file_path FROM release_tracks rt
        JOIN files f ON f.version_id = rt.version_id JOIN releases r ON r.id = rt.release_id
        WHERE r.musicbrainz_release_id IS NULL AND r.cover_file IS NULL"""):
        library.setdefault(release_id, []).append(path)
    return official, library


def main(argv=None):
    parser = argparse.ArgumentParser(description="Release covers and streaming links.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--library-covers", action="store_true",
                        help="re-take the covers of library releases from their own folder")
    args = parser.parse_args(argv)
    connection = sqlite3.connect(DATABASE_PATH)
    try:
        if args.library_covers:
            changed = 0
            for release_id, (folder, image) in sorted(library_folder_covers(connection).items()):
                current = connection.execute("SELECT cover_file FROM releases WHERE id = ?", (release_id,)).fetchone()[0]
                data = image.read_bytes()
                target = COVERS_FOLDER / (current or f"release_{release_id}.jpg")
                if not is_image(data) or (target.exists() and target.read_bytes() == data):
                    continue
                changed += 1
                print(f"{release_id:>4}  {folder[:70]}  <- {image.name}")
                if args.apply:
                    target.write_bytes(data)
                    if current is None:
                        with connection:
                            connection.execute("UPDATE releases SET cover_file = ? WHERE id = ?", (target.name, release_id))
            print(f"{changed} library covers to replace" + ("" if args.apply else " (dry run; --apply writes them)"))
            return
        connection.execute("PRAGMA foreign_keys = ON")
        official, library = plan(connection)
        todo = [r for r in official if r[4] is None or r[5] == 0]
        print(f"Official releases: {len(official)}; to look up: {len(todo)}; library releases without cover: "
              f"{len(library)}")
        if not args.apply:
            print("Dry run; use --apply to download covers and store links (make a backup first).")
            return
        COVERS_FOLDER.mkdir(parents=True, exist_ok=True)
        covers = links_added = 0
        group_cache = {}
        for index, (release_id, title, mbid, group_id, cover_file, link_count) in enumerate(todo, start=1):
            if cover_file is None:
                target = COVERS_FOLDER / f"release_{release_id}.jpg"
                if download_cover(mbid, group_id, target):
                    with connection:
                        connection.execute("UPDATE releases SET cover_file = ? WHERE id = ?", (target.name, release_id))
                    covers += 1
            if link_count == 0 and group_id:
                if group_id not in group_cache:
                    group_cache[group_id] = group_links(group_id) or {}
                with connection:
                    for service, url in group_cache[group_id].items():
                        connection.execute("INSERT OR IGNORE INTO release_links (release_id, service, url, source) "
                                           "VALUES (?, ?, ?, 'MusicBrainz')", (release_id, service, url))
                links_added += len(group_cache[group_id])
            if index % 20 == 0:
                print(f"  ... {index}/{len(todo)}", flush=True)
        copied = 0
        for release_id, paths in library.items():
            image = local_cover(paths)
            if image is None:
                continue
            target = COVERS_FOLDER / f"release_{release_id}{'.png' if image.suffix.lower() == '.png' else '.jpg'}"
            data = image.read_bytes()
            if not is_image(data):
                continue
            target.write_bytes(data)
            with connection:
                connection.execute("UPDATE releases SET cover_file = ? WHERE id = ?", (target.name, release_id))
            copied += 1
        print(f"Covers downloaded: {covers}; links stored: {links_added}; library covers copied: {copied}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
