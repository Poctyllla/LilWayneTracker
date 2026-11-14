# Lil Wayne Tracker

A personal, non-commercial project that builds a **verifiable catalogue of Lil Wayne's music**
and a **local tracker website** to browse and play it.

The catalogue is metadata: releases and their editions, songs, versions (album version,
remixes, demos, snippets, live and alternate versions…), credits, eras and the sources
every fact came from. It is assembled by Python tools from public metadata services and
reviewed by hand where the sources disagree.

> Not affiliated with Lil Wayne, Young Money or Cash Money. No audio is hosted or
> distributed by this project.

## Features

**Catalogue (Python + SQLite)**
- Discography from MusicBrainz: studio albums, collaboration and group albums, EPs, singles,
  official mixtapes, with alternate editions stored as compact deltas.
- One recording per song even when sources use several IDs; versions are kept apart by type
  (album, remix, demo, snippet, original, alternate, extended, live, acapella).
- Official and unofficial discography kept separate; released / leaked / unreleased status
  computed per song and per version.
- Guest appearances, credits (producers, writers, featured artists, engineers…) from Genius
  and Discogs with their source, eras by solo studio album, cross-checks against Wikipedia
  and LilWayneHQ lists.
- Every change goes through validation, a transaction and `verify_catalog.py`.

**Tracker website (local, standard library only)**
- Songs (list / grid / compact, filters, search, CSV export), releases, eras with covers and
  summaries, timeline, people and collaborations, statistics, "on this day".
- Song pages with all versions side by side, releases, credits by role and source links.
- Player: queue, shuffle, repeat, volume, full-screen view with synced lyrics (local
  experiment). Playback uses the official **Spotify** and **Apple Music** embeds; on the
  owner's own computer it can also play files from the owner's private library.
- Colour styles, light / dark theme, settings stored in the browser.

## Getting started

Requirements: Python 3.12+ (developed on 3.15), Windows / macOS / Linux.

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows  (macOS / Linux: source .venv/bin/activate)
pip install -r requirements.txt   # only mutagen (tags of local audio files)
```

The database is **not part of this repository** (see below). `src/database.py` creates an
empty schema; the import tools in `src/` fill it (MusicBrainz scan → review queue → release
group packages → import). Some tools need API keys in environment variables:

| Variable | Used for |
|---|---|
| `GENIUS_ACCESS_TOKEN` | Genius songs and credits |
| `DISCOGS_TOKEN` | Discogs credits |
| `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` | Spotify album / track links |

MusicBrainz, the Cover Art Archive, Wikipedia / Wikidata and the iTunes Search API need no key.
The tools respect the services' rate limits.

### Run the tracker

```bash
cd src
python tracker_server.py --port 8765
```

Open http://127.0.0.1:8765. The server listens on 127.0.0.1 only and opens the database
read-only. `python export_static.py` writes a static copy of the site to `dist/` (without
anything from the private music library).

### Tests

```bash
python -m unittest discover -s tests
```

`tests/test_api_contract.py` keeps the shape of every API answer stable.

## Local API

The tracker serves JSON under `/api/`: `summary`, `songs`, `song/<id>`, `releases`,
`release/<id>`, `artist/<id>`, `people`, `era/<id>`, `stats`, `timeline`,
`on-this-day/<MM-DD>`, `profile`. Local-only: `audio/<file id>` (the owner's own files,
HTTP range requests), `lyrics/<version id>`, `review`. A versioned `/api/v1` with a
separate audio-provider layer is in progress.

## Project layout

```text
src/     catalogue tools, import / review / verification, tracker server and data queries
tests/   unit tests (unittest)
web/     the tracker page (HTML, CSS, plain JavaScript; no build step)
```

## What is deliberately not in this repository

- **The database** (`data/`): it contains paths of the owner's private music library.
- **API caches** (`exports/`): the services' terms do not allow re-publishing their data.
- **Cover art** (`web/covers/`): copyrighted; only the fallback photo is included.
- **Audio of any kind.** Unreleased or leaked recordings are catalogued as metadata only and
  are never uploaded or shared. Public playback uses official streaming embeds.

## Data sources and credits

Metadata from [MusicBrainz](https://musicbrainz.org) (CC0), [Genius](https://genius.com),
[Discogs](https://www.discogs.com), [Wikipedia](https://en.wikipedia.org) (text under
CC BY-SA, linked where shown), the [Cover Art Archive](https://coverartarchive.org),
LilWayneHQ (pages saved by hand) and [LRCLIB](https://lrclib.net) for the local lyrics
experiment. Streaming links lead to the official services.
Fallback photo: "Lil Wayne in Concert" by Michael Williams, CC BY 2.0, via Wikimedia Commons.

## License

Not chosen yet; until a license file is added, all rights are reserved.
