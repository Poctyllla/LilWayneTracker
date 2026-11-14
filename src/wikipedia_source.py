"""Wikipedia as a second source for cross-checking (read-only).

Uses the official MediaWiki API (no page scraping).  The parser reads the
release tables of "Lil Wayne albums discography" (e.g. the Mixtapes table):
title, collaborators ("with ..."), release date, label and format.
MusicBrainz stays the identifier backbone; Wikipedia data is used to decide
which releases belong to the official discography and to compare details.

usage:  python wikipedia_source.py Mixtapes
"""

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


API_URL = "https://en.wikipedia.org/w/api.php"
DISCOGRAPHY_PAGE = "Lil Wayne albums discography"
HEADERS = {"User-Agent": "LilWayneTracker/0.1 (local catalog project)"}


def api_get(params):
    url = API_URL + "?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": 2})
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as error:
        print(f"Wikipedia request failed: {error}")
        return None


def fetch_section_wikitext(section_title, page=DISCOGRAPHY_PAGE):
    """Wikitext of one section (matched by heading, case-insensitive), or None."""
    data = api_get({"action": "parse", "page": page, "prop": "sections|revid"})
    if not data:
        return None
    sections = data["parse"]["sections"]
    index = next((s["index"] for s in sections
                  if s["line"].strip().casefold() == section_title.casefold()), None)
    if index is None:
        return None
    time.sleep(1)
    data = api_get({"action": "parse", "page": page, "prop": "wikitext", "section": index})
    return data["parse"]["wikitext"] if data else None


def strip_markup(text):
    """Plain text from a wikitext fragment (refs, templates, links, tags removed)."""
    text = re.sub(r"<ref[^>/]*/>", "", text)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("''", "")
    return re.sub(r"\s+", " ", text).strip()


def parse_release_table(wikitext):
    """Rows of a discography table as dicts: title, with, released, label, format."""
    rows = []
    for chunk in re.split(r"\n\|-[^\n]*\n", wikitext or ""):
        header = re.search(r"^!\s*scope=\"row\"[^|]*\|(.*)$", chunk, flags=re.M)
        if not header:
            continue
        cell = header.group(1)
        with_match = re.search(r"\(with ([^)]*)\)", strip_markup(cell))
        title_part = re.split(r"<br\s*/?>", cell)[0]
        details = {}
        for key in ("Released", "Label", "Format"):
            match = re.search(rf"^\*\s*{key}s?:\s*(.*)$", chunk, flags=re.M)
            if match:
                details[key.casefold()] = strip_markup(match.group(1))
        rows.append({
            "title": strip_markup(title_part),
            "with": strip_markup(with_match.group(1)) if with_match else None,
            "released": details.get("released"),
            "label": details.get("label"),
            "format": details.get("format"),
        })
    return rows


def fetch_releases(section_title):
    """Parsed rows of a section of the albums discography page ([] on failure)."""
    return parse_release_table(fetch_section_wikitext(section_title))


def main():
    section = " ".join(sys.argv[1:]) or "Mixtapes"
    rows = fetch_releases(section)
    for row in rows:
        print(f"{row['released'] or '?':20} | {row['title']}"
              + (f" (with {row['with']})" if row["with"] else "")
              + f" | {row['label'] or ''}")
    print(f"Total: {len(rows)}")


if __name__ == "__main__":
    main()
