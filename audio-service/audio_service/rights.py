"""Who may hear or download an asset.

An asset is served to the public only when its rights allow redistribution AND a
moderator approved it.  Everything else (unknown rights, the owner's private
library, leaks and unreleased songs) is served on the owner's own computer only.
"""

PUBLIC_STATUSES = {"owner_permission", "licensed", "public_domain", "promo"}
ALL_STATUSES = {"unknown", "private"} | PUBLIC_STATUSES


def may_stream_publicly(rights_status: str | None, moderation_state: str) -> bool:
    return rights_status in PUBLIC_STATUSES and moderation_state == "approved"


def may_download_publicly(rights_status: str | None, allows_download: bool, moderation_state: str) -> bool:
    return may_stream_publicly(rights_status, moderation_state) and bool(allows_download)
