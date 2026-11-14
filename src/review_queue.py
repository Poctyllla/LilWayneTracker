"""Open questions for the web tracker's "Needs review" page.

Read-only for SQLite.  Two kinds of questions:
  guest   a guest-survey song whose title matches catalogued songs but the
          evidence was not enough (guest_matches.py verdict 'review');
          answers: guest_same (it is catalogued song N) or guest_different
          (add it as a guest song)
  split   a recording that looks like a version ("X (Live)") of one of
          several songs called X (split_versions.py review); answers:
          make_version (version of song N) or keep_separate
Decisions are appended to data/song_review_decisions.json (record_decision);
they reach SQLite only through song_review.py --apply (after a backup).
"""

import json
import sqlite3
from datetime import date
from pathlib import Path

import split_versions
from version_types import split_version


PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPORTS_FOLDER = PROJECT_ROOT / "exports"
DECISIONS_PATH = PROJECT_ROOT / "data" / "song_review_decisions.json"
ACTIONS = {"guest_same", "guest_different", "make_version", "keep_separate", "skip", "split_off"}


def latest(pattern):
    files = sorted(EXPORTS_FOLDER.glob(pattern))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def load_decisions(path=DECISIONS_PATH):
    if Path(path).exists():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return {"about": "Manual song review decisions of the project owner.", "decisions": []}


def decided_keys(decisions):
    return {d.get("key") for d in decisions["decisions"] if d.get("key")}


def song_facts(connection, recording_id):
    """Short description of a catalogued song for a review card."""
    row = connection.execute("""
        SELECT r.id, r.title, a.name AS artist, av.availability FROM recordings r
        JOIN artists a ON a.id = r.artist_id JOIN recording_availability av ON av.recording_id = r.id
        WHERE r.id = ?""", (recording_id,)).fetchone()
    if row is None:
        return None
    tracks = connection.execute("""
        SELECT x.title, x.release_date, x.discography, rt.duration_ms, v.version_type FROM versions v
        JOIN release_tracks rt ON rt.version_id = v.id JOIN releases x ON x.id = rt.release_id
        WHERE v.recording_id = ? ORDER BY x.release_date LIMIT 6""", (recording_id,)).fetchall()
    credits = [r[0] for r in connection.execute("""
        SELECT DISTINCT a.name FROM credits c JOIN artists a ON a.id = c.artist_id
        LEFT JOIN versions v ON v.id = c.version_id
        WHERE (c.recording_id = ? OR v.recording_id = ?) AND c.role IN ('main_artist', 'featured_artist')
        LIMIT 8""", (recording_id, recording_id))]
    return {"id": row[0], "title": row[1], "artist": row[2], "status": row[3], "artists": credits,
            "tracks": [{"release": t[0], "date": t[1], "discography": t[2], "duration_ms": t[3], "type": t[4]}
                       for t in tracks]}


def guest_items(connection, details=None):
    matches, survey = latest("guest_matches_*.json"), latest("guest_survey_*.json")
    if not matches or not survey:
        return []
    by_key = {(s["main_artist"], s["title"]): s for s in survey["songs"]}
    items = []
    for match in matches["songs"]:
        if match["verdict"] != "review":
            continue
        song = by_key.get((match["main_artist"], match["title"]), {})
        albums = sorted({((details or {}).get(g["id"]) or {}).get("album") and details[g["id"]]["album"].get("name")
                         or "" for g in song.get("genius", [])} - {""})
        candidates = [facts for facts in (song_facts(connection, r) for r in match["candidates"]) if facts]
        items.append({
            "key": f"guest:{match['main_artist']}|{match['title']}", "kind": "guest",
            "title": match["title"], "main_artist": match["main_artist"], "sources": match["sources"],
            "evidence": match["evidence"], "genius_albums": albums,
            "lengths": [m.get("length") for m in song.get("musicbrainz", []) if m.get("length")],
            "links": [g["url"] for g in song.get("genius", [])]
                     + [f"https://musicbrainz.org/recording/{m['id']}" for m in song.get("musicbrainz", [])],
            "hq_projects": song.get("hq_projects", []),
            "candidates": candidates,
        })
    return items


def split_items(connection):
    reviews = split_versions.find_splits(connection)["review"]
    if not reviews:
        return []
    songs_by_key = {}  # catalogued songs (no version marker) by comparable title
    for recording_id, title in connection.execute("SELECT id, title FROM recordings WHERE appearance IS NULL"):
        if split_version(title)[1] is None:
            songs_by_key.setdefault(split_versions.lookup_key(title), []).append(recording_id)
    items = []
    for item in reviews:
        base, version_type, version_name = split_version(item["title"])
        candidates = [r for r in songs_by_key.get(split_versions.lookup_key(base), []) if r != item["recording_id"]]
        items.append({
            "key": f"split:{item['recording_id']}", "kind": "split", "title": item["title"],
            "recording": song_facts(connection, item["recording_id"]),
            "version_type": version_type, "version_name": version_name, "reason": item["reason"],
            "candidates": [f for f in (song_facts(connection, r) for r in candidates) if f],
        })
    return items


