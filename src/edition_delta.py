"""Package schema v2: edition deltas relative to the canonical edition.

An additional edition stores:

* ``track_changes`` - structural differences, applied in order:
    - ``replace``: ``target`` position must exist; ``track`` is the full new
      track and keeps the target position.
    - ``add``: ``track`` is a full track on a position that must be free.
    - ``remove``: ``target`` position must exist.
* ``track_metadata`` - for tracks inherited unchanged from the canonical
  edition: this edition's ``musicbrainz_track_id``, ``duration_ms`` and, only
  when spelled differently, ``title``.  MusicBrainz Track IDs are unique per
  edition, so inherited tracks never keep the canonical Track ID or duration.

A position is ``(disc_number, number)``; a missing disc_number means 1.
All functions are pure: no SQL, no file access, inputs are never modified.
"""

import copy


OPERATIONS = ("replace", "add", "remove")
EDITION_SPECIFIC_FIELDS = ("musicbrainz_track_id", "duration_ms")
METADATA_FIELDS = EDITION_SPECIFIC_FIELDS + ("title",)
POSITION_FIELDS = ("disc_number", "number")


class DeltaError(ValueError):
    """Raised when an edition delta cannot be applied or built safely."""


def _positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def track_position(item, label="track"):
    """Return (disc_number, number) of a track or target object."""
    if not isinstance(item, dict):
        raise DeltaError(f"{label} must be an object.")
    disc_number = item.get("disc_number", 1)
    number = item.get("number")
    if not _positive_int(disc_number):
        raise DeltaError(f"{label} has an invalid disc_number: {disc_number!r}.")
    if not _positive_int(number):
        raise DeltaError(f"{label} has an invalid number: {number!r}.")
    return disc_number, number


def _position_text(position):
    return f"disc {position[0]}, track {position[1]}"


def _position_object(position):
    return {"disc_number": position[0], "number": position[1]}


def _index_tracks(tracks, label):
    if not isinstance(tracks, list):
        raise DeltaError(f"{label} must be a list.")
    indexed = {}
    for index, track in enumerate(tracks, start=1):
        position = track_position(track, f"{label} entry {index}")
        if position in indexed:
            raise DeltaError(f"{label}: duplicate position {_position_text(position)}.")
        indexed[position] = track
    return indexed


def resolve_edition_tracks(canonical_tracks, track_changes, track_metadata):
    """Return the full track list of an additional edition (sorted by position)."""
    canonical = _index_tracks(canonical_tracks, "canonical tracks")
    if not isinstance(track_changes, list):
        raise DeltaError("track_changes must be a list.")
    if not isinstance(track_metadata, list):
        raise DeltaError("track_metadata must be a list.")

    tracks = {}
    for position, track in canonical.items():
        inherited = copy.deepcopy(track)
        for field in EDITION_SPECIFIC_FIELDS:
            inherited.pop(field, None)
        inherited["disc_number"] = position[0]
        tracks[position] = inherited
    inherited_positions = set(tracks)

    for index, change in enumerate(track_changes, start=1):
        label = f"track_changes[{index}]"
        if not isinstance(change, dict):
            raise DeltaError(f"{label} must be an object.")
        operation = change.get("operation")
        if operation not in OPERATIONS:
            raise DeltaError(
                f"{label} has unknown operation {operation!r}; "
                f"use one of: {', '.join(OPERATIONS)}."
            )

        if operation == "remove":
            if "track" in change:
                raise DeltaError(f"{label}: 'remove' must not contain a track.")
            position = track_position(change.get("target"), f"{label} target")
            if position not in tracks:
                raise DeltaError(f"{label}: nothing to remove at {_position_text(position)}.")
            del tracks[position]
            inherited_positions.discard(position)
            continue

        if not isinstance(change.get("track"), dict):
            raise DeltaError(f"{label}: '{operation}' requires a track object.")
        new_track = copy.deepcopy(change["track"])

        if operation == "replace":
            position = track_position(change.get("target"), f"{label} target")
            if position not in tracks:
                raise DeltaError(f"{label}: nothing to replace at {_position_text(position)}.")
            new_track.setdefault("disc_number", position[0])
            new_track.setdefault("number", position[1])
            if track_position(new_track, f"{label} track") != position:
                raise DeltaError(f"{label}: replacement must keep the target position.")
            tracks[position] = new_track
            inherited_positions.discard(position)
            continue

        # add
        if "target" in change:
            raise DeltaError(f"{label}: 'add' uses the track position, not a target.")
        position = track_position(new_track, f"{label} track")
        if position in tracks:
            raise DeltaError(f"{label}: {_position_text(position)} is already occupied.")
        new_track["disc_number"] = position[0]
        tracks[position] = new_track

    seen = set()
    for index, entry in enumerate(track_metadata, start=1):
        label = f"track_metadata[{index}]"
        position = track_position(entry, label)
        if position in seen:
            raise DeltaError(f"{label}: duplicate position {_position_text(position)}.")
        seen.add(position)
        if position not in tracks:
            raise DeltaError(f"{label}: no track at {_position_text(position)}.")
        if position not in inherited_positions:
            raise DeltaError(
                f"{label}: {_position_text(position)} was replaced or added; "
                "its track object already carries its own data."
            )
        unknown = set(entry) - set(POSITION_FIELDS) - set(METADATA_FIELDS)
        if unknown:
            raise DeltaError(f"{label}: unknown fields {sorted(unknown)}.")
        for field in METADATA_FIELDS:
            if field in entry:
                tracks[position][field] = entry[field]

    return [tracks[position] for position in sorted(tracks)]


