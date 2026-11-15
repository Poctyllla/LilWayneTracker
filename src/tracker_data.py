"""Read-only queries for the web tracker (tracker_server.py).

Everything here only reads SQLite.  What the player can play per version
(Spotify, Apple Music, the owner's own files on the local server only) comes
from audio_providers.py.
"""

import functools
import json
import re
import sqlite3
from pathlib import Path

import eras
from import_release import DATABASE_PATH
# Playback sources live in audio_providers.py (names kept here for callers).
from audio_providers import AUDIO_TYPES, audio_file, playables, song_play  # noqa: F401


def connect(path=None):
    connection = sqlite3.connect(f"file:{path or DATABASE_PATH}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def summary(connection):
    """Totals, statuses and eras with song counts (own songs and guest songs)."""
    status = {row["availability"]: row["n"] for row in connection.execute("""
        SELECT a.availability, count(*) AS n FROM recording_availability a
        JOIN recordings r ON r.id = a.recording_id WHERE r.appearance IS NULL GROUP BY 1""")}
    era_columns = {row[1] for row in connection.execute("PRAGMA table_info(eras)")}
    extra = ", ".join(f"e.{column}" if column in era_columns else f"NULL AS {column}"
                      for column in ("summary", "summary_url", "cover_file"))
    eras = [dict(row) for row in connection.execute(f"""
        SELECT e.id, e.name, e.start_date, e.end_date, {extra},
               sum(r.appearance IS NULL) AS songs, sum(r.appearance = 'guest') AS guest_songs
        FROM eras e LEFT JOIN recordings r ON r.era_id = e.id
        GROUP BY e.id ORDER BY COALESCE(e.end_date, '9999')""")]
    counts = dict(connection.execute("""
        SELECT 'releases', count(*) FROM releases UNION ALL
        SELECT 'songs', count(*) FROM recordings WHERE appearance IS NULL UNION ALL
        SELECT 'guest_songs', count(*) FROM recordings WHERE appearance = 'guest' UNION ALL
        SELECT 'versions', count(*) FROM versions UNION ALL
        SELECT 'credits', count(*) FROM credits UNION ALL
        SELECT 'files', count(*) FROM files""").fetchall())
    return {"counts": counts, "status": status, "eras": eras}


def songs(connection):
    """Compact rows of all songs (the page filters them in the browser)."""
    versions, files, first_dates, releases = {}, set(), {}, {}
    for recording_id, version_type in connection.execute("SELECT recording_id, version_type FROM versions"):
        versions.setdefault(recording_id, []).append(version_type)
    for (recording_id,) in connection.execute(
            "SELECT DISTINCT v.recording_id FROM files f JOIN versions v ON v.id = f.version_id"):
        files.add(recording_id)
    for recording_id, date, count in connection.execute("""
        SELECT v.recording_id, min(r.release_date), count(DISTINCT r.id) FROM versions v
        JOIN release_tracks rt ON rt.version_id = v.id JOIN releases r ON r.id = rt.release_id
        GROUP BY v.recording_id"""):
        first_dates[recording_id], releases[recording_id] = date, count
    aliases = {}
    for recording_id, title in connection.execute("SELECT recording_id, title FROM recording_aliases"):
        aliases.setdefault(recording_id, []).append(title)
    covers = song_covers(connection)
    playable = playables(connection)
    play = song_play(connection, playable)
    # the other versions (live, demo, remix ...): the Versions filter lists them one by one
    variants = {}
    for version_id, recording_id, version_type, name, date, status, release_id, release_title, has_file in connection.execute("""
        WITH first_release AS (
            SELECT rt.version_id, r.id, r.title, r.release_date,
                   row_number() OVER (PARTITION BY rt.version_id
                                      ORDER BY r.release_date IS NULL, r.release_date, r.id) AS n
            FROM release_tracks rt JOIN releases r ON r.id = rt.release_id)
        SELECT v.id, v.recording_id, v.version_type, v.version_name,
               COALESCE(fr.release_date, v.first_known_date), va.availability, fr.id, fr.title,
               EXISTS (SELECT 1 FROM files f WHERE f.version_id = v.id)
        FROM versions v JOIN version_availability va ON va.version_id = v.id
        LEFT JOIN first_release fr ON fr.version_id = v.id AND fr.n = 1
        WHERE v.version_type != 'album' ORDER BY v.id"""):
        variants.setdefault(recording_id, []).append({
            "id": version_id, "type": version_type, "name": name, "date": date, "status": status,
            "album": {"id": release_id, "title": release_title} if release_id else None, "file": bool(has_file),
            "play": {"v": version_id, **playable[version_id]} if version_id in playable else None})
    # "wanted": known from snippets only -- no other version leaked or released (the trackers' grails)
    heard = {}
    for recording_id, version_type, availability in connection.execute(
            "SELECT v.recording_id, v.version_type, va.availability FROM versions v "
            "JOIN version_availability va ON va.version_id = v.id"):
        entry = heard.setdefault(recording_id, {"snippet": False, "full": False})
        if version_type == "snippet":
            entry["snippet"] = True
        elif availability in ("leaked", "released"):
            entry["full"] = True
    albums = song_albums(connection)
    credits = release_credits(connection)
    producers = {}
    for recording_id, name in connection.execute("""
        SELECT DISTINCT COALESCE(c.recording_id, v.recording_id), a.name FROM credits c
        JOIN artists a ON a.id = c.artist_id LEFT JOIN versions v ON v.id = c.version_id
        WHERE c.role = 'producer' AND (c.recording_id IS NOT NULL OR v.version_type = 'album')
        ORDER BY a.name"""):
        producers.setdefault(recording_id, []).append(name)
    rows = []
    for row in connection.execute("""
        SELECT r.id, r.title, a.name AS artist, r.appearance, r.era_id, r.recording_date,
               av.availability, r.genius_song_id, r.first_known_date, r.leak_date
        FROM recordings r JOIN artists a ON a.id = r.artist_id
        JOIN recording_availability av ON av.recording_id = r.id ORDER BY r.title COLLATE NOCASE"""):
        types = versions.get(row["id"], [])
        rows.append({
            "id": row["id"], "title": row["title"], "artist": row["artist"],
            "guest": row["appearance"] == "guest", "era": row["era_id"], "status": row["availability"],
            # first release, else the recording / first-known / leak date (songs known from sources only)
            "date": first_dates.get(row["id"]) or row["recording_date"] or row["first_known_date"] or row["leak_date"],
            "versions": len(types), "types": sorted({t for t in types if t and t != "album"}),
            "variants": variants.get(row["id"], []),
            "wanted": bool(heard.get(row["id"], {}).get("snippet") and not heard[row["id"]]["full"]),
            "releases": releases.get(row["id"], 0), "file": row["id"] in files,
            "aliases": aliases.get(row["id"], []), "producers": producers.get(row["id"], []),
            "cover": covers.get(row["id"]), "play": play.get(row["id"]), "album": albums.get(row["id"]),
            "credit": None if row["appearance"] == "guest" or not albums.get(row["id"])
            else credits.get(albums[row["id"]]["id"]),
        })
    return rows


def has_table(connection, name):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


PACKAGES_FOLDER = Path(__file__).resolve().parent.parent / "data" / "release_groups"


@functools.lru_cache(maxsize=1)
def edition_labels(folder=PACKAGES_FOLDER):
    """{MusicBrainz release ID: edition label} from the release group packages
    ("Additional: US — Official — 2008-06-10 — Target limited edition" ->
    "Target limited edition").  Read once; empty without packages."""
    labels = {}
    for path in Path(folder).glob("**/*.json"):
        try:
            package = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for edition in [package.get("canonical_edition") or {}] + list(package.get("additional_editions") or []):
            release_id = (edition.get("release") or {}).get("musicbrainz_release_id")
            name = edition.get("edition_name") or ""
            label = name.rsplit(" — ", 1)[1].strip() if " — " in name else ""
            # keep what tells editions apart (deluxe, bonus, Target ...), not format markers
            parts = [part.strip() for part in label.split(",")
                     if part.strip() and part.strip().casefold() not in ("explicit", "clean", "official")
                     and not any(word in part.casefold() for word in ("bit", "khz"))
                     and not part.strip().replace("-", "").isdigit()]
            if release_id and parts:
                labels[release_id] = ", ".join(parts)
    return labels


FEATURING_SPLIT = re.compile(r"\s+(?:feat\.?|featuring|ft\.)\s+", re.IGNORECASE)
SOLO_NAMES = ("lil wayne", "lil' wayne")


@functools.lru_cache(maxsize=1)
def credit_names(folder=PACKAGES_FOLDER):
    """{MusicBrainz release ID: main artist credit} of releases not credited to Lil Wayne
    alone ("Hot Boy$", "Birdman & Lil Wayne", "Young Money"); featured artists are cut off."""
    names = {}
    for path in Path(folder).glob("**/*.json"):
        try:
            package = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for edition in [package.get("canonical_edition") or {}] + list(package.get("additional_editions") or []):
            release = edition.get("release") or {}
            credit = FEATURING_SPLIT.split(str(release.get("artist_credit") or ""))[0].strip()
            if release.get("musicbrainz_release_id") and credit and credit.casefold() not in SOLO_NAMES:
                names[release["musicbrainz_release_id"]] = credit
    return names


def release_credits(connection):
    """{release_id: main artist credit} for releases not credited to Lil Wayne alone.
    A collaboration album is his by definition (owner's decision: ColleGrove = 2 Chainz &
    Lil Wayne although MusicBrainz credits 2 Chainz only)."""
    names = credit_names()
    credits = {}
    for release_id, mbid, kind in connection.execute(
            "SELECT id, musicbrainz_release_id, release_type FROM releases WHERE musicbrainz_release_id IS NOT NULL"):
        credit = names.get(mbid)
        if credit and kind == "collaboration_album" and "lil wayne" not in credit.casefold():
            credit = f"{credit} & Lil Wayne"
        if credit:
            credits[release_id] = credit
    return credits


ARTIST_IMAGES_PATH = Path(__file__).resolve().parent.parent / "data" / "artist_images.json"


def artist_images():
    """{artist_id: image file under web/covers/} from people_images.py (empty without it)."""
    try:
        return {int(k): v["file"] for k, v in json.loads(ARTIST_IMAGES_PATH.read_text(encoding="utf-8")).items()}
    except (OSError, ValueError):
        return {}


def has_column(connection, table, column):
    return any(row[1] == column for row in connection.execute(f"PRAGMA table_info({table})"))


SONG_IMAGES_PATH = Path(__file__).resolve().parent.parent / "data" / "song_images.json"


def song_images():
    """{recording_id: image file under web/covers/} -- original covers of guest songs (song_images.py)."""
    try:
        return {int(k): v["file"] for k, v in json.loads(SONG_IMAGES_PATH.read_text(encoding="utf-8")).items()}
    except (OSError, ValueError):
        return {}


def song_covers(connection):
    """{recording_id: cover file} -- the cover of the release a song came out on:
    the earliest official release with a cover, else any release with a cover; a guest
    song (not on his releases) has the original cover of its own release (song_images.py)."""
    if not has_column(connection, "releases", "cover_file"):
        return {}
    covers = dict(song_images())
    for recording_id, cover in connection.execute("""
        SELECT v.recording_id, r.cover_file FROM versions v
        JOIN release_tracks rt ON rt.version_id = v.id JOIN releases r ON r.id = rt.release_id
        WHERE r.cover_file IS NOT NULL
        ORDER BY r.discography = 'official' DESC, v.version_type = 'album' DESC,
                 COALESCE(r.release_date, '9999'), r.id"""):
        covers.setdefault(recording_id, cover)
    return covers


# The project a song is known from: albums / mixtapes before singles.
TYPE_RANK = ("studio_album", "collaboration_album", "group_album", "mixtape", "ep", "compilation",
             "remix", "bootleg", "other", "single", "group_single")


def song_albums(connection):
    """{recording_id: {"id": release id, "title": ...}} -- the release a song is known
    from: official before unofficial, then albums / mixtapes before singles, then the earliest."""
    rank = " ".join(f"WHEN '{t}' THEN {i}" for i, t in enumerate(TYPE_RANK))
    albums = {}
    for recording_id, release_id, title in connection.execute(f"""
        SELECT v.recording_id, r.id, r.title FROM versions v
        JOIN release_tracks rt ON rt.version_id = v.id JOIN releases r ON r.id = rt.release_id
        ORDER BY r.discography = 'official' DESC, CASE r.release_type {rank} ELSE 99 END,
                 v.version_type = 'album' DESC, COALESCE(r.release_date, '9999'), r.id"""):
        albums.setdefault(recording_id, {"id": release_id, "title": title})
    return albums


def listen_links(connection, release_ids):
    """[{service, url}] of the given releases (one per service, earliest release first)."""
    if not release_ids or not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'release_links'").fetchone():
        return []
    marks = ",".join("?" * len(release_ids))
    best = {}
    for service, url in connection.execute(f"""
        SELECT l.service, l.url FROM release_links l JOIN releases r ON r.id = l.release_id
        WHERE l.release_id IN ({marks}) ORDER BY COALESCE(r.release_date, '9999'), r.id, l.id""",
                                           list(release_ids)):
        best.setdefault(service, url)
    return [{"service": service, "url": url} for service, url in best.items()]


def artist(connection, artist_id):
    """One person / artist: every song they are credited on, with their roles."""
    head = connection.execute("SELECT id, name FROM artists WHERE id = ?", (artist_id,)).fetchone()
    if head is None:
        return None
    songs_by_id = {}
    for row in connection.execute("""
        SELECT r.id, r.title, a.name AS artist, r.appearance, e.name AS era, av.availability, c.role
        FROM credits c LEFT JOIN versions v ON v.id = c.version_id
        JOIN recordings r ON r.id = COALESCE(c.recording_id, v.recording_id)
        JOIN artists a ON a.id = r.artist_id JOIN recording_availability av ON av.recording_id = r.id
        LEFT JOIN eras e ON e.id = r.era_id
        WHERE c.artist_id = ?
        UNION
        SELECT r.id, r.title, a.name, r.appearance, e.name, av.availability, 'main_artist'
        FROM recordings r JOIN artists a ON a.id = r.artist_id
        JOIN recording_availability av ON av.recording_id = r.id LEFT JOIN eras e ON e.id = r.era_id
        WHERE r.artist_id = ? AND r.artist_id != (SELECT id FROM artists WHERE name = 'Lil Wayne')
        """, (artist_id, artist_id)):
        song = songs_by_id.setdefault(row["id"], {
            "id": row["id"], "title": row["title"], "artist": row["artist"], "guest": row["appearance"] == "guest",
            "era": row["era"], "status": row["availability"], "roles": []})
        if row["role"] not in song["roles"]:
            song["roles"].append(row["role"])
    role_counts = {}
    for song in songs_by_id.values():
        for role in song["roles"]:
            role_counts[role] = role_counts.get(role, 0) + 1
    return {"id": head["id"], "name": head["name"], "roles": role_counts, "image": artist_images().get(head["id"]),
            "songs": sorted(songs_by_id.values(), key=lambda s: s["title"].casefold())}


def people(connection, limit=300):
    """The most credited people (not Lil Wayne himself) with their main roles."""
    rows = connection.execute("""
        SELECT a.id, a.name, count(DISTINCT COALESCE(c.recording_id, v.recording_id)) AS songs,
               group_concat(DISTINCT c.role) AS roles
        FROM credits c JOIN artists a ON a.id = c.artist_id LEFT JOIN versions v ON v.id = c.version_id
        WHERE a.name != 'Lil Wayne'
        GROUP BY a.id ORDER BY songs DESC, a.name LIMIT ?""", (limit,)).fetchall()
    years = song_years(connection)
    span = {}
    for artist_id, recording_id in connection.execute("""
        SELECT DISTINCT c.artist_id, COALESCE(c.recording_id, v.recording_id)
        FROM credits c LEFT JOIN versions v ON v.id = c.version_id"""):
        if recording_id in years:
            first, last = span.get(artist_id, (years[recording_id], years[recording_id]))
            span[artist_id] = (min(first, years[recording_id]), max(last, years[recording_id]))
    images = artist_images()
    return [{"id": row["id"], "name": row["name"], "songs": row["songs"],
             "roles": sorted(set((row["roles"] or "").split(",")) - {""}),
             "years": list(span.get(row["id"], ())), "image": images.get(row["id"])} for row in rows]


NETWORK_GROUPS = {"artist": ("main_artist", "featured_artist"), "producer": ("producer", "co_producer")}


def network(connection, limit=60):
    """Lil Wayne's collaborators for the network view: per group (artists he made songs
    with, producers) the people with the most shared songs, overall and per era."""
    images = artist_images()
    groups = []
    for group, roles in NETWORK_GROUPS.items():
        marks = ",".join("?" * len(roles))
        counts = {}
        for artist_id, name, era_id, songs in connection.execute(f"""
            SELECT a.id, a.name, r.era_id, count(DISTINCT r.id)
            FROM credits c JOIN artists a ON a.id = c.artist_id
            LEFT JOIN versions v ON v.id = c.version_id
            JOIN recordings r ON r.id = COALESCE(c.recording_id, v.recording_id)
            WHERE c.role IN ({marks}) AND a.name != 'Lil Wayne'
            GROUP BY a.id, r.era_id""", roles):
            person = counts.setdefault(artist_id, {"id": artist_id, "name": name, "songs": 0, "eras": {},
                                                   "image": images.get(artist_id)})
            person["songs"] += songs
            if era_id is not None:
                person["eras"][str(era_id)] = songs
        top = sorted(counts.values(), key=lambda p: (-p["songs"], p["name"]))[:limit]
        groups.append({"group": group, "people": top})
    return {"groups": groups}


def song_years(connection):
    """{recording_id: 'YYYY'} -- the year a song first appeared (earliest release,
    else its recording / first-known date)."""
    years = {}
    for recording_id, year in connection.execute("""
        SELECT v.recording_id, min(substr(x.release_date, 1, 4)) FROM versions v
        JOIN release_tracks rt ON rt.version_id = v.id JOIN releases x ON x.id = rt.release_id
        WHERE length(x.release_date) >= 4 GROUP BY v.recording_id"""):
        years[recording_id] = year
    for recording_id, date in connection.execute(
            "SELECT id, COALESCE(recording_date, first_known_date, leak_date) AS d FROM recordings "
            "WHERE length(COALESCE(recording_date, first_known_date, leak_date)) >= 4"):
        years.setdefault(recording_id, date[:4])
    return years


def song(connection, recording_id):
    """Everything about one song: versions with their releases, credits, aliases, sources."""
    head = connection.execute("""
        SELECT r.id, r.title, a.name AS artist, r.appearance, r.recording_date, r.leak_date,
               r.musicbrainz_recording_id, r.genius_song_id, r.era_source, e.name AS era,
               av.availability
        FROM recordings r JOIN artists a ON a.id = r.artist_id
        JOIN recording_availability av ON av.recording_id = r.id
        LEFT JOIN eras e ON e.id = r.era_id WHERE r.id = ?""", (recording_id,)).fetchone()
    if head is None:
        return None
    result = dict(head)
    result["versions"] = []
    play = playables(connection)
    for version in connection.execute("""
        SELECT v.id, v.version_name, v.version_type, v.first_known_date, v.musicbrainz_recording_id,
               v.genius_song_id, va.availability
        FROM versions v JOIN version_availability va ON va.version_id = v.id
        WHERE v.recording_id = ? ORDER BY v.version_type != 'album', v.id""", (recording_id,)):
        item = dict(version)
        cover = "r.cover_file" if has_column(connection, "releases", "cover_file") else "NULL"
        item["tracks"] = [dict(row) for row in connection.execute(f"""
            SELECT r.id AS release_id, r.title AS release, r.release_type, r.release_date, r.discography,
                   {cover} AS cover_file, rt.disc_number, rt.track_number, rt.track_title, rt.duration_ms,
                   COALESCE(r.musicbrainz_release_group_id, 'release:' || r.id) AS group_id,
                   (SELECT count(*) FROM release_tracks n WHERE n.release_id = r.id) AS release_tracks,
                   r.musicbrainz_release_id
            FROM release_tracks rt JOIN releases r ON r.id = rt.release_id
            WHERE rt.version_id = ? ORDER BY r.release_date, r.id""", (version["id"],))]
        for track in item["tracks"]:  # edition label from the packages (e.g. "Target limited edition")
            track["edition"] = edition_labels().get(track.pop("musicbrainz_release_id"))
        item["files"] = connection.execute("SELECT count(*) FROM files WHERE version_id = ?",
                                           (version["id"],)).fetchone()[0]
        item["links"] = [dict(row) for row in connection.execute(
            "SELECT service, url FROM version_links WHERE version_id = ? ORDER BY id", (version["id"],))] \
            if has_table(connection, "version_links") else []
        item["play"] = play.get(version["id"])
        item["quality"] = file_quality(connection, version["id"])
        result["versions"].append(item)
    credits = {}
    for row in connection.execute("""
        SELECT c.role, a.id AS artist_id, a.name, c.source, v.version_name
        FROM credits c JOIN artists a ON a.id = c.artist_id LEFT JOIN versions v ON v.id = c.version_id
        WHERE c.recording_id = ? OR v.recording_id = ?
        ORDER BY c.role, a.name""", (recording_id, recording_id)):
        credits.setdefault(row["role"], []).append(
            {"id": row["artist_id"], "name": row["name"], "source": row["source"], "version": row["version_name"]})
    result["artist_id"] = connection.execute("SELECT artist_id FROM recordings WHERE id = ?",
                                             (recording_id,)).fetchone()[0]
    result["credits"] = credits
    result["aliases"] = [row[0] for row in connection.execute(
        "SELECT title FROM recording_aliases WHERE recording_id = ?", (recording_id,))]
    result["sources"] = [dict(row) for row in connection.execute("""
        SELECT DISTINCT s.name, s.url, rs.notes, v.version_name AS version FROM recording_sources rs
        JOIN sources s ON s.id = rs.source_id LEFT JOIN versions v ON v.id = rs.version_id
        WHERE rs.recording_id = ? OR v.recording_id = ? ORDER BY s.name""", (recording_id, recording_id))]
    result["musicbrainz_alternates"] = [row[0] for row in connection.execute(
        "SELECT musicbrainz_recording_id FROM recording_musicbrainz_ids WHERE recording_id = ?", (recording_id,))]
    official = {t["release_id"] for v in result["versions"] for t in v["tracks"] if t["discography"] == "official"}
    listen = {link["service"]: link["url"] for link in listen_links(connection, sorted(official))}
    for version in result["versions"]:  # a track link (main version first) beats the album link
        if version["version_type"] == "album" or not any(v["version_type"] == "album" for v in result["versions"]):
            for link in version["links"]:
                if listen.get(link["service"]) is None or "/album/" in listen[link["service"]]:
                    listen[link["service"]] = link["url"]
            break
    result["listen"] = [{"service": service, "url": url} for service, url in listen.items()]
    result["first_known_date"] = connection.execute(
        "SELECT first_known_date FROM recordings WHERE id = ?", (recording_id,)).fetchone()[0] \
        if has_column(connection, "recordings", "first_known_date") else None
    result["genius_album"] = genius_album(result) if not any(v["tracks"] for v in result["versions"]) else None
    result["history"] = song_history(result)
    result["former"] = former(result)
    result["connections"] = song_connections(connection, result)
    result["videos"] = videos(connection, recording_id)
    result["charts"], result["certifications"] = chart_data(connection, "recording_id", [recording_id])
    result["studios"] = [dict(row) for row in connection.execute(
        "SELECT kind, studio FROM song_studios WHERE recording_id = ? ORDER BY CASE kind WHEN 'recorded' THEN 0 "
        "WHEN 'mixed' THEN 1 ELSE 2 END, studio", (recording_id,))] if has_table(connection, "song_studios") else []
    result["cover_file"] = song_covers_for(connection, recording_id)
    album = song_albums(connection).get(recording_id) if head["appearance"] != "guest" else None
    result["credit"] = release_credits(connection).get(album["id"]) if album else None
    return result


def file_quality(connection, version_id):
    """How good the best local file of a version is (the trackers' "Quality" column):
    lossless, high (>= 256 kbps), medium (>= 160 kbps) or low; None without a file."""
    best = None
    for fmt, bitrate in connection.execute("SELECT format, bitrate FROM files WHERE version_id = ?", (version_id,)):
        fmt = (fmt or "").lower().lstrip(".")
        if fmt in ("flac", "wav", "alac", "aiff"):
            rank = (3, "lossless")
        elif not bitrate:
            rank = (-1, None)
        else:
            rank = (2, "high") if bitrate >= 256000 else (1, "medium") if bitrate >= 160000 else (0, "low")
        best = max(best or rank, rank)
    return best[1] if best else None


HISTORY_ORDER = {"recorded": 0, "first_known": 1, "leaked": 2, "version": 3, "bootleg": 4, "released": 5, "title": 6}


def song_history(song):
    """The life of a song as dated events: recorded, first known, leaked, each version's
    first appearance, the first official release, other titles it was printed under."""
    events = []

    def add(date, kind, text, version=None):
        events.append({"date": date, "kind": kind, "text": text, "version": version})

    if song.get("recording_date"):
        add(song["recording_date"], "recorded", "Recorded")
    album = song.get("genius_album")
    if song.get("first_known_date") and album and song.get("availability") == "released":
        # released on another artist's album that is not a release of this catalogue
        add(song["first_known_date"], "released",
            f"Released on {album['name']}" + (f" by {album['artist']}" if album.get("artist") else ""))
    elif song.get("first_known_date"):
        source = next((s["name"] for s in song.get("sources", [])
                       if (s.get("notes") or "").startswith("First known date")), None)
        add(song["first_known_date"], "first_known", "First known" + (f" ({source})" if source else ""))
    if song.get("leak_date"):
        add(song["leak_date"], "leaked", "Leaked")
    from titles import normalized_title
    from version_types import split_version
    titles = {normalized_title(song["title"])}
    for version in song["versions"]:
        name = None if version["version_type"] == "album" else (version["version_name"] or version["version_type"])
        tracks = sorted((t for t in version["tracks"] if t.get("release_date")), key=lambda t: t["release_date"])
        official = next((t for t in tracks if t["discography"] == "official"), None)
        unofficial = next((t for t in tracks if t["discography"] != "official"), None)
        if version.get("first_known_date"):
            add(version["first_known_date"], "version", f"{name or 'Main version'} first known", name)
        if unofficial and (not official or unofficial["release_date"] < official["release_date"]):
            add(unofficial["release_date"], "bootleg",
                f"{name + ' on' if name else 'On'} the bootleg {unofficial['release']}", name)
        if official:
            add(official["release_date"], "released",
                f"{name + ' released' if name else 'Released'} on {official['release']}", name)
        for track in tracks:  # other titles the song was printed under (not "22. A MILLI (LIVE)")
            title = re.sub(r"^\s*\d{1,2}\s*[.)-]\s*", "", track.get("track_title") or "")
            key = normalized_title(split_version(title)[0]) if title else None
            if key and key not in titles:
                titles.add(key)
                title = split_version(title)[0]
                add(track["release_date"], "title", f"Titled \u201c{title}\u201d on {track['release']}", name)

    def order(event):
        period = eras.date_range(event["date"])
        return (period[0] if period else "9999", HISTORY_ORDER[event["kind"]])
    return sorted(events, key=order)


def former(song):
    """{"known": date, "released": date} when the song was known (a leak, a bootleg, a
    source) before its first official release -- the trackers' "Former" tag; else None."""
    official = [t["release_date"] for v in song["versions"] for t in v["tracks"]
                if t["discography"] == "official" and eras.date_range(t.get("release_date"))]
    if not official:
        return None
    released = min(official, key=lambda d: eras.date_range(d)[0])
    candidates = [song.get("leak_date"), song.get("first_known_date")]
    candidates += [v.get("first_known_date") for v in song["versions"]]
    candidates += [t["release_date"] for v in song["versions"] for t in v["tracks"] if t["discography"] != "official"]
    earlier = [d for d in candidates if eras.date_range(d) and eras.date_range(d)[1] < eras.date_range(released)[0]]
    if not earlier:
        return None
    return {"known": min(earlier, key=lambda d: eras.date_range(d)[0]), "released": released}


CONNECTION_LABELS = {"samples": "Samples", "sampled_in": "Sampled in", "interpolates": "Interpolates",
                     "interpolated_by": "Interpolated by", "cover_of": "Cover of", "covered_by": "Covered by",
                     "remix_of": "Remix of", "remixed_by": "Remixed by"}


@functools.lru_cache(maxsize=1)
def _genius_cache(modified):
    from genius_details import load_cache
    return load_cache()


def genius_cache():
    """exports/genius_song_details.json (genius_details.py), read again when it changes."""
    from genius_details import CACHE_PATH
    return _genius_cache(CACHE_PATH.stat().st_mtime if CACHE_PATH.exists() else 0)


@functools.lru_cache(maxsize=1)
def _genius_albums(modified):
    from genius_details import load_albums
    return load_albums()


def genius_album(song):
    """The Genius album of a song that is on no catalogued release (most guest
    appearances): name, artist, date and page, so "Appears on" is not empty."""
    from genius_details import ALBUMS_PATH
    albums = _genius_albums(ALBUMS_PATH.stat().st_mtime if ALBUMS_PATH.exists() else 0)
    cache = genius_cache()
    for genius_id in [song.get("genius_song_id")] + [v.get("genius_song_id") for v in song["versions"]]:
        details = cache.get(genius_id) or {}
        album = details.get("album") or {}
        if album.get("id"):
            full = albums.get(album["id"]) or {}
            return {"id": album["id"], "name": album.get("name"), "artist": album.get("artist"),
                    "release_date": album.get("release_date"), "url": full.get("url"),
                    "song_url": details.get("url")}
    return None


def song_connections(connection, song):
    """Samples, interpolations, covers and remixes from Genius (song relationships);
    a related song that is in the catalogue links to it (song_id)."""
    ids = {song.get("genius_song_id")} | {v.get("genius_song_id") for v in song["versions"]}
    ids.discard(None)
    if not ids:
        return []
    cache = genius_cache()
    by_genius = dict(connection.execute("SELECT genius_song_id, id FROM recordings WHERE genius_song_id IS NOT NULL"))
    result = {}
    for genius_id in ids:
        for relation in (cache.get(genius_id) or {}).get("relationships") or []:
            group = result.setdefault(relation["type"], {})
            for other in relation["songs"]:
                group.setdefault(other["id"], {**other, "song_id": by_genius.get(other["id"])})
    order = list(CONNECTION_LABELS)
    return [{"type": kind, "label": CONNECTION_LABELS.get(kind, kind), "songs": list(songs.values())}
            for kind, songs in sorted(result.items(), key=lambda item: order.index(item[0]) if item[0] in order else 99)]


def song_covers_for(connection, recording_id):
    return song_covers(connection).get(recording_id) if has_column(connection, "releases", "cover_file") else None


def first_editions(connection, cover="NULL AS cover_file"):
    """The first edition of every official release group: the earliest full date
    (YYYY-MM-DD); a year / month-only date counts only when the group has no full date."""
    groups = {}
    for row in connection.execute(f"""
            SELECT id, title, release_type, release_date, {cover}, musicbrainz_release_group_id
            FROM releases WHERE discography = 'official' AND COALESCE(release_date, '') != ''"""):
        release = dict(row)
        key = release.pop("musicbrainz_release_group_id") or f"release:{release['id']}"
        groups.setdefault(key, []).append(release)
    firsts = []
    for releases in groups.values():
        full = [r for r in releases if len(r["release_date"]) >= 10]
        first = min(full or releases, key=lambda r: (r["release_date"], r["id"]))
        firsts.append({**first, "editions": len(releases)})
    return sorted(firsts, key=lambda r: (r["release_date"], r["id"]))


SINGLE_TYPES = ("single", "group_single")


def single_album_eras(connection):
    """{release_id: era_id} of singles whose songs (their main versions) are on a solo
    studio album (recordings.era_source = 'album'): the era of that album (the most
    common one when a single carries songs of several albums)."""
    marks = ",".join("?" * len(SINGLE_TYPES))
    counts = {}
    for release_id, era_id, n in connection.execute(f"""
            SELECT rt.release_id, rec.era_id, count(*) FROM release_tracks rt
            JOIN releases r ON r.id = rt.release_id JOIN versions v ON v.id = rt.version_id
            JOIN recordings rec ON rec.id = v.recording_id
            WHERE r.release_type IN ({marks}) AND rec.era_source = 'album' AND rec.era_id IS NOT NULL
              AND v.version_type = 'album'  -- a remix single (A Milli remixes, 2023) is not a single of the album
            GROUP BY 1, 2""", SINGLE_TYPES):
        counts.setdefault(release_id, []).append((n, -era_id, era_id))
    return {release_id: max(items)[2] for release_id, items in counts.items()}


def era(connection, era_id):
    """One era: its text and cover, the official releases of its period, its songs
    (own and guest) and its most credited producers and featured artists."""
    head = connection.execute("SELECT * FROM eras WHERE id = ?", (era_id,)).fetchone()
    if head is None:
        return None
    result = dict(head)
    start, end = result.get("start_date") or "0000", result.get("end_date") or "9999"
    cover = "cover_file" if has_column(connection, "releases", "cover_file") else "NULL AS cover_file"
    # One entry per release group, in the era of its first edition (later editions,
    # deluxe versions and reissues do not move an album into a later era); a single
    # from a solo studio album belongs to that album's era even when it came out later.
    from_album = single_album_eras(connection)
    all_eras = [dict(row) for row in connection.execute("SELECT id, start_date, end_date FROM eras ORDER BY COALESCE(end_date, '9999')")]
    def release_era(release):  # a year-only date: the era covering most of that year (eras.py rule)
        if release["id"] in from_album:
            return from_album[release["id"]]
        index = eras.era_of(all_eras, release["release_date"])
        index = index if index is not None else eras.earliest_era_of(all_eras, release["release_date"])
        return all_eras[index]["id"] if index is not None else None
    result["releases"] = [release for release in first_editions(connection, cover) if release_era(release) == era_id]
    result["songs"] = [dict(row) for row in connection.execute("""
        SELECT r.id, r.title, a.name AS artist, r.appearance = 'guest' AS guest, av.availability AS status
        FROM recordings r JOIN artists a ON a.id = r.artist_id JOIN recording_availability av ON av.recording_id = r.id
        WHERE r.era_id = ? ORDER BY r.title COLLATE NOCASE""", (era_id,))]
    def top(role):
        return [dict(row) for row in connection.execute("""
            SELECT a.id, a.name, count(DISTINCT r.id) AS songs FROM credits c
            JOIN artists a ON a.id = c.artist_id LEFT JOIN versions v ON v.id = c.version_id
            JOIN recordings r ON r.id = COALESCE(c.recording_id, v.recording_id)
            WHERE r.era_id = ? AND c.role = ? AND a.name != 'Lil Wayne'
            GROUP BY a.id ORDER BY songs DESC, a.name LIMIT 8""", (era_id, role))]
    result["producers"], result["featured"] = top("producer"), top("featured_artist")
    return result


def videos(connection, recording_id=None):
    """YouTube videos (videos.py) with their songs; one song's videos with recording_id."""
    if not has_table(connection, "videos"):
        return []
    where, params = ("WHERE v.id IN (SELECT video_id FROM video_recordings WHERE recording_id = ?)", (recording_id,)) \
        if recording_id else ("", ())
    result = []
    for row in connection.execute(f"""
            SELECT v.id, v.youtube_id, v.title, v.channel, v.kind, v.event, v.date, v.official
            FROM videos v {where} ORDER BY v.official DESC, v.id""", params):
        video = dict(row)
        video["official"] = bool(video["official"])
        video["songs"] = [dict(s) for s in connection.execute("""
            SELECT r.id, r.title, r.era_id AS era, a.name AS artist,
                   (SELECT min(x.release_date) FROM versions vv JOIN release_tracks t ON t.version_id = vv.id
                    JOIN releases x ON x.id = t.release_id WHERE vv.recording_id = r.id
                    AND COALESCE(x.release_date, '') != '') AS song_date
            FROM video_recordings vr JOIN recordings r ON r.id = vr.recording_id JOIN artists a ON a.id = r.artist_id
            WHERE vr.video_id = ? ORDER BY r.title""", (video["id"],))]
        video["song_date"] = min((s["song_date"] for s in video["songs"] if s["song_date"]), default=None)
        result.append(video)
    return result


def studios(connection):
    """The studios of the catalogue (studios.py): per kind, the studios with the most songs."""
    if not has_table(connection, "song_studios"):
        return []
    return [dict(row) for row in connection.execute("""
        SELECT kind, studio, count(*) AS songs FROM song_studios GROUP BY kind, studio
        ORDER BY CASE kind WHEN 'recorded' THEN 0 WHEN 'mixed' THEN 1 ELSE 2 END, songs DESC, studio""")]


CHART_ORDER = ["US", "US R&B/HH", "US Rap", "CAN", "UK", "AUS", "NZ", "IRL", "FRA", "SWI"]


def chart_data(connection, column, ids):
    """(charts [{chart, peak}], certifications [{body, level, note}]) of songs / releases (charts_source.py)."""
    if not ids or not has_table(connection, "chart_entries"):
        return [], []
    marks = ",".join("?" * len(ids))
    peaks = {}
    for chart, peak in connection.execute(f"SELECT chart, peak FROM chart_entries WHERE {column} IN ({marks})", list(ids)):
        peaks[chart] = min(peak, peaks.get(chart, peak))
    order = {name: i for i, name in enumerate(CHART_ORDER)}
    charts = [{"chart": c, "peak": p} for c, p in sorted(peaks.items(), key=lambda item: (order.get(item[0], 99), item[0]))]
    certs = [dict(row) for row in connection.execute(
        f"SELECT DISTINCT body, level, note FROM certifications WHERE {column} IN ({marks}) "
        "ORDER BY body != 'RIAA', body, note IS NOT NULL", list(ids))]
    return charts, certs


def charts(connection):
    """The Charts page: every charting / certified song and album."""
    if not has_table(connection, "chart_entries"):
        return {"charts": CHART_ORDER, "songs": [], "albums": []}
    def collect(column, query):
        result = []
        for row in connection.execute(query):
            peaks, certs = chart_data(connection, column, [row["id"]])
            result.append({**dict(row), "peaks": {p["chart"]: p["peak"] for p in peaks}, "certifications": certs})
        return result
    songs = collect("recording_id", """
        SELECT DISTINCT r.id, r.title, a.name AS artist, r.appearance = 'guest' AS guest FROM recordings r
        JOIN artists a ON a.id = r.artist_id
        WHERE r.id IN (SELECT recording_id FROM chart_entries UNION SELECT recording_id FROM certifications)""")
    albums = collect("release_id", """
        SELECT DISTINCT r.id, r.title, r.release_date, r.release_type FROM releases r
        WHERE r.id IN (SELECT release_id FROM chart_entries UNION SELECT release_id FROM certifications)""")
    for song in songs:
        song["guest"] = bool(song["guest"])
    return {"charts": CHART_ORDER, "songs": songs, "albums": albums}


REVIEW_QUEUE_PATH = Path(__file__).resolve().parent.parent / "data" / "reviewed_candidates.json"


def completeness(connection):
    """How complete and checked the catalogue is (the Completeness page): per release
    category and for songs, how many have a date, cover, source, era ... and the open questions."""
    def pct(done, total):
        return {"done": done, "total": total}

    releases = []
    for discography, release_type, total, dated, covered, musicbrainz, linked in connection.execute("""
        SELECT r.discography, r.release_type, count(*),
               sum(COALESCE(r.release_date, '') != ''), sum(r.cover_file IS NOT NULL),
               sum(r.musicbrainz_release_id IS NOT NULL),
               sum(EXISTS (SELECT 1 FROM release_links l WHERE l.release_id = r.id))
        FROM releases r GROUP BY 1, 2 ORDER BY r.discography, count(*) DESC"""):
        releases.append({"discography": discography, "type": release_type, "total": total,
                         "checks": {"date": pct(dated, total), "cover": pct(covered, total),
                                    "musicbrainz": pct(musicbrainz, total),
                                    # bootlegs are never on streaming services
                                    "listen": pct(linked, total) if discography == "official" else None}})
    def ids(sql):
        return {row[0] for row in connection.execute(sql)}

    on_release = """SELECT v.recording_id FROM versions v JOIN release_tracks t ON t.version_id = v.id"""
    has = {
        "era": ids("SELECT id FROM recordings WHERE era_id IS NOT NULL"),
        "date": ids("""SELECT id FROM recordings WHERE recording_date IS NOT NULL OR leak_date IS NOT NULL
                       OR first_known_date IS NOT NULL
                       UNION SELECT recording_id FROM versions WHERE first_known_date IS NOT NULL
                       UNION SELECT v.recording_id FROM versions v JOIN release_tracks t ON t.version_id = v.id
                       JOIN releases r ON r.id = t.release_id WHERE COALESCE(r.release_date, '') != ''"""),
        "source": ids(f"""SELECT id FROM recordings WHERE musicbrainz_recording_id IS NOT NULL OR genius_song_id IS NOT NULL
                          UNION SELECT recording_id FROM recording_sources WHERE recording_id IS NOT NULL
                          UNION {on_release}"""),
        "producer": ids("""SELECT recording_id FROM credits WHERE role = 'producer' AND recording_id IS NOT NULL
                           UNION SELECT v.recording_id FROM credits c JOIN versions v ON v.id = c.version_id
                           WHERE c.role = 'producer'"""),
        "listen": ids("""SELECT v.recording_id FROM versions v JOIN version_links l ON l.version_id = v.id
                         UNION SELECT v.recording_id FROM versions v JOIN release_tracks t ON t.version_id = v.id
                         JOIN release_links l ON l.release_id = t.release_id"""),
    }
    groups = {}
    for recording_id, appearance in connection.execute("SELECT id, appearance FROM recordings"):
        groups.setdefault("guest" if appearance == "guest" else "own", []).append(recording_id)
    songs = [{"kind": kind, "total": len(members),
              "checks": {key: pct(sum(1 for m in members if m in found), len(members)) for key, found in has.items()}}
             for kind, members in sorted(groups.items(), key=lambda item: item[0] != "own")]
    from titles import normalized_title
    seen = {}
    for recording_id, title, appearance in connection.execute("SELECT id, title, appearance FROM recordings"):
        seen.setdefault((normalized_title(title), appearance or "own"), []).append(recording_id)
    duplicates = sum(1 for ids in seen.values() if len(ids) > 1)
    pending = 0
    if REVIEW_QUEUE_PATH.exists():
        queue = json.loads(REVIEW_QUEUE_PATH.read_text(encoding="utf-8"))
        pending = sum(1 for c in queue.get("candidates", []) if c.get("decision") == "pending")
    return {
        "releases": releases, "songs": songs,
        "open": [
            {"key": "queue", "label": "MusicBrainz release groups not decided yet", "count": pending},
            {"key": "same_title", "label": "Song titles used by more than one song (possible duplicates)", "count": duplicates},
        ],
    }


def stats(connection):
    """Numbers for the statistics page."""
    status = dict(connection.execute("SELECT recording_id, availability FROM recording_availability"))
    eras = {row["id"]: {"id": row["id"], "name": row["name"], "end_date": row["end_date"],
                        "released": 0, "leaked": 0, "unreleased": 0, "guest": 0}
            for row in connection.execute("SELECT id, name, end_date FROM eras ORDER BY COALESCE(end_date, '9999')")}
    for recording_id, era_id, appearance in connection.execute("SELECT id, era_id, appearance FROM recordings"):
        if era_id in eras:
            eras[era_id]["guest" if appearance == "guest" else status.get(recording_id, "unreleased")] += 1
    by_era = list(eras.values())
    # Year a song first appeared (earliest release it is on), own songs only.
    by_year = {}
    for recording_id, year in connection.execute("""
        SELECT r.id, min(substr(x.release_date, 1, 4)) FROM recordings r
        JOIN versions v ON v.recording_id = r.id JOIN release_tracks rt ON rt.version_id = v.id
        JOIN releases x ON x.id = rt.release_id
        WHERE r.appearance IS NULL AND length(x.release_date) >= 4 GROUP BY r.id"""):
        bucket = by_year.setdefault(year, {"year": year, "released": 0, "leaked": 0})
        if status.get(recording_id) in bucket:
            bucket[status[recording_id]] += 1
    def top(role, limit=12):
        return [dict(row) for row in connection.execute("""
            SELECT a.id, a.name, count(DISTINCT COALESCE(c.recording_id, v.recording_id)) AS songs
            FROM credits c JOIN artists a ON a.id = c.artist_id LEFT JOIN versions v ON v.id = c.version_id
            WHERE c.role = ? AND a.name != 'Lil Wayne' GROUP BY a.id ORDER BY songs DESC, a.name LIMIT ?""",
                                                    (role, limit))]
    versions = dict(connection.execute(
        "SELECT version_type, count(*) FROM versions WHERE version_type != 'album' GROUP BY 1 ORDER BY 2 DESC").fetchall())
    return {"by_era": by_era, "by_year": sorted(by_year.values(), key=lambda b: b["year"]),
            "producers": top("producer"), "featured": top("featured_artist"), "writers": top("writer"),
            "versions": versions}


def timeline(connection):
    """[{year, eras: [...], releases: [...], bootlegs: n, bootleg_list: [...], first_known: n}] by year.

    One entry per official release group (its first edition); bootlegs are
    counted per year; 'first_known' counts versions first known that year
    from sources (Genius dates of demos / snippets ...)."""
    cover = "cover_file" if has_column(connection, "releases", "cover_file") else "NULL AS cover_file"
    years, seen = {}, set()
    first_ids = {release["id"] for release in first_editions(connection)}
    editions = dict(connection.execute("""
        SELECT COALESCE(musicbrainz_release_group_id, 'release:' || id), count(*) FROM releases
        WHERE discography = 'official' GROUP BY 1"""))
    for row in connection.execute(f"""
        SELECT id, title, release_type, release_date, discography, {cover},
               COALESCE(musicbrainz_release_group_id, 'release:' || id) AS group_key
        FROM releases WHERE length(release_date) >= 4 ORDER BY release_date, id"""):
        year = years.setdefault(row["release_date"][:4], {"year": row["release_date"][:4], "eras": [], "releases": [],
                                                           "bootlegs": 0, "bootleg_list": [], "first_known": 0})
        if row["discography"] == "unofficial":
            year["bootlegs"] += 1
            year["bootleg_list"].append({"id": row["id"], "title": row["title"], "date": row["release_date"],
                                         "cover_file": row["cover_file"]})
        elif row["id"] in first_ids and row["group_key"] not in seen:
            # the album's first edition: an exact date before a year-only one (Rebirth 2010-02-02, not "2009")
            seen.add(row["group_key"])
            year["releases"].append({"id": row["id"], "title": row["title"], "type": row["release_type"],
                                     "date": row["release_date"], "cover_file": row["cover_file"],
                                     "editions": editions.get(row["group_key"], 1)})
    for row in connection.execute("SELECT id, name, end_date FROM eras WHERE end_date IS NOT NULL"):
        if row["end_date"][:4] in years:
            years[row["end_date"][:4]]["eras"].append({"id": row["id"], "name": row["name"], "date": row["end_date"]})
    for (year,) in connection.execute("SELECT substr(first_known_date, 1, 4) FROM versions WHERE first_known_date IS NOT NULL"):
        if year in years:
            years[year]["first_known"] += 1
    return [years[y] for y in sorted(years)]


def on_this_day(connection, month_day):
    """Official releases and bootlegs released on this MM-DD in earlier years."""
    cover = "cover_file" if has_column(connection, "releases", "cover_file") else "NULL AS cover_file"
    rows = connection.execute(f"""
        SELECT id, title, release_type, release_date, discography, {cover},
               COALESCE(musicbrainz_release_group_id, 'release:' || id) AS group_key
        FROM releases WHERE length(release_date) = 10 AND substr(release_date, 6, 5) = ?
        ORDER BY release_date, id""", (month_day,)).fetchall()
    seen, result = set(), []
    for row in rows:
        if row["group_key"] in seen:
            continue
        seen.add(row["group_key"])
        result.append({key: row[key] for key in ("id", "title", "release_type", "release_date", "discography", "cover_file")})
    return result


def releases(connection):
    """All releases; `group_id` joins the editions of one album, `edition` names one
    ("Target limited edition", "deluxe edition", ...) where the packages know it."""
    cover = "r.cover_file" if has_column(connection, "releases", "cover_file") else "NULL"
    labels = edition_labels()
    credits = release_credits(connection)
    rows = []
    for row in connection.execute(f"""
            SELECT r.id, r.title, r.release_type, r.release_date, r.discography, {cover} AS cover_file,
                   count(rt.id) AS tracks, COALESCE(r.musicbrainz_release_group_id, 'release:' || r.id) AS group_id,
                   r.musicbrainz_release_id
            FROM releases r LEFT JOIN release_tracks rt ON rt.release_id = r.id
            GROUP BY r.id ORDER BY r.discography, r.release_date, r.title"""):
        item = dict(row)
        item["edition"] = labels.get(item.pop("musicbrainz_release_id"))
        item["artist_credit"] = credits.get(item["id"])
        rows.append(item)
    return rows


def release(connection, release_id):
    head = connection.execute("SELECT * FROM releases WHERE id = ?", (release_id,)).fetchone()
    if head is None:
        return None
    result = dict(head)
    result["tracks"] = [dict(row) for row in connection.execute("""
        SELECT rt.disc_number, rt.track_number, COALESCE(rt.track_title, rec.title) AS title, rt.duration_ms,
               rec.id AS recording_id, v.id AS version_id, v.version_type, v.version_name,
               EXISTS (SELECT 1 FROM files f WHERE f.version_id = v.id) AS file
        FROM release_tracks rt JOIN versions v ON v.id = rt.version_id JOIN recordings rec ON rec.id = v.recording_id
        WHERE rt.release_id = ? ORDER BY rt.disc_number, rt.track_number""", (release_id,))]
    play = playables(connection)
    for track in result["tracks"]:
        track["play"] = play.get(track["version_id"])
    result["listen"] = listen_links(connection, [release_id])
    if not any(l["service"] in ("spotify", "apple_music") for l in result["listen"]) and has_table(connection, "version_links"):
        # a single the stores have no page for: its song's track links (from the album)
        seen = {}
        for service, url in connection.execute("""
                SELECT l.service, l.url FROM release_tracks rt JOIN version_links l ON l.version_id = rt.version_id
                WHERE rt.release_id = ? ORDER BY rt.disc_number, rt.track_number, l.id""", (release_id,)):
            seen.setdefault(service, url)
        if len(result["tracks"]) <= 3:
            have = {l["service"] for l in result["listen"]}
            result["listen"] += [{"service": s, "url": u} for s, u in seen.items() if s not in have]
    result["edition"] = edition_labels().get(result.get("musicbrainz_release_id"))
    result["artist_credit"] = release_credits(connection).get(release_id)
    group = result.get("musicbrainz_release_group_id")
    result["editions"] = [] if not group else [  # the other editions of this album
        {"id": row["id"], "title": row["title"], "release_date": row["release_date"], "tracks": row["tracks"],
         "edition": edition_labels().get(row["musicbrainz_release_id"])}
        for row in connection.execute("""
            SELECT r.id, r.title, r.release_date, r.musicbrainz_release_id,
                   (SELECT count(*) FROM release_tracks t WHERE t.release_id = r.id) AS tracks
            FROM releases r WHERE r.musicbrainz_release_group_id = ? AND r.id != ?
            ORDER BY r.release_date, r.id""", (group, release_id))]
    result["comparison"] = compare_editions(connection, [release_id] + [e["id"] for e in result["editions"]])         if result["editions"] else None
    result["charts"], result["certifications"] = chart_data(
        connection, "release_id", [release_id] + [e["id"] for e in result["editions"]])
    return result


def compare_editions(connection, release_ids):
    """Editions of one album side by side: every song with its position on each edition
    (None: not on it), so added, removed and moved tracks show at a glance."""
    songs, order = {}, []
    for release_id in release_ids:
        for recording_id, title, disc, number in connection.execute("""
            SELECT v.recording_id, r.title, rt.disc_number, rt.track_number
            FROM release_tracks rt JOIN versions v ON v.id = rt.version_id JOIN recordings r ON r.id = v.recording_id
            WHERE rt.release_id = ? ORDER BY rt.disc_number, rt.track_number""", (release_id,)):
            if recording_id not in songs:
                songs[recording_id] = {"id": recording_id, "title": title, "positions": {}}
                order.append(recording_id)
            songs[recording_id]["positions"].setdefault(str(release_id), f"{disc}-{number}" if disc and disc > 1 else str(number))
    rows = [songs[i] for i in order]
    for row in rows:
        row["on_all"] = len(row["positions"]) == len(release_ids)
    return {"releases": release_ids, "songs": rows}
