"""Eras of the tracker, one per solo studio album (user decision, Nov 13, 2026),
preceded by one era "Before Tha Block Is Hot" for everything before the first
solo album's era -- the Hot Boys years (user decision, Nov 15, 2026; it
replaced the two group-album eras of Nov 14).

Era boundaries (user decision, Nov 15, 2026): an album's era begins with the
first single from that album (its earliest full-dated official single whose
main versions are on the album) or with the album itself, whichever is
earlier, and lasts until the next album's era begins; so an era continues
after its album's release until the next project's first single.  The first
era has no start, the latest album's era stays open (no separate "After"
era).  Release date = earliest full date of the album's release group.

A song's era (recordings.era_id, era_source):
  'album'  it is on a solo studio album (any edition, deluxe included):
           the earliest such album -- unless the song came out officially
           (single, mixtape ...) in an earlier era: then that era, 'date'
           (Hot Revolver: 2009 single, on I Am Not a Human Being II in 2013)
  'date'   otherwise the era of its earliest known date: release dates of
           the releases it is on, versions.first_known_date,
           recording_date, leak_date.  A year- or month-only date counts
           only when the whole period lies in one era.
  'year'   a year- or month-only date that crosses an era boundary: the era
           covering most of that period ("2008" -> Tha Carter III, as the
           user decided on Nov 13, 2026; ties go to the earlier era)
  'manual' set by hand; never overwritten.
Songs without any usable date stay without an era.

usage:
    python eras.py            # dry run: eras and assignment counts
    python eras.py --apply    # write eras and recordings.era_id (one transaction)
"""

import argparse
import calendar
import datetime
import re
import sqlite3

from import_release import DATABASE_PATH


FULL_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


ERA_TYPES = ("studio_album", "group_album")


def album_eras(connection):
    """[{name, start_date, end_date, release_group_id}] in release order.

    Solo studio albums, plus the group albums (Hot Boys) released before the
    first solo studio album (user decision Nov 14, 2026: eras before Lil Wayne
    became a solo artist)."""
    albums = {}
    for release_id, title, release_date, group_id, release_type in connection.execute("""
        SELECT id, title, release_date, COALESCE(musicbrainz_release_group_id, 'release:' || id), release_type
        FROM releases WHERE release_type IN ('studio_album', 'group_album') AND discography = 'official'
        ORDER BY id
    """):
        album = albums.setdefault(group_id, {"release_group_id": group_id, "editions": [], "type": release_type})
        if release_date:
            album["editions"].append((release_date, release_id, title))
    for album in albums.values():
        # The first edition with a full date names the era ("I Am Not a Human Being II").
        full = sorted(e for e in album["editions"] if FULL_DATE.match(e[0]))
        first = (full or sorted(album["editions"]) or [(None, None, None)])[0]
        album["release_date"], album["name"] = first[0], first[2]
    ordered = sorted((a for a in albums.values() if a["release_date"] and a["type"] == "studio_album"),
                     key=lambda a: a["release_date"])
    singles = first_singles(connection)
    # The era begins with the album's first single when that came out before the album
    # (never before the previous album's release).
    starts, previous = [], None
    for album in ordered:
        start = album["release_date"]
        # the first single after the previous album (an older song re-released as a single does not count)
        single = next((s for s in singles.get(album["release_group_id"], []) if previous is None or s[0] > previous), None)
        if single and single[0] < start:
            start = single[0]
        starts.append((start, single if start != album["release_date"] else None))
        previous = album["release_date"]
    eras = []
    for index, album in enumerate(ordered):
        start, single = starts[index]
        following = starts[index + 1][0] if index + 1 < len(ordered) else None
        opening = f"from its first single {single[1]} ({single[0]})" if single else f"from its release ({album['release_date']})"
        eras.append({"name": album["name"], "start_date": start,
                     "end_date": _previous_day(following) if following else None,
                     "release_group_id": album["release_group_id"],
                     "description": f"The {album['name']} era: {opening} until the next project's first single."})
    if eras:  # the years before the first solo album (Hot Boys, Cash Money)
        first = eras[0]
        eras.insert(0, {"name": f"Before {first['name']}", "start_date": None, "end_date": _previous_day(first["start_date"]),
                        "release_group_id": None,
                        "description": f"Before {first['name']}: the Hot Boys and early Cash Money years, until "
                                       f"{first['start_date']}."})
    return eras


