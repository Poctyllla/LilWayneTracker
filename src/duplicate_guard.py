"""Same song under different MusicBrainz Recording IDs (user decision, Nov 2026).

MusicBrainz often gives one song different Recording IDs on different
releases (single vs album, deluxe re-releases, other pressings).  In this
catalogue such tracks are ONE recording; the extra MusicBrainz IDs are kept
as alternates (table recording_musicbrainz_ids).

Rule for a track whose Recording ID is not catalogued, compared with
catalogued recordings of the same normalized title (remixes excluded):

* duration differs by at most 15 s -> same song (link, store alternate ID)
* more than 15 s, or no same title  -> a different song (new recording)

(User decision, November 12, 2026: differences up to 15 s are normal for
singles; earlier the 5-15 s band went to manual review.)

A track without a known duration is the same song when exactly one
catalogued song has that title; otherwise (several candidates) it goes to
manual review.
"""

import sqlite3

from titles import normalized_title


SAME_SONG_MS = 15000
REVIEW_MS = 15000  # no separate review band any more; kept for clarity

SAME = "same song"
REVIEW = "manual review"
NEW = "new song"


def classify_durations(duration, catalogued_durations):
    """SAME / REVIEW / NEW for one candidate recording's known durations."""
    known = [value for value in catalogued_durations if value is not None]
    if duration is None or not known:
        return REVIEW
    difference = min(abs(duration - value) for value in known)
    if difference <= SAME_SONG_MS:
        return SAME
    if difference <= REVIEW_MS:
        return REVIEW
    return NEW


# Generic titles shared by many different songs (mixtapes have dozens of
# intros and skits): stricter tolerance, and no match without a duration.
GENERIC_TITLE_WORDS = ("intro", "outro", "interlude", "skit", "freestyle", "prelude")
GENERIC_SAME_SONG_MS = 3000


def is_generic_title(title):
    normalized = normalized_title(title)
    return any(word in normalized.split() for word in GENERIC_TITLE_WORDS)


def match_recording(title, duration, candidates, allow_unknown_duration=True):
    """Decide against {recording_id: durations} of the same normalized title.

    allow_unknown_duration: a track without a duration may match a single
    catalogued song (used for singles only; on mixtapes many freestyles share
    titles with other songs, so there it is a new song).

    Returns (SAME, recording_id), (REVIEW, recording_id) or (NEW, None).
    """
    if duration is None and not allow_unknown_duration:
        return NEW, None
    if is_generic_title(title):
        if duration is None:
            return NEW, None
        close = [
            recording_id for recording_id, durations in candidates.items()
            if any(d is not None and abs(duration - d) <= GENERIC_SAME_SONG_MS for d in durations)
        ]
        return (SAME, close[0]) if len(close) == 1 else (NEW, None)
    if duration is None and len(candidates) == 1:
        # User decision (Nov 12, 2026): a single without a known duration and
        # exactly one song of that title in the catalogue is that song.
        return SAME, next(iter(candidates))
    if duration is not None:
        # Catalogued songs without any known duration cannot be compared;
        # they must not block the decision for the others.
        candidates = {
            recording_id: durations for recording_id, durations in candidates.items()
            if any(value is not None for value in durations)
        }
    results = {
        recording_id: classify_durations(duration, durations)
        for recording_id, durations in candidates.items()
    }
    same = [recording_id for recording_id, result in results.items() if result == SAME]
    review = [recording_id for recording_id, result in results.items() if result == REVIEW]
    if len(same) == 1 and not review:
        return SAME, same[0]
    if same or review:
        return REVIEW, (same + review)[0]
    return NEW, None


