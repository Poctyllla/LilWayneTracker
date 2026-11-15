"""Photos of the most credited people for the People page (Genius artist images).

For each person of the People list (tracker_data.people) the Genius search is
asked for the person's name; the artist image of an exact name match is used
(Genius's default avatar is skipped).  Images go to web/covers/people/<id>.jpg
(not in git, like the covers); data/artist_images.json keeps for every artist
the file and where it came from.  SQLite is not changed.

The token comes from GENIUS_ACCESS_TOKEN (genius_source.py).

usage:
    python people_images.py [--limit 300] [--apply]     # dry run without --apply
"""

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import genius_source
import tracker_data
from release_media import is_image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FOLDER = PROJECT_ROOT / "web" / "covers" / "people"
MAP_PATH = tracker_data.ARTIST_IMAGES_PATH


def find_image(name):
    """(image url, Genius artist page) of the artist called exactly `name`, or (None, None)."""
    data = genius_source.api_get("/search", {"q": name, "per_page": 20}) or {}
    for hit in data.get("hits", []):
        result = hit.get("result") or {}
        for artist in [result.get("primary_artist") or {}] + list(result.get("featured_artists") or []):
            if artist.get("name", "").casefold() == name.casefold():
                url = artist.get("image_url") or ""
                if url and "default_avatar" not in url and "default-avatar" not in url:
                    return url, artist.get("url")
    return None, None


def download(url):
    request = urllib.request.Request(url, headers={"User-Agent": genius_source.USER_AGENT})
    try:
        time.sleep(0.3)
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return None
    return data if is_image(data) else None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Photos of the most credited people (Genius).")
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    images = json.loads(MAP_PATH.read_text(encoding="utf-8")) if MAP_PATH.exists() else {}
    connection = tracker_data.connect()
    try:
        people = tracker_data.people(connection, limit=args.limit)
    finally:
        connection.close()
    found = missing = 0
    for person in people:
        key = str(person["id"])
        if key in images:
            continue
        url, page = find_image(person["name"])
        print(f"{person['name'][:30]:<30} {'photo' if url else '-'}", flush=True)
        if not url:
            missing += 1
            continue
        found += 1
        if args.apply:
            data = download(url)
            if not data:
                continue
            FOLDER.mkdir(parents=True, exist_ok=True)
            (FOLDER / f"{person['id']}.jpg").write_bytes(data)
            images[key] = {"file": f"people/{person['id']}.jpg", "source": "Genius", "page": page, "image": url,
                           "fetched": time.strftime("%Y-%m-%d")}
            MAP_PATH.write_text(json.dumps(images, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{found} photos found, {missing} without" + ("" if args.apply else " (dry run; --apply downloads them)"))


if __name__ == "__main__":
    main()
