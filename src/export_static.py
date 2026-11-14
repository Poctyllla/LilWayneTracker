"""Static copy of the web tracker for publishing (no server needed).

Writes dist/: the page (web/), the covers, and every API answer as a JSON file
(api/songs.json, api/song/<id>.json, ...).  The page detects the static copy
(dist/static.js sets window.LWT_STATIC) and reads those files instead of the
server.  Left out on purpose: the "Needs review" page (it writes decisions)
and everything about the owner's local music library (the "local file"
marks and counts) -- they describe a private collection, not the catalogue.
No audio is ever copied.

usage:
    python export_static.py [--out ../dist]
"""

import argparse
import json
import shutil
from pathlib import Path

import tracker_data
import tracker_server


PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_FOLDER = PROJECT_ROOT / "web"


def without_local_files(value):
    """Drop local-library facts from an API answer (recursively): file marks and
    counts, file lists and the player's local file ids (no audio in the public copy)."""
    if isinstance(value, dict):
        return {k: (False if k == "file" else 0 if k == "files" and isinstance(v, int) else without_local_files(v))
                for k, v in value.items() if not (k == "files" and not isinstance(v, int)) and k != "file_id"}
    if isinstance(value, list):
        return [without_local_files(v) for v in value]
    return value


def write(out, path, data):
    target = out / "api" / f"{path}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(without_local_files(data), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def export(out, connection):
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(WEB_FOLDER, out)
    (out / "static.js").write_text("window.LWT_STATIC = true;\n", encoding="utf-8")
    index = (out / "index.html").read_text(encoding="utf-8")
    (out / "index.html").write_text(index.replace('<script src="app.js">', '<script src="static.js"></script>\n<script src="app.js">'),
                                    encoding="utf-8")
    summary = tracker_data.summary(connection)
    summary["counts"]["files"] = 0
    write(out, "summary", summary)
    songs = tracker_data.songs(connection)
    write(out, "songs", songs)
    write(out, "releases", tracker_data.releases(connection))
    people = tracker_data.people(connection, limit=300)
    write(out, "people", people)
    write(out, "stats", tracker_data.stats(connection))
    write(out, "timeline", tracker_data.timeline(connection))
    profile = tracker_server.artist_profile()
    if profile:
        write(out, "profile", profile)
    for song in songs:
        write(out, f"song/{song['id']}", tracker_data.song(connection, song["id"]))
    for release in tracker_data.releases(connection):
        write(out, f"release/{release['id']}", tracker_data.release(connection, release["id"]))
    for era in summary["eras"]:
        write(out, f"era/{era['id']}", tracker_data.era(connection, era["id"]))
    artist_ids = {row[0] for row in connection.execute("SELECT DISTINCT artist_id FROM credits")}
    artist_ids |= {row[0] for row in connection.execute("SELECT DISTINCT artist_id FROM recordings")}
    for artist_id in sorted(artist_ids):
        write(out, f"artist/{artist_id}", tracker_data.artist(connection, artist_id))
    for month in range(1, 13):
        for day in range(1, 32):
            write(out, f"on-this-day/{month:02d}-{day:02d}", tracker_data.on_this_day(connection, f"{month:02d}-{day:02d}"))
    return len(songs), len(artist_ids)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Static copy of the web tracker.")
    parser.add_argument("--out", default=str(PROJECT_ROOT / "dist"))
    args = parser.parse_args(argv)
    connection = tracker_data.connect()
    try:
        songs, artists = export(Path(args.out), connection)
    finally:
        connection.close()
    size = sum(f.stat().st_size for f in Path(args.out).rglob("*") if f.is_file())
    print(f"Static site in {args.out}: {songs} songs, {artists} artists, {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
