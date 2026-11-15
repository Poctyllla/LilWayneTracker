"""Check guest-survey songs whose title matches a catalogued song.

guest_survey.py marks a guest song 'in_catalogue' when a catalogued song has
the same title -- often Lil Wayne's own song on a DJ-hosted mixtape that
MusicBrainz credits to the DJ, sometimes another artist's song that only
shares the title.  Evidence per song (read-only):
  same_id       a MusicBrainz ID of the guest song is catalogued (that recording)
  dj_host       the main artist is a DJ / mixtape host -> Lil Wayne's own song
  credited      the catalogued recording already credits the main artist
  duration      a MusicBrainz length is within 15 s of a catalogued duration
  far           all known lengths differ by more than 15 s -> another song
  genius_album  (only for a 'review' song) the album Genius names is a
                release of exactly one candidate -> same; the album is known
                and no candidate is on it -> different
Verdict: 'same' (same_id, dj_host + duration/no data, credited, duration),
'different' (far and none of the above), otherwise 'review'.

--apply (user decision Nov 13, 2026): 'same' -> the catalogued song gets the
guest song's MusicBrainz IDs (alternates), Genius ID, sources and the other
credited artists (not for DJ hosts); 'different' -> added as a guest song
(guest_appearances.py); 'review' -> only listed in the export.

usage:
    python guest_matches.py exports/guest_survey_<date>.json [--apply]
writes exports/guest_matches_<date>.json
"""

import argparse
import json
import re
import sqlite3
from datetime import date
from pathlib import Path

import guest_appearances
from duplicate_guard import SAME_SONG_MS
from genius_details import load_cache
from guest_survey import artist_key
from import_release import DATABASE_PATH, get_or_create_artist
from library_matching import release_key


PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPORTS_FOLDER = PROJECT_ROOT / "exports"
# Mixtape DJs / hosts that MusicBrainz names as main artist of Wayne's mixtapes.
DJ_HOST = re.compile(r"^(dj|drama|mick boogie|whiteowl|trigga|clinton sparks|bigga|ill will|keyz|hard hitta|"
                     r"skee|kay slay|green lantern|scoob doo|lazy k|nik bean|mr thanksgiving|sense)",
                     re.IGNORECASE)


def new_fact():
    return {"durations": set(), "mb_ids": set(), "artists": set(), "releases": set()}


def catalogue_facts(connection):
    """{recording_id: {'durations', 'mb_ids', 'artists', 'releases'}} for own songs."""
    facts = {}
    for recording_id, duration, release_title in connection.execute("""
        SELECT v.recording_id, rt.duration_ms, r.title FROM versions v
        JOIN release_tracks rt ON rt.version_id = v.id JOIN releases r ON r.id = rt.release_id
    """):
        fact = facts.setdefault(recording_id, new_fact())
        fact["durations"].add(duration)
        fact["releases"].add(release_key(release_title))
    for recording_id, mb_id in connection.execute("""
        SELECT id, musicbrainz_recording_id FROM recordings WHERE musicbrainz_recording_id IS NOT NULL
        UNION SELECT v.recording_id, v.musicbrainz_recording_id FROM versions v WHERE v.musicbrainz_recording_id IS NOT NULL
        UNION SELECT recording_id, musicbrainz_recording_id FROM recording_musicbrainz_ids
    """):
        facts.setdefault(recording_id, new_fact())["mb_ids"].add(mb_id)
    for recording_id, name in connection.execute("""
        SELECT COALESCE(c.recording_id, v.recording_id), a.name FROM credits c
        JOIN artists a ON a.id = c.artist_id LEFT JOIN versions v ON v.id = c.version_id
    """):
        if recording_id is not None:
            facts.setdefault(recording_id, new_fact())["artists"].add(artist_key(name))
    return facts


def genius_albums(song, details):
    """Release keys of the Genius albums of a guest song (genius_details.py cache)."""
    return {release_key(details[g["id"]]["album"]["name"]) for g in song["genius"]
            if details.get(g["id"], {}).get("album") and details[g["id"]]["album"].get("name")}


def judge(song, facts, details=None):
    """(verdict, recording_id or None, evidence list) for one in_catalogue song."""
    verdict = judge_by_lengths(song, facts)
    if verdict[0] != "review" or not details:
        return verdict
    albums = genius_albums(song, details)
    if not albums:
        return verdict
    on_album = [r for r in song["in_catalogue"] if facts.get(r, new_fact())["releases"] & albums]
    if len(on_album) == 1:
        return "same", on_album[0], ["genius_album"]
    if not on_album and all(facts.get(r, new_fact())["releases"] for r in song["in_catalogue"]):
        return "different", None, ["genius_album_elsewhere"]
    return verdict


