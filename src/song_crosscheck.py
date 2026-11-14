"""Cross-check of song lists: Genius, LilWayneHQ "Every Song" CSV and the catalogue.

Read-only (never writes SQLite).  Every song title from Genius (Lil Wayne as
primary artist) and from the HQ CSV is compared with the catalogue (main
recording titles and the edition spellings of all release tracks, official
and unofficial).  Result per song:
    in_catalogue   same title as a catalogued recording (released / leaked)
    probable       a similar spelling on the project HQ names ("Black
                   Republicans" ~ "Black Republican" on Da Drought 3)
    variant        only the base title is catalogued: "A Milli [Demo]" next
                   to "A Milli" -- a demo / snippet / freestyle / remix of it
    missing        no catalogued recording with that title
Out-of-scope variants (clean, instrumental, Screwed & Chopped) are skipped.

usage:
    python song_crosscheck.py [genius_export.json] [hq_songs.csv]
writes exports/song_crosscheck_<date>.json
"""

import csv
import json
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

from import_release import DATABASE_PATH
from difflib import SequenceMatcher

from library_matching import censored_match, release_key, squash
from titles import normalized_title
from version_policy import excluded_track_reason


PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPORTS_FOLDER = PROJECT_ROOT / "exports"
DEFAULT_GENIUS = EXPORTS_FOLDER / "genius_songs_4_20261112.json"
DEFAULT_HQ = PROJECT_ROOT / "data" / "sources" / "lilwaynehq_songs.csv"
LIL_WAYNE_GENIUS_ID = 4
NO_PROJECT = {"–", "-", "—", ""}

# Bracketed labels that do not make another song or version.
NEUTRAL_MARKER = re.compile(
    r"[(\[]\s*(?:mixed|mastered|unmastered|cdq|dirty|explicit|hq|disc \d|cd \d|"
    r"(?:the )?w\. carter collection)\s*[)\]]",
    re.IGNORECASE,
)
BRACKETS = re.compile(r"[(\[][^)\]]*[)\]]")
LEAD_CREDIT = re.compile(r"^\s*lil wayne\b", re.IGNORECASE)


STAR = "ⱺ"  # a letter, so normalized_title keeps censor stars: "F**k" vs "Fuck"


def _key(title):
    return squash(normalized_title(title.replace("*", STAR)).replace(STAR, "*"))


def song_key(title):
    """Comparable song title (neutral labels ignored, versions kept)."""
    return _key(NEUTRAL_MARKER.sub("", str(title or "")))


def base_key(title):
    """Title without any bracketed part: the song a variant belongs to."""
    return _key(BRACKETS.sub("", str(title or "")))


class CatalogueIndex:
    """Lookup of catalogued recordings by song key (exact and censored)."""

    def __init__(self, rows):
        # rows: (recording_id, title, availability)
        self.by_key, self.by_base = {}, {}
        for recording_id, title, availability in rows:
            entry = (recording_id, availability)
            key = song_key(title)
            if key:
                self.by_key.setdefault(key, set()).add(entry)
            if base_key(title) and base_key(title) == key:
                self.by_base.setdefault(key, set()).add(entry)
        self._starred = [key for key in self.by_key if "*" in key]

    def find(self, title):
        key = song_key(title)
        if not key:
            return set()
        found = set(self.by_key.get(key, ()))
        for other in self._starred if "*" not in key else self.by_key:
            if len(other) == len(key) and censored_match(other, key):
                found |= self.by_key[other]
        return found

    def find_base(self, title):
        key = base_key(title)
        return set(self.by_base.get(key, ())) if key and key != song_key(title) else set()


PROBABLE_RATIO = 0.8
VERSION_WORDS = re.compile(
    r"\b(re)?mix\b|\b(original|demo|snippet|live|freemix|alternate|og|reference|acapella)\b",
    re.IGNORECASE)


def version_markers(title):
    """{'remix', 'original', ...} -- words that make another version of a song."""
    words = {match.group(0).casefold() for match in VERSION_WORDS.finditer(str(title or ""))}
    return {"remix" if word == "mix" else word for word in words}


def similar(a, b):
    """Spelling variants of one title: 'Black Republicans' ~ 'Black Republican'."""
    ka, kb = base_key(a), base_key(b)
    if not (ka and kb) or version_markers(a) != version_markers(b):
        return False
    if re.findall(r"\d+", ka) != re.findall(r"\d+", kb):
        return False  # "On Tha Block #1" is not "#2"
    if ka == kb or (min(len(ka), len(kb)) >= 5 and (ka in kb or kb in ka)):
        return True
    return SequenceMatcher(None, ka, kb).ratio() >= PROBABLE_RATIO


def release_track_rows(connection):
    """{release_key: [(recording_id, title, availability)]} of all release tracks."""
    tracks = {}
    for release_title, recording_id, title, availability in connection.execute("""
        SELECT x.title, r.id, COALESCE(rt.track_title, r.title), a.availability
        FROM release_tracks rt JOIN releases x ON x.id = rt.release_id
        JOIN versions v ON v.id = rt.version_id JOIN recordings r ON r.id = v.recording_id
        JOIN recording_availability a ON a.recording_id = r.id
    """):
        tracks.setdefault(release_key(release_title), []).append((recording_id, title, availability))
    return tracks


