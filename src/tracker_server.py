"""Local web tracker for the catalogue (read-only, standard library only).

    python tracker_server.py [--port 8765]
then open http://127.0.0.1:8765 in the browser.

The server binds to 127.0.0.1 only and opens SQLite read-only.  It serves
the page in web/ and the JSON API of api_routes.py: /api/v1/... (GET
/api/v1/ lists the endpoints) and the old /api/... paths below as aliases:
    /api/summary          totals, statuses, eras
    /api/songs            all songs (compact)
    /api/song/<id>        one song: versions, releases, credits, sources
    /api/releases         all releases
    /api/release/<id>     one release with its tracks
    /api/artist/<id>      one person: songs and roles
    /api/people           the most credited people
    /api/era/<id>, /api/stats, /api/profile (data/artist_profile.json)
    /api/audio/<file id>  a file of the owner's own library for the player
                          (this server only: it listens on 127.0.0.1; files
                          known to the files table only; Range requests for
                          seeking).  The static export never contains audio.
    /api/lyrics/<version>  synced lyrics from LRCLIB for the player (fetched on
                          demand, memory only, never stored or exported)
"""

import argparse
import json
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import api_routes
import audio_providers
import audio_stream
import tracker_data


WEB_FOLDER = Path(__file__).resolve().parent.parent / "web"
ROUTES = api_routes.ROUTES  # the one route list (api_routes.py)


PROFILE_PATH = Path(__file__).resolve().parent.parent / "data" / "artist_profile.json"


def artist_profile():
    """data/artist_profile.json (artist_profile.py), or None."""
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8")) if PROFILE_PATH.exists() else None


class Handler(BaseHTTPRequestHandler):
    database_path = None

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path.startswith("/api/"):
            return self.api(path)
        return self.static(path)

    def api(self, path):
        route, value = api_routes.find(path)
        if route is None:
            return self.send(404, b'{"error": "unknown endpoint"}', "application/json")
        if route.kind == "audio":
            return self.audio(value)
        context = {"mode": "local", "profile": artist_profile}
        connection = tracker_data.connect(self.database_path)
        try:
            data = route.handler(connection, value, context)
        finally:
            connection.close()
        if data is None:
            return self.send(404, b'{"error": "not found"}', "application/json")
        return self.send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json")

    def audio(self, file_id):
        connection = tracker_data.connect(self.database_path)
        try:
            found = audio_providers.audio_file(connection, file_id)
        finally:
            connection.close()
        if found is None:
            return self.send(404, b'{"error": "no such file"}', "application/json")
        path, kind = found
        audio_stream.send_file(self, path, kind)

    def static(self, path):
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB_FOLDER / name).resolve()
        if WEB_FOLDER.resolve() not in target.parents or not target.is_file():
            return self.send(404, b"not found", "text/plain")
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return self.send(200, target.read_bytes(), kind)

    def send(self, status, body, kind):
        self.send_response(status)
        self.send_header("Content-Type", f"{kind}; charset=utf-8" if kind.startswith(("text", "application/json"))
                         else kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # quiet console
        pass


class TrackerServer(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second server bind the same port silently
    # (requests then reach the old one); fail instead.
    allow_reuse_address = sys.platform != "win32"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local web tracker (read-only).")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--database", help="SQLite file (default: data/lilwayne.db)")
    args = parser.parse_args(argv)
    Handler.database_path = args.database
    try:
        server = TrackerServer(("127.0.0.1", args.port), Handler)
    except OSError as error:
        print(f"Port {args.port} is in use (is the tracker already running?): {error}")
        return
    print(f"Lil Wayne Tracker: http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
