"""Chart peaks and certifications of Lil Wayne's singles and albums from Wikipedia.

Reads the tables of "Lil Wayne singles discography" (as lead artist, as featured
artist, promotional singles, other charted songs) and "Lil Wayne albums discography"
(studio and collaboration albums) through the MediaWiki API (cached in
exports/wikipedia_charts.json), and stores
  * chart_entries:   song or release, chart (US, US R&B/HH, UK ...), peak position
  * certifications:  song or release, body (RIAA, BPI ...), level ("6× Platinum")
Songs are matched by title (featured singles: also the main artist), albums by title
among the official releases.  Sales figures are not kept.

usage:
    python charts_source.py            # dry run: what would be stored, what is not matched
    python charts_source.py --apply    # replace the stored chart data (back up first)
"""

import argparse
import json
import re
import sqlite3
import time
from pathlib import Path

from import_release import DATABASE_PATH
from streaming_search import letters
from titles import normalized_title
from wikipedia_source import api_get

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = PROJECT_ROOT / "exports" / "wikipedia_charts.json"
SINGLES_PAGE, ALBUMS_PAGE = "Lil Wayne singles discography", "Lil Wayne albums discography"
SINGLES_SECTIONS = ("As lead artist", "1999–2009", "2010–2019", "2020–present", "Promotional singles", "Other charted songs")
ALBUM_SECTIONS = ("Studio albums", "Collaboration albums")
BODIES = ("RIAA", "BPI", "MC", "ARIA", "RMNZ", "IFPI DEN", "IFPI SWE", "BVMI", "SNEP", "GLF", "IFPI NOR", "FIMI", "PROMUSICAE",
          "Pro-Música Brasil", "NVPI", "IFPI AUT", "IFPI SWI", "ZPAV", "IFPI")
LEVEL = re.compile(r"(?:\d+\s*[×x]\s*)?(?:Diamond|Platinum|Gold|Silver)", re.IGNORECASE)


def wiki_url(page):
    return "https://en.wikipedia.org/wiki/" + page.replace(" ", "_")


def section_texts():
    """{(page, section): wikitext}, cached."""
    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
    for page, sections in ((SINGLES_PAGE, SINGLES_SECTIONS), (ALBUMS_PAGE, ALBUM_SECTIONS)):
        if all(f"{page}|{s}" in cache for s in sections):
            continue
        time.sleep(3)
        data = api_get({"action": "parse", "page": page, "prop": "sections"})
        index = {s["line"]: s["index"] for s in (data or {}).get("parse", {}).get("sections", [])}
        for section in sections:
            key = f"{page}|{section}"
            if key in cache or section not in index:
                continue
            time.sleep(4)
            text = api_get({"action": "parse", "page": page, "prop": "wikitext", "section": index[section]})
            if text:
                cache[key] = text["parse"]["wikitext"]
                CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return {tuple(k.split("|", 1)): v for k, v in cache.items()}


def plain(text):
    """Wiki markup -> text."""
    text = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", text or "", flags=re.S)
    text = re.sub(r"\{\{(?:small|nowrap|abbr)\|([^|}]*)[^}]*\}\}", r"\1", text)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"<br\s*/?>", " ", text)
    text = re.sub(r"<[^>]+>|'''?", "", text)
    return re.sub(r"\s+", " ", text).strip()


