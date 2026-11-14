"""Read-only queries for the web tracker (tracker_server.py).

Everything here only reads SQLite.  What the player can play per version
(Spotify, Apple Music, the owner's own files on the local server only) comes
from audio_providers.py.
"""

import functools
import json
import sqlite3
from pathlib import Path

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
    play = song_play(connection)
    albums = song_albums(connection)
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
               av.availability, r.genius_song_id
        FROM recordings r JOIN artists a ON a.id = r.artist_id
        JOIN recording_availability av ON av.recording_id = r.id ORDER BY r.title COLLATE NOCASE"""):
        types = versions.get(row["id"], [])
        rows.append({
            "id": row["id"], "title": row["title"], "artist": row["artist"],
            "guest": row["appearance"] == "guest", "era": row["era_id"], "status": row["availability"],
            "date": first_dates.get(row["id"]) or row["recording_date"],
            "versions": len(types), "types": sorted({t for t in types if t and t != "album"}),
            "releases": releases.get(row["id"], 0), "file": row["id"] in files,
            "aliases": aliases.get(row["id"], []), "producers": producers.get(row["id"], []),
            "cover": covers.get(row["id"]), "play": play.get(row["id"]), "album": albums.get(row["id"]),
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


def has_column(connection, table, column):
    return any(row[1] == column for row in connection.execute(f"PRAGMA table_info({table})"))


def song_covers(connection):
    """{recording_id: cover file} -- the cover of the release a song came out on:
    the earliest official release with a cover, else any release with a cover."""
    if not has_column(connection, "releases", "cover_file"):
        return {}
    covers = {}
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
    return {"id": head["id"], "name": head["name"], "roles": role_counts,
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
    return [{"id": row["id"], "name": row["name"], "songs": row["songs"],
             "roles": sorted(set((row["roles"] or "").split(",")) - {""}),
             "years": list(span.get(row["id"], ()))} for row in rows]


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
            "SELECT id, recording_date FROM recordings WHERE length(recording_date) >= 4"):
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
        SELECT DISTINCT s.name, s.url, rs.notes FROM recording_sources rs JOIN sources s ON s.id = rs.source_id
        LEFT JOIN versions v ON v.id = rs.version_id
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
    result["cover_file"] = song_covers_for(connection, recording_id)
    return result


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
        firsts.append(min(full or releases, key=lambda r: (r["release_date"], r["id"])))
    return sorted(firsts, key=lambda r: (r["release_date"], r["id"]))


SINGLE_TYPES = ("single", "group_single")


def single_album_eras(connection):
    """{release_id: era_id} of singles whose songs are on a solo studio album
    (recordings.era_source = 'album'): the era of that album (the most common one
    when a single carries songs of several albums)."""
    marks = ",".join("?" * len(SINGLE_TYPES))
    counts = {}
    for release_id, era_id, n in connection.execute(f"""
            SELECT rt.release_id, rec.era_id, count(*) FROM release_tracks rt
            JOIN releases r ON r.id = rt.release_id JOIN versions v ON v.id = rt.version_id
            JOIN recordings rec ON rec.id = v.recording_id
            WHERE r.release_type IN ({marks}) AND rec.era_source = 'album' AND rec.era_id IS NOT NULL
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
    result["releases"] = [release for release in first_editions(connection, cover)
                          if (from_album[release["id"]] == era_id if release["id"] in from_album
                              else start <= release["release_date"][:10] <= end)]
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
        elif row["group_key"] not in seen:
            seen.add(row["group_key"])
            year["releases"].append({"id": row["id"], "title": row["title"], "type": row["release_type"],
                                     "date": row["release_date"], "cover_file": row["cover_file"]})
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
    rows = []
    for row in connection.execute(f"""
            SELECT r.id, r.title, r.release_type, r.release_date, r.discography, {cover} AS cover_file,
                   count(rt.id) AS tracks, COALESCE(r.musicbrainz_release_group_id, 'release:' || r.id) AS group_id,
                   r.musicbrainz_release_id
            FROM releases r LEFT JOIN release_tracks rt ON rt.release_id = r.id
            GROUP BY r.id ORDER BY r.discography, r.release_date, r.title"""):
        item = dict(row)
        item["edition"] = labels.get(item.pop("musicbrainz_release_id"))
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
    result["edition"] = edition_labels().get(result.get("musicbrainz_release_id"))
    group = result.get("musicbrainz_release_group_id")
    result["editions"] = [] if not group else [  # the other editions of this album
        {"id": row["id"], "title": row["title"], "release_date": row["release_date"], "tracks": row["tracks"],
         "edition": edition_labels().get(row["musicbrainz_release_id"])}
        for row in connection.execute("""
            SELECT r.id, r.title, r.release_date, r.musicbrainz_release_id,
                   (SELECT count(*) FROM release_tracks t WHERE t.release_id = r.id) AS tracks
            FROM releases r WHERE r.musicbrainz_release_group_id = ? AND r.id != ?
            ORDER BY r.release_date, r.id""", (group, release_id))]
    return result