def _composition_fields(track):
    ignored = set(METADATA_FIELDS) | set(POSITION_FIELDS)
    return {key: value for key, value in track.items() if key not in ignored}


def is_same_track(canonical_track, edition_track):
    """True when the edition track is the canonical track with only
    edition-specific differences (Track ID, duration, spelling)."""
    if _composition_fields(canonical_track) != _composition_fields(edition_track):
        return False
    if canonical_track.get("musicbrainz_recording_id"):
        return True
    # Without a Recording ID only a case-insensitive title match is accepted.
    return (
        str(canonical_track.get("title", "")).casefold()
        == str(edition_track.get("title", "")).casefold()
    )


def _normalized(tracks):
    result = []
    for track in tracks:
        track = dict(track)
        track.setdefault("disc_number", 1)
        result.append(track)
    return sorted(result, key=lambda track: track_position(track))


def diff_edition_tracks(canonical_tracks, edition_tracks):
    """Build (track_changes, track_metadata) for a full edition track list.

    The result is verified: resolving it must reproduce edition_tracks exactly.
    """
    canonical = _index_tracks(canonical_tracks, "canonical tracks")
    edition = _index_tracks(edition_tracks, "edition tracks")
    track_changes = []
    track_metadata = []

    for position in sorted(set(canonical) | set(edition)):
        canonical_track = canonical.get(position)
        edition_track = edition.get(position)

        if edition_track is None:
            track_changes.append({
                "operation": "remove",
                "target": _position_object(position),
            })
        elif canonical_track is None:
            track_changes.append({
                "operation": "add",
                "track": copy.deepcopy(edition_track),
            })
        elif is_same_track(canonical_track, edition_track):
            entry = _position_object(position)
            for field in EDITION_SPECIFIC_FIELDS:
                if field in edition_track:
                    entry[field] = edition_track[field]
            if edition_track.get("title") != canonical_track.get("title"):
                entry["title"] = edition_track.get("title")
            track_metadata.append(entry)
        else:
            track_changes.append({
                "operation": "replace",
                "target": _position_object(position),
                "track": copy.deepcopy(edition_track),
            })

    resolved = resolve_edition_tracks(canonical_tracks, track_changes, track_metadata)
    if _normalized(resolved) != _normalized(edition_tracks):
        raise DeltaError("Delta round-trip check failed; the edition cannot be stored as a delta.")
    return track_changes, track_metadata


def make_delta_edition(canonical_tracks, full_edition):
    """Convert a full additional edition {edition_name, release, tracks} to v2."""
    track_changes, track_metadata = diff_edition_tracks(
        canonical_tracks, full_edition["tracks"]
    )
    return {
        "edition_name": full_edition["edition_name"],
        "release": copy.deepcopy(full_edition["release"]),
        "track_changes": track_changes,
        "track_metadata": track_metadata,
    }
