"""Songs and versions known from Genius / LilWayneHQ, put into the catalogue.

Input: the cross-check (song_crosscheck.py; only songs where Lil Wayne is the
lead artist -- guest appearances are a later step).  Per status:
  missing       new song: a recording without any release, so its status
                is 'unreleased' (recording_availability).  "X (Demo)" etc.
                becomes a typed version of song X (X is created when it is
                not catalogued).  A plain song gets no version yet: the
                first release / file that has it adds its main version.
  variant       a typed version (demo, snippet, original, live ...) of the
                catalogued song
  probable      other spelling -> recording_aliases
  in_catalogue  Genius song ID (recording or the matching typed version)
Every entry keeps its provenance: Genius song page and/or the HQ "Every
Song" list (recording_sources), HQ co-artists as credits.

usage:
    python unreleased_songs.py exports/song_crosscheck_<date>.json           # dry run
    python unreleased_songs.py exports/song_crosscheck_<date>.json --apply   # one transaction
"""

import argparse
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from duplicate_guard import is_generic_title
from import_release import DATABASE_PATH, get_or_create_artist, get_or_create_source
from library_packages import split_artists
from song_crosscheck import song_key
from titles import normalized_title
from version_types import split_version


HQ_SOURCE = {"name": "LilWayneHQ", "url": "https://www.lilwaynehq.com/songs/"}
HQ_NOTE = "LilWayneHQ 'Every Song' list (user's CSV export, Nov 13, 2026)"
NOT_A_SONG = re.compile(
    r"\b(interview|speech|apology|tweet|statement|tracklist|liner notes|is coming|"
    r"message to)\b|^\W*\w?\W*$",
    re.IGNORECASE,
)
HQ_CREDIT = re.compile(r"^\s*lil wayne\s*(?:(featuring|feat\.?|ft\.?|and|&|x|with)\s+(.+))?$", re.IGNORECASE)


def clean_title(title):
    return re.sub(r"\s*\*+\s*$", "", str(title or "")).strip()  # Genius "(Demo)*"


def hq_credits(artists_list):
    """[{'artist', 'role'}] from HQ credits like 'Lil Wayne featuring Gudda Gudda'."""
    credits = []
    for artists in artists_list:
        match = HQ_CREDIT.match(artists or "")
        if not match or not match.group(2):
            continue
        role = "featured_artist" if match.group(1).casefold().startswith(("feat", "ft", "with")) else "main_artist"
        for name in split_artists(match.group(2)):
            if {"artist": name, "role": role} not in credits:
                credits.append({"artist": name, "role": role})
    return credits


def genius_date(text):
    """'March 3, 2008' -> '2008-03-03'; 'March 2008' -> '2008-03'; '2008' -> '2008'."""
    for fmt, out in (("%B %d, %Y", "%Y-%m-%d"), ("%B %Y", "%Y-%m"), ("%Y", "%Y")):
        try:
            return datetime.strptime(str(text or "").strip(), fmt).strftime(out)
        except ValueError:
            continue
    return None


def skip_reason(song):
    title = clean_title(song["title"])
    if NOT_A_SONG.search(title):
        return "not a song"
    if is_generic_title(title) and len(normalized_title(title).split()) <= 1:
        return "generic title without context"
    return None


def existing_versions(connection, recording_id):
    return {(vtype, (vname or "").casefold()): vid for vid, vtype, vname in connection.execute(
        "SELECT id, version_type, version_name FROM versions WHERE recording_id = ?", (recording_id,))}


