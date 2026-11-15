"""Apply the project owner's manual song review decisions.

Decisions live in data/song_review_decisions.json (what was decided, when and
why).  Actions:
  merge         recording `drop` is the same song as `keep` (merge_recordings.merge)
  alias         another spelling of a recording (recording_aliases)
  make_version  the catalogued recording becomes a typed version of song `of`
  add_version   a typed version known only from sources (HQ / Genius) of song `of`
  keep_separate documented only: the recordings are different songs
  guest_same    (web review page) a guest-survey song is catalogued song
                `recording`: its IDs, sources and credits are attached
  guest_different  (web review page) add the guest-survey song as a guest song
  skip          (web review page) documented only
  split_off     (web review page) the main-version tracks of `recording` on
                `releases` are another song of the same title -> new recording
  detach_version  a version linked to the wrong song leaves it: to the recording
                `to` (it becomes its main version) or to a new song `title`
Already applied decisions are skipped, so re-running is safe.

usage:
    python song_review.py            # dry run
    python song_review.py --apply    # one transaction (make a backup first)
"""

import argparse
import json
import sqlite3
from pathlib import Path

import guest_appearances
from guest_matches import attach_to_catalogued
from import_release import DATABASE_PATH
from merge_recordings import merge
from split_versions import apply_move
from unreleased_songs import apply as apply_source_actions, clean_title, existing_versions


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DECISIONS_PATH = PROJECT_ROOT / "data" / "song_review_decisions.json"
EXPORTS_FOLDER = PROJECT_ROOT / "exports"


def latest_crosscheck():
    files = sorted(EXPORTS_FOLDER.glob("song_crosscheck_*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8"))["songs"] if files else []


def exists(connection, recording_id):
    return connection.execute("SELECT 1 FROM recordings WHERE id = ?", (recording_id,)).fetchone() is not None


def plan(connection, decisions, crosscheck_songs=()):
    """[(decision, status)] with status 'apply', 'done' or an error text."""
    by_title = {clean_title(s["title"]): s for s in crosscheck_songs}
    result = []
    for decision in decisions:
        action = decision["action"]
        if action == "keep_separate":
            result.append((decision, "noted"))
        elif action == "merge":
            if not exists(connection, decision["drop"]):
                result.append((decision, "done"))
            elif not exists(connection, decision["keep"]):
                result.append((decision, f"recording {decision['keep']} is missing"))
            else:
                result.append((decision, "apply"))
        elif action == "alias":
            done = connection.execute("SELECT 1 FROM recording_aliases WHERE recording_id = ? AND title = ?",
                                      (decision["recording"], decision["title"])).fetchone()
            result.append((decision, "done" if done else "apply" if exists(connection, decision["recording"])
                           else f"recording {decision['recording']} is missing"))
        elif action == "make_version":
            if not exists(connection, decision["recording"]):
                result.append((decision, "done"))
                continue
            versions = connection.execute("SELECT id FROM versions WHERE recording_id = ?",
                                          (decision["recording"],)).fetchall()
            if len(versions) != 1 or not exists(connection, decision["of"]):
                result.append((decision, f"needs one version and song {decision['of']}"))
            else:
                result.append(({**decision, "version_id": versions[0][0]}, "apply"))
        elif action == "add_version":
            if (decision["version_type"], decision["version_name"].casefold()) in existing_versions(
                    connection, decision["of"]):
                result.append((decision, "done"))
                continue
            song = by_title.get(decision.get("genius_title") or decision["title"], {})
            result.append(({**decision, "genius": song.get("genius", []),
                            "hq": decision.get("hq", bool(song.get("hq_artists"))),
                            "hq_projects": song.get("hq_projects", [])}, "apply"))
        elif action == "skip":
            result.append((decision, "noted"))
        elif action == "split_off":
            left = connection.execute(f"""
                SELECT count(*) FROM release_tracks rt JOIN versions v ON v.id = rt.version_id
                WHERE v.recording_id = ? AND v.version_type = 'album'
                  AND rt.release_id IN ({','.join('?' * len(decision['releases']))})""",
                [decision["recording"], *decision["releases"]]).fetchone()[0]
            result.append((decision, "apply" if left else "done"))
        elif action == "detach_version":
            row = connection.execute("SELECT recording_id FROM versions WHERE id = ?", (decision["version"],)).fetchone()
            if row is None:
                result.append((decision, f"version {decision['version']} is missing"))
            elif row[0] != decision["from"]:
                result.append((decision, "done"))
            elif decision.get("to") and not exists(connection, decision["to"]):
                result.append((decision, f"recording {decision['to']} is missing"))
            else:
                result.append((decision, "apply"))
        elif action in ("guest_same", "guest_different"):
            song = survey_song(decision)
            if song is None:
                result.append((decision, "guest song not in the latest survey"))
            elif action == "guest_same":
                ok = exists(connection, decision["recording"])
                ids = {row[0] for row in connection.execute(
                    "SELECT musicbrainz_recording_id FROM recording_musicbrainz_ids WHERE recording_id = ? "
                    "UNION SELECT musicbrainz_recording_id FROM recordings WHERE id = ?",
                    (decision["recording"], decision["recording"]))}
                pages = {row[0] for row in connection.execute(
                    "SELECT s.url FROM recording_sources rs JOIN sources s ON s.id = rs.source_id WHERE rs.recording_id = ?",
                    (decision["recording"],))}
                attached = (ok and (song["musicbrainz"] or song["genius"])
                            and all(mb["id"] in ids for mb in song["musicbrainz"])
                            and all(g["url"] in pages for g in song["genius"]))  # attached before: nothing to repeat
                result.append(({**decision, "song": song}, "done" if attached else "apply" if ok else
                               f"recording {decision['recording']} is missing"))
            else:
                actions, _ = guest_appearances.plan({"songs": [{**song, "in_catalogue": []}]}, connection)
                result.append(({**decision, "actions": actions}, "apply" if actions else "done"))
        else:
            result.append((decision, f"unknown action {action}"))
    return result


