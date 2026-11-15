"""Static copy of the web tracker for publishing (no server needed).

Writes dist/: the page (web/) and every public route of api_routes.py as a
JSON file under api/v1/ (api/v1/songs.json, api/v1/songs/<id>.json, ...,
api/v1/index.json = the overview in "static" mode).  web/api.js reads the
overview and then those files.  Left out on purpose: the local-only routes
(streaming of the owner's files, lyrics) and every
fact about the owner's local music library (file marks, counts, ids).
No audio is ever copied.

usage:
    python export_static.py [--out ../dist]
"""

import argparse
import json
import shutil
from pathlib import Path

import api_routes
import audio_providers
import tracker_data
import tracker_server


PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_FOLDER = PROJECT_ROOT / "web"


PRIVATE_PLAY_KEYS = audio_providers.private_keys()  # sources of non-public providers (local files)


def without_local_files(value):
    """Drop local-library facts from an API answer (recursively): file marks and
    counts, file lists and the play fields of non-public providers
    (audio_providers: no audio in the public copy)."""
    if isinstance(value, dict):
        return {k: (False if k == "file" else 0 if k == "files" and isinstance(v, int) else without_local_files(v))
                for k, v in value.items() if not (k == "files" and not isinstance(v, int)) and k not in PRIVATE_PLAY_KEYS}
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
    # Every public route of api_routes.py, once per id; local-only routes (the owner's
    # files, lyrics) are never written.  "files" counts become 0 in write().
    context = {"mode": "static", "profile": tracker_server.artist_profile}
    written = {}
    for route in api_routes.ROUTES:
        if route.local_only or route.kind != "json":
            continue
        values = route.items(connection) if route.param else [None]
        for value in values:
            data = route.handler(connection, value, context)
            if data is not None:
                write(out, route.file_name(value), data)
                written[route.name] = written.get(route.name, 0) + 1
    return written.get("song", 0), written.get("artist", 0)


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