def first_singles(connection):
    """{album release group: [(date, title), ...] by date} of the full-dated official
    singles whose main versions are on that album (a remix single is not a single of it)."""
    singles = {}
    for group_id, date, title in connection.execute("""
        SELECT COALESCE(a.musicbrainz_release_group_id, 'release:' || a.id), s.release_date, s.title
        FROM releases s JOIN release_tracks st ON st.release_id = s.id JOIN versions sv ON sv.id = st.version_id
        JOIN versions av ON av.recording_id = sv.recording_id AND av.version_type = 'album'
        JOIN release_tracks at ON at.version_id = av.id JOIN releases a ON a.id = at.release_id
        WHERE s.release_type IN ('single', 'group_single') AND s.discography = 'official'
          AND sv.version_type = 'album' AND length(s.release_date) = 10
          AND a.release_type IN ('studio_album', 'group_album') AND a.discography = 'official'"""):
        singles.setdefault(group_id, []).append((date, title))
    return {group: sorted(set(items)) for group, items in singles.items()}


def _previous_day(date_text):
    year, month, day = map(int, date_text.split("-"))
    if day > 1:
        return f"{year:04d}-{month:02d}-{day - 1:02d}"
    if month > 1:
        return f"{year:04d}-{month - 1:02d}-{calendar.monthrange(year, month - 1)[1]:02d}"
    return f"{year - 1:04d}-12-31"


def _next_day(date_text):
    if not date_text or not FULL_DATE.match(date_text):
        return None
    year, month, day = map(int, date_text.split("-"))
    if day < calendar.monthrange(year, month)[1]:
        return f"{year:04d}-{month:02d}-{day + 1:02d}"
    if month < 12:
        return f"{year:04d}-{month + 1:02d}-01"
    return f"{year + 1:04d}-01-01"


def date_range(date_text):
    """('2008-01-01', '2008-12-31') for '2008'; same for 'YYYY-MM'; None if unusable."""
    text = str(date_text or "").strip()
    if FULL_DATE.match(text):
        return text, text
    if re.fullmatch(r"\d{4}-\d{2}", text):
        year, month = map(int, text.split("-"))
        return f"{text}-01", f"{text}-{calendar.monthrange(year, month)[1]:02d}"
    if re.fullmatch(r"\d{4}", text):
        return f"{text}-01-01", f"{text}-12-31"
    return None


def era_of(eras, date_text):
    """Index of the era that contains the whole period of date_text, or None."""
    period = date_range(date_text)
    if period is None:
        return None
    for index, era in enumerate(eras):
        starts = era["start_date"] is None or era["start_date"] <= period[0]
        ends = era["end_date"] is None or period[1] <= era["end_date"]
        if starts and ends:
            return index
    return None  # the period crosses an era boundary


def earliest_era_of(eras, date_text):
    """Index of the era covering most of a period ("2008": Tha Carter II has
    Jan 1 - Apr 27, Tha Carter III Apr 28 - Dec 31 -> Tha Carter III, the user's
    example of Nov 13, 2026); ties go to the earlier era."""
    period = date_range(date_text)
    if period is None:
        return None
    best, best_days = None, 0
    for index, era in enumerate(eras):
        start = max(period[0], era["start_date"] or "0000-01-01")
        end = min(period[1], era["end_date"] or "9999-12-31")
        if start <= end:
            days = (_as_date(end) - _as_date(start)).days + 1
            if days > best_days:
                best, best_days = index, days
    return best


def _as_date(text):
    return datetime.date(*map(int, text.split("-")))


def earliest(dates):
    """The date by which the song surely existed: smallest period end
    (['2008', '2008-06-10'] -> '2008-06-10')."""
    usable = [d for d in dates if date_range(d)]
    return min(usable, key=lambda d: (date_range(d)[1], date_range(d)[0]), default=None)


