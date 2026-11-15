"""Catalogue scope for versions (user decision, November 11, 2026).

Screwed & Chopped, clean/edited and instrumental variants are NOT catalogued,
neither as editions nor as tracks.  Besides the original, the only version
type kept is an official remix.

Track titles are checked only inside brackets, e.g. "(Screwed & Chopped mix)"
or "[Instrumental]", so ordinary song titles are not affected.
"""

import re


EXCLUDED_VARIANTS = {
    "screwed_and_chopped": r"screwed|chopped",
    "clean": r"clean|edited|radio edit",
    "instrumental": r"instrumentals?",
}

_EDITION_PATTERNS = {
    name: re.compile(rf"\b(?:{pattern})\b", re.IGNORECASE)
    for name, pattern in EXCLUDED_VARIANTS.items()
}
_TRACK_PATTERNS = {
    name: re.compile(rf"[(\[][^)\]]*\b(?:{pattern})\b[^)\]]*[)\]]", re.IGNORECASE)
    for name, pattern in EXCLUDED_VARIANTS.items()
}


# ... and as a " - " suffix: "Walk In - Chopped & Screwed" (Genius titles of the
# Tha Carter Screwed & Chopped edition), "Lollipop - Radio Edit".
_SUFFIX_PATTERNS = {
    name: re.compile(rf"\s-\s[^-]*\b(?:{pattern})\b[^-]*$", re.IGNORECASE)
    for name, pattern in EXCLUDED_VARIANTS.items()
}


def excluded_track_reason(title):
    """Return the excluded variant name for a track title, or None."""
    for patterns in (_TRACK_PATTERNS, _SUFFIX_PATTERNS):
        for name, pattern in patterns.items():
            if pattern.search(str(title or "")):
                return name
    return None


def without_excluded_tracks(details, keep_reasons=()):
    """Copy of MusicBrainz release details without out-of-scope tracks.

    keep_reasons lists excluded variants that are kept anyway (("clean",)
    for a single that exists only as a clean version).

    Returns (details_copy, removed_titles).  Kept tracks keep their printed
    positions; the copy records the removed titles under
    '_removed_out_of_scope_tracks' so packages can note the shortened list.
    """
    import copy

    filtered = copy.deepcopy(details)
    removed = []
    for medium in filtered.get("media", []):
        kept = []
        for track in medium.get("tracks", []):
            reason = excluded_track_reason(track.get("title"))
            if reason and reason not in keep_reasons:
                removed.append(track.get("title"))
            else:
                kept.append(track)
        medium["tracks"] = kept
    filtered["media"] = [medium for medium in filtered.get("media", []) if medium["tracks"]]
    if removed:
        filtered["_removed_out_of_scope_tracks"] = removed
    return filtered, removed


def excluded_edition_reason(*texts):
    """Return the excluded variant name for an edition, or None.

    Pass the edition title, MusicBrainz disambiguation and similar labels.
    Bracketed markers in the title and any marker in a disambiguation count.
    """
    for text in texts:
        text = str(text or "")
        for name, pattern in _EDITION_PATTERNS.items():
            if pattern.search(text):
                return name
    return None
