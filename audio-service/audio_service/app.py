"""The service API (FastAPI).  Stage 1: health, asset metadata, assets of a catalogue
version, streaming with Range.  Uploads, accounts and moderation come in later stages.

Local mode (the only mode for now): the service listens on 127.0.0.1 and serves every
asset to the owner.  Public mode (a server, stage 7) will serve only what rights.py allows.
"""

import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from . import __version__, db, rights
from .config import Settings, load_settings
from .storage import LocalStorage

CONTENT_TYPES = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "aac": "audio/aac", "flac": "audio/flac",
                 "ogg": "audio/ogg", "opus": "audio/ogg", "wav": "audio/wav"}
RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


def create_app(settings: Settings | None = None, public: bool = False) -> FastAPI:
    settings = settings or load_settings()
    connection = db.connect(settings.database_path)
    db.initialize(connection)
    storage = LocalStorage(settings.storage_dir)
    app = FastAPI(title="Lil Wayne Tracker Audio Service", version=__version__)
    app.state.connection, app.state.storage, app.state.public = connection, storage, public

    def asset_row(asset_id: int):
        row = connection.execute("""
            SELECT a.*, r.status AS rights_status, r.license, r.allows_download
            FROM assets a LEFT JOIN rights r ON r.asset_id = a.id WHERE a.id = ?""", (asset_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "no such asset")
        return row

    def describe(row) -> dict:
        state = db.moderation_state(connection, row["id"])
        streamable = not public or rights.may_stream_publicly(row["rights_status"], state)
        return {
            "id": row["id"], "sha256": row["sha256"], "size": row["size"], "format": row["format"],
            "duration": row["duration"], "bitrate": row["bitrate"], "sample_rate": row["sample_rate"],
            "rights": row["rights_status"] or "unknown", "license": row["license"], "moderation": state,
            "stream_url": f"/v1/assets/{row['id']}/stream" if streamable else None,
            "versions": [r["version_id"] for r in connection.execute(
                "SELECT version_id FROM asset_versions WHERE asset_id = ? ORDER BY version_id", (row["id"],))],
        }

    @app.get("/health")
    def health():
        count = connection.execute("SELECT count(*) FROM assets").fetchone()[0]
        return {"status": "ok", "version": __version__, "mode": "public" if public else "local", "assets": count}

    @app.get("/v1/assets/{asset_id}")
    def get_asset(asset_id: int):
        row = asset_row(asset_id)
        if public and describe(row)["stream_url"] is None:
            raise HTTPException(404, "no such asset")  # private assets do not exist for the public
        return describe(row)

    @app.get("/v1/versions/{version_id}/assets")
    def version_assets(version_id: int):
        """What the tracker asks: the playable assets of one catalogue version (best first)."""
        rows = connection.execute("""
            SELECT a.*, r.status AS rights_status, r.license, r.allows_download
            FROM asset_versions av JOIN assets a ON a.id = av.asset_id LEFT JOIN rights r ON r.asset_id = a.id
            WHERE av.version_id = ? ORDER BY COALESCE(a.bitrate, 0) DESC, a.id""", (version_id,)).fetchall()
        return [d for d in (describe(row) for row in rows) if d["stream_url"]]

    @app.get("/v1/assets/{asset_id}/stream")
    def stream(asset_id: int, request: Request):
        row = asset_row(asset_id)
        if describe(row)["stream_url"] is None:
            raise HTTPException(404, "no such asset")
        path: Path = storage.path_for(row["sha256"])
        if not path.is_file():
            raise HTTPException(410, "the stored file is missing")
        content_type = row["content_type"] or CONTENT_TYPES.get((row["format"] or "").lower(), "application/octet-stream")
        size = path.stat().st_size
        header = request.headers.get("range")
        if not header:
            return FileResponse(path, media_type=content_type, headers={"Accept-Ranges": "bytes"})
        match = RANGE.match(header.strip())
        if not match or match.groups() == ("", ""):
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        first, last = match.groups()
        start, end = (max(size - int(last), 0), size - 1) if first == "" else (int(first), min(int(last), size - 1) if last else size - 1)
        if start > end or start >= size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})

        def chunks():
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    data = f.read(min(256 * 1024, left))
                    if not data:
                        break
                    left -= len(data)
                    yield data
        return StreamingResponse(chunks(), status_code=206, media_type=content_type, headers={
            "Accept-Ranges": "bytes", "Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(end - start + 1)})

    return app
