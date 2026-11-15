"""Audio providers: where a version of a song can be played (server side).

Each provider reads the catalogue (read-only) and says, per version, how the
player can play it.  Adding a provider = one class here + one adapter in
web/player.js; the catalogue queries do not change.

    provider.key     the field in a version's "play" object ("spotify", ...)
    provider.public  may appear in the public static copy?  Local library
                     files never do (no rights to publish leaked audio).
    provider.kind    "embed" (the service's own player) or "stream" (our
                     /api/audio/<id>, Range requests: audio_stream.py)

    playables(connection)  {version_id: {"spotify": id, "apple": url, "file_id": id}}
    song_play(connection)  {recording_id: {"v": version_id, ...}}  (album version first)
    private_keys()         play fields that must not leave this computer
"""

import os
import re
from pathlib import Path

AUDIO_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".flac": "audio/flac",
               ".ogg": "audio/ogg", ".wav": "audio/wav"}
SPOTIFY_TRACK = re.compile(r"open\.spotify\.com/(?:intl-[a-z]+/)?track/([A-Za-z0-9]{10,})")


def has_table(connection, name):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


class LinkProvider:
    """A streaming service with track links in version_links (official embeds)."""
    public = True
    kind = "embed"
    service = None  # version_links.service

    def values(self, connection):
        """{version_id: value} -- the first usable track link of each version."""
        found = {}
        if not has_table(connection, "version_links"):
            return found
        for version_id, url in connection.execute(
                "SELECT version_id, url FROM version_links WHERE service = ? ORDER BY id", (self.service,)):
            value = self.value(url)
            if value is not None:
                found.setdefault(version_id, value)
        return found

    def value(self, url):
        raise NotImplementedError

    def value_for(self, connection, version_id):
        """The value for one version (the first usable link), or None."""
        if not has_table(connection, "version_links"):
            return None
        for (url,) in connection.execute("SELECT url FROM version_links WHERE version_id = ? AND service = ? ORDER BY id",
                                         (version_id, self.service)):
            value = self.value(url)
            if value is not None:
                return value
        return None

    def source(self, value):
        return {"provider": self.name, "key": self.key, "kind": self.kind, "public": self.public, "ref": value}


class SpotifyProvider(LinkProvider):
    """Spotify iFrame API embed: whole tracks when signed in, 30 s previews otherwise."""
    name, key, service = "spotify", "spotify", "spotify"

    def value(self, url):
        match = SPOTIFY_TRACK.search(url)
        return match.group(1) if match else None


class AppleMusicProvider(LinkProvider):
    """Apple Music embed (embed.music.apple.com); only a track page (?i=) is playable."""
    name, key, service = "apple_music", "apple", "apple_music"

    def value(self, url):
        return url if "music.apple.com" in url and "i=" in url else None


class LocalFileProvider:
    """The owner's own library files, streamed by the local server only."""
    name, key = "local_file", "file_id"
    public = False
    kind = "stream"

    def values(self, connection):
        found = {}
        for version_id, file_id, file_path in connection.execute(
                "SELECT version_id, id, file_path FROM files WHERE version_id IS NOT NULL "
                "ORDER BY COALESCE(bitrate, 0) DESC, id"):
            if Path(file_path).suffix.lower() in AUDIO_TYPES:
                found.setdefault(version_id, file_id)
        return found

    def value_for(self, connection, version_id):
        for file_id, file_path in connection.execute(
                "SELECT id, file_path FROM files WHERE version_id = ? ORDER BY COALESCE(bitrate, 0) DESC, id", (version_id,)):
            if Path(file_path).suffix.lower() in AUDIO_TYPES:
                return file_id
        return None

    def source(self, file_id):
        return {"provider": self.name, "key": self.key, "kind": self.kind, "public": self.public,
                "url": f"/api/v1/stream/{file_id}", "supports_range": True}

    def resolve(self, connection, file_id):
        """(path, content type) of a file that may be streamed, or None.  The Audio
        Service's storage (by sha256) comes first; file_path is where the file came from."""
        row = connection.execute("SELECT file_path, sha256 FROM files WHERE id = ?", (file_id,)).fetchone()
        if row is None:
            return None
        original = Path(row[0])
        kind = AUDIO_TYPES.get(original.suffix.lower())
        if not kind:
            return None
        stored = service_object(row[1])
        if stored is not None:
            return stored, kind
        return (original, kind) if original.is_file() else None


def service_storage():
    """The Audio Service's object folder (audio-service/data/objects unless AUDIO_SERVICE_DATA says otherwise)."""
    data = os.environ.get("AUDIO_SERVICE_DATA")
    return (Path(data) if data else Path(__file__).resolve().parent.parent / "audio-service" / "data") / "objects"


def service_object(sha256):
    """The stored copy of a file in the Audio Service, or None."""
    if not sha256 or len(sha256) != 64 or not all(ch in "0123456789abcdef" for ch in sha256):
        return None
    path = service_storage() / sha256[:2] / sha256[2:4] / sha256
    return path if path.is_file() else None


PROVIDERS = [SpotifyProvider(), AppleMusicProvider(), LocalFileProvider()]
LOCAL_FILES = PROVIDERS[2]


def playables(connection, providers=None):
    """{version_id: {provider key: value}} -- only what can be played as that exact track."""
    play = {}
    for provider in providers or PROVIDERS:
        for version_id, value in provider.values(connection).items():
            play.setdefault(version_id, {})[provider.key] = value
    return play


def song_play(connection, play=None):
    """{recording_id: {"v": version id, ...playable}} -- the album version when it is
    playable, else the first playable version."""
    play = playables(connection) if play is None else play
    result = {}
    for recording_id, version_id, _type in connection.execute(
            "SELECT recording_id, id, version_type FROM versions ORDER BY version_type != 'album', id"):
        if version_id in play and recording_id not in result:
            result[recording_id] = {"v": version_id, **play[version_id]}
    return result


def audio_file(connection, file_id):
    return LOCAL_FILES.resolve(connection, file_id)


def sources(connection, version_id, public_only=False, providers=None):
    """[{provider, key, kind, public, ref | url, ...}] -- how one version can be played,
    in the providers' order (the player picks by the listener's choice); None for an
    unknown version.  public_only: the static copy (no local files)."""
    if not connection.execute("SELECT 1 FROM versions WHERE id = ?", (version_id,)).fetchone():
        return None
    result = []
    for provider in providers or PROVIDERS:
        if public_only and not provider.public:
            continue
        value = provider.value_for(connection, version_id)
        if value is not None:
            result.append(provider.source(value))
    return result


def private_keys(providers=None):
    """Play fields of providers that must never reach the public copy."""
    return {provider.key for provider in providers or PROVIDERS if not provider.public}