def find_on_projects(song, tracks_by_release):
    """Catalogued tracks with a similar title on the projects HQ names."""
    found = set()
    for project in song["hq_projects"]:
        for recording_id, title, availability in tracks_by_release.get(release_key(project), ()):
            if similar(song["title"], title):
                found.add((recording_id, availability))
    return found


def catalogue_rows(connection, own_only=True):
    """(recording_id, title, availability) of catalogued songs; own_only leaves
    out other artists' songs with a Lil Wayne guest spot (appearance 'guest')."""
    has_appearance = any(row[1] == "appearance" for row in connection.execute("PRAGMA table_info(recordings)"))
    own = "AND r.appearance IS NULL" if own_only and has_appearance else ""
    return connection.execute(f"""
        SELECT r.id, r.title, a.availability FROM recordings r
        JOIN recording_availability a ON a.recording_id = r.id
        WHERE 1 = 1 {own}
        UNION
        SELECT r.id, rt.track_title, a.availability FROM release_tracks rt
        JOIN versions v ON v.id = rt.version_id
        JOIN recordings r ON r.id = v.recording_id
        JOIN recording_availability a ON a.recording_id = r.id
        WHERE rt.track_title IS NOT NULL {own}
    """).fetchall()


def load_genius(path):
    songs = json.loads(Path(path).read_text(encoding="utf-8"))
    return [s for s in songs if s.get("primary_artist_id") == LIL_WAYNE_GENIUS_ID]


def load_hq(path):
    with open(path, encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def collect_songs(genius_songs, hq_rows):
    """{song_key: song} merged from both sources; out-of-scope variants skipped."""
    songs, skipped = {}, 0

    def entry(title):
        key = song_key(title)
        return songs.setdefault(key, {"title": title, "genius": [], "hq_projects": [], "hq_artists": []})

    for song in genius_songs:
        if excluded_track_reason(song["title"]) or not song_key(song["title"]):
            skipped += 1
            continue
        entry(song["title"])["genius"].append({"id": song["genius_id"], "url": song["url"],
                                               "release_date": song.get("release_date")})
    for row in hq_rows:
        title = row["Song Title"].strip()
        if excluded_track_reason(title) or not song_key(title):
            skipped += 1
            continue
        song = entry(title)
        project = row["Project"].strip()
        if project not in NO_PROJECT and project not in song["hq_projects"]:
            song["hq_projects"].append(project)
        if project in NO_PROJECT:
            song["hq_no_project"] = True
        if row["Artists"] not in song["hq_artists"]:
            song["hq_artists"].append(row["Artists"])
    return songs, skipped


def classify(songs, index, tracks_by_release=None):
    """Adds status / catalogue matches to every song; returns {status: count}."""
    counts = {}
    for song in songs.values():
        found = index.find(song["title"])
        if found:
            song["status"] = "in_catalogue"
        elif tracks_by_release and (found := find_on_projects(song, tracks_by_release)):
            song["status"] = "probable"  # spelling variant on the project HQ names
        else:
            found = index.find_base(song["title"])
            song["status"] = "variant" if found else "missing"
        song["catalogue"] = [dict(recording_id=r, availability=a) for r, a in sorted(found)]
        sources = ("genius" if song["genius"] else "") + ("+hq" if song["hq_artists"] else "")
        song["sources"] = sources.strip("+")
        # Lead = Genius primary artist or an HQ credit that starts with Lil Wayne;
        # the others are guest appearances (a later step).
        song["role"] = "lead" if song["genius"] or any(
            LEAD_CREDIT.match(artists) for artists in song["hq_artists"]) else "featured"
        bucket = counts.setdefault(song["role"], {}).setdefault(song["status"], {})
        bucket[song["sources"]] = bucket.get(song["sources"], 0) + 1
    return counts


def main():
    genius_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_GENIUS
    hq_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_HQ
    connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
    try:
        index = CatalogueIndex(catalogue_rows(connection))
        tracks_by_release = release_track_rows(connection)
    finally:
        connection.close()
    songs, skipped = collect_songs(load_genius(genius_path), load_hq(hq_path))
    counts = classify(songs, index, tracks_by_release)
    EXPORTS_FOLDER.mkdir(exist_ok=True)
    out = EXPORTS_FOLDER / f"song_crosscheck_{date.today():%Y%m%d}.json"
    ordered = sorted(songs.values(), key=lambda s: (s["status"], s["title"].casefold()))
    out.write_text(json.dumps({"counts": counts, "songs": ordered}, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    print(f"Songs: {len(songs)} (out-of-scope variants skipped: {skipped})")
    for role, by_status in sorted(counts.items(), reverse=True):
        print(f" {role}: {sum(sum(s.values()) for s in by_status.values())}")
        for status, by_source in sorted(by_status.items()):
            print(f"  {status:<13} {sum(by_source.values()):>5}  {by_source}")
    print(f"Saved: {out.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
