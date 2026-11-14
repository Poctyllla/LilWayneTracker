"""Link official remixes to the recording they remix (pure functions).

Catalogue model: a remix is a *version* of the original song recording, not a
recording of its own.  A track is linked through ``remix_of_recording_id``
(the MusicBrainz Recording ID of the original):

1. from a MusicBrainz "remix" relationship, set by create_release_json(); or
2. when the title carries a bracketed remix marker, e.g.
   "Stuntin' Like My Daddy (Rock remix)", and a non-remix track with the same
   base title is in the same package (same release group).

Anything else is left unlinked; package validation then reports it for
manual review instead of creating a separate recording.
"""

import copy
import re

from titles import normalized_title


# "(Rock remix)", "[Remix]", "(SIDEPIECE extended mix)" ...
REMIX_MARKER = re.compile(r"\s*[(\[]([^)\]]*\b(?:re)?mix\b[^)\]]*)[)\]]\s*$", re.IGNORECASE)


def remix_parts(title):
    """Return (base_title, version_name) for 'Song (Rock remix)', else None."""
    match = REMIX_MARKER.search(str(title or ""))
    if not match:
        return None
    base = str(title)[:match.start()].strip()
    return (base, match.group(1).strip()) if base else None


def is_remix_title(title):
    return remix_parts(title) is not None


def remix_version_name(title):
    parts = remix_parts(title)
    return parts[1] if parts else "Remix"


def mark_unlinked_as_songs(track_lists):
    """Singles: a remix marker without any catalogued original names the song.

    E.g. the single 'F**k Today (Rebirth mix)' is the only release of that
    song, so the track is the song itself (remix_marker_ignored).
    """
    marked = []
    for tracks in track_lists:
        new_tracks = []
        for track in tracks:
            track = copy.deepcopy(track)
            if (is_remix_title(track.get("title")) and not track.get("remix_of_recording_id")
                    and not track.get("remix_of_external")):
                track["remix_marker_ignored"] = True
            new_tracks.append(track)
        marked.append(new_tracks)
    return marked


def mark_external_remixes(track_lists):
    """Mixtapes: remixes left unlinked are remixes of other artists' songs.

    Lil Wayne often raps on remixes of songs that are not his, so the
    original will never be in this catalogue.  Such a track is marked
    'remix_of_external' and imported as its own recording.
    """
    marked = []
    for tracks in track_lists:
        new_tracks = []
        for track in tracks:
            track = copy.deepcopy(track)
            if (is_remix_title(track.get("title")) and not track.get("remix_of_recording_id")
                    and not track.get("remix_marker_ignored")):
                track["remix_of_external"] = True
            new_tracks.append(track)
        marked.append(new_tracks)
    return marked


def link_remixes(track_lists, catalogue_originals=None):
    """Return copies of the track lists with remix_of_recording_id filled in.

    track_lists: full track lists of all editions of one package.  Existing
    links (from MusicBrainz relationships) are kept.  catalogue_originals
    optionally maps normalized titles to MusicBrainz Recording IDs of songs
    already in the catalogue; it is used only when the package itself has no
    original with that title, and only when exactly one song matches.
    """
    originals = {}
    for tracks in track_lists:
        for track in tracks:
            recording_id = track.get("musicbrainz_recording_id")
            if recording_id and not is_remix_title(track.get("title")):
                originals.setdefault(normalized_title(track.get("title")), set()).add(recording_id)

    linked = []
    for tracks in track_lists:
        new_tracks = []
        for track in tracks:
            track = copy.deepcopy(track)
            parts = remix_parts(track.get("title"))
            if parts and not track.get("remix_of_recording_id"):
                base = normalized_title(parts[0])
                own_id = track.get("musicbrainz_recording_id")
                if own_id and (own_id in originals.get(base, set())
                               or own_id in (catalogue_originals or {}).get(base, set())):
                    # The same recording appears in this package without the
                    # marker (e.g. "I'm Single (remix)" vs "I'm Single"): the
                    # marker is only a title variant, not a separate remix.
                    track["remix_marker_ignored"] = True
                    new_tracks.append(track)
                    continue
                candidates = originals.get(base) or (catalogue_originals or {}).get(base, set())
                if len(candidates) == 1:
                    track["remix_of_recording_id"] = next(iter(candidates))
            new_tracks.append(track)
        linked.append(new_tracks)
    return linked


def remix_issues(track):
    """Validation errors for one resolved track."""
    issues = []
    remix_of = track.get("remix_of_recording_id")
    if remix_of and remix_of == track.get("musicbrainz_recording_id"):
        issues.append(f"track '{track.get('title')}' is linked as a remix of itself.")
    if (is_remix_title(track.get("title")) and not remix_of
            and not track.get("remix_marker_ignored") and not track.get("remix_of_external")):
        issues.append(
            f"remix '{track.get('title')}' is not linked to its original recording "
            "(no MusicBrainz remix relationship and no original with the same "
            "base title in this package); review it manually."
        )
    return issues
