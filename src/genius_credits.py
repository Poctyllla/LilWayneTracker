"""Credits from Genius for every catalogued song with a Genius ID.

Uses the cache of genius_details.py (no API calls here).  Roles:
  primary artist      main_artist     (not for Lil Wayne's own songs: he is
  featured artists    featured_artist  the recording's artist already)
  producers           producer
  writers             writer
  other credited roles ("Mixing Engineer", "Recorded At" ...) as snake_case
Business entries (label, publisher, copyright, distributor, video ...) are
left out.  A Genius ID on a recording gives recording credits, one on a
version (a demo, a snippet ...) gives credits of that version.  Every
credit keeps credits.source = 'Genius'; existing credits are not repeated.

usage:
    python genius_credits.py           # dry run
    python genius_credits.py --apply   # one transaction (backup first)
"""

import argparse
import re
import sqlite3

from genius_details import load_cache
from import_release import DATABASE_PATH, get_or_create_artist


SOURCE = "Genius"
WAYNE = re.compile(r"^\s*lil'?\s*wa[yn]{2}e\s*$", re.IGNORECASE)
BUSINESS = re.compile(r"label|publisher|copyright|distribut|manufactur|marketing|video|management|"
                      r"a&r|licens|clearance|booking|publicity|art direction|photograph|graphic|design",
                      re.IGNORECASE)


def role_name(label):
    """'Mixing Engineer' -> 'mixing_engineer'."""
    return re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")


def genius_credits(details, own_song):
    """[(artist, role)] from one cached Genius song."""
    credits = []

    def add(name, role):
        if name and (name, role) not in credits:
            credits.append((name, role))

    for name in [details.get("primary_artist")]:
        if not (own_song and WAYNE.match(name or "")):
            add(name, "main_artist")
    for name in details.get("featured_artists", []):
        if not (own_song and WAYNE.match(name)):
            add(name, "featured_artist")
    for name in details.get("producers", []):
        add(name, "producer")
    for name in details.get("writers", []):
        add(name, "writer")
    for performance in details.get("custom_performances", []):
        label = performance.get("label") or ""
        if not label or BUSINESS.search(label) or re.search(r"\bat$", label.strip(), re.IGNORECASE):
            continue  # business entries; "Recorded At" / "Mixed At" name studios, not people
        for name in performance.get("artists", []):
            add(name, role_name(label))
    return credits


def targets(connection):
    """[(column, id, genius_id, own_song)] for recordings and versions with a Genius ID."""
    rows = [("recording_id", recording_id, genius_id, appearance is None) for recording_id, genius_id, appearance
            in connection.execute("SELECT id, genius_song_id, appearance FROM recordings "
                                  "WHERE genius_song_id IS NOT NULL")]
    rows += [("version_id", version_id, genius_id, appearance is None) for version_id, genius_id, appearance
             in connection.execute("""
                 SELECT v.id, v.genius_song_id, r.appearance FROM versions v
                 JOIN recordings r ON r.id = v.recording_id WHERE v.genius_song_id IS NOT NULL""")]
    return rows


def plan(connection, cache):
    """[(column, target id, artist, role)] of credits to add; missing details counted."""
    existing = {}
    for column in ("recording_id", "version_id"):
        for target_id, name, role in connection.execute(f"""
            SELECT c.{column}, a.name, c.role FROM credits c JOIN artists a ON a.id = c.artist_id
            WHERE c.{column} IS NOT NULL"""):
            existing.setdefault((column, target_id), set()).add((name.casefold(), role))
    additions, missing = [], 0
    for column, target_id, genius_id, own_song in targets(connection):
        details = cache.get(genius_id)
        if details is None:
            missing += 1
            continue
        have = existing.setdefault((column, target_id), set())
        for name, role in genius_credits(details, own_song):
            if (name.casefold(), role) not in have:
                have.add((name.casefold(), role))
                additions.append((column, target_id, name, role))
    return additions, missing


def apply(connection, additions):
    """Insert the credits; caller commits."""
    for column, target_id, name, role in additions:
        connection.execute(f"INSERT INTO credits (artist_id, {column}, role, source) VALUES (?, ?, ?, ?)",
                           (get_or_create_artist(connection, name), target_id, role, SOURCE))
    return len(additions)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Credits from Genius (cached song details).")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    connection = sqlite3.connect(DATABASE_PATH)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        additions, missing = plan(connection, load_cache())
        roles = {}
        for _, _, _, role in additions:
            roles[role] = roles.get(role, 0) + 1
        print(f"Credits to add: {len(additions)}; songs without cached details: {missing}")
        print("By role:", dict(sorted(roles.items(), key=lambda x: -x[1])[:15]))
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
