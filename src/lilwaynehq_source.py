"""LilWayneHQ.com as a cross-check source, from pages saved by the user.

The site refuses automated requests (HTTP 403), so it is never fetched; the
user saves discography pages in the browser and this module parses the local
HTML files (standard library only, the content is treated as plain data).

A list page has an <h1> heading followed by one <p> per release, e.g.
"Grindin (feat. Drake)" or, on the collaboration page, the title and the
artist credit as separate text pieces ("T-Wayne", "Lil Wayne & T-Pain").

usage:
    python lilwaynehq_source.py singles  "<saved Lil Wayne Singles.html>"
    python lilwaynehq_source.py mixtapes "<saved mixtapes page.html>"
    python lilwaynehq_source.py collabs  "<saved collaboration albums page.html>"
"""

import re
import sqlite3
import sys
from html.parser import HTMLParser

from import_release import DATABASE_PATH
from titles import normalized_title


class _ListPage(HTMLParser):
    """Collect the text pieces of every <p> between the <h1> and the footer."""

    def __init__(self):
        super().__init__()
        self.heading = None
        self.in_h1 = False
        self.started = False
        self.finished = False
        self.skip = 0
        self.items = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self.skip += 1
        elif tag == "h1" and not self.started:
            self.in_h1 = True
        elif tag == "p" and self.started and not self.finished:
            self.current = []
        elif tag == "h2" and self.started:
            self.finished = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self.skip:
            self.skip -= 1
        elif tag == "h1" and self.in_h1:
            self.in_h1 = False
            self.started = True
        elif tag == "p" and self.current is not None:
            pieces = [piece for piece in self.current if piece]
            if pieces:
                self.items.append(pieces)
            self.current = None

    def handle_data(self, data):
        if self.skip:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if self.in_h1:
            self.heading = text
        elif self.current is not None and text:
            if text.startswith("»"):
                self.finished = True
                self.current = None
            else:
                self.current.append(text)


CREDIT = re.compile(r"\s*\((with|feat\.?|featuring)\s+([^)]*)\)\s*$", re.IGNORECASE)


def parse_list_page(html):
    """Return (heading, entries); entry = {title, with, featuring, credit}."""
    page = _ListPage()
    page.feed(html)
    entries = []
    for pieces in page.items:
        # Bracketed credit pieces, e.g. "(with Young Money)", belong to the title.
        while len(pieces) > 1 and pieces[1].startswith("("):
            pieces = [f"{pieces[0]} {pieces[1]}"] + pieces[2:]
        text = pieces[0]
        if text.startswith(("Here,", "If you have")) or len(" ".join(pieces)) > 200:
            continue  # introduction / help paragraphs
        entry = {"title": text, "with": None, "featuring": None,
                 "credit": " ".join(pieces[1:]) or None}
        while True:
            match = CREDIT.search(entry["title"])
            if not match:
                break
            key = "with" if match.group(1).casefold() == "with" else "featuring"
            entry[key] = match.group(2).strip()
            entry["title"] = entry["title"][:match.start()].strip()
        entries.append(entry)
    return page.heading, entries


def load_list_page(path):
    with open(path, encoding="utf-8", errors="replace") as file:
        return parse_list_page(file.read())


def catalogue_titles(connection, release_types=None):
    """{normalized title: [(title, release_type, discography)]} of catalogued releases."""
    sql = "SELECT DISTINCT title, release_type, discography FROM releases"
    params = ()
    if release_types:
        sql += f" WHERE release_type IN ({', '.join('?' for _ in release_types)})"
        params = tuple(release_types)
    titles = {}
    for title, release_type, discography in connection.execute(sql, params):
        titles.setdefault(normalized_title(title), []).append((title, release_type, discography))
    return titles


def compare(entries, titles):
    """Split entries into (found, missing) by normalized title (prefix match allowed)."""
    found, missing = [], []
    for entry in entries:
        key = normalized_title(TITLE_ALIASES.get(entry["title"], entry["title"]))
        match = titles.get(key) or next(
            (value for name, value in titles.items()
             if name.startswith(key + " ") or key.startswith(name + " ")), None)
        (found if match else missing).append((entry, match))
    return found, missing


KINDS = {
    "singles": ("single", "group_single"),
    "mixtapes": ("mixtape", "collaboration_album"),
    "collabs": ("collaboration_album", "group_album", "mixtape", "studio_album"),
}

# LilWayneHQ title -> catalogue (MusicBrainz) title, where they differ.
TITLE_ALIASES = {
    "D6: Reloaded": "Dedication 6: Reloaded",
    "The Carter #2 (Like Father, Like Son)": "The Carter 2, Part 2: Like Father Like Son",
    "Fuck Today": "F**k Today",
}


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in KINDS:
        print(__doc__)
        return
    kind, path = sys.argv[1], sys.argv[2]
    heading, entries = load_list_page(path)
    connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
    try:
        found, missing = compare(entries, catalogue_titles(connection, KINDS[kind]))
    finally:
        connection.close()
    print(f"{heading}: {len(entries)} entries, in catalogue {len(found)}, missing {len(missing)}")
    for entry, _ in missing:
        extra = ", ".join(f"{k} {entry[k]}" for k in ("with", "featuring", "credit") if entry[k])
        print(f"   MISSING  {entry['title']}" + (f"  ({extra})" if extra else ""))


if __name__ == "__main__":
    main()