def plan(crosscheck, connection):
    """List of actions (dicts); nothing is changed."""
    actions, skipped = [], []
    new_songs = {}  # song_key -> planned new song title (created in this run)
    # Songs already in SQLite (also protects a second run with an old cross-check).
    in_db = {}
    has_appearance = any(row[1] == "appearance" for row in connection.execute("PRAGMA table_info(recordings)"))
    for recording_id, title in connection.execute(
            "SELECT id, title FROM recordings" + (" WHERE appearance IS NULL" if has_appearance else "")):
        in_db.setdefault(song_key(title), []).append(recording_id)
    lead = [s for s in crosscheck["songs"] if s.get("role") == "lead"]
    # Plain songs first, so "X (Demo)" can join a song X that is new in this run.
    lead.sort(key=lambda s: split_version(clean_title(s["title"]))[1] is not None)
    for song in lead:
        title = clean_title(song["title"])
        base, version_type, version_name = split_version(title)
        matches = [m["recording_id"] for m in song.get("catalogue", [])]
        released = [m["recording_id"] for m in song.get("catalogue", []) if m["availability"] == "released"]
        if len(matches) > 1 and len(released) == 1:
            matches = released  # e.g. Lollipop: the album song, not a bootleg copy
        status = song["status"]
        common = {"title": title, "genius": song.get("genius", []), "hq": bool(song.get("hq_artists")),
                  "hq_projects": song.get("hq_projects", []), "credits": hq_credits(song.get("hq_artists", []))}
        if status == "in_catalogue":
            if len(matches) == 1 and song.get("genius"):
                actions.append({**common, "action": "genius_id", "recording_id": matches[0],
                                "version_type": version_type, "version_name": version_name})
            continue
        if status == "probable":
            if len(matches) == 1:
                actions.append({**common, "action": "alias", "recording_id": matches[0]})
            else:
                skipped.append((title, f"probable, {len(matches)} matches"))
            continue
        reason = skip_reason(song)
        if reason:
            skipped.append((title, reason))
            continue
        if status == "variant" and version_type:
            if len(matches) != 1:
                skipped.append((title, f"variant, {len(matches)} base songs"))
                continue
            if (version_type, version_name.casefold()) in existing_versions(connection, matches[0]):
                skipped.append((title, "version exists"))
                continue
            actions.append({**common, "action": "version", "recording_id": matches[0],
                            "version_type": version_type, "version_name": version_name})
            continue
        # missing (or a variant whose marker is not a version type, e.g. a freestyle)
        if version_type:
            key = song_key(base)
            if len(in_db.get(key, [])) == 1:
                recording_id = in_db[key][0]
                if (version_type, version_name.casefold()) in existing_versions(connection, recording_id):
                    skipped.append((title, "version exists"))
                else:
                    actions.append({**common, "action": "version", "recording_id": recording_id,
                                    "version_type": version_type, "version_name": version_name})
                continue
            if in_db.get(key):
                skipped.append((title, f"{len(in_db[key])} songs '{base}' in the catalogue"))
                continue
            actions.append({**common, "action": "version", "recording_id": None,
                            "new_song": key not in new_songs, "base_title": new_songs.get(key, base),
                            "version_type": version_type, "version_name": version_name})
            new_songs.setdefault(key, base)
        else:
            key = song_key(title)
            if key in new_songs or key in in_db:
                skipped.append((title, "already catalogued" if key in in_db else "duplicate in this run"))
                continue
            new_songs[key] = title
            actions.append({**common, "action": "song"})
    return actions, skipped


def link_sources(connection, action, recording_id=None, version_id=None):
    target = ("version_id", version_id) if version_id else ("recording_id", recording_id)
    links = [({"name": "Genius", "url": g["url"]}, "Genius song page") for g in action["genius"]]
    if action["hq"]:
        projects = ", ".join(action["hq_projects"]) or "no project"
        links.append((HQ_SOURCE, f"{HQ_NOTE}; project: {projects}"))
    for source, note in links:
        source_id = get_or_create_source(connection, source)
        if not connection.execute(f"SELECT 1 FROM recording_sources WHERE source_id = ? AND {target[0]} = ?",
                                  (source_id, target[1])).fetchone():
            connection.execute(f"INSERT INTO recording_sources (source_id, {target[0]}, notes) VALUES (?, ?, ?)",
                               (source_id, target[1], note))


def add_credits(connection, action, recording_id=None, version_id=None):
    column, value = ("version_id", version_id) if version_id else ("recording_id", recording_id)
    for credit in action["credits"]:
        artist_id = get_or_create_artist(connection, credit["artist"])
        if not connection.execute(f"SELECT 1 FROM credits WHERE artist_id = ? AND {column} = ? AND role = ?",
                                  (artist_id, value, credit["role"])).fetchone():
            connection.execute(f"INSERT INTO credits (artist_id, {column}, role) VALUES (?, ?, ?)",
                               (artist_id, value, credit["role"]))


