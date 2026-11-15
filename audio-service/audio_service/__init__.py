"""Lil Wayne Tracker -- Audio Service (stage 1: skeleton).

A separate service next to the tracker with its own storage, database and API.
It keeps audio files (assets) by content (sha256), their technical metadata, the
rights under which each may be played or downloaded, and links to versions of the
catalogue.  The tracker reaches it only through its API.

Rule that never changes: leaked / unreleased audio is never served publicly; an
asset is public only when its rights allow it (rights.py).
"""

__version__ = "0.1.0"