def detach_version(connection, decision):
    """A version linked to the wrong song (e.g. a 2002 mixtape remix attached to a
    2020 album song of the same title) leaves that song.  With `to` it becomes
    the main version of that recording (e.g. the guest song it really is);
    without, it becomes its own song titled `title`.  Release tracks, files,
    credits and sources stay on the version.  Caller commits."""
    version_id = decision["version"]
    mbid = connection.execute("SELECT musicbrainz_recording_id FROM versions WHERE id = ?", (version_id,)).fetchone()[0]
    keep_printed = connection.execute("SELECT title FROM recordings WHERE id = ?", (decision["from"],)).fetchone()[0]
    connection.execute("UPDATE release_tracks SET track_title = COALESCE(track_title, ?) WHERE version_id = ?",
                       (keep_printed, version_id))
    target = decision.get("to")
    if target is None:
        artist_id = connection.execute("SELECT artist_id FROM recordings WHERE id = ?", (decision["from"],)).fetchone()[0]
        target = connection.execute("INSERT INTO recordings (title, artist_id, description) VALUES (?, ?, ?)",
                                    (decision["title"], artist_id, decision.get("note"))).lastrowid
    has_main = connection.execute("SELECT 1 FROM versions WHERE recording_id = ? AND version_type = 'album'",
                                  (target,)).fetchone()
    connection.execute("UPDATE versions SET recording_id = ?, version_type = ?, version_name = ? WHERE id = ?",
                       (target, "remix" if has_main else "album", decision.get("version_name") or
                        ("Remix" if has_main else "Album Version"), version_id))
    if not has_main and mbid and not connection.execute(
            "SELECT musicbrainz_recording_id FROM recordings WHERE id = ?", (target,)).fetchone()[0]:
        # The version is now the song itself: its MusicBrainz ID belongs to the recording.
        connection.execute("UPDATE versions SET musicbrainz_recording_id = NULL WHERE id = ?", (version_id,))
        connection.execute("UPDATE recordings SET musicbrainz_recording_id = ? WHERE id = ?", (mbid, target))
    connection.execute("UPDATE recording_musicbrainz_ids SET recording_id = ? WHERE version_id = ?", (target, version_id))
    if not connection.execute("SELECT 1 FROM versions WHERE recording_id = ?", (decision["from"],)).fetchone():
        # Nothing of the old song is left: it was only this version (credits, sources, aliases move along).
        merge(connection, target, decision["from"])
    return target


FILE_MATCH_MS = 2000


