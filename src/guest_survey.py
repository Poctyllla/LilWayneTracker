"""Survey of Lil Wayne's guest appearances from MusicBrainz, Genius and LilWayneHQ.

Read-only (never writes SQLite).  User decision (Nov 13, 2026): guest
appearances are based on MusicBrainz AND Genius, cross-checked with the HQ
"Every Song" list.
  MusicBrainz  recordings whose artist credit includes Lil Wayne but does
               not start with him (browse by artist, 100 per request, 1 s delay)
  Genius       songs of artist 4 whose primary artist is someone else
               (exports/genius_songs_4_<date>.json)
  LilWayneHQ   CSV rows whose credit does not start with Lil Wayne
Songs are matched by (main artist, song key).  Out-of-scope variants
(clean, instrumental, S&C) are skipped.

usage:
    python guest_survey.py [genius_export.json] [hq_songs.csv]
writes exports/guest_survey_<date>.json (MusicBrainz data cached in
exports/musicbrainz_guest_recordings_<date>.json)
"""

import json
import re
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

from import_release import DATABASE_PATH
from musicbrainz_catalog import BASE_URL, api_request
from song_crosscheck import (
    DEFAULT_GENIUS, DEFAULT_HQ, LEAD_CREDIT, CatalogueIndex, catalogue_rows, load_hq, song_key,
)
from titles import normalized_title
from version_policy import excluded_track_reason


PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPORTS_FOLDER = PROJECT_ROOT / "exports"
LIL_WAYNE_MBID = "ac9a487a-d9d2-4f27-bb23-0f4686488345"
LIL_WAYNE_GENIUS_ID = 4
PAGE = 100


ARTIST_ALIASES = {"baby": "birdman", "bg": "bg", "lilwayne": "lilwayne", "youngmoneyentertainment": "youngmoney"}
# Groups Lil Wayne belongs to: their songs are group songs, not guest spots.
GROUPS = {"hotboys", "sqadup", "youngmoney", "richgang", "squadup"}


def artist_key(name):
    """Comparable main-artist name: 'Jay‐Z' = 'JAY-Z', 'The Game' = 'Game', 'Baby' = 'Birdman'."""
    key = re.sub(r"^the ", "", normalized_title(name)).replace(" ", "")
    return ARTIST_ALIASES.get(key, key)


def guest_song_key(title):
    """song_key with '#' as a censor star ('Rich As F#ck' = 'Rich As Fuck')."""
    return song_key(str(title or "").replace("#", "*"))


def fetch_musicbrainz(attempts=3):
    """All recordings credited to Lil Wayne (browse), as plain dicts."""
    recordings, offset, total = [], 0, None
    while total is None or offset < total:
        data = None
        for attempt in range(attempts):
            time.sleep(1 + 2 * attempt)
            data = api_request(f"{BASE_URL}recording?artist={LIL_WAYNE_MBID}&inc=artist-credits"
                               f"&limit={PAGE}&offset={offset}&fmt=json")
            if data:
                break
        if not data:
            raise RuntimeError(f"MusicBrainz page at offset {offset} could not be retrieved.")
        total = data.get("recording-count", 0)
        for recording in data.get("recordings", []):
            credit = recording.get("artist-credit", [])
            recordings.append({
                "id": recording["id"],
                "title": recording.get("title"),
                "length": recording.get("length"),
                "first_release_date": recording.get("first-release-date"),
                "video": recording.get("video", False),
                "credit": [{"name": c.get("name"), "artist_id": c.get("artist", {}).get("id"),
                            "joinphrase": c.get("joinphrase", "")} for c in credit],
            })
        offset += PAGE
        if offset % 1000 == 0:
            print(f"  ... {offset}/{total}")
    return recordings


def musicbrainz_guests(recordings):
    """Recordings where Lil Wayne is credited but not first."""
    guests = []
    for recording in recordings:
        credit = recording["credit"]
        if not credit or credit[0]["artist_id"] == LIL_WAYNE_MBID:
            continue
        text = "".join(c["name"] + c["joinphrase"] for c in credit)
        role = "featured" if re.search(r"\b(feat|ft)\.?|featuring|with\b", text, re.IGNORECASE) else "main"
        guests.append({**recording, "main_artist": credit[0]["name"], "credit_text": text, "role": role})
    return guests


def genius_guests(path):
    songs = json.loads(Path(path).read_text(encoding="utf-8"))
    return [s for s in songs if s.get("primary_artist_id") != LIL_WAYNE_GENIUS_ID]


