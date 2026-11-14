"""Automatic edition selection for one release group (pure functions, no I/O).

Rules (agreed with the user, November 2026):

* Canonical edition: the first usable standard edition in review sort order
  (official first, standard, then US, worldwide, others, then date),
  skipping editions outside the catalogue scope.  If another usable edition
  of the same rank has a track list that is a strict subset of it, that base
  edition becomes canonical.
* Additional editions: editions whose set of *songs* really differs from
  every edition already selected.  Represses, regional and digital copies
  with the same track list, and editions with the same songs in another
  order, disc layout or under other MusicBrainz Recording IDs (another
  pressing, an Atmos mix ...) are skipped.
* Same song: a track counts as an already selected song when its Recording
  ID is known, or when the same-song rule of duplicate_guard matches it
  (same normalized title, duration within 5 s).  The import then stores the
  other ID as an alternate of that recording.
* Excluded: editions with a status that is not allowed (promotion, bootleg
  ... for the official discography), clean / Screwed & Chopped /
  instrumental editions, and editions containing such tracks.
* Manual review: a track with the same title as a selected song whose
  duration differs by 5-15 s (or is unknown).
"""

from duplicate_guard import NEW, REVIEW, SAME, match_recording
from edition_delta import DeltaError, diff_edition_tracks
from titles import normalized_title
from version_policy import excluded_edition_reason, excluded_track_reason


SELECTED = "selected"
SAME_TRACKLIST = "same tracklist as canonical"
DUPLICATE = "same tracklist as another selected edition"
SAME_RECORDINGS = "same songs, other order/layout/Recording IDs"
NOT_OFFICIAL = "not official"
OUT_OF_SCOPE = "out of catalogue scope"
NO_TRACKLIST = "track list unavailable"
MANUAL_REVIEW = "manual review"


def tracklist_signature(tracks):
    return tuple(sorted(
        (track.get("disc_number", 1), track["number"], track.get("musicbrainz_recording_id"))
        for track in tracks
    ))


def scope_problem(edition, tracks, allowed_track_reasons=()):
    reason = excluded_edition_reason(edition.get("title"), edition.get("disambiguation"))
    if reason:
        return f"{reason} edition"
    for track in tracks or []:
        reason = excluded_track_reason(track.get("title"))
        if reason and reason not in allowed_track_reasons:
            return f"contains {reason} track '{track.get('title')}'"
    return None


def recording_set(tracks):
    return {track.get("musicbrainz_recording_id") for track in tracks
            if track.get("musicbrainz_recording_id")}


class SongIndex:
    """Songs selected so far: normalized title -> {song id: durations}."""

    def __init__(self):
        self.by_title = {}
        self.alias = {}  # Recording ID -> song id

    def identify(self, track):
        """(SAME|REVIEW|NEW, song id) for one track."""
        recording_id = track.get("musicbrainz_recording_id")
        if recording_id in self.alias:
            return SAME, self.alias[recording_id]
        if track.get("remix_of_recording_id"):
            return NEW, recording_id
        outcome, matched = match_recording(
            track.get("title"), track.get("duration_ms"),
            self.by_title.get(normalized_title(track.get("title")), {}),
        )
        if outcome in (SAME, REVIEW):
            return outcome, matched
        return NEW, recording_id

    def songs(self, tracks):
        """(set of song ids, titles needing review) of a track list."""
        songs, review = set(), []
        for track in tracks:
            outcome, song = self.identify(track)
            if outcome == REVIEW:
                review.append(track.get("title"))
            songs.add(song if song is not None else ("untitled", track.get("title")))
        return frozenset(songs), review

    def remember(self, tracks):
        for track in tracks:
            outcome, song = self.identify(track)
            if outcome == REVIEW or song is None:
                song = track.get("musicbrainz_recording_id")
            if song is None:
                continue
            self.alias[track.get("musicbrainz_recording_id")] = song
            self.by_title.setdefault(normalized_title(track.get("title")), {}).setdefault(
                song, set()
            ).add(track.get("duration_ms"))


RANK_FIELDS = 3  # sort key prefix: official, standard, country preference


def choose_canonical(usable, sort_key):
    """First edition in sort order, unless a later edition of the same rank
    (same status class, standard, same or better country) has a track list
    that is a strict subset of it (by Recording ID).  The base track list is
    then canonical and bigger editions are stored as additions."""
    canonical = usable[0]
    for edition, tracks in usable[1:]:
        if sort_key(edition)[:RANK_FIELDS] > sort_key(canonical[0])[:RANK_FIELDS]:
            continue
        smaller, current = recording_set(tracks), recording_set(canonical[1])
        if smaller and smaller < current:
            canonical = (edition, tracks)
    return canonical


def select_editions(editions, sort_key, statuses=("Official",), allowed_track_reasons=()):
    """Choose canonical and additional editions.

    editions: list of (musicbrainz_edition, tracks_or_None) pairs, where
    musicbrainz_edition has id/title/date/country/status/disambiguation and
    tracks is the full track list from create_release_json().

    statuses: MusicBrainz release statuses that may be used (only "Official"
    for the official discography).

    Returns (canonical_pair or None, [additional pairs], decisions) where
    decisions is a list of (musicbrainz_edition, status, detail).
    """
    decisions = []
    usable = []
    for edition, tracks in sorted(editions, key=lambda pair: sort_key(pair[0])):
        if edition.get("status") not in statuses:
            decisions.append((edition, NOT_OFFICIAL, edition.get("status") or "unknown status"))
        elif scope_problem(edition, tracks, allowed_track_reasons):
            decisions.append((edition, OUT_OF_SCOPE,
                              scope_problem(edition, tracks, allowed_track_reasons)))
        elif not tracks:
            decisions.append((edition, NO_TRACKLIST, ""))
        else:
            usable.append((edition, tracks))

    if not usable:
        return None, [], decisions

    canonical = choose_canonical(usable, sort_key)
    decisions.append((canonical[0], SELECTED, "canonical"))
    canonical_tracks = canonical[1]
    index = SongIndex()
    index.remember(canonical_tracks)
    seen_signatures = {tracklist_signature(canonical_tracks)}
    seen_song_sets = {index.songs(canonical_tracks)[0]}
    additional = []

    for edition, tracks in usable:
        if edition is canonical[0]:
            continue
        try:
            changes, _ = diff_edition_tracks(canonical_tracks, tracks)
        except DeltaError as error:
            decisions.append((edition, MANUAL_REVIEW, str(error)))
            continue
        if not changes:
            decisions.append((edition, SAME_TRACKLIST, ""))
            continue
        signature = tracklist_signature(tracks)
        if signature in seen_signatures:
            decisions.append((edition, DUPLICATE, ""))
            continue
        songs, review = index.songs(tracks)
        if review:
            decisions.append((
                edition, MANUAL_REVIEW,
                "same title, duration differs 5-15 s: " + ", ".join(review[:3])
                + (" ..." if len(review) > 3 else ""),
            ))
            continue
        if songs in seen_song_sets:
            decisions.append((edition, SAME_RECORDINGS, ""))
            continue
        seen_signatures.add(signature)
        seen_song_sets.add(songs)
        index.remember(tracks)
        additional.append((edition, tracks))
        counts = {}
        for change in changes:
            counts[change["operation"]] = counts.get(change["operation"], 0) + 1
        decisions.append((edition, SELECTED, "additional: " + ", ".join(
            f"{operation} {count}" for operation, count in sorted(counts.items()))))

    return canonical, additional, decisions
