# Audio Service

A separate service next to Lil Wayne Tracker: its own storage, database and API for
audio files, linked to the tracker's catalogue by version ids. Stage 1 of the plan:
the skeleton.

- **Storage** by content (sha256): an identical file is kept once.
- **Database** (SQLite): assets with technical metadata, rights per asset, links to
  catalogue versions, a moderation log.
- **Rule**: an asset is served to the public only when its rights allow redistribution
  *and* a moderator approved it. Leaked or unreleased audio is never public.
- **Local only for now**: listens on 127.0.0.1; no uploads, accounts or public mode yet.

```bash
cd audio-service
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe run.py                     # http://127.0.0.1:8770/health
.venv/Scripts/python.exe -m unittest discover -s tests
```

| Endpoint | |
|---|---|
| `GET /health` | status, mode, number of assets |
| `GET /v1/assets/<id>` | technical metadata, rights, moderation, catalogue versions |
| `GET /v1/versions/<version id>/assets` | playable assets of a catalogue version (best first) |
| `GET /v1/assets/<id>/stream` | the audio, with Range requests for seeking |

Data lives in `audio-service/data/` (not in the repository); `AUDIO_SERVICE_DATA`
and `AUDIO_SERVICE_PORT` override the folder and the port.

Next stages: import of the owner's library (private), the tracker's player using the
service, audio fingerprints, accounts and uploads, moderation, public delivery.