def split_off(connection, decision):
    """The tracks of `recording`'s main version on `releases` are another song of
    the same title: they get a new recording (main version); a local file
    follows when its length matches a moved track (within 2 s) and no kept
    track.  Caller commits."""
    recording_id, releases = decision["recording"], decision["releases"]
    marks = ",".join("?" * len(releases))
    version_id, artist_id = connection.execute("""
        SELECT v.id, r.artist_id FROM versions v JOIN recordings r ON r.id = v.recording_id
        WHERE v.recording_id = ? AND v.version_type = 'album' ORDER BY v.id LIMIT 1""", (recording_id,)).fetchone()
    moved = connection.execute(f"SELECT id, duration_ms FROM release_tracks WHERE version_id = ? AND release_id IN ({marks})",
                               [version_id, *releases]).fetchall()
    kept = [d for (d,) in connection.execute(
        f"SELECT duration_ms FROM release_tracks WHERE version_id = ? AND release_id NOT IN ({marks})",
        [version_id, *releases]) if d]
    new_recording = connection.execute("INSERT INTO recordings (title, artist_id, description) VALUES (?, ?, ?)",
                                       (decision["title"], artist_id, decision.get("note"))).lastrowid
    new_version = connection.execute("""INSERT INTO versions (recording_id, version_name, status, version_type)
        VALUES (?, 'Album Version', 'official_release', 'album')""", (new_recording,)).lastrowid
    title = connection.execute("SELECT title FROM recordings WHERE id = ?", (recording_id,)).fetchone()[0]
    for track_id, _ in moved:
        connection.execute("UPDATE release_tracks SET version_id = ?, track_title = COALESCE(track_title, ?) WHERE id = ?",
                           (new_version, title, track_id))
    moved_lengths = [d for _, d in moved if d]
    for file_id, seconds in connection.execute("SELECT id, duration FROM files WHERE version_id = ? AND duration IS NOT NULL",
                                               (version_id,)).fetchall():
        near = lambda lengths: any(abs(seconds * 1000 - d) <= FILE_MATCH_MS for d in lengths)
        if near(moved_lengths) and not near(kept):
            connection.execute("UPDATE files SET version_id = ? WHERE id = ?", (new_version, file_id))
    return new_recording


_SURVEY = {}


def survey_song(decision):
    """The guest-survey song a web decision refers to (latest exports/guest_survey_*.json)."""
    if not _SURVEY:
        files = sorted(EXPORTS_FOLDER.glob("guest_survey_*.json"))
        if files:
            for song in json.loads(files[-1].read_text(encoding="utf-8"))["songs"]:
                _SURVEY[(song["main_artist"], song["title"])] = song
    return _SURVEY.get(tuple(decision.get("survey_key") or ()))


def apply(connection, planned):
    """Apply the 'apply' entries; caller commits."""
    for decision, status in planned:
        if status != "apply":
            continue
        action = decision["action"]
        if action == "merge":
            merge(connection, decision["keep"], decision["drop"])
        elif action == "alias":
            connection.execute("INSERT OR IGNORE INTO recording_aliases (recording_id, title, source, note) "
                               "VALUES (?, ?, ?, ?)", (decision["recording"], decision["title"],
                                                       decision.get("source"), "song review"))
        elif action == "make_version":
            title = connection.execute("SELECT title FROM recordings WHERE id = ?",
                                       (decision["recording"],)).fetchone()[0]
            apply_move(connection, {"recording_id": decision["recording"], "base_id": decision["of"],
                                    "version_id": decision["version_id"], "title": title,
                                    "version_type": decision["version_type"],
                                    "version_name": decision["version_name"]})
        elif action == "add_version":
            apply_source_actions(connection, [{
                "action": "version", "recording_id": decision["of"], "title": decision["title"],
                "version_type": decision["version_type"], "version_name": decision["version_name"],
                "genius": decision["genius"], "hq": decision["hq"], "hq_projects": decision["hq_projects"],
                "credits": decision.get("credits", [])}])
        elif action == "guest_same":
            attach_to_catalogued(connection, decision["song"], decision["recording"])
        elif action == "detach_version":
            detach_version(connection, decision)
        elif action == "split_off":
            split_off(connection, decision)
        elif action == "guest_different":
            guest_appearances.apply(connection, decision["actions"])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Apply manual song review decisions.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--decisions", default=str(DECISIONS_PATH))
    args = parser.parse_args(argv)
    decisions = json.loads(Path(args.decisions).read_text(encoding="utf-8"))["decisions"]
    connection = sqlite3.connect(DATABASE_PATH)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        planned = plan(connection, decisions, latest_crosscheck())
        for decision, status in planned:
            target = decision.get("drop") or decision.get("recording") or decision.get("of") or decision.get(
                "recordings")
            print(f"{status:<8} {decision['action']:<13} {target}  {decision.get('note') or decision.get('title', '')}")
        errors = [d for d, s in planned if s not in ("apply", "done", "noted")]
        if errors:
            print(f"{len(errors)} decision(s) cannot be applied; nothing changed.")
            return planned
        if not args.apply:
            print("Dry run; use --apply to change the database (make a backup first).")
            return planned
        with connection:
            apply(connection, planned)
        print(f"Applied: {sum(1 for _, s in planned if s == 'apply')}")
        return planned
    finally:
        connection.close()


if __name__ == "__main__":
    main()