def hq_guests(rows):
    guests = []
    for row in rows:
        if LEAD_CREDIT.match(row["Artists"]):
            continue
        main = re.split(r"\s+(?:featuring|feat\.?|ft\.?|and|&|with|x)\s+|,", row["Artists"], maxsplit=1,
                        flags=re.IGNORECASE)[0].strip()
        guests.append({**row, "main_artist": main})
    return guests


def merge_sources(mb, genius, hq):
    """{(artist key, song key): entry} with the evidence of each source."""
    songs, skipped = {}, 0

    def entry(artist, title):
        key = (artist_key(artist), guest_song_key(title))
        return songs.setdefault(key, {"main_artist": artist, "title": title, "musicbrainz": [], "genius": [],
                                      "hq_projects": [], "hq": False})

    for recording in mb:
        if excluded_track_reason(recording["title"]) or recording.get("video"):
            skipped += 1
            continue
        item = entry(recording["main_artist"], recording["title"])
        item["musicbrainz"].append({"id": recording["id"], "length": recording["length"],
                                    "first_release_date": recording["first_release_date"],
                                    "credit": recording["credit_text"], "role": recording["role"],
                                    "credit_parts": [[c["name"], c["joinphrase"]] for c in recording["credit"]]})
    for song in genius:
        if excluded_track_reason(song["title"]):
            skipped += 1
            continue
        item = entry(song["primary_artist"] or "", song["title"])
        item["genius"].append({"id": song["genius_id"], "url": song["url"], "release_date": song.get("release_date")})
    for row in hq:
        if excluded_track_reason(row["Song Title"]):
            skipped += 1
            continue
        item = entry(row["main_artist"], row["Song Title"])
        item["hq"] = True
        if row["Project"].strip() not in ("–", "-", ""):
            item["hq_projects"].append(row["Project"].strip())
        item["hq_credit"] = row["Artists"]
    for item in songs.values():
        item["sources"] = "+".join(name for name, present in (("musicbrainz", item["musicbrainz"]),
                                                              ("genius", item["genius"]), ("hq", item["hq"]))
                                   if present)
        item["group_song"] = artist_key(item["main_artist"]) in GROUPS
    return songs, skipped


def mark_catalogued(songs, index):
    """in_catalogue: the song is already catalogued (often Wayne's own song on a
    DJ-hosted mixtape that MusicBrainz credits to the DJ, or a group song)."""
    for item in songs.values():
        found = index.find(item["title"].replace("#", "*"))
        item["in_catalogue"] = sorted({recording_id for recording_id, _ in found})


def main():
    genius_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_GENIUS
    hq_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_HQ
    EXPORTS_FOLDER.mkdir(exist_ok=True)
    cache = EXPORTS_FOLDER / f"musicbrainz_guest_recordings_{date.today():%Y%m%d}.json"
    if cache.exists():
        recordings = json.loads(cache.read_text(encoding="utf-8"))
    else:
        recordings = fetch_musicbrainz()
        cache.write_text(json.dumps(recordings, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    mb = musicbrainz_guests(recordings)
    songs, skipped = merge_sources(mb, genius_guests(genius_path), hq_guests(load_hq(hq_path)))
    connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
    try:
        mark_catalogued(songs, CatalogueIndex(catalogue_rows(connection)))
    finally:
        connection.close()
    counts = {}
    for item in songs.values():
        group = "catalogued" if item["in_catalogue"] else "group song" if item["group_song"] else "guest"
        counts.setdefault(group, {})
        counts[group][item["sources"]] = counts[group].get(item["sources"], 0) + 1
    out = EXPORTS_FOLDER / f"guest_survey_{date.today():%Y%m%d}.json"
    ordered = sorted(songs.values(), key=lambda s: (s["main_artist"].casefold(), s["title"].casefold()))
    out.write_text(json.dumps({"counts": counts, "songs": ordered}, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    print(f"MusicBrainz recordings credited to Lil Wayne: {len(recordings)}; as guest: {len(mb)}")
    print(f"Guest songs (merged): {len(songs)}; skipped out-of-scope/video: {skipped}")
    for group, by_sources in counts.items():
        print(f" {group}: {sum(by_sources.values())}")
        for sources, count in sorted(by_sources.items(), key=lambda x: -x[1]):
            print(f"   {sources:<24} {count}")
    print(f"Saved: {out.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
