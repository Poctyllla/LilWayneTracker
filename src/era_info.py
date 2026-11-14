"""Cover art and a short description for every era (web tracker).

For an era named after an album (eras.musicbrainz_release_group_id):
  * cover: Cover Art Archive front image of the release group (500 px),
    saved once to web/covers/era_<id>.jpg (the page then works offline);
  * summary: the first sentences of the album's English Wikipedia article,
    found through MusicBrainz -> Wikidata -> enwiki (no guessed titles);
    the article URL is kept as attribution (Wikipedia text is CC BY-SA).
An era without an album gets a short generated summary and no cover.
eras.py never overwrites these fields.

usage:
    python era_info.py           # dry run: what would be fetched / written
    python era_info.py --apply   # download covers, write eras.summary/summary_url/cover_file
"""

import argparse
import re
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path

import wikipedia_source
from import_release import DATABASE_PATH
from musicbrainz_catalog import BASE_URL as MUSICBRAINZ_URL, HEADERS, api_request as musicbrainz_request


PROJECT_ROOT = Path(__file__).resolve().parent.parent
COVERS_FOLDER = PROJECT_ROOT / "web" / "covers"
COVER_URL = "https://coverartarchive.org/release-group/{}/front-500"
WIKIDATA_URL = "https://www.wikidata.org/wiki/Special:EntityData/{}.json"
SENTENCES = 3


def musicbrainz_wikidata_id(release_group_id):
    """Wikidata QID from the release group's URL relations, or None."""
    for attempt in range(3):
        time.sleep(1 + 2 * attempt)
        data = musicbrainz_request(f"{MUSICBRAINZ_URL}release-group/{release_group_id}?inc=url-rels&fmt=json")
        if data:
            break
    else:
        return None
    for relation in data.get("relations", []):
        match = re.search(r"wikidata\.org/wiki/(Q\d+)", (relation.get("url") or {}).get("resource", ""))
        if match:
            return match.group(1)
    return None


def http_json(url):
    import json
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


def enwiki_title(qid):
    data = http_json(WIKIDATA_URL.format(qid)) or {}
    entity = data.get("entities", {}).get(qid, {})
    return (entity.get("sitelinks", {}).get("enwiki") or {}).get("title")


def first_sentences(text, count=SENTENCES):
    """The first sentences of a plain-text intro (no footnote marks, no parenthesised pronunciations)."""
    text = re.sub(r"\s*\[\d+\]", "", text or "").strip()
    sentences = re.findall(r".+?(?:[.!?](?=\s+[A-Z\"“(])|$)", text.replace("\n", " "))
    return " ".join(s.strip() for s in sentences[:count]).strip()


def wikipedia_summary(title):
    """(summary, url) of an English Wikipedia article, or (None, None)."""
    data = wikipedia_source.api_get({"action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1,
                                     "redirects": 1, "titles": title}) or {}
    pages = data.get("query", {}).get("pages", [])
    if not pages or "extract" not in pages[0]:
        return None, None
    page_title = pages[0]["title"]
    return first_sentences(pages[0]["extract"]), f"https://en.wikipedia.org/wiki/{page_title.replace(' ', '_')}"


def download_cover(release_group_id, target):
    request = urllib.request.Request(COVER_URL.format(release_group_id), headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
    except (urllib.error.URLError, TimeoutError):
        return False
    if not data[:3] == b"\xff\xd8\xff" and not data[:8] == b"\x89PNG\r\n\x1a\n":
        return False  # not an image
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return True


def era_plan(connection):
    """[{id, name, release_group_id, has_cover, has_summary}] of all eras."""
    return [dict(zip(("id", "name", "release_group_id", "end_date", "cover_file", "summary"), row))
            for row in connection.execute("SELECT id, name, musicbrainz_release_group_id, end_date, cover_file, "
                                          "summary FROM eras ORDER BY COALESCE(end_date, '9999')")]


def generated_summary(era, eras):
    """Summary of an era without an album ('After Tha Carter VI')."""
    previous = [e for e in eras if e["end_date"] and e["release_group_id"]]
    last = previous[-1]["name"] if previous else "the last album"
    return f"Songs recorded or released after {last}, until Lil Wayne's next solo studio album."


def main(argv=None):
    parser = argparse.ArgumentParser(description="Era covers and summaries for the web tracker.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="fetch again even when already stored")
    args = parser.parse_args(argv)
    connection = sqlite3.connect(DATABASE_PATH)
    try:
        eras = era_plan(connection)
        updates = []
        for era in eras:
            if era["cover_file"] and era["summary"] and not args.refresh:
                continue
            if not era["release_group_id"]:
                updates.append((generated_summary(era, eras), None, None, era["id"]))
                print(f"{era['name']}: generated summary, no cover")
                continue
            if not args.apply:
                print(f"{era['name']}: would fetch cover + Wikipedia summary ({era['release_group_id']})")
                continue
            cover = COVERS_FOLDER / f"era_{era['id']}.jpg"
            has_cover = download_cover(era["release_group_id"], cover)
            qid = musicbrainz_wikidata_id(era["release_group_id"])
            title = enwiki_title(qid) if qid else None
            summary, url = wikipedia_summary(title) if title else (None, None)
            updates.append((summary, url, cover.name if has_cover else None, era["id"]))
            print(f"{era['name']}: cover {'ok' if has_cover else 'missing'}; Wikipedia: {title or 'not found'}")
        if not args.apply:
            print("Dry run; use --apply to download covers and write the summaries (make a backup first).")
            return updates
        with connection:
            for summary, url, cover_file, era_id in updates:
                connection.execute("UPDATE eras SET summary = COALESCE(?, summary), summary_url = COALESCE(?, summary_url), "
                                   "cover_file = COALESCE(?, cover_file) WHERE id = ?",
                                   (summary, url, cover_file, era_id))
        print(f"Eras updated: {len(updates)}")
        return updates
    finally:
        connection.close()


if __name__ == "__main__":
    main()