def judge_by_lengths(song, facts):
    """(verdict, recording_id or None, evidence list) from IDs, credits and lengths."""
    candidates = song["in_catalogue"]
    mb_ids = {m["id"] for m in song["musicbrainz"]}
    lengths = [m["length"] for m in song["musicbrainz"] if m.get("length")]
    for recording_id in candidates:
        if mb_ids & facts.get(recording_id, {}).get("mb_ids", set()):
            return "same", recording_id, ["same_id"]
    dj_host = bool(DJ_HOST.match(song["main_artist"]))
    main = artist_key(song["main_artist"])
    scored = []
    for recording_id in candidates:
        fact = facts.get(recording_id, new_fact())
        known = [d for d in fact["durations"] if d]
        evidence = []
        if main in fact["artists"]:
            evidence.append("credited")
        if lengths and known:
            gap = min(abs(a - b) for a in lengths for b in known)
            evidence.append("duration" if gap <= SAME_SONG_MS else "far")
        if dj_host:
            evidence.append("dj_host")
        scored.append((recording_id, evidence))
    # Lengths that differ by more than 15 s outweigh a credit or a DJ host.
    same = [(r, e) for r, e in scored if "duration" in e or ("far" not in e and ("credited" in e or "dj_host" in e))]
    if len(same) == 1:
        return "same", same[0][0], same[0][1]
    if not same and scored and all("far" in e for _, e in scored):
        return "different", None, ["far"]
    return "review", None, [f"{r}:{'+'.join(e) or 'no data'}" for r, e in scored]


def attach_to_catalogued(connection, song, recording_id):
    """Evidence of a 'same' guest song goes to the catalogued recording; caller commits."""
    known = guest_appearances.known_musicbrainz_ids(connection)
    for mb in song["musicbrainz"]:
        if mb["id"] not in known:
            connection.execute("""
                INSERT INTO recording_musicbrainz_ids (recording_id, musicbrainz_recording_id, source, note)
                VALUES (?, ?, 'guest survey', 'Same song credited to another main artist in MusicBrainz')
            """, (recording_id, mb["id"]))
            known.add(mb["id"])
        guest_appearances.link_source(connection, recording_id, {
            "name": "MusicBrainz", "url": f"https://musicbrainz.org/recording/{mb['id']}"},
            f"MusicBrainz credit: {mb.get('credit')}")
    if song["genius"]:
        connection.execute("UPDATE recordings SET genius_song_id = COALESCE(genius_song_id, ?) WHERE id = ?",
                           (song["genius"][0]["id"], recording_id))
        for genius in song["genius"]:
            guest_appearances.link_source(connection, recording_id, {"name": "Genius", "url": genius["url"]},
                                          "Genius song page")
    if DJ_HOST.match(song["main_artist"]):
        return  # a mixtape host is not a performer of the track
    credited = {artist_id for (artist_id,) in connection.execute("""
        SELECT c.artist_id FROM credits c LEFT JOIN versions v ON v.id = c.version_id
        WHERE c.recording_id = ? OR v.recording_id = ?
    """, (recording_id, recording_id))}
    for credit in guest_appearances.song_credits(song):
        if credit["artist"] == guest_appearances.WAYNE:
            continue
        artist_id = get_or_create_artist(connection, credit["artist"])  # "T Pain" finds T-Pain
        if artist_id in credited:
            continue
        connection.execute("INSERT INTO credits (artist_id, recording_id, role) VALUES (?, ?, ?)",
                           (artist_id, recording_id, credit["role"]))
        credited.add(artist_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Judge guest songs whose title matches a catalogued song.")
    parser.add_argument("survey")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    survey = json.loads(Path(args.survey).read_text(encoding="utf-8"))
    connection = sqlite3.connect(DATABASE_PATH)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        facts = catalogue_facts(connection)
        details = load_cache()  # Genius albums (genius_details.py), if downloaded
        results, counts, same, different = [], {}, [], []
        for song in survey["songs"]:
            if not song.get("in_catalogue"):
                continue
            verdict, recording_id, evidence = judge(song, facts, details)
            counts[verdict] = counts.get(verdict, 0) + 1
            results.append({"title": song["title"], "main_artist": song["main_artist"], "sources": song["sources"],
                            "verdict": verdict, "recording_id": recording_id, "evidence": evidence,
                            "candidates": song["in_catalogue"]})
            if verdict == "same":
                same.append((song, recording_id))
            elif verdict == "different":
                different.append({**song, "in_catalogue": []})
        out = EXPORTS_FOLDER / f"guest_matches_{date.today():%Y%m%d}.json"
        out.write_text(json.dumps({"counts": counts, "songs": results}, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
        print(f"Title matches: {len(results)} {counts}")
        print(f"Saved: {out.relative_to(PROJECT_ROOT)}")
        actions, skipped = guest_appearances.plan({"songs": different}, connection)
        print(f"Different songs to add as guest songs: {len(actions)}; skipped {skipped}")
        if not args.apply:
            print("Dry run; use --apply to change the database (make a backup first).")
            return results
        with connection:
            for song, recording_id in same:
                attach_to_catalogued(connection, song, recording_id)
            added = guest_appearances.apply(connection, actions)
        print(f"Applied: evidence attached to {len(same)} catalogued songs, {added} guest songs added.")
        return results
    finally:
        connection.close()


if __name__ == "__main__":
    main()