def load_catalogue_from_connection(connection):
    known_ids = {row[0] for row in connection.execute("""
        SELECT musicbrainz_recording_id FROM recordings
        WHERE musicbrainz_recording_id IS NOT NULL
        UNION
        SELECT musicbrainz_recording_id FROM versions
        WHERE musicbrainz_recording_id IS NOT NULL
        UNION
        SELECT musicbrainz_recording_id FROM recording_musicbrainz_ids
    """)}
    by_title, durations = {}, {}
    for recording_id, title, track_title, duration in connection.execute("""
        SELECT rec.id, rec.title, rt.track_title, rt.duration_ms
        FROM recordings rec
        JOIN versions v ON v.recording_id = rec.id
        JOIN release_tracks rt ON rt.version_id = v.id
        WHERE v.version_type = 'album'  -- main version only (no remix / demo / snippet / live ...)
    """):
        durations.setdefault(recording_id, set()).add(duration)
        for name in {title, track_title}:
            if name:
                entry = by_title.setdefault(normalized_title(name), {})
                entry.setdefault(recording_id, set()).add(duration)
    if _has_table(connection, "recording_aliases"):
        # Other spellings known from HQ / Genius find the same song.
        for recording_id, alias in connection.execute("SELECT recording_id, title FROM recording_aliases"):
            if recording_id in durations:
                by_title.setdefault(normalized_title(alias), {}).setdefault(
                    recording_id, set()).update(durations[recording_id])
    # Songs known only from a demo / snippet / live version or from sources
    # (no main version yet): a later full track of that title is this song.
    without_main = {}
    guest_filter = "AND rec.appearance IS NULL" if _has_column(connection, "recordings", "appearance") else ""
    for recording_id, title in connection.execute(f"""
        SELECT rec.id, rec.title FROM recordings rec
        WHERE NOT EXISTS (SELECT 1 FROM versions v
                          WHERE v.recording_id = rec.id AND v.version_type = 'album')
        {guest_filter}  -- another artist's song is never Lil Wayne's own track
    """):
        without_main.setdefault(normalized_title(title), set()).add(recording_id)
    originals = {}
    for recording_id, musicbrainz_id in connection.execute(
        "SELECT id, musicbrainz_recording_id FROM recordings WHERE musicbrainz_recording_id IS NOT NULL"
    ):
        for title in by_title_of_recording(by_title, recording_id):
            originals.setdefault(title, set()).add(musicbrainz_id)
    return {"known_ids": known_ids, "by_title": by_title, "originals": originals,
            "without_main": without_main}


def _has_table(connection, name):
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _has_column(connection, table, column):
    return any(row[1] == column for row in connection.execute(f"PRAGMA table_info({table})"))


def song_without_main_version(title, catalogue):
    """Recording ID of the one catalogued song of that title that has no main
    version yet (only a snippet / demo / ... or only source data), else None.
    Generic titles (intro, skit ...) never match."""
    if is_generic_title(title):
        return None
    found = catalogue.get("without_main", {}).get(normalized_title(title), set())
    return next(iter(found)) if len(found) == 1 else None


def by_title_of_recording(by_title, recording_id):
    return [title for title, recordings in by_title.items() if recording_id in recordings]


def load_catalogue(database_path):
    """Read recordings, their MusicBrainz IDs and durations (read-only)."""
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        return load_catalogue_from_connection(connection)
    finally:
        connection.close()


def classify_tracks(tracks, catalogue, allow_unknown_duration=True):
    """Return {'same': [...], 'review': [...]} as (title, recording_id) pairs."""
    result = {"same": [], "review": []}
    for track in tracks:
        recording_id = track.get("musicbrainz_recording_id")
        if recording_id and recording_id in catalogue["known_ids"]:
            continue
        if track.get("remix_of_recording_id"):
            continue  # becomes a version of its original, not a recording
        candidates = catalogue["by_title"].get(normalized_title(track.get("title")), {})
        outcome, catalogued_id = match_recording(
            track.get("title"), track.get("duration_ms"), candidates, allow_unknown_duration
        )
        if outcome == NEW and song_without_main_version(track.get("title"), catalogue) is not None:
            outcome, catalogued_id = SAME, song_without_main_version(track.get("title"), catalogue)
        if outcome == SAME:
            result["same"].append((track.get("title"), catalogued_id))
        elif outcome == REVIEW:
            result["review"].append((track.get("title"), catalogued_id))
    return result


def possible_duplicates(tracks, catalogue):
    """Tracks that need manual review (kept for callers of the first version)."""
    return classify_tracks(tracks, catalogue)["review"]
