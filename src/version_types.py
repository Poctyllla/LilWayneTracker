"""Version types of one song, read from bracketed title markers.

User decision (Nov 13, 2026): every version of a song is kept apart --
demos, snippets, original / early versions, alternate versions, live
performances, extended versions and acapellas are versions of the song
recording, like remixes.  Still out of scope: clean / radio edits,
instrumentals, Screwed & Chopped (version_policy.py).  Freestyles and covers
are their own songs (their title names the song they are built on).

    split_version("A Milli (Demo)")              -> ("A Milli", "demo", "Demo")
    split_version("Forever (Remix) (Snippet)")   -> ("Forever", "snippet", "Remix (Snippet)")
    split_version("Mr. Carter (Alternate Version)") -> ("Mr. Carter", "alternate", "Alternate Version")
    split_version("Lollipop")                    -> ("Lollipop", None, None)
"""

import re


# Checked in this order: the first type found in any marker wins
# ("Forever (Remix) (Snippet)" is a snippet of the remix).
VERSION_TYPE_PATTERNS = (
    ("snippet", r"snippet|preview"),
    ("demo", r"demo|reference|ref(?:erence)? track|rough"),
    ("live", r"live|concert|performance"),
    ("acapella", r"a ?capp?ella"),
    ("original", r"original|og|first version|early version|old version|unreleased version"),
    ("extended", r"extended(?!\s+(?:re)?mix)"),  # "Extended Remix" is a remix
    ("alternate", r"alternate|alt\.? version|unreleased verse|another version|v\d|version \d|"
                  r"\w+ version"),
    ("remix", r"(?:re)?mix|remix #?\d"),
)
_TYPE_RES = [(name, re.compile(rf"\b(?:{pattern})\b", re.IGNORECASE)) for name, pattern in VERSION_TYPE_PATTERNS]

# Labels that make neither another song nor another version.
NEUTRAL = re.compile(
    r"^(?:dirty|explicit|official(?: dirty)?(?: version)?|lp version|album version|dirty version|"
    r"explicit version|bonus(?: track)?|(?:un)?mastered(?: version)?|cdq|hq|exclusive|hot!*|new|"
    r"full version|radio version|"
    r"prod\.? by .*|produced by .*|unreleased)$",
    re.IGNORECASE,
)
# Version words that stay part of the song title.
OWN_SONG = re.compile(r"\b(?:freestyle|cover|skit|interlude|intro|outro|part|pt\.?)\b", re.IGNORECASE)
MARKER = re.compile(r"\s*[(\[]([^)\]]*)[)\]]")


def marker_type(marker):
    """Version type of one bracketed marker, 'neutral', or None (part of the title)."""
    text = marker.strip()
    if NEUTRAL.match(text):
        return "neutral"
    if OWN_SONG.search(text) and not re.search(r"snippet|demo|live|original", text, re.IGNORECASE):
        return None
    for name, pattern in _TYPE_RES:
        if pattern.search(text):
            return name
    return None


def split_version(title):
    """(song title, version type or None, version name or None).

    Only trailing bracketed markers count; markers in the middle of a title
    stay ("Hands Up (My Last) (Freestyle)" is one song title).
    """
    title = str(title or "").strip()
    markers, rest = [], title
    while True:
        match = re.search(r"\s*[(\[]([^)\]]*)[)\]]\s*$", rest)
        if not match or not rest[:match.start()].strip():
            break
        kind = marker_type(match.group(1))
        if kind is None:
            break
        markers.insert(0, (kind, match.group(1).strip()))
        rest = rest[:match.start()].rstrip()
    kept = [(kind, text) for kind, text in markers if kind != "neutral"]
    if not kept:
        return rest if markers else title, None, None
    order = [name for name, _ in VERSION_TYPE_PATTERNS]
    version_type = min((kind for kind, _ in kept), key=order.index)
    if version_type == "original" and any(kind == "remix" for kind, _ in kept):
        version_type = "remix"  # "Remix (Original)": the original remix, not an early version of the song
    name = kept[0][1] + "".join(f" ({text})" for _, text in kept[1:])
    return rest, version_type, name