def first_genius(action):
    return action["genius"][0] if action["genius"] else {}


def apply(connection, actions):
    """Apply the plan; caller commits.  Returns {action: count}."""
    wayne = get_or_create_artist(connection, "Lil Wayne")
    created = {}  # base title key -> recording id created in this run
    counts = {}
    for action in actions:
        kind = action["action"]
        genius = first_genius(action)
        if kind == "song":
            recording_id = connection.execute(
                "INSERT INTO recordings (title, artist_id, genius_song_id, recording_date) VALUES (?, ?, ?, ?)",
                (action["title"], wayne, genius.get("id"), genius_date(genius.get("release_date")))).lastrowid
            created[song_key(action["title"])] = recording_id
            link_sources(connection, action, recording_id=recording_id)
            add_credits(connection, action, recording_id=recording_id)
        elif kind == "version":
            recording_id = action["recording_id"] or created.get(song_key(action["base_title"]))
            if recording_id is None:
                recording_id = connection.execute(
                    "INSERT INTO recordings (title, artist_id) VALUES (?, ?)",
                    (action["base_title"], wayne)).lastrowid
                created[song_key(action["base_title"])] = recording_id
            if (action["version_type"], action["version_name"].casefold()) in existing_versions(
                    connection, recording_id):
                counts["version exists"] = counts.get("version exists", 0) + 1
                continue
            version_id = connection.execute("""
                INSERT INTO versions (recording_id, version_name, status, version_type,
                                      first_known_date, genius_song_id)
                VALUES (?, ?, 'unreleased', ?, ?, ?)
            """, (recording_id, action["version_name"], action["version_type"],
                  genius_date(genius.get("release_date")), genius.get("id"))).lastrowid
            link_sources(connection, action, version_id=version_id)
            add_credits(connection, action, version_id=version_id)
        elif kind == "alias":
            connection.execute("INSERT OR IGNORE INTO recording_aliases (recording_id, title, source, note) "
                               "VALUES (?, ?, ?, ?)",
                               (action["recording_id"], action["title"],
                                "LilWayneHQ" if action["hq"] else "Genius", "song_crosscheck: probable"))
            link_sources(connection, action, recording_id=action["recording_id"])
        elif kind == "genius_id":
            if action["version_type"]:
                version_id = existing_versions(connection, action["recording_id"]).get(
                    (action["version_type"], (action["version_name"] or "").casefold()))
                if version_id:
                    connection.execute("UPDATE versions SET genius_song_id = COALESCE(genius_song_id, ?) "
                                       "WHERE id = ?", (genius.get("id"), version_id))
                    link_sources(connection, action, version_id=version_id)
            else:
                connection.execute("UPDATE recordings SET genius_song_id = COALESCE(genius_song_id, ?) "
                                   "WHERE id = ?", (genius.get("id"), action["recording_id"]))
                link_sources(connection, action, recording_id=action["recording_id"])
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description="Unreleased songs and versions from Genius / LilWayneHQ.")
    parser.add_argument("crosscheck")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    crosscheck = json.loads(Path(args.crosscheck).read_text(encoding="utf-8"))
    connection = sqlite3.connect(DATABASE_PATH)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        actions, skipped = plan(crosscheck, connection)
        summary = {}
        for action in actions:
            label = action["action"] + (f":{action['version_type']}" if action["action"] == "version" else "")
            summary[label] = summary.get(label, 0) + 1
        print("Planned:", dict(sorted(summary.items())))
        print(f"New songs from versions only: {sum(1 for a in actions if a.get('new_song'))}")
        print(f"Skipped: {len(skipped)}")
        for title, reason in skipped:
            print(f"   SKIP {title}: {reason}")
        if not args.apply:
            print("Dry run; use --apply to change the database (back up first: python database.py --backup <label>).")
            return actions, skipped
        with connection:
            counts = apply(connection, actions)
        print("Applied:", counts)
        return actions, skipped
    finally:
        connection.close()


if __name__ == "__main__":
    main()