def chart_names(header):
    """The chart columns of a table: the labels of the second header row."""
    names = []
    for cell in re.findall(r'^!\s*scope="col"[^|\n]*\|\s*(.*)$', header, re.M):
        label = re.split(r"<ref", cell)[0]
        link = re.search(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", label)
        names.append(plain(link.group(1) if link else label))
    return names


def cells(row):
    """The data cells of a table row, in order (lines starting with '|', split at '||')."""
    result = []
    for line in row.split("\n"):
        if line.startswith("|") and not line.startswith("|-") and not line.startswith("|}"):
            for part in line[1:].split("||"):
                part = re.sub(r'^\s*(?:rowspan|colspan|style|align)="?[^|"]*"?\s*\|', "", part)
                result.append(part.strip())
    return result


def parse_table(text):
    """[{"title", "small", "peaks": {chart: peak}, "certs": [(body, level)]}] of one section."""
    rows = re.split(r"\n\|-", text)
    if len(rows) < 3:
        return []
    charts = chart_names(rows[1])
    n = len(charts)
    result = []
    for row in rows[2:]:
        head = re.search(r'scope="row"[^|\n]*\|\s*(.+)', row)
        if not head:
            continue
        title_cell = head.group(1)
        quoted = re.search(r'"(.+?)"', title_cell)
        title = plain(quoted.group(1) if quoted else re.sub(r"\{\{small.*", "", title_cell))
        small = re.search(r"\{\{small\|\((.*?)\)\}\}", title_cell)
        # footnotes ("—{{efn|did not enter ...}}", "<ref ...>") are not part of the value
        values = [re.sub(r"\{\{efn.*$|<ref.*$", "", v, flags=re.S).strip() for v in cells(row)]
        peaks = {}
        for start in range(len(values) - n + 1):
            window = values[start:start + n]
            if all(re.fullmatch(r"\d{1,3}|—|-|–|\{\{[^}]*\}\}\s*\d{1,3}|\d{1,3}\s*<ref.*", v) for v in window) \
                    and any(re.search(r"\d", v) or v in ("—", "–") for v in window):
                for chart, value in zip(charts, window):
                    m = re.match(r"(?:\{\{[^}]*\}\}\s*)?(\d{1,3})", value)
                    if m:
                        peaks[chart] = int(m.group(1))
                break
        certs = []
        for line in re.findall(r"^\*\s*(.+)$", row, re.M):
            line = plain(line)
            m = re.match(r"([A-Za-zÀ-ÿ\- ]+?):\s*(.+)$", line)
            if not m:
                continue
            body, rest = m.group(1).strip(), m.group(2)
            level = LEVEL.search(rest)
            if body in BODIES and level:
                note = re.search(r"\(([^)]*)\)", rest)
                certs.append((body, level.group(0).replace("x", "×").replace(" ", ""), note.group(1) if note else None))
        result.append({"title": title, "small": plain(small.group(1)) if small else "", "peaks": peaks, "certs": certs})
    return result


def main_artist(small, featured_section):
    """'(Drake featuring Lil Wayne)' -> 'Drake' for featured singles; Lil Wayne otherwise."""
    if not featured_section:
        return "Lil Wayne"
    return re.split(r"\s+featuring\s+|\s+and\s+|,|\s+&\s+|\s+with\s+", small or "", maxsplit=1)[0].strip() or "Lil Wayne"


def song_lookup(connection):
    index = {}
    for rid, title, artist, released in connection.execute("""
            SELECT r.id, r.title, a.name, av.availability = 'released' FROM recordings r
            JOIN artists a ON a.id = r.artist_id JOIN recording_availability av ON av.recording_id = r.id"""):
        index.setdefault(normalized_title(title), []).append((rid, letters(artist), bool(released)))
        index.setdefault("letters:" + letters(title), []).append((rid, letters(artist), bool(released)))
    return index


def find_song(index, title, artist):
    candidates = index.get(normalized_title(title), []) or index.get("letters:" + letters(title), [])  # "Go D.J." = "Go DJ"
    by_artist = [c for c in candidates if c[1] == letters(artist)]
    pool = by_artist or ([c for c in candidates if c[1] == letters("Lil Wayne")] if artist != "Lil Wayne" else [])
    if not pool and len(candidates) == 1:
        pool = candidates  # the only song of that title in the catalogue (the artist was written differently)
    released = [c for c in pool if c[2]] or pool
    return released[0][0] if len(released) >= 1 and len({c[0] for c in released}) == 1 else (released[0][0] if released else None)


def find_release(connection, title):
    row = connection.execute("""
        SELECT id FROM releases WHERE discography = 'official' AND lower(title) = lower(?)
        ORDER BY length(release_date) DESC, release_date, id LIMIT 1""", (title,)).fetchone()
    return row[0] if row else None


def plan(connection):
    texts = section_texts()
    index = song_lookup(connection)
    entries, missing = [], []
    for (page, section), text in texts.items():
        for row in parse_table(text):
            if not row["peaks"] and not row["certs"]:
                continue
            if page == ALBUMS_PAGE:
                target = ("release", find_release(connection, row["title"]))
            else:
                featured = section in ("1999–2009", "2010–2019", "2020–present")
                target = ("recording", find_song(index, row["title"], main_artist(row["small"], featured)))
            if target[1] is None:
                missing.append(f"{section}: {row['title']} {('(' + row['small'] + ')') if row['small'] else ''}")
                continue
            entries.append({**row, "target": target, "source": wiki_url(page)})
    return entries, missing


def ensure_tables(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS chart_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recording_id INTEGER REFERENCES recordings(id), release_id INTEGER REFERENCES releases(id),
            chart TEXT NOT NULL, peak INTEGER NOT NULL, source TEXT,
            CHECK ((recording_id IS NOT NULL) + (release_id IS NOT NULL) = 1))""")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS certifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recording_id INTEGER REFERENCES recordings(id), release_id INTEGER REFERENCES releases(id),
            body TEXT NOT NULL, level TEXT NOT NULL, note TEXT, source TEXT,
            CHECK ((recording_id IS NOT NULL) + (release_id IS NOT NULL) = 1))""")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Chart peaks and certifications from Wikipedia.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        entries, missing = plan(connection)
        songs = sum(1 for e in entries if e["target"][0] == "recording")
        print(f"Matched: {songs} songs, {len(entries) - songs} albums; "
              f"{sum(len(e['peaks']) for e in entries)} chart peaks, {sum(len(e['certs']) for e in entries)} certifications")
        for e in entries[:8]:
            print(f"  {e['title']}: {e['peaks']} {e['certs'][:3]}")
        print(f"Not matched ({len(missing)}): {missing[:25]}")
        if not args.apply:
            print("Dry run; --apply stores them (back up first).")
            return
        with connection:
            ensure_tables(connection)
            connection.execute("DELETE FROM chart_entries")
            connection.execute("DELETE FROM certifications")
            # one song can stand in two rows (same title): the best peak per chart, each certification once
            best, certs = {}, {}
            for e in entries:
                column = "recording_id" if e["target"][0] == "recording" else "release_id"
                for chart, peak in e["peaks"].items():
                    key = (column, e["target"][1], chart)
                    if key not in best or peak < best[key][0]:
                        best[key] = (peak, e["source"])
                for body, level, note in e["certs"]:
                    certs.setdefault((column, e["target"][1], body, level, note), e["source"])
            for (column, target, chart), (peak, source) in best.items():
                connection.execute(f"INSERT INTO chart_entries ({column}, chart, peak, source) VALUES (?, ?, ?, ?)",
                                   (target, chart, peak, source))
            for (column, target, body, level, note), source in certs.items():
                connection.execute(f"INSERT INTO certifications ({column}, body, level, note, source) VALUES (?, ?, ?, ?, ?)",
                                   (target, body, level, note, source))
        print(f"Stored. Foreign keys: {connection.execute('PRAGMA foreign_key_check').fetchall()[:3]}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
