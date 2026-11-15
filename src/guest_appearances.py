"""Lil Wayne's guest appearances as songs (no releases), from guest_survey.py.

User decisions (Nov 13, 2026): guest appearances are songs only -- the
recording of another artist's song (recordings.artist_id = main artist,
recordings.appearance = 'guest') with credits for every credited artist
(Lil Wayne as featured_artist, or main_artist on "X & Lil Wayne"); all
surveyed songs at once.  Skipped: songs already in the catalogue, group songs
(Hot Boys, Sqad Up, Young Money ...), songs whose MusicBrainz ID is already
catalogued.
Status: a song known from MusicBrainz (it is on a release there) or with an
HQ project is 'released' (availability_override, date = first release date);
a song known only from Genius / HQ without a project keeps the computed
status ('unreleased').  Provenance: MusicBrainz recording pages, Genius song
page, HQ list (recording_sources); extra MusicBrainz IDs of the same song in
recording_musicbrainz_ids.

usage:
    python guest_appearances.py exports/guest_survey_<date>.json           # dry run
    python guest_appearances.py exports/guest_survey_<date>.json --apply   # one transaction
    python guest_appearances.py --promote [--apply]   # guest songs now on a release -> Wayne's songs
"""

import argparse
import json
import re
import sqlite3
from pathlib import Path

from guest_survey import artist_key, guest_song_key
from import_release import DATABASE_PATH, get_or_create_artist, get_or_create_source
from library_packages import split_artists
from unreleased_songs import genius_date


WAYNE = "Lil Wayne"
WAYNE_NAME = re.compile(r"^\s*lil'?\s*wa[yn]{2}e\s*$", re.IGNORECASE)
FEAT = re.compile(r"\b(?:feat\.?|ft\.?|featuring|with)\b", re.IGNORECASE)
HQ_NOTE = "LilWayneHQ 'Every Song' list (user's CSV export, Nov 13, 2026)"


def clean_name(name):
    return re.sub(r"[‐‑‒–]", "-", str(name or "")).strip()


def musicbrainz_credits(parts):
    """[{'artist', 'role'}] from MusicBrainz credit parts [[name, joinphrase], ...]:
    the first artist is main; after 'feat.' / 'with' artists are featured."""
    credits, featured = [], False
    for index, (name, joinphrase) in enumerate(parts):
        role = "main_artist" if index == 0 or not featured else "featured_artist"
        credits.append({"artist": clean_name(name), "role": role})
        featured = featured or bool(FEAT.search(joinphrase or ""))
    return credits


def hq_credits(text):
    """'Juvenile featuring Lil Wayne, Turk, and B.G.' -> main Juvenile, featured Lil Wayne, Turk, B.G."""
    parts = re.split(r"\s+(?:featuring|feat\.?|ft\.?|with)\s+", text, maxsplit=1, flags=re.IGNORECASE)
    main, featured = parts[0], parts[1] if len(parts) > 1 else ""

    def names(part):
        return [clean_name(n) for n in split_artists(re.sub(r",?\s+and\s+", ", ", part))]

    return ([{"artist": n, "role": "main_artist"} for n in names(main)]
            + [{"artist": n, "role": "featured_artist"} for n in names(featured)])


def song_credits(song):
    if song["musicbrainz"]:
        credits = musicbrainz_credits(song["musicbrainz"][0]["credit_parts"])
    elif song.get("hq_credit"):
        credits = hq_credits(song["hq_credit"])
    else:
        credits = [{"artist": clean_name(song["main_artist"]), "role": "main_artist"}]
    for credit in credits:
        if WAYNE_NAME.match(credit["artist"]):
            credit["artist"] = WAYNE
    if not any(c["artist"] == WAYNE for c in credits):
        credits.append({"artist": WAYNE, "role": "featured_artist"})
    return credits


def existing_guests(connection):
    """{(artist key, song key)} of catalogued guest songs."""
    return {(artist_key(name), guest_song_key(title)) for title, name in connection.execute("""
        SELECT r.title, a.name FROM recordings r JOIN artists a ON a.id = r.artist_id
        WHERE r.appearance = 'guest'
    """)}


def known_musicbrainz_ids(connection):
    return {row[0] for row in connection.execute("""
        SELECT musicbrainz_recording_id FROM recordings WHERE musicbrainz_recording_id IS NOT NULL
        UNION SELECT musicbrainz_recording_id FROM versions WHERE musicbrainz_recording_id IS NOT NULL
        UNION SELECT musicbrainz_recording_id FROM recording_musicbrainz_ids
    """)}


