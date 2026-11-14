"""Credits from Discogs for the official discography (cache of discogs_source.py).

For every catalogued edition of a release group with Discogs data, tracks are
matched by position + title (else a unique title match in that edition).
Credits of a matched track = its own Discogs credits + release credits whose
track range covers it ("1 to 5, 7"); album-wide credits without a range are
left out (executive producer, A&R, design ...), except mastering.
Roles use the same names as Genius credits (producer, writer,
featured_artist, mixing_engineer ...).  'Dwayne Carter' is Lil Wayne; the
Discogs number suffix "(2)" is dropped.  Album-version credits go to the
recording, other versions (remixes ...) get version credits.  Existing
credits are not repeated; new ones have credits.source = 'Discogs'.

usage:
    python discogs_credits.py           # dry run
    python discogs_credits.py --apply   # one transaction (backup first)
"""

import argparse
import re
import sqlite3

from discogs_source import load_cache
from genius_credits import BUSINESS, WAYNE, role_name
from import_release import DATABASE_PATH, get_or_create_artist
from library_matching import titles_match


SOURCE = "Discogs"
WAYNE_REAL_NAMES = re.compile(r"^(?:dwayne(?: michael)? carter(?:,? jr\.?)?|d\. carter)$", re.IGNORECASE)
ROLE_MAP = {
    "producer": "producer", "co-producer": "co_producer", "written-by": "writer", "written by": "writer",
    "lyrics by": "writer",
    "songwriter": "writer", "words by": "writer", "music by": "composer", "featuring": "featured_artist",
    "mixed by": "mixing_engineer", "recorded by": "recording_engineer", "mastered by": "mastering_engineer",
    "engineer": "engineer", "remix": "remixer", "vocals": "vocals", "scratches": "scratches",
}
ALBUM_WIDE = {"mastering_engineer"}


ARTIST_ALIASES = {"baby": "Birdman", "bryan williams": "Birdman"}


def artist_name(name):
    name = re.sub(r"\s*\(\d+\)$", "", str(name or "")).strip()
    if WAYNE_REAL_NAMES.match(name) or WAYNE.match(name):
        return "Lil Wayne"
    return ARTIST_ALIASES.get(name.casefold(), name)


def roles(text):
    """'Producer [Additional], Mixed By' -> ['producer', 'mixing_engineer']; business roles dropped."""
    result = []
    for part in re.split(r",\s*(?![^\[]*\])", str(text or "")):
        base = re.sub(r"\s*\[.*?\]", "", part).strip()
        if not base or BUSINESS.search(base) or re.search(r"a&r|director|coordinat|legal|photo", base, re.I):
            continue
        result.append(ROLE_MAP.get(base.casefold(), role_name(base)))
    return result


def position_number(position):
    """'2-5' -> (2, 5); '7' -> (1, 7); 'B2' -> (1, None)."""
    match = re.fullmatch(r"\s*(?:(\d+)[-.])?(\d+)\s*", str(position or ""))
    if not match:
        return None
    return int(match.group(1) or 1), int(match.group(2))


def in_range(position, spec):
    """Does a Discogs track range ('1 to 5, 7', 'A1 to B2') include position?"""
    if not spec:
        return False
    order = lambda p: [int(x) if x.isdigit() else x for x in re.findall(r"\d+|[A-Za-z]+", p)]
    for part in re.split(r"\s*,\s*", spec):
        bounds = re.split(r"\s+to\s+", part.strip())
        try:
            if len(bounds) == 2 and order(bounds[0]) <= order(position) <= order(bounds[1]):
                return True
        except TypeError:
            continue
        if part.strip() == position:
            return True
    return False


def track_credits(track, release):
    """[(artist, role)] of one Discogs track: its own credits, release credits
    whose range covers it, and album-wide roles (mastering) without a range."""
    credits = []

    def add(person, allowed=None):
        for role in roles(person.get("role")):
            item = (artist_name(person.get("name")), role)
            if (allowed is None or role in allowed) and item not in credits:
                credits.append(item)

    for person in track.get("extraartists", []):
        add(person)
    position = track.get("position", "")
    for person in release.get("extraartists", []):
        if person.get("tracks"):
            if in_range(position, person["tracks"]):
                add(person)
        else:
            add(person, ALBUM_WIDE)
    return credits


