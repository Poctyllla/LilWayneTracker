"""The one list of API routes (Catalog API), shared by tracker_server.py and
export_static.py.

Every route has a versioned path under /api/v1/ (resource names in the
plural, as in the Juice WRLD API) and, while the page still uses them, its
old path under /api/ (alias).  A route says whether it is local-only (never
in the public static copy: streaming of the owner's files, lyrics)
and, if it has an id, how to list its ids for the static copy.

    route.handler(connection, value, context) -> JSON-able data or None (404)
    context: {"mode": "local" | "static", "profile": fn() -> dict | None}
"""

import re

import audio_providers
import lyrics_source
import tracker_data

API_VERSION = "1"
PARAMS = {"id": (r"(\d+)", int), "month_day": (r"(\d\d-\d\d)", str)}


class Route:
    def __init__(self, name, path, handler, legacy=None, local_only=False, items=None, kind="json"):
        self.name, self.path, self.legacy = name, path, legacy
        self.handler, self.local_only, self.items, self.kind = handler, local_only, items, kind
        self.param = next((p for p in PARAMS if "{" + p + "}" in path), None)
        self.patterns = [self.compile(f"/api/v1/{path}", optional_slash=True)]
        if legacy is not None:
            self.patterns.append(self.compile(f"/api/{legacy}"))

    def compile(self, template, optional_slash=False):
        pattern = re.escape(template)
        for param, (regex, _cast) in PARAMS.items():
            pattern = pattern.replace(re.escape("{" + param + "}"), regex)
        pattern = pattern.rstrip("/") + ("/?" if optional_slash else "")
        return re.compile(f"^{pattern}$")

    def match(self, path):
        """The parameter value (or True for routes without one) when `path` is this route."""
        for pattern in self.patterns:
            found = pattern.match(path)
            if found:
                return PARAMS[self.param][1](found.group(1)) if self.param else True
        return None

    def file_name(self, value=None):
        """Name of this answer in the static copy: api/<name>.json, the v1 path
        (web/api.js maps "/api/v1/songs/5" to "api/v1/songs/5.json"; the overview is v1/index)."""
        template = f"v1/{self.path or 'index'}"
        return template.replace("{" + self.param + "}", str(value)) if self.param else template


def overview(connection, context):
    """GET /api/v1/: what this API is and what it can do in this mode."""
    local = context.get("mode", "local") == "local"
    return {
        "name": "Lil Wayne Tracker API", "version": API_VERSION, "mode": "local" if local else "static",
        "features": {"stream": local, "lyrics": local},
        "counts": tracker_data.summary(connection)["counts"],
        "endpoints": [{"name": r.name, "path": f"/api/v1/{r.path}", "local_only": r.local_only}
                      for r in ROUTES if local or not r.local_only],
    }


def song_ids(connection):
    return [row[0] for row in connection.execute("SELECT id FROM recordings ORDER BY id")]


def release_ids(connection):
    return [row[0] for row in connection.execute("SELECT id FROM releases ORDER BY id")]


def era_ids(connection):
    return [era["id"] for era in tracker_data.summary(connection)["eras"]]


def artist_ids(connection):
    ids = {row[0] for row in connection.execute("SELECT DISTINCT artist_id FROM credits")}
    ids |= {row[0] for row in connection.execute("SELECT DISTINCT artist_id FROM recordings")}
    return sorted(ids)


def playable_versions(connection):
    """Versions with a public source (the static copy writes their source lists)."""
    public = [p for p in audio_providers.PROVIDERS if p.public]
    return sorted(audio_providers.playables(connection, public))


def month_days(connection):
    return [f"{month:02d}-{day:02d}" for month in range(1, 13) for day in range(1, 32)]


ROUTES = [
    Route("overview", "", lambda c, v, x: overview(c, x)),
    Route("summary", "summary", lambda c, v, x: tracker_data.summary(c), legacy="summary"),
    Route("songs", "songs", lambda c, v, x: tracker_data.songs(c), legacy="songs"),
    Route("song", "songs/{id}", lambda c, v, x: tracker_data.song(c, v), legacy="song/{id}", items=song_ids),
    Route("releases", "releases", lambda c, v, x: tracker_data.releases(c), legacy="releases"),
    Route("release", "releases/{id}", lambda c, v, x: tracker_data.release(c, v), legacy="release/{id}",
          items=release_ids),
    Route("artists", "artists", lambda c, v, x: tracker_data.people(c), legacy="people"),
    Route("artist", "artists/{id}", lambda c, v, x: tracker_data.artist(c, v), legacy="artist/{id}", items=artist_ids),
    Route("eras", "eras", lambda c, v, x: tracker_data.summary(c)["eras"]),
    Route("version_sources", "versions/{id}/sources", lambda c, v, x: audio_providers.sources(
        c, v, public_only=x.get("mode") == "static"), items=playable_versions),
    Route("era", "eras/{id}", lambda c, v, x: tracker_data.era(c, v), legacy="era/{id}", items=era_ids),
    Route("stats", "stats", lambda c, v, x: tracker_data.stats(c), legacy="stats"),
    Route("completeness", "completeness", lambda c, v, x: tracker_data.completeness(c)),
    Route("timeline", "timeline", lambda c, v, x: tracker_data.timeline(c), legacy="timeline"),
    Route("on_this_day", "on-this-day/{month_day}", lambda c, v, x: tracker_data.on_this_day(c, v),
          legacy="on-this-day/{month_day}", items=month_days),
    Route("profile", "profile", lambda c, v, x: x["profile"](), legacy="profile"),
    Route("lyrics", "lyrics/{id}", lambda c, v, x: lyrics_source.lookup(c, v), legacy="lyrics/{id}", local_only=True),
    Route("stream", "stream/{id}", None, legacy="audio/{id}", local_only=True, kind="audio"),
]
BY_NAME = {route.name: route for route in ROUTES}


def find(path):
    """(route, value) for a request path, or (None, None)."""
    for route in ROUTES:
        value = route.match(path)
        if value is not None:
            return route, value
    return None, None