SPREAD_YEARS = 6


def spread_items(connection):
    """Songs whose main version is on a bootleg years before (or after) an official
    release -- two different songs of the same title may have been joined
    (e.g. a 2006 bootleg 'Hollywood' and the 2020 'HOLLYWOOD')."""
    items = []
    for recording_id, title in connection.execute(f"""
        SELECT r.id, r.title FROM recordings r
        JOIN versions v ON v.recording_id = r.id AND v.version_type = 'album'
        JOIN release_tracks rt ON rt.version_id = v.id JOIN releases x ON x.id = rt.release_id
        WHERE r.appearance IS NULL AND x.release_date IS NOT NULL
        GROUP BY r.id
        HAVING max(CASE WHEN x.discography = 'official' THEN CAST(substr(x.release_date, 1, 4) AS INTEGER) END)
             - min(CASE WHEN x.discography = 'unofficial' THEN CAST(substr(x.release_date, 1, 4) AS INTEGER) END)
             >= {SPREAD_YEARS}
           AND min(CASE WHEN x.discography = 'unofficial' THEN CAST(substr(x.release_date, 1, 4) AS INTEGER) END)
             < min(CASE WHEN x.discography = 'official' THEN CAST(substr(x.release_date, 1, 4) AS INTEGER) END)"""):
        tracks = [dict(zip(("release_id", "release", "date", "discography", "duration_ms", "track_title"), row))
                  for row in connection.execute("""
            SELECT x.id, x.title, x.release_date, x.discography, rt.duration_ms, rt.track_title
            FROM versions v JOIN release_tracks rt ON rt.version_id = v.id JOIN releases x ON x.id = rt.release_id
            WHERE v.recording_id = ? AND v.version_type = 'album' ORDER BY x.release_date""", (recording_id,))]
        first_official = min(t["date"][:4] for t in tracks if t["discography"] == "official" and t["date"])
        early = [t["release_id"] for t in tracks if t["discography"] == "unofficial" and (t["date"] or "9999")[:4] < first_official]
        items.append({"key": f"spread:{recording_id}", "kind": "spread", "title": title,
                      "recording": song_facts(connection, recording_id), "tracks": tracks,
                      "early_releases": early, "candidates": []})
    return items


def review_items(connection, details=None, decisions=None):
    """All open questions (already decided ones are left out)."""
    done = decided_keys(decisions or load_decisions())
    items = guest_items(connection, details) + split_items(connection) + spread_items(connection)
    return [item for item in items if item["key"] not in done]


def validate(decision, items_by_key):
    """The decision to store, or raises ValueError."""
    action, key = decision.get("action"), decision.get("key")
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    item = items_by_key.get(key)
    if item is None:
        raise ValueError("unknown or already decided question")
    stored = {"key": key, "action": action, "decided": date.today().isoformat(), "note": "web review page"}
    candidate_ids = {c["id"] for c in item["candidates"]}
    if action == "guest_same":
        if decision.get("recording") not in candidate_ids:
            raise ValueError("pick one of the candidates")
        stored.update(recording=decision["recording"], survey_key=[item["main_artist"], item["title"]])
    elif action == "guest_different":
        if item["kind"] != "guest":
            raise ValueError("not a guest question")
        stored["survey_key"] = [item["main_artist"], item["title"]]
    elif action == "make_version":
        if item["kind"] != "split" or decision.get("of") not in candidate_ids:
            raise ValueError("pick one of the candidates")
        stored.update(recording=item["recording"]["id"], of=decision["of"],
                      version_type=item["version_type"], version_name=item["version_name"])
    elif action == "split_off":
        if item["kind"] != "spread":
            raise ValueError("not a 'same title, years apart' question")
        stored.update(recording=item["recording"]["id"], releases=item["early_releases"],
                      title=item["title"])
    elif action == "keep_separate":
        ids = [item["recording"]["id"]] if item["kind"] == "split" else []
        stored["recordings"] = ids + sorted(candidate_ids)
    return stored


def record_decision(decision, items, path=DECISIONS_PATH):
    """Validate one decision from the page and append it to the decisions file."""
    stored = validate(decision, {item["key"]: item for item in items})
    data = load_decisions(path)
    data["decisions"].append(stored)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return stored
