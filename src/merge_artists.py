"""Join artists that are one person under several spellings.

Names that are equal when case and punctuation are ignored ("T-Pain", "T Pain",
"t-pain"; "B.G.", "BG"; "Lil' Wayne", "Lil.Wayne") are one person; with
--aliases also the pairs listed in data/artist_aliases.json (a stage name and a
real name, e.g. "Shondrae Crawford" = "Bangladesh", found by artist_aliases.py).
The name used most often in the catalogue stays; credits, recordings and
releases move to it, a credit keeps the spelling it was printed with in
`credited_as`, identical credits are joined, and a People photo
(data/artist_images.json) moves along.

usage:
    python merge_artists.py [--aliases] [--apply]     # dry run without --apply
"""

import argparse
import json
import re
import sqlite3
from pathlib import Path

from import_release import ARTIST_ALIASES_PATH, DATABASE_PATH, artist_key

PROJECT_ROOT = Path(__file__).resolve().parent.parent
IMAGES_PATH = PROJECT_ROOT / "data" / "artist_images.json"
ALIASES_PATH = ARTIST_ALIASES_PATH


name_key = artist_key  # the same key as get_or_create_artist (import_release.py)


def untidy(name):
    """Spellings that should not be the shown name when a tidier one exists: ALL CAPS
    words ("MIKE DEAN"), all lower case ("t-pain"), a second script
    ("Miri Ben-Ari - מירי בן-ארי"), stray endings ("Dre)")."""
    letters = [ch for ch in name if ch.isalpha()]
    shouting = len(letters) >= 5 and all(ch.isupper() for ch in letters) and " " in name.strip()
    shouting = shouting or (len(letters) >= 6 and all(ch.isupper() for ch in letters))
    shouting = shouting or (len(letters) >= 3 and all(ch.islower() for ch in letters))  # "t-pain", "nicki minaj"
    foreign = any(ord(ch) > 0x24F for ch in name if ch.isalpha())
    stray = name.rstrip()[-1:] in (")", ".", "@") and "(" not in name and not re.search(r"\b[A-Z]\.$", name)
    return shouting + foreign + stray


def usage(connection):
    """{artist_id: number of references}"""
    counts = {}
    for table in ("credits", "recordings", "releases"):
        for artist_id, n in connection.execute(f"SELECT artist_id, count(*) FROM {table} GROUP BY artist_id"):
            counts[artist_id] = counts.get(artist_id, 0) + n
    return counts


def plan(connection, aliases=None):
    """[(keep id, keep name, [(drop id, drop name), ...])] -- one entry per person."""
    names = dict(connection.execute("SELECT id, name FROM artists"))
    counts = usage(connection)
    groups = {}
    for artist_id, name in names.items():
        if name_key(name):
            groups.setdefault(name_key(name), set()).add(artist_id)
    # stage name = real name pairs join their groups
    by_key = {key: ids for key, ids in groups.items()}
    for first, second, *_evidence in aliases or []:
        a, b = by_key.get(name_key(first)), by_key.get(name_key(second))
        if a and b and a is not b:
            a |= b
            for key, ids in list(by_key.items()):
                if ids is b:
                    by_key[key] = a
    merged, seen = [], set()
    for ids in by_key.values():
        if len(ids) < 2 or id(ids) in seen:
            continue
        seen.add(id(ids))
        alias_names = {name_key(pair[0]) for pair in aliases or []}
        # the stage name of an alias pair stays, else the most used spelling (then the lowest id)
        keep = min(ids, key=lambda i: (name_key(names[i]) not in alias_names, untidy(names[i]), -counts.get(i, 0), i))
        merged.append((keep, names[keep], sorted((i, names[i]) for i in ids if i != keep)))
    return sorted(merged, key=lambda m: m[1].casefold())


def apply(connection, merges):
    """Move every reference to the kept artist; caller commits."""
    for keep, _name, drops in merges:
        for drop, drop_name in drops:
            connection.execute("UPDATE credits SET credited_as = ? WHERE artist_id = ? AND credited_as IS NULL",
                               (drop_name, drop))
            connection.execute("UPDATE credits SET artist_id = ? WHERE artist_id = ?", (keep, drop))
            connection.execute("UPDATE recordings SET artist_id = ? WHERE artist_id = ?", (keep, drop))
            connection.execute("UPDATE releases SET artist_id = ? WHERE artist_id = ?", (keep, drop))
            connection.execute("DELETE FROM artists WHERE id = ?", (drop,))
        # identical credits (same song / version, role and source) become one
        connection.execute("""
            DELETE FROM credits WHERE artist_id = ? AND id NOT IN (
                SELECT min(id) FROM credits WHERE artist_id = ?
                GROUP BY recording_id, version_id, role, COALESCE(source, ''))""", (keep, keep))


def move_images(merges):
    if not IMAGES_PATH.exists():
        return 0
    images = json.loads(IMAGES_PATH.read_text(encoding="utf-8"))
    moved = 0
    for keep, _name, drops in merges:
        for drop, _drop_name in drops:
            if str(drop) in images:
                if str(keep) not in images:
                    images[str(keep)] = images[str(drop)]
                    moved += 1
                del images[str(drop)]
    IMAGES_PATH.write_text(json.dumps(images, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return moved


def main(argv=None):
    parser = argparse.ArgumentParser(description="Join artists that are one person under several spellings.")
    parser.add_argument("--aliases", action="store_true", help="also join the pairs of data/artist_aliases.json")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    aliases = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))["pairs"] if args.aliases and ALIASES_PATH.exists() else []
    connection = sqlite3.connect(DATABASE_PATH)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        merges = plan(connection, aliases)
        for keep, name, drops in merges:
            print(f"{name}  <-  {', '.join(n for _i, n in drops)}")
        print(f"{len(merges)} people, {sum(len(d) for _k, _n, d in merges)} duplicate artists")
        if not args.apply:
            print("Dry run; use --apply to join them (make a backup first).")
            return
        with connection:
            apply(connection, merges)
        print(f"Joined; {move_images(merges)} photos moved. Foreign keys: {connection.execute('PRAGMA foreign_key_check').fetchall()[:3]}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
