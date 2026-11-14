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

    def resolve(self, connection, file_id):
        """(path, content type) of a file that may be streamed, or None."""
        row = connection.execute("SELECT file_path FROM files WHERE id = ?", (file_id,)).fetchone()
        if row is None:
            return None
        path = Path(row[0])
        kind = AUDIO_TYPES.get(path.suffix.lower())
        return (path, kind) if kind and path.is_file() else None


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


def private_keys(providers=None):
    """Play fields of providers that must never reach the public copy."""
    return {provider.key for provider in providers or PROVIDERS if not provider.public}
