"""Videos of Lil Wayne songs on YouTube (official embeds only; nothing is downloaded).

Sources (no API key needed):
  * Wikidata: the YouTube video ID (P1651) of songs performed by Lil Wayne or a
    group with him (official music videos), matched to catalogued songs by
    Genius / MusicBrainz id, else by title;
  * Genius: the YouTube link on the song page of every catalogued song
    (genius_details.py --media keeps them).
Every video is checked with YouTube's oEmbed endpoint (title and channel; a video
that is gone, private or not embeddable has no oEmbed and is dropped), cached in
exports/youtube_oembed.json.  Audio-only uploads (Official Audio, lyric videos,
"- Topic" art tracks) are left out.  The kind (music video, freestyle, performance,
interview) comes from the title; "official" means the channel is a VEVO channel,
one of the song's artists, a label or a broadcaster.

usage:
    python videos.py            # dry run
    python videos.py --apply    # store videos and their songs (back up first: python database.py --backup <label>)
"""

import argparse
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from import_release import DATABASE_PATH
from streaming_search import letters
from titles import normalized_title

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OEMBED_CACHE = PROJECT_ROOT / "exports" / "youtube_oembed.json"
GENIUS_PATH = PROJECT_ROOT / "exports" / "genius_song_details.json"
USER_AGENT = "LilWayneTracker/0.1 (local catalog project)"
YOUTUBE_ID = re.compile(r"(?:v=|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})")
LILWAYNE_QID = "Q15615"
AUDIO_ONLY = re.compile(r"official audio|\baudio\b|lyric|lyrics|visuali[sz]er|- topic$", re.IGNORECASE)
KINDS = (("freestyle", re.compile(r"freestyle", re.IGNORECASE)),
         ("performance", re.compile(r"\blive\b|performance|performs|awards|grammy|\bbet\b|\bvma|concert|tour|"
                                    r"tiny desk|unplugged|jimmy kimmel|tonight show|\bsnl\b|saturday night live", re.IGNORECASE)),
         ("interview", re.compile(r"interview|talks|breakfast club|podcast", re.IGNORECASE)))
OFFICIAL_CHANNELS = {letters(name) for name in (
    "Lil Wayne", "Young Money", "Young Money Entertainment", "Cash Money Records", "Cash Money", "Republic Records",
    "Universal Music Group", "Def Jam Recordings", "Atlantic Records", "Interscope Records", "Epic Records",
    "RCA Records", "Columbia Records", "Warner Records", "Capitol Records", "Island Records", "Motown",
    "Maybach Music Group", "GOOD Music", "Aftermath", "Roc Nation", "Asylum Records", "Universal Records",
    "BET Networks", "BET", "GRAMMYs", "Recording Academy / GRAMMYs", "MTV", "HOT 97", "Sway's Universe",
    "Power 106 Los Angeles", "Funk Flex", "LA Leakers", "Tim Westwood TV", "The Breakfast Club Power 105.1 FM",
    "Complex", "XXL", "Genius", "COLORS", "NPR Music", "Jimmy Kimmel Live", "The Tonight Show Starring Jimmy Fallon",
    "Saturday Night Live", "Late Night with Seth Meyers", "The Late Show with Stephen Colbert", "SiriusXM",
)}


def youtube_id(url):
    match = YOUTUBE_ID.search(url or "")
    return match.group(1) if match else None


def wikidata_videos():
    """[{"youtube_id", "label", "genius", "musicbrainz"}] -- music videos on Wikidata."""
    query = f"""SELECT ?item ?itemLabel ?yt ?mb ?genius (GROUP_CONCAT(DISTINCT ?performerName; separator="|") AS ?performers)
      WHERE {{
      {{ ?item wdt:P175 wd:{LILWAYNE_QID} }} UNION {{ ?item wdt:P175 ?group . ?group wdt:P527 wd:{LILWAYNE_QID} }}
      ?item wdt:P1651 ?yt .
      OPTIONAL {{ ?item wdt:P175 ?performer . ?performer rdfs:label ?performerName . FILTER(LANG(?performerName) = "en") }}
      OPTIONAL {{ ?item wdt:P4404 ?mb }} OPTIONAL {{ ?item wdt:P6218 ?genius }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }} }}
      GROUP BY ?item ?itemLabel ?yt ?mb ?genius"""
    url = "https://query.wikidata.org/sparql?" + urllib.parse.urlencode({"query": query, "format": "json"})
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=90) as response:
        rows = json.loads(response.read().decode("utf-8"))["results"]["bindings"]
    return [{"youtube_id": r["yt"]["value"], "label": r["itemLabel"]["value"],
             "genius": (r.get("genius") or {}).get("value"), "musicbrainz": (r.get("mb") or {}).get("value"),
             "performers": {letters(n) for n in (r.get("performers") or {}).get("value", "").split("|") if n}}
            for r in rows]


class OEmbed:
    """YouTube oEmbed (title, channel), cached; None for gone / private / not embeddable videos."""

    def __init__(self):
        self.cache = json.loads(OEMBED_CACHE.read_text(encoding="utf-8")) if OEMBED_CACHE.exists() else {}
        self.calls = 0

    def get(self, video_id):
        if video_id not in self.cache:
            url = "https://www.youtube.com/oembed?" + urllib.parse.urlencode(
                {"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"})
            time.sleep(0.5)
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=20) as r:
                    data = json.loads(r.read().decode("utf-8"))
                self.cache[video_id] = {"title": data.get("title"), "channel": data.get("author_name")}
            except urllib.error.HTTPError as error:
                self.cache[video_id] = None if error.code in (400, 401, 403, 404) else {"error": error.code}
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                return None  # not cached: try again next run
            self.calls += 1
            if self.calls % 50 == 0:
                self.save()
        value = self.cache[video_id]
        return value if value and "error" not in value else None

    def save(self):
        OEMBED_CACHE.write_text(json.dumps(self.cache, ensure_ascii=False, indent=0), encoding="utf-8")


