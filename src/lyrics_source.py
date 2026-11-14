"""Synced lyrics for the player (experiment, local server only).

Lyrics are copyrighted: they are fetched on demand from LRCLIB (lrclib.net,
an open database of time-synced lyrics), kept in memory while the local
server runs and never written to SQLite, project files or the static
export.  A public site would need a licensed provider (e.g. Musixmatch's
commercial API) instead.

    lookup(connection, version_id) -> {"synced": [[seconds, line], ...] | None,
                                       "plain": text | None, "source": ..., ...}
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

API = "https://lrclib.net/api/search"
USER_AGENT = "LilWayneTracker/0.1 (local catalog project)"
MAX_DIFFERENCE = 10  # seconds between our length and LRCLIB's
LRC_LINE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
CACHE = {}


def parse_lrc(text):
    """[[seconds, line], ...] from LRC text ("[01:02.34] words"; a line may carry several stamps)."""
    lines = []
    for raw in (text or "").splitlines():
        stamps = LRC_LINE.findall(raw)
        if not stamps:
            continue
        words = LRC_LINE.sub("", raw).strip()
        for minutes, seconds in stamps:
            lines.append([round(int(minutes) * 60 + float(seconds), 2), words])
    return sorted(lines, key=lambda line: line[0])


def choose(results, duration):
    """The best LRCLIB result: within MAX_DIFFERENCE seconds of our length (when
    known), synced lyrics first, then the closest length."""
    def difference(result):
        return abs((result.get("duration") or 0) - duration) if duration else 0
    candidates = [r for r in results if not r.get("instrumental") and (r.get("syncedLyrics") or r.get("plainLyrics"))
                  and (not duration or difference(r) <= MAX_DIFFERENCE)]
    if not candidates:
        return None
    return min(candidates, key=lambda r: (not r.get("syncedLyrics"), difference(r)))


def song_facts(connection, version_id):
    """(title, artist, duration in seconds or None) of one version."""
    row = connection.execute("""
        SELECT r.title, a.name, v.version_type,
               (SELECT max(rt.duration_ms) FROM release_tracks rt WHERE rt.version_id = v.id) AS ms,
               (SELECT max(f.duration) FROM files f WHERE f.version_id = v.id) AS file_seconds
        FROM versions v JOIN recordings r ON r.id = v.recording_id JOIN artists a ON a.id = r.artist_id
        WHERE v.id = ?""", (version_id,)).fetchone()
    if row is None:
        return None
    title, artist, _type, ms, file_seconds = row
    seconds = ms / 1000 if ms else file_seconds
    return title, artist, seconds


def fetch(title, artist):
    query = urllib.parse.urlencode({"track_name": title, "artist_name": artist})
    request = urllib.request.Request(f"{API}?{query}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def lookup(connection, version_id, fetcher=fetch):
    if version_id in CACHE:
        return CACHE[version_id]
    facts = song_facts(connection, version_id)
    if facts is None:
        return None
    title, artist, seconds = facts
    clean = re.sub(r"\s*[\(\[](?:feat|ft|with)\.?\s[^\)\]]*[\)\]]", "", title, flags=re.I).strip()
    try:
        best = choose(fetcher(clean, artist), seconds)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
        return {"synced": None, "plain": None, "source": "LRCLIB", "error": str(error)}  # not cached: retry later
    result = {"synced": None, "plain": None, "source": "LRCLIB", "url": None}
    if best:
        result.update(synced=parse_lrc(best.get("syncedLyrics")) or None, plain=best.get("plainLyrics"),
                      url=f"https://lrclib.net/api/get/{best['id']}" if best.get("id") else None)
    CACHE[version_id] = result
    return result