EXCLUDED_PATH = Path(__file__).resolve().parent.parent / "data" / "excluded_guest_songs.json"


def excluded_songs(path=EXCLUDED_PATH):
    """{(artist key, song key)} of songs that are not Lil Wayne guest appearances (owner,
    Nov 15, 2026: he is only a writer, or it is a cover / remix / re-upload)."""
    if not Path(path).exists():
        return set()
    return {(artist_key(s["artist"]), guest_song_key(s["title"]))
            for s in json.loads(Path(path).read_text(encoding="utf-8"))["songs"]}


def plan(survey, connection):
    """(actions, skipped): one action per guest song to add."""
    guests, known = existing_guests(connection), known_musicbrainz_ids(connection)
    excluded = excluded_songs()
    actions, skipped = [], {}

    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    for song in survey["songs"]:
        if song.get("in_catalogue"):
            skip("already in the catalogue")
            continue
        if song.get("group_song"):
            skip("group song")
            continue
        if (artist_key(song["main_artist"]), guest_song_key(song["title"])) in excluded:
            skip("excluded: not a Lil Wayne performance")
            continue
        if (artist_key(song["main_artist"]), guest_song_key(song["title"])) in guests:
            skip("guest song already added")
            continue
        mb_ids = [m["id"] for m in song["musicbrainz"]]
        if any(mb_id in known for mb_id in mb_ids):
            skip("MusicBrainz ID already catalogued")
            continue
        dates = sorted(d for d in [m.get("first_release_date") for m in song["musicbrainz"]] if d)
        genius = song["genius"][0] if song["genius"] else {}
        released = bool(song["musicbrainz"]) or bool(song.get("hq_projects"))
        actions.append({
            "title": song["title"],
            "main_artist": clean_name(song["main_artist"]),
            "credits": song_credits(song),
            "musicbrainz_ids": mb_ids,
            "genius": song["genius"],
            "hq": song.get("hq", False),
            "hq_projects": song.get("hq_projects", []),
            "date": dates[0] if dates else genius_date(genius.get("release_date")),
            "released": released,
        })
    return actions, skipped


def link_source(connection, recording_id, source, note):
    source_id = get_or_create_source(connection, source)
    if not connection.execute("SELECT 1 FROM recording_sources WHERE source_id = ? AND recording_id = ?",
                              (source_id, recording_id)).fetchone():
        connection.execute("INSERT INTO recording_sources (source_id, recording_id, notes) VALUES (?, ?, ?)",
                           (source_id, recording_id, note))


def apply(connection, actions):
    """Insert the guest songs; caller commits.  Returns the number added."""
    for action in actions:
        main_id = get_or_create_artist(connection, action["main_artist"])
        mb_ids = action["musicbrainz_ids"]
        genius = action["genius"][0] if action["genius"] else {}
        recording_id = connection.execute("""
            INSERT INTO recordings (title, artist_id, musicbrainz_recording_id, genius_song_id,
                                    first_known_date, availability_override, appearance)
            VALUES (?, ?, ?, ?, ?, ?, 'guest')
        """, (action["title"], main_id, mb_ids[0] if mb_ids else None, genius.get("id"), action["date"],
              "released" if action["released"] else None)).lastrowid
        for mb_id in mb_ids[1:]:
            connection.execute("""
                INSERT OR IGNORE INTO recording_musicbrainz_ids (recording_id, musicbrainz_recording_id, source, note)
                VALUES (?, ?, 'guest survey', 'Same guest song under another MusicBrainz ID')
            """, (recording_id, mb_id))
        for credit in action["credits"]:
            artist_id = get_or_create_artist(connection, credit["artist"])
            if not connection.execute("SELECT 1 FROM credits WHERE artist_id = ? AND recording_id = ? AND role = ?",
                                      (artist_id, recording_id, credit["role"])).fetchone():
                connection.execute("INSERT INTO credits (artist_id, recording_id, role) VALUES (?, ?, ?)",
                                   (artist_id, recording_id, credit["role"]))
        for mb_id in mb_ids:
            link_source(connection, recording_id, {"name": "MusicBrainz",
                                                   "url": f"https://musicbrainz.org/recording/{mb_id}"},
                        "Guest appearance (MusicBrainz recording)")
        for song in action["genius"]:
            link_source(connection, recording_id, {"name": "Genius", "url": song["url"]}, "Genius song page")
        if action["hq"]:
            projects = ", ".join(action["hq_projects"]) or "no project"
            link_source(connection, recording_id, {"name": "LilWayneHQ", "url": "https://www.lilwaynehq.com/songs/"},
                        f"{HQ_NOTE}; project: {projects}")
    return len(actions)