def kind_of(title):
    for kind, pattern in KINDS:
        if pattern.search(title or ""):
            return kind
    return "music_video"


def song_index(connection):
    """Lookups to find catalogued songs: by Genius id, MusicBrainz id and title."""
    by_genius, by_mb, by_title = {}, {}, {}
    for recording_id, genius_id in connection.execute("""
            SELECT id, genius_song_id FROM recordings WHERE genius_song_id IS NOT NULL
            UNION SELECT recording_id, genius_song_id FROM versions WHERE genius_song_id IS NOT NULL"""):
        by_genius.setdefault(str(genius_id), recording_id)
    for recording_id, mbid in connection.execute("""
            SELECT id, musicbrainz_recording_id FROM recordings WHERE musicbrainz_recording_id IS NOT NULL
            UNION SELECT recording_id, musicbrainz_recording_id FROM recording_musicbrainz_ids"""):
        by_mb.setdefault(mbid, recording_id)
    for recording_id, title, released, artist in connection.execute("""
            SELECT r.id, r.title, av.availability = 'released', a.name FROM recordings r
            JOIN artists a ON a.id = r.artist_id JOIN recording_availability av ON av.recording_id = r.id"""):
        by_title.setdefault(normalized_title(title), []).append((not released, recording_id, letters(artist)))
    return by_genius, by_mb, by_title


def artist_names(connection, recording_id):
    return {letters(name) for (name,) in connection.execute("""
        SELECT a.name FROM recordings r JOIN artists a ON a.id = r.artist_id WHERE r.id = ?
        UNION SELECT a.name FROM credits c JOIN artists a ON a.id = c.artist_id
        WHERE c.recording_id = ? AND c.role IN ('main_artist', 'featured_artist')""", (recording_id, recording_id))}


def plan(connection, oembed, use_wikidata=True):
    """{youtube id: {"title", "channel", "kind", "official", "source", "songs": set()}}"""
    by_genius, by_mb, by_title = song_index(connection)
    found = {}

    def add(video_id, recording_id, source):
        info = oembed.get(video_id)
        if not info or AUDIO_ONLY.search(info["title"] or "") or AUDIO_ONLY.search(info["channel"] or ""):
            return
        names = artist_names(connection, recording_id) | {letters("Lil Wayne")}
        channel = letters(re.sub(r"vevo$", "", info["channel"] or "", flags=re.IGNORECASE))
        official = (info["channel"] or "").upper().endswith("VEVO") or channel in names or letters(info["channel"] or "") in OFFICIAL_CHANNELS
        entry = found.setdefault(video_id, {"title": info["title"], "channel": info["channel"], "kind": kind_of(info["title"]),
                                            "official": official, "source": source, "songs": set()})
        entry["songs"].add(recording_id)

    genius = json.loads(GENIUS_PATH.read_text(encoding="utf-8")) if GENIUS_PATH.exists() else {}
    for genius_id, recording_id in by_genius.items():
        for media in (genius.get(genius_id) or {}).get("media") or []:
            if media.get("provider") == "youtube":
                video_id = youtube_id(media.get("url"))
                if video_id:
                    add(video_id, recording_id, "Genius")
    if use_wikidata:
        for row in wikidata_videos():
            recording_id = by_genius.get(row["genius"] or "") or by_mb.get(row["musicbrainz"] or "")
            if recording_id is None:  # by title: only a song whose artist is one of the video's performers
                candidates = sorted(c for c in by_title.get(normalized_title(row["label"]), []) if c[2] in row["performers"])
                if len(candidates) == 1 or (len(candidates) > 1 and candidates[0][0] != candidates[1][0]):
                    recording_id = candidates[0][1]  # one song, or the only released one
            if recording_id is not None:
                add(row["youtube_id"], recording_id, "Wikidata")
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description="YouTube videos of catalogued songs.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.execute("PRAGMA foreign_keys = ON")
    oembed = OEmbed()
    try:
        found = plan(connection, oembed)
        oembed.save()
        kinds = {}
        for video in found.values():
            kinds[video["kind"]] = kinds.get(video["kind"], 0) + 1
        print(f"Videos: {len(found)} {kinds}; official channels: {sum(v['official'] for v in found.values())}")
        for video_id, video in list(found.items())[:15]:
            print(f"  {video['kind']:<12} {'official' if video['official'] else 'other   '} {video['channel']}: {video['title']}")
        if not args.apply:
            print("Dry run; --apply stores them (back up first).")
            return
        with connection:  # the table is rebuilt from the sources each time
            connection.execute("DELETE FROM video_recordings")
            connection.execute("DELETE FROM videos")
            for video_id, video in found.items():
                connection.execute("""
                    INSERT INTO videos (youtube_id, title, channel, kind, official, source) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT (youtube_id) DO UPDATE SET title = excluded.title, channel = excluded.channel,
                        official = excluded.official""",
                    (video_id, video["title"], video["channel"], video["kind"], int(video["official"]), video["source"]))
                row_id = connection.execute("SELECT id FROM videos WHERE youtube_id = ?", (video_id,)).fetchone()[0]
                for recording_id in video["songs"]:
                    connection.execute("INSERT OR IGNORE INTO video_recordings (video_id, recording_id) VALUES (?, ?)",
                                       (row_id, recording_id))
        print(f"Stored. Foreign keys: {connection.execute('PRAGMA foreign_key_check').fetchall()[:3]}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