def plan_assignments(connection, eras):
    """{recording_id: (era index, source)} for recordings that are not set manually."""
    era_by_group = {era["release_group_id"]: index for index, era in enumerate(eras) if era["release_group_id"]}
    album_era = {}
    for recording_id, group_id in connection.execute("""
        SELECT DISTINCT v.recording_id, COALESCE(r.musicbrainz_release_group_id, 'release:' || r.id)
        FROM versions v JOIN release_tracks rt ON rt.version_id = v.id
        JOIN releases r ON r.id = rt.release_id
        WHERE r.release_type IN ('studio_album', 'group_album') AND r.discography = 'official'
    """):
        if group_id in era_by_group:
            album_era[recording_id] = min(album_era.get(recording_id, 99999), era_by_group[group_id])
    official = {}  # dates of official releases (an earlier official release beats the album rule)
    for recording_id, date_text in connection.execute("""
        SELECT v.recording_id, r.release_date FROM versions v
        JOIN release_tracks rt ON rt.version_id = v.id JOIN releases r ON r.id = rt.release_id
        WHERE r.discography = 'official' AND r.release_date IS NOT NULL AND r.release_date != ''"""):
        official.setdefault(recording_id, []).append(date_text)
    dates = {}
    for recording_id, date_text in connection.execute("""
        SELECT v.recording_id, r.release_date FROM versions v
        JOIN release_tracks rt ON rt.version_id = v.id JOIN releases r ON r.id = rt.release_id
        UNION ALL SELECT recording_id, first_known_date FROM versions
        UNION ALL SELECT id, recording_date FROM recordings
        UNION ALL SELECT id, leak_date FROM recordings
    """):
        if date_text:
            dates.setdefault(recording_id, []).append(date_text)
    plan = {}
    for (recording_id, era_source) in connection.execute("SELECT id, era_source FROM recordings"):
        if era_source == "manual":
            continue
        if recording_id in album_era:
            first = earliest(official.get(recording_id, []))
            released = era_of(eras, first) if first else None
            if released is None and first:
                released = earliest_era_of(eras, first)
            if released is not None and released < album_era[recording_id]:
                plan[recording_id] = (released, "date")
            else:
                plan[recording_id] = (album_era[recording_id], "album")
            continue
        date_text = earliest(dates.get(recording_id, []))
        index = era_of(eras, date_text)
        if index is not None:
            plan[recording_id] = (index, "date")
            continue
        index = earliest_era_of(eras, date_text)
        plan[recording_id] = (index, "year") if index is not None else (None, None)
    return plan


def apply(connection, eras, plan):
    """Write eras (by name) and the assignments; caller commits."""
    ids = []
    for era in eras:
        connection.execute("""
            INSERT INTO eras (name, start_date, end_date, description, musicbrainz_release_group_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET start_date = excluded.start_date, end_date = excluded.end_date,
                description = excluded.description,
                musicbrainz_release_group_id = excluded.musicbrainz_release_group_id
        """, (era["name"], era["start_date"], era["end_date"], era["description"], era["release_group_id"]))
        ids.append(connection.execute("SELECT id FROM eras WHERE name = ?", (era["name"],)).fetchone()[0])
    for recording_id, (index, source) in plan.items():
        connection.execute("UPDATE recordings SET era_id = ?, era_source = ? WHERE id = ?",
                           (ids[index] if index is not None else None, source, recording_id))
    marks = ",".join("?" * len(ids))
    stale = [row[0] for row in connection.execute(f"SELECT id FROM eras WHERE id NOT IN ({marks})", ids)]
    for era_id in stale:  # an era no longer in the list (e.g. the former "After Tha Carter VI")
        if not connection.execute("SELECT 1 FROM recordings WHERE era_id = ?", (era_id,)).fetchone():
            connection.execute("DELETE FROM eras WHERE id = ?", (era_id,))
    return ids


def main(argv=None):
    parser = argparse.ArgumentParser(description="Eras by solo studio album.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    connection = sqlite3.connect(DATABASE_PATH)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        eras = album_eras(connection)
        plan = plan_assignments(connection, eras)
        status = dict(connection.execute("SELECT recording_id, availability FROM recording_availability"))
        print(f"{'Era':<28} {'from':<11} {'to':<11} {'album':>6} {'date':>6} {'year':>6}"
              f"   released/leaked/unreleased")
        for index, era in enumerate(eras):
            members = [(r, s) for r, (i, s) in plan.items() if i == index]
            by_status = [sum(1 for r, _ in members if status.get(r) == k) for k in ("released", "leaked", "unreleased")]
            by_source = [sum(1 for _, s in members if s == k) for k in ("album", "date", "year")]
            print(f"{era['name']:<28} {era['start_date'] or '':<11} {era['end_date'] or '(open)':<11} "
                  + "".join(f"{n:>6} " for n in by_source) + f"  {'/'.join(map(str, by_status))}")
        unassigned = [r for r, (i, _) in plan.items() if i is None]
        print(f"Without era (no usable date or period crosses a boundary): {len(unassigned)} "
              f"{ {k: sum(1 for r in unassigned if status.get(r) == k) for k in ('released', 'leaked', 'unreleased')} }")
        if not args.apply:
            print("Dry run; use --apply to change the database (make a backup first).")
            return eras, plan
        with connection:
            apply(connection, eras, plan)
        print("Applied.")
        return eras, plan
    finally:
        connection.close()


if __name__ == "__main__":
    main()