def promote_released_guests(connection):
    """A guest song that is now on a release of Lil Wayne's own discography
    (e.g. ColleGrove, imported as a collaboration album) becomes his song:
    appearance cleared, artist Lil Wayne (the other artist stays in the
    credits), status computed from the release again.  Caller commits.
    Returns the recording IDs."""
    wayne = get_or_create_artist(connection, WAYNE)
    # Only official releases: a guest song copied onto a bootleg stays a guest song.
    ids = [row[0] for row in connection.execute("""
        SELECT DISTINCT r.id FROM recordings r
        JOIN versions v ON v.recording_id = r.id JOIN release_tracks rt ON rt.version_id = v.id
        JOIN releases x ON x.id = rt.release_id
        WHERE r.appearance = 'guest' AND x.discography = 'official'
    """)]
    artists, releases = set(), set()
    for recording_id in ids:
        main_id = connection.execute("SELECT artist_id FROM recordings WHERE id = ?", (recording_id,)).fetchone()[0]
        if main_id != wayne:
            artists.add(main_id)
            if not connection.execute("SELECT 1 FROM credits WHERE recording_id = ? AND artist_id = ?",
                                       (recording_id, main_id)).fetchone():
                connection.execute("INSERT INTO credits (artist_id, recording_id, role) VALUES (?, ?, 'main_artist')",
                                   (main_id, recording_id))
        connection.execute("""
            UPDATE recordings SET appearance = NULL, artist_id = ?, availability_override = NULL WHERE id = ?
        """, (wayne, recording_id))
        releases.update(row[0] for row in connection.execute(
            "SELECT DISTINCT rt.release_id FROM versions v JOIN release_tracks rt ON rt.version_id = v.id "
            "WHERE v.recording_id = ?", (recording_id,)))
    # Guest copies of other tracks of the same releases (MusicBrainz gave the
    # album track another Recording ID): same main artist + same title -> merge.
    from merge_recordings import merge
    tracks = {}
    for release_id in releases:
        for recording_id, title, track_title in connection.execute("""
            SELECT DISTINCT r.id, r.title, rt.track_title FROM release_tracks rt
            JOIN versions v ON v.id = rt.version_id JOIN recordings r ON r.id = v.recording_id
            WHERE rt.release_id = ? AND r.appearance IS NULL
        """, (release_id,)):
            for name in {title, track_title} - {None}:
                tracks.setdefault(guest_song_key(name), set()).add(recording_id)
    for artist_id in artists:
        for guest_id, title in connection.execute(
                "SELECT id, title FROM recordings WHERE appearance = 'guest' AND artist_id = ?", (artist_id,)).fetchall():
            targets = tracks.get(guest_song_key(title), set())
            if len(targets) == 1:
                target = next(iter(targets))
                merge(connection, target, guest_id)
                ids.append(guest_id)
    return ids


def main(argv=None):
    parser = argparse.ArgumentParser(description="Add Lil Wayne's guest appearances (songs only).")
    parser.add_argument("survey", nargs="?")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--promote", action="store_true",
                        help="only turn guest songs that are now on a release into Lil Wayne's songs")
    args = parser.parse_args(argv)
    connection = sqlite3.connect(DATABASE_PATH)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        if args.promote:
            with connection:
                ids = promote_released_guests(connection)
                if not args.apply:
                    connection.rollback()
            print(f"Guest songs now on a release: {len(ids)}" + ("" if args.apply else " (dry run)"))
            return ids
        survey = json.loads(Path(args.survey).read_text(encoding="utf-8"))
        actions, skipped = plan(survey, connection)
        released = sum(1 for a in actions if a["released"])
        print(f"Guest songs to add: {len(actions)} (released {released}, status from sources unknown "
              f"{len(actions) - released})")
        print(f"Skipped: {skipped}")
        if not args.apply:
            print("Dry run; use --apply to change the database (back up first: python database.py --backup <label>).")
            return actions
        with connection:
            print(f"Added: {apply(connection, actions)}")
        return actions
    finally:
        connection.close()


if __name__ == "__main__":
    main()
