"""Stage name = real name pairs ("Bangladesh" = "Shondrae Crawford") for merge_artists.py.

Genius credits people by their stage name, Discogs often by a real-name page.
1. Candidates: on the same song and in the same role, a name only Discogs
   credits and a name only Genius credits, on at least MIN_SONGS songs and on
   most of the Discogs name's songs.
2. Confirmed only when the Discogs artist page of the Discogs name lists the
   Genius name as an alias or a name variation (or as the same person's
   real name) -- nothing is guessed.  Discogs token: DISCOGS_TOKEN.
Writes data/artist_aliases.json {"pairs": [[stage name, other name, evidence], ...]};
pairs added by hand (typos such as "Curreny") are kept.

usage:
    python artist_aliases.py            # candidates and the Discogs check, no file written
    python artist_aliases.py --apply    # write data/artist_aliases.json
"""

import argparse
import json
import sqlite3
from pathlib import Path

import discogs_source
from import_release import DATABASE_PATH
from merge_artists import ALIASES_PATH, name_key

MIN_SONGS = 2


def candidates(connection):
    """[(discogs name, genius name, shared songs, songs of the discogs name)]"""
    by_song = {}
    for recording, role, name, source in connection.execute("""
            SELECT COALESCE(c.recording_id, v.recording_id), c.role, a.name, c.source
            FROM credits c JOIN artists a ON a.id = c.artist_id LEFT JOIN versions v ON v.id = c.version_id
            WHERE c.source IN ('Genius', 'Discogs')"""):
        by_song.setdefault((recording, role), {"Genius": set(), "Discogs": set()})[source].add(name)
    together, songs_of = {}, {}
    for (recording, _role), names in by_song.items():
        only_discogs = names["Discogs"] - names["Genius"]
        only_genius = names["Genius"] - names["Discogs"]
        for discogs_name in only_discogs:
            songs_of.setdefault(discogs_name, set()).add(recording)
            for genius_name in only_genius:
                together.setdefault((discogs_name, genius_name), set()).add(recording)
    result = []
    for (discogs_name, genius_name), songs in together.items():
        if len(songs) >= MIN_SONGS and len(songs) * 2 > len(songs_of[discogs_name]):
            result.append((discogs_name, genius_name, len(songs), len(songs_of[discogs_name])))
    return sorted(result, key=lambda c: -c[2])


def discogs_names(name):
    """Names the Discogs artist page of `name` knows: its name, real name, aliases,
    name variations, or None when there is no exact artist of that name."""
    found = discogs_source.api_get("/database/search", {"q": name, "type": "artist", "per_page": 10}) or {}
    for hit in found.get("results", []):
        if name_key(hit.get("title", "").split(" (")[0]) == name_key(name):
            artist = discogs_source.api_get(f"/artists/{hit['id']}") or {}
            names = {artist.get("name", ""), artist.get("realname") or ""}
            names |= {a.get("name", "") for a in artist.get("aliases") or []}
            names |= set(artist.get("namevariations") or [])
            return {name_key(n.split(" (")[0]) for n in names if n}
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stage name = real name pairs, confirmed on Discogs.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
    try:
        found = candidates(connection)
    finally:
        connection.close()
    print(f"{len(found)} candidates (same song and role, Discogs name vs Genius name)")
    confirmed = []
    for discogs_name, genius_name, shared, total in found:
        known = discogs_names(discogs_name)
        ok = known is not None and name_key(genius_name) in known
        print(f"  {'YES' if ok else ' - '} {discogs_name} = {genius_name}  ({shared} of {total} songs)", flush=True)
        if ok:
            confirmed.append([genius_name, discogs_name, f"Discogs aliases of '{discogs_name}'; {shared} shared songs"])
    data = json.loads(ALIASES_PATH.read_text(encoding="utf-8")) if ALIASES_PATH.exists() else {"pairs": []}
    manual = [p for p in data["pairs"] if not str(p[2] if len(p) > 2 else "").startswith("Discogs")]
    print(f"{len(confirmed)} confirmed on Discogs; {len(manual)} pairs added by hand kept")
    if args.apply:
        ALIASES_PATH.write_text(json.dumps({"about": "Stage name = other name pairs for merge_artists.py --aliases",
                                            "pairs": manual + confirmed}, ensure_ascii=False, indent=1) + "\n",
                                encoding="utf-8")
        print(f"Saved {ALIASES_PATH.name}")


if __name__ == "__main__":
    main()