def match_edition(edition_tracks, release):
    """[(our track, discogs track)] for one catalogued edition."""
    discogs_tracks = [t for t in release.get("tracklist", []) if (t.get("type") or "track") == "track"]
    by_position = {position_number(t.get("position")): t for t in discogs_tracks if position_number(t.get("position"))}
    pairs = []
    for track in edition_tracks:
        candidate = by_position.get((track["disc"], track["number"]))
        if candidate and titles_match(track["title"], candidate.get("title")):
            pairs.append((track, candidate))
            continue
        same_title = [t for t in discogs_tracks if titles_match(track["title"], t.get("title"))]
        if len(same_title) == 1:
            pairs.append((track, same_title[0]))
    return pairs


def editions(connection):
    """{release group key: [[track dicts of one edition], ...]} for official releases."""
    result = {}
    for release_id, group_id in connection.execute(
            "SELECT id, musicbrainz_release_group_id FROM releases WHERE discography = 'official'"):
        tracks = [{"disc": disc or 1, "number": number, "title": title, "version_id": version_id,
                   "recording_id": recording_id, "version_type": version_type, "own": appearance is None}
                  for disc, number, title, version_id, recording_id, version_type, appearance in connection.execute("""
                      SELECT rt.disc_number, rt.track_number, COALESCE(rt.track_title, r.title), v.id, r.id,
                             v.version_type, r.appearance
                      FROM release_tracks rt JOIN versions v ON v.id = rt.version_id
                      JOIN recordings r ON r.id = v.recording_id
                      WHERE rt.release_id = ? ORDER BY rt.disc_number, rt.track_number""", (release_id,))]
        result.setdefault(group_id or f"release:{release_id}", []).append(tracks)
    return result


def plan(connection, cache):
    """(additions [(column, id, artist, role)], stats)."""
    existing = {}
    for column in ("recording_id", "version_id"):
        for target_id, name, role in connection.execute(f"""
            SELECT c.{column}, a.name, c.role FROM credits c JOIN artists a ON a.id = c.artist_id
            WHERE c.{column} IS NOT NULL"""):
            existing.setdefault((column, target_id), set()).add((name.casefold(), role))
    additions, stats = [], {"release groups": 0, "tracks matched": 0, "tracks unmatched": 0}
    for key, edition_list in editions(connection).items():
        release = (cache.get(key) or {}).get("discogs")
        if not release:
            continue
        stats["release groups"] += 1
        seen_versions = set()
        for edition_tracks in edition_list:
            pairs = match_edition(edition_tracks, release)
            matched = {id(track) for track, _ in pairs}
            stats["tracks unmatched"] += sum(1 for t in edition_tracks if id(t) not in matched
                                             and t["version_id"] not in seen_versions)
            for track, discogs_track in pairs:
                if track["version_id"] in seen_versions:
                    continue
                seen_versions.add(track["version_id"])
                stats["tracks matched"] += 1
                column, target = (("recording_id", track["recording_id"]) if track["version_type"] == "album"
                                  else ("version_id", track["version_id"]))
                have = existing.setdefault((column, target), set())
                recording_have = existing.get(("recording_id", track["recording_id"]), set())
                for name, role in track_credits(discogs_track, release):
                    if track["own"] and name == "Lil Wayne" and role in ("main_artist", "featured_artist"):
                        continue
                    if (name.casefold(), role) in have or (name.casefold(), role) in recording_have:
                        continue
                    have.add((name.casefold(), role))
                    additions.append((column, target, name, role))
    return additions, stats


def apply(connection, additions):
    for column, target_id, name, role in additions:
        connection.execute(f"INSERT INTO credits (artist_id, {column}, role, source) VALUES (?, ?, ?, ?)",
                           (get_or_create_artist(connection, name), target_id, role, SOURCE))
    return len(additions)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Credits from Discogs (cached releases).")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    connection = sqlite3.connect(DATABASE_PATH)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        additions, stats = plan(connection, load_cache())
        by_role = {}
        for *_, role in additions:
            by_role[role] = by_role.get(role, 0) + 1
        print(f"{stats}; credits to add: {len(additions)}")
        print("By role:", dict(sorted(by_role.items(), key=lambda x: -x[1])[:15]))
        if not args.apply:
            print("Dry run; use --apply to change the database (make a backup first).")
            return additions
        with connection:
            print(f"Added: {apply(connection, additions)}")
        return additions
    finally:
        connection.close()


if __name__ == "__main__":
    main()
