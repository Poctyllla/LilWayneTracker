"""Title normalization used for comparisons (never for storage)."""

import re


_CLEAN_MARKER = re.compile(
    r"[(\[]\s*(?:clean|radio edit|radio version|edited|explicit|dirty)(?: version)?\s*[)\]]"
)


def normalized_title(title):
    """Case-, punctuation- and feature-insensitive title used for comparisons.

    Clean / explicit markers are ignored too: in this catalogue a clean edit
    is the same song (it is not tracked as a version).
    """
    text = str(title or "").casefold()
    text = re.sub(r"[(\[](?:feat|ft|featuring)\.?[^)\]]*[)\]]", "", text)
    text = _CLEAN_MARKER.sub("", text)
    text = text.replace("’", "'").replace("‐", "-").replace("&", " and ")
    text = text.replace("'", "")
    return re.sub(r"[\W_]+", " ", text).strip()
