# Lil Wayne Tracker

## 1. Project Overview

Lil Wayne Tracker is a Python-based music discography and archive project focused on Lil Wayne.

The goal is to build a comprehensive, structured and verifiable catalogue of Lil Wayne's musical output.

The project should eventually cover:
- Studio albums
- Mixtapes
- EPs
- Singles
- Compilations
- Remixes
- Collaborations and guest appearances
- Alternate editions and tracklists
- Different versions of recordings
- Unreleased music, snippets and other archival material
- Credits, metadata and source provenance

The long-term goal is to automate as much of the collection and verification process as possible.

The project is not intended to be just a manually maintained list of albums. It should become a reliable music metadata database with automated discovery, classification, deduplication, verification and import workflows.

## 2. Who Is Working on This Project

The user is the project owner and makes final decisions about:
- Catalogue structure
- Release classification
- Inclusion and exclusion of releases
- Recording and version relationships
- Data accuracy
- Project priorities

The AI coding agent is a development assistant responsible for:
- Understanding the existing codebase before changing it
- Writing and improving Python code
- Designing database workflows
- Finding bugs and inconsistencies
- Automating repetitive operations
- Running and interpreting tests
- Explaining technical decisions clearly

Do not make major architectural or data-model decisions without explaining their consequences.

The user prefers incremental development, practical explanations and testing each meaningful change before proceeding.

## 3. Development Environment

Operating system: Windows.

IDE: PyCharm.

Python version: 3.15.2.

Project directory:

`C:\Users\Lenovo\LilWayneTracker`

Virtual environment:

`C:\Users\Lenovo\LilWayneTracker\.venv`

Database:

`C:\Users\Lenovo\LilWayneTracker\data\lilwayne.db`

Main project structure:

```text
LilWayneTracker/
├── .venv/
├── data/
│   ├── lilwayne.db
│   ├── lilwayne_backup_*.db        # backups before each change to the database
│   ├── reviewed_candidates.json
│   └── release_groups/
│       ├── official/               # official discography
│       │   ├── studio_albums/      # one package per release group,
│       │   ├── collaboration_albums/   # named only by title, e.g. tha_carter_iii.json
│       │   ├── eps/
│       │   ├── singles/
│       │   └── mixtapes/ ...
│       └── unofficial/             # unofficial discography
│           ├── bootlegs/
│           ├── mixtapes/
│           ├── compilations/
│           └── remixes/ ...
├── src/
├── tests/
├── web/                            # local web tracker page (tracker_server.py)
├── exports/
└── README.md
```

Category folders are created on demand. Category names (`studio_album`,
`collaboration_album`, `group_album`, `group_single`, `mixtape`, `ep`,
`single`, `remix`, `compilation`, `bootleg`, `other`) and folder names are defined once in
`src/release_group_package.py`. `collaboration_album` (folder
`collaboration_albums/`) is for albums credited jointly to Lil Wayne and other
artists; packages keep the MusicBrainz credit in `release.artist_credit`, and
validation warns when category and credit do not match. In SQLite such
releases stay linked to Lil Wayne with `release_type = 'collaboration_album'`.
The old per-edition folder `data/releases/` no longer exists.

Use the existing virtual environment when running project scripts.

Do not recreate the database or overwrite existing data without explicit approval.

## 4. Database Architecture

The project uses SQLite.

Main tables:

- `artists`
- `releases`
- `recordings`
- `versions`
- `files`
- `sources`
- `release_tracks`
- `credits`
- `recording_sources`

Important relationships:

`release_tracks.version_id -> versions.id -> versions.recording_id -> recordings.id`

This distinction is fundamental:

- A release is a particular edition of an album, single, mixtape or other release.
- A recording is the underlying musical recording.
- A version represents a particular version or variation of that recording.
- A release track connects a version to a particular position on a release.

Different editions can share the same recordings and versions.

Do not create duplicate recordings merely because the same track appears on different editions.

Do not deduplicate releases based only on their titles. Use MusicBrainz IDs, release groups, tracklists and other evidence.

## 5. MusicBrainz Integration

MusicBrainz is currently the primary external metadata source.

Lil Wayne MusicBrainz Artist ID:

`ac9a487a-d9d2-4f27-bb23-0f4686488345`

MusicBrainz provides useful information:
- Artists
- Release groups
- Releases and editions
- Tracklists
- Recordings
- Recording IDs
- Release IDs
- Release Group IDs
- Durations
- Some credits and labels

However, MusicBrainz is community-maintained and should not be treated as an unquestionable source of truth.

Its classifications can be inaccurate or incomplete.

Always preserve MusicBrainz identifiers and source provenance where available.

Respect API rate limits. The current project uses a one-second delay between MusicBrainz requests and a descriptive User-Agent.

## 6. Current Database State

As of November 12, 2026 the database holds the official discography so far
(all `official`): 14 solo studio albums, 2 collaboration albums, 6 group
albums (Hot Boys x3, We Are Young Money, Rise of an Empire, Rich Gang),
2 original EPs, 79 singles (incl. 4 remix singles and 16 singles known only
from LilWayneHQ), 5 group singles and 33 official mixtapes:
177 releases (editions), 1194 recordings, 1202 versions (7 remix versions),
1773 release tracks, 75 alternate MusicBrainz Recording IDs
(the 3 newest backups are kept). Since November 13, 2026 the database also
holds 110 unofficial bootlegs from the local library — 287 releases,
2861 recordings in total; see "Unofficial discography from the local
library" below (latest backup:
`lilwayne_backup_20261113_022311_pre_bootlegs.db`).
`verify_catalog.py`: 0 errors, 44 warnings — all MusicBrainz data limits
(year-only dates, missing dates/durations, numbering gaps on singles whose
clean/instrumental tracks were left out). Releases known only from
LilWayneHQ are reported as INFO, not as warnings.

Cross-check with LilWayneHQ (saved pages): singles 53/53, mixtapes 31/31,
collaboration/group albums 8/9 — only "True Story" (B.G. & Lil Wayne, 1995)
is missing: not in MusicBrainz and HQ gives no track list.

### Group releases and LilWayneHQ-only singles (user decisions, Nov 12, 2026)

Categories `group_album` / `group_single` (folders `group_albums/`,
`group_singles/`, default official) hold releases of groups Lil Wayne
belongs to (Hot Boys, Young Money, Rich Gang). Duo releases stay
`collaboration_album` / `single` with `release.artist_credit`.
Singles that LilWayneHQ lists but MusicBrainz lacks are built by
`src/hq_packages.py` from the saved HQ singles page: `release_group.source =
"LilWayneHQ"`, no MusicBrainz IDs (allowed only for that source), the HQ page
as release source; the import links the single track to the catalogued song
(single without duration, exactly one song with that title).
Mixtapes that only LilWayneHQ calls official (Blow, The W. Carter Collection
1-2, The Carter Files, Lil Weezy Ana Vol. 1, The Carter 2 Part 2) carry
`release_group.evidence`. `add_release_groups.py --evidence "..."` stores
such notes. For singles, a remix marker without any catalogued original
names the song itself (e.g. "F**k Today (Rebirth mix)").

Mixtapes (Nov 12, 2026): the official list comes from Wikipedia ("Lil Wayne
albums discography", Mixtapes table, 29 entries, read with
`src/wikipedia_source.py` through the MediaWiki API) and was matched to
MusicBrainz (`exports/mixtape_mapping_20261112.json`). Imported 27: SQ1-SQ7,
Da Drought 1-3, The Prefix (promo only), Young Money: The Mixtape Vol. 1,
The Suffix (Remastered), Dedication 2-6, Dedication 6: Reloaded,
No Ceilings 1-3, Sorry 4 the Wait 1-2, T-Wayne, Trust Fund Babies, Tha Fix
Before tha VI. Not found in MusicBrainz: SQ6: Tha Remix, The Dedication.
Mixtape rules: remixes of other artists' songs are their own recordings
(`remix_of_external`); a track without a duration is never linked to a
catalogued song outside singles; generic titles (intro, outro, skit,
interlude, freestyle, prelude) are the same song only within 3 s.
`add_release_groups.py --add-missing` adds release groups that are not in
the review queue (e.g. credited to "Young Money").

### Same song under different MusicBrainz IDs (user decision, Nov 11, 2026)

One song = one recording, even when MusicBrainz uses several Recording IDs
(single vs album, deluxe re-releases, other pressings, Atmos mixes). Extra
IDs are stored in `recording_musicbrainz_ids` (unique; source 'same-song rule'
or 'merge'). Rule (`src/duplicate_guard.py`, used by import, edition
selection and `add_release_groups.py`): same normalized title (`src/titles.py`;
clean/explicit markers are ignored when comparing), not a remix, duration
within 15 s -> same song (user decision Nov 12, 2026: 5-15 s is normal for
singles); unknown duration with exactly one candidate -> same song; several
candidates -> manual review; more than 15 s -> different song.
`recording_musicbrainz_ids.version_id` marks alternate IDs of one version
(the same remix under another ID). Clean-only singles (e.g. Hustler Musik)
are added with `release.clean_only = true`. A remix marker on a recording that
appears in the same package without the marker (e.g. "I'm Single (remix)" vs
"I'm Single") is only a title variant (`remix_marker_ignored`).
`(... mix)` counts as a remix marker (e.g. "SIDEPIECE extended mix").
`src/merge_recordings.py` merged the 3 earlier duplicates (Shooter, Lollipop,
How to Love). Promo-only singles are official: when a single has no usable
Official edition, a Promotion edition is used without clean / radio edit /
instrumental tracks (`release.musicbrainz_status`,
`release.removed_out_of_scope_tracks` in the package). Remixes also link to an
original already in the catalogue when exactly one song has that base title.

Singles: survey in `exports/single_survey_20261111.json`. Only singles where
Lil Wayne is the lead artist (solo, "Lil Wayne feat. X", "Lil Wayne & X");
121 singles where he is featured belong to future guest appearances.
`release_group_relations` stores MusicBrainz "single from" links;
`src/release_relations.py` lists the singles of an album (stored links plus
shared recordings).
Not imported: Off Off Off (no MusicBrainz status), Hercules (bootleg),
F**k Today (Rebirth mix) (needs checking), singles with Mixtape/Street type or
"bootleg" in the title (unofficial), and 45 singles credited "X & Lil Wayne"
(Wayne not first) — decide together with guest appearances.

External sources: lilwaynehq.com returns HTTP 403 to automated requests
(never bypassed). The user saves pages in the browser; `src/lilwaynehq_source.py`
parses those local HTML files (singles, mixtapes, collaboration albums) and
compares them with the catalogue. HQ also has an "Every Song" list of about
1800 tracks (30+ alphabetical pages, not exported).
Wikipedia "Lil Wayne albums discography" lists 29 mixtapes (used for the
mixtape step).

EPs: only original EPs. Label compilation EPs (Weezy Workout, Weezy Flow,
Skateboard Weezy, Funeral Features) are excluded in the review queue; The
Leak 3 and Mr. Carter look like compilations and stay pending with a note.

Added on November 11, 2026 with `add_release_groups.py` (editions selected
automatically): Rebirth, I Am Not a Human Being, Tha Carter IV,
I Am Not a Human Being II, FWA, Tha Carter V, Funeral, Tha Carter VI.
`verify_catalog.py` reports 2 warnings: two additional editions (Rebirth 2009,
FWA 2015) have only year-level dates in MusicBrainz.

The first six release groups:

| Release | Database ID | Tracks |
|---|---:|---:|
| Tha Block Is Hot | 1 | 17 |
| Lights Out | 2 | 19 |
| 500 Degreez | 3 | 21 |
| Tha Carter | 5 | 21 |
| Tha Carter II (standard US CD, canonical) | 7 | 22 |
| Tha Carter III (US original, canonical) | 8 | 16 |
| Tha Carter III (Target limited edition) | 9 | 23 |
| Tha Carter III (2008-08-18 "new version", Pussy Monster at 14) | 10 | 16 |

IDs 8–10 were verified in the database after the import.

The Tha Carter II deluxe edition (ID 6: 22 tracks + 15 Screwed & Chopped
mixes) was removed on November 11, 2026 together with its 15 S&C recordings,
following the version scope rule below
(backup: `lilwayne_backup_20261111_115906_pre_remove_sc.db`). Database IDs 4
and 6 are unused; do not reuse them.

### Version scope (user decision, November 11, 2026)

Screwed & Chopped, clean/edited and instrumental variants are not catalogued —
neither as editions nor as tracks. Besides the original, the only version type
kept is an official remix, i.e. a remix on a release of the original
(official) discography. `src/version_policy.py` enforces this in package
validation and in edition selection.

Extended on November 13, 2026 (user decision): every version of a song is
kept apart. Version types: `album` (the main version), `remix`, `demo`,
`snippet`, `original` (OG / early / "unreleased version"), `alternate`
(alternate / "X version" / unreleased verse), `extended`, `live`, `acapella`.
`src/version_types.py` (`split_version`) reads them from trailing bracketed
markers; neutral labels (Dirty, Mastered, Bonus, Prod. by ..., Full Version)
are the same version; freestyles, covers, skits stay their own songs. Clean,
instrumental and S&C stay out of scope. Code that needs "the song itself"
(same-song durations, merges, remix unlinking) uses only `album` versions.
`src/split_versions.py [--apply]` turned 40 earlier separate recordings into
typed versions (17 moved under their song, 23 recordings without a
catalogued base became the song with a typed version; 11 ambiguous ones are
listed for review). Backup before: `lilwayne_backup_20261113_024629_pre_split_versions.db`.

### Official / unofficial discography (implemented November 11, 2026)

Every release group belongs to the `official` or `unofficial` discography,
independently of its category. Stored in packages as
`release_group.discography`, in the review queue as `confirmed_discography`
and in SQLite as `releases.discography`; package folders are
`data/release_groups/<official|unofficial>/<category folder>/`.
Rules (`resolve_discography` in `release_group_package.py`):
`studio_album`, `collaboration_album` are always official, `bootleg` always
unofficial; `ep` and `single` default to official; `mixtape`, `remix`,
`compilation`, `other` must be decided per release (e.g. official mixtapes
vs. bootleg "mixtapes"). The official discography uses only MusicBrainz
releases with status Official; unofficial may also use Bootleg / no status
(never Promotion). A recording counts as official if it appears on at least
one official release (computed, not stored). An import never flips an
existing release's discography silently. In the unofficial discography an
ambiguous same-song match creates a new recording instead of stopping the
import (`merge_recordings.py` joins such pairs afterwards).

### Remix model (implemented November 11, 2026)

A remix is a version (`version_type = 'remix'`, e.g. `version_name = 'Rock remix'`)
of the original song recording; its own MusicBrainz Recording ID is stored in
`versions.musicbrainz_recording_id` (unique), the recording keeps the
original's ID. Packages mark remix tracks with `remix_of_recording_id`
(and `remix_of_title` when MusicBrainz names the original), set from a
MusicBrainz "remix" relationship or, as a fallback, from a bracketed remix
marker plus a same-title original in the same package (`src/remix_links.py`).
An unlinked remix title is a validation error (manual review). If MusicBrainz
names an original that is not catalogued yet, the original song recording is
created without versions; its own release adds the album version later.
`src/relink_remixes.py` converts remixes imported earlier as separate
recordings (used once for "Banned From NO (remix)").

The database has been verified:
- MusicBrainz Release IDs are present.
- Release Group IDs are present.
- Track IDs are present.
- Recording IDs are present.
- Track durations are present.
- No duplicate MusicBrainz recording IDs were detected.
- No foreign key errors were detected.
- Track positions and disc numbering were checked.


## 7. Existing Scripts

Before changing any script, read its current implementation.

### `verify_catalog.py`

Checks the database dynamically.

It verifies:
- Releases and track counts
- Missing MusicBrainz metadata
- Missing recordings and versions
- Missing durations
- Duplicate track positions
- Track numbering continuity
- Duplicate MusicBrainz IDs
- Foreign key integrity
- Release date precision

### `import_release.py`

Shared import logic for one release edition (`import_release_data`), run
inside the caller's transaction. It has no CLI of its own; packages are
imported with `import_release_group.py`.

`release_tracks.track_title` stores the title as printed on each edition;
`recordings.title` stays the main title. A changed edition title on re-import
stops the import instead of being overwritten silently.

It handles:
- Artists
- Sources
- Releases
- Recordings
- Versions
- Release tracks
- Credits
- MusicBrainz identifiers

Imports are transactional and roll back on errors.

Existing releases are matched primarily by MusicBrainz Release ID.

### `validator.py`

Validates one release edition (release + full tracks); used for every edition
of a package by `release_group_package.py`.

It checks:
- Required release fields
- Source structure
- Track structure
- Track positions
- Credits
- Duplicate track positions
- Track numbering continuity

### `musicbrainz_catalog.py`

MusicBrainz API helpers: `api_request`, `get_tracklist`, `display_tracklist`,
`create_release_json`. No CLI.

### `release_group_package.py`

Package format helpers: category list and folders, title-only filenames
(`package_filename`, `package_path`), collision helpers, package validation
(schema 1 and 2; new packages are schema 2), `iter_resolved_editions` and
`upgrade_package_to_v2`.

### `edition_delta.py`

Schema 2 deltas, pure functions: `resolve_edition_tracks` (canonical tracks +
`track_changes` + `track_metadata` -> full track list) and
`diff_edition_tracks` / `make_delta_edition` (full track list -> delta,
round-trip checked).

### `version_policy.py`

Version scope rule: detects Screwed & Chopped, clean/edited and instrumental
editions and bracketed track markers.

### `edition_selection.py`

Automatic edition selection (pure functions): canonical = first official
standard edition (standard, then US, worldwide, others, then date; a
same-rank edition with a strict subset track list wins); additional = official
in-scope editions with a different set of songs. Skips represses, the same
songs in another order/disc layout or under other Recording IDs, non-official
and out-of-scope editions. Songs are identified with the same-song rule;
5-15 s differences go to manual review.

### `add_release_groups.py`

Batch CLI: `--category <category> [--discography official|unofficial]
[--dry-run] [--replace] RELEASE_GROUP_ID ...` (`--discography` is required for
mixtape, remix, compilation and other).
Approves the groups in the review queue, selects editions, builds and saves
schema 2 packages. Never writes to SQLite; incomplete MusicBrainz data aborts
that release group.

### `remix_links.py`, `relink_remixes.py`

Remix detection/linking (pure functions) and the tool that turns earlier
separately imported remix recordings into remix versions (`--apply`).

### `duplicate_guard.py`, `titles.py`, `merge_recordings.py`

Same-song rule and catalogue lookup; title normalization; merge tool for
same-song recordings (`--apply`, dry run by default; 5-15 s pairs are only
listed).

### `wikipedia_source.py`

Second source via the MediaWiki API: `fetch_releases("Mixtapes")` parses a
table of "Lil Wayne albums discography" (title, with, released, label,
format). `python wikipedia_source.py Mixtapes`.

### `release_relations.py`

`python release_relations.py "Tha Carter III"` lists the singles of an album.

### `remove_release.py`

Removes one edition by MusicBrainz Release ID with only its exclusive data
(shared recordings/versions are kept); refuses when local files are linked.
CLI makes a dry run and asks for confirmation.

### `build_release_group_package.py`

Builds a package for an approved candidate from `reviewed_candidates.json`
and saves it to `data/release_groups/<category folder>/<title>.json`.
Refuses to save when the file belongs to a different release group.

### `import_release_group.py`

Imports all editions of one package in a single SQLite transaction; any
error rolls back the whole package. Re-importing the same package is safe.

### `review_candidates.py`

Interactive review queue (`data/reviewed_candidates.json`): approve, exclude
or keep pending, confirm category, select canonical and additional editions.
Never writes to SQLite.

### `view_release.py`, `search_catalog.py`

Read-only viewers: tracklist of a release (edition title, with main title when
different) and search over release titles, main and edition track titles and
credited artists.

### `scan_discography.py`

Read-only MusicBrainz discography scanner.

It:
1. Finds Lil Wayne's MusicBrainz artist entry.
2. Retrieves release groups using pagination.
3. Compares discovered release groups with SQLite.
4. Displays existing releases and new candidates.
5. Shows MusicBrainz category statistics.
6. Runs preliminary release classification.

It does not modify the database.

### `classify_release.py`

Contains preliminary release classification logic.

It attempts to classify releases into categories such as:
- Studio album
- Mixtape
- EP
- Single
- Remix
- Other
- Manual review

A synthetic classification test has passed, but the classifier is not yet sufficiently reliable for automatic database imports.

### `genius_source.py`

Genius API (official). The token is read from the user environment variable
`GENIUS_ACCESS_TOKEN` (never stored in project files). Lil Wayne = Genius
artist 4. `python genius_source.py songs` exports all his Genius songs to
`exports/genius_songs_4_<date>.json` (Nov 12, 2026: 3102 songs, 1328 with
Lil Wayne as primary artist; 692 of those have no title match in the
catalogue — the base for unreleased/leaked songs).

Local library (user's own files, Nov 12, 2026):
a folder of the owner's own music files (its path is given on the command line) —
about 190 folders, 3795 MP3s with covers, .nfo and playlists: retail albums,
official mixtapes, ~120 bootlegs (2007-2011) and unreleased collections.
Files stay where they are; only paths and metadata are catalogued.

### Unofficial discography from the local library (Nov 13, 2026)

`src/library_packages.py` built 110 bootleg packages (category `bootleg`,
discography `unofficial`, `release_group.source = "LocalLibrary"`, no
MusicBrainz IDs, durations from the files; bonus-disc folders join their
release as disc 2; clean/instrumental tracks left out; another artist's song
keeps that artist as `main_artist` credit). Import: tracks reuse catalogued
songs by the same-song rule; in the unofficial discography an ambiguous match
becomes its own recording instead of stopping the package, and
`merge_recordings.py --apply` (repeated until "Nothing to merge") joins the
remaining same-song pairs. Result: 287 releases (177 official, 110
unofficial), 2861 recordings (1194 released, 1667 leaked), 2541 linked local
files (2098 recordings have a file). Backup before:
`lilwayne_backup_20261113_022311_pre_bootlegs.db`.

Tracker model (schema): table `eras` (empty so far), `recordings.era_id`,
`recordings.leak_date`, `recordings.availability_override`,
`recordings.genius_song_id`; view `recording_availability` computes
released (on an official release) / leaked (only on unofficial releases) /
unreleased (no release), a manual override wins.
`data/sources/lilwaynehq_songs.csv`: the user's export of LilWayneHQ's
"Every Song" list (1827 rows: Letter, Song Title, Artists, Project; '–' = no
project).

### `song_crosscheck.py` (Nov 13, 2026, read-only)

`python song_crosscheck.py [genius.json] [hq.csv]` compares the Genius songs
(Lil Wayne primary) and the HQ CSV with all catalogued titles (recordings and
edition spellings) -> `exports/song_crosscheck_<date>.json`. Statuses:
`in_catalogue` (same key; censor stars and labels like Mastered/CDQ ignored),
`probable` (similar spelling on the project HQ names; version markers and
numbers must agree), `variant` (only the base title is catalogued: demo,
snippet, remix...), `missing`. `role` = lead (Genius primary or HQ credit
starting with Lil Wayne) / featured. Result Nov 13: 2398 songs; lead 1697
(1000 in catalogue, 54 probable, 119 variant, 524 missing), featured 701.

### Unreleased songs and versions (Nov 13, 2026, `unreleased_songs.py`)

User decision: songs without a release form the "unreleased" part of the
tracker; every version is kept apart. `python unreleased_songs.py
<crosscheck.json> [--apply]` (lead songs only): missing -> new recording
without versions (status `unreleased`; the first release or file that has
it adds the `album` version — `duplicate_guard.song_without_main_version`,
used by the import); "X (Demo)" etc. -> typed version of X (status
'unreleased', `versions.first_known_date` and `versions.genius_song_id` from
Genius); probable -> `recording_aliases` (other spellings; the same-song
lookup uses them); in_catalogue -> `genius_song_id`. Provenance in
`recording_sources` (Genius song page, HQ list with project), HQ co-artists
as credits. Non-songs (tweets, apologies...) and ambiguous bases are
skipped. A second run with an old cross-check creates nothing.
New schema: `recording_aliases`, `versions.genius_song_id`, view
`version_availability` (per version; `versions.availability_status` is a
manual override). Result: 3395 recordings (1192 released, 1652 leaked,
551 unreleased), 2988 versions, 48 aliases, 1128 recordings / 65 versions
with Genius ID. Backup before: `lilwayne_backup_20261113_025328_pre_unreleased.db`.
After new imports run `split_versions.py` (marker titles -> versions) and
`merge_recordings.py`.

### Manual song review (Nov 13, 2026, `song_review.py`)

The owner's decisions on ambiguous songs are stored in
`data/song_review_decisions.json` (action, IDs, date, reason) and applied by
`python song_review.py [--apply]` (already applied ones are skipped):
`merge`, `alias`, `make_version` (a catalogued recording becomes a typed
version of another song), `add_version` (version known only from HQ/Genius),
`keep_separate` (documented different songs). First round: Sorry 4 the Wait
2022 reissue renamed the 2011 freestyles (same positions/durations) — 10
duplicates merged, 2022 names kept as edition titles and aliases; Hustler
Musik radio edit, Fireman (The Suffix: Remastered) and a bootleg My Life
merged; Miami (W. Carter Collection) = original version of Miami (Blow);
Where the Cash At (W. Carter Collection 2) = alternate version of the
Dedication 2 track; HQ "Grown Man [Original]" and "Famous (2014 Version)"
added as versions; Mr. Carter (2006) / Grown Man (Lights Out vs C2) / Red
Magic / First Place Winner kept separate. `merge_recordings.merge` now also
moves aliases (the dropped title becomes one), the Genius ID and keeps
printed track titles. Result: 3375 recordings, 2978 versions, 66 aliases.
Backup before: `lilwayne_backup_20261113_031227_pre_song_review.db`.
Still open: A Millie (3 bootleg recordings), I'm Good demo/remix (3 songs of
that title), Thinking To Myself, Grindin' remixes, Mr. Carter [Original] /
(Alternate Version), Grown Man [Unreleased Verse], Red Magic (Live), My Life
snippet.

### Eras (Nov 13, 2026, `eras.py`)

User decision: eras by solo studio album. `python eras.py [--apply]` builds
them from the catalogue: an album's era runs from the day after the previous
solo studio album to its own release (earliest full date of the release
group; the first era is open at the start, "After Tha Carter VI" is open at
the end) — 15 eras, `eras.musicbrainz_release_group_id`. Song era
(`recordings.era_id`, `era_source`): 'album' = on a solo studio album (any
edition), otherwise 'date' = era of the date by which the song surely
existed (release dates, first_known_date, recording_date, leak_date); a
year/month-only date that lies in one era is 'date'; one that crosses an era
boundary gets the earliest era of that period, 'year' (user decision: songs
on bootlegs are usually recorded before they leak; "2008" -> Tha Carter III);
'manual' is never overwritten. Re-run after imports. Result: 3136 songs with
an era (918 'year'), 259 without any date (239 unreleased, mostly HQ-only).
Backup before: `lilwayne_backup_20261113_030128_pre_eras_year.db`.

### Remaining library folders (Nov 13, 2026)

`library_matching.py` now cleans tag titles before comparing ("(Prod. by
...)", "(Feat. ...)", trailing "Freestyle", "Freestyle (X)" = X, "pt." =
part, cp1252 apostrophe), maps "SQ6 Remixz" to SQ6: The Remix, links the
matching files of an official folder found by name even when its track list
differs (SQ1/SQ2/SQ4, No Ceilings And BONUS: a file belongs to a version, not
to a position; `PARTIAL_MIN`), and matches a misnamed official folder
(retail / official_mixtape only) by its track list alone when both overlaps
are >= 0.8. `library_packages.py` got `--only/--exclude/--category/
--discography/--title/--release-date/--label/--evidence`; in a folder with
per-file artist tags the tag names the track's main artist (credits).
User decisions: "The Dedication" (Wikipedia: 2005-12-13, On the Low; not in
MusicBrainz) is an official mixtape built from the library folder (the
Gangsta Grillz copy links to it); 36 DJ compilations / unofficial mixtapes
became bootlegs, mixed Juelz Santana / Eminem compilations with all tracks;
left out: Ringtones VR 7, an interview, a DJ apology speech, NoFS mash-ups,
Ebony & Ivory (mash-ups), Dedication III Buck'D Up (DJ re-edit).
`merge_recordings.py` safeguards: all durations of a pair within the limit
(no drift along chains), never two recordings of one release, never
`keep_separate` pairs. Result: 324 releases (178 official, 146 unofficial),
3950 recordings (1205 released, 2254 leaked, 491 unreleased), 3491 linked
files (2787 recordings with a file); all library folders are handled.
Backup before: `lilwayne_backup_20261113_133834_pre_library_step2.db`.
Open: earlier merges (before the safeguards) joined some different tracks of
one release (e.g. "Wasted" 300 s / 386 s on Jail Time and Just Record Me,
3 "Interlude" on Starring In Mardi Gras, "Prom Queen (Remix) BONUS" on Pre
Rebirth) — needs a split tool; 7 recordings with ambiguous version bases
(`split_versions.py`).

`split_release_tracks.py [--apply]` gave the wrongly merged tracks of one
unofficial release their own recordings (3 cases: Prom Queen (Remix) BONUS,
two Interludes, an "unreleased" track; files follow by duration). Different
remix versions on one release (Wasted Remix / Mega-Mix) are correct.

### Guest appearances (Nov 13, 2026)

User decisions: guest appearances are based on MusicBrainz AND Genius
(cross-checked with HQ); they are stored as songs only, without the other
artist's releases; all surveyed songs at once.
`python guest_survey.py` (read-only): MusicBrainz recordings credited to
Lil Wayne but not first (browse, 5810 recordings, 2445 as guest; cached in
`exports/musicbrainz_guest_recordings_<date>.json`), Genius songs with
another primary artist, HQ rows not led by Lil Wayne; matched by (main
artist, song key; 'Baby' = Birdman, '#' = censor star) ->
`exports/guest_survey_<date>.json`: 1915 guest songs, 923 already in the
catalogue by title (mostly Wayne's own songs on DJ-hosted mixtapes that
MusicBrainz credits to the DJ), 22 group songs (Hot Boys, Sqad Up, Young
Money).
`python guest_appearances.py <survey> [--apply]`: recording with
`artist_id` = main artist, `recordings.appearance = 'guest'`, credits for
every credited artist (Lil Wayne featured_artist, or main_artist on "X &
Lil Wayne"), first MusicBrainz ID on the recording, the others in
`recording_musicbrainz_ids`, Genius ID, provenance (MusicBrainz recording
pages, Genius page, HQ). Known from MusicBrainz or with an HQ project ->
`availability_override = 'released'`, date = first release date; Genius/HQ
only -> computed 'unreleased' (release unknown). Own-song tools ignore guest
songs (`song_without_main_version`, `song_crosscheck.catalogue_rows`,
`unreleased_songs`; `split_versions` skips songs without versions).
Result: 5870 recordings (1915 guest: 1137 released, 778 status unknown).
Backup before: `lilwayne_backup_20261113_135853_pre_guests.db`.
`python guest_matches.py <survey> [--apply]` judges the 923 title matches
(`exports/guest_matches_<date>.json`): 'same' (MusicBrainz ID already
catalogued, length within 15 s, main artist already credited or a mixtape
DJ/host -- a credit or host never outweighs lengths more than 15 s apart)
-> the catalogued song gets the MusicBrainz IDs, Genius ID, sources and the
other artists as credits (not DJ hosts); 'different' (all lengths > 15 s
apart) -> added as guest songs; 'review' -> only listed (user decision).
Re-run until the counts stop changing (new credits can settle a review).
Result (stable): 419 same, 99 different, 405 review; 5969 recordings, 2014
guest songs (1236 released, 778 status unknown). Backup before:
`lilwayne_backup_20261113_140837_pre_guest_matches3.db`.
Open: 405 'review' title matches (mostly Genius/HQ-only without lengths);
778 Genius/HQ-only guest songs without a known release; 22 group songs.

ColleGrove (2016, MusicBrainz credits only 2 Chainz): user decision -- a
2 Chainz & Lil Wayne collaboration album (evidence in the package); imported
from `data/release_groups/official/collaboration_albums/collegrove.json`.
`guest_appearances.py --promote [--apply]` turns guest songs that are now on
a release of Wayne's discography into his songs (appearance cleared, artist
Lil Wayne, the other artist as main_artist credit) and merges guest copies
of the same artist with the same title as another track of that release.
Backup before: `lilwayne_backup_20261113_142018_pre_collegrove.db`.

### Credits from Genius (step 3, Nov 13, 2026)

User: credits for every song, from Genius (and Discogs later), automated.
`python genius_details.py [--guest-matches ... --guest-survey ...]` downloads
/songs/:id for every catalogued Genius ID (and the review list) into
`exports/genius_song_details.json` (resumable; album, primary/featured
artists, producers, writers, other credited roles). `genius_source.api_get`
also retries read timeouts. `python genius_credits.py [--apply]` writes
credits (`credits.source = 'Genius'`; roles main_artist, featured_artist,
producer, writer, other roles in snake_case; business entries left out;
Lil Wayne is not added as main/featured on his own songs; a version's
Genius ID gives version credits). `guest_matches.py` uses the Genius album
to settle 'review' matches (album = release of exactly one candidate ->
same; album known and no candidate on it -> different).

Discogs (token in the user environment variable `DISCOGS_TOKEN`, never in
project files; User-Agent, 1.1 s between requests):
`python discogs_source.py fetch` finds the Discogs master of every official
release group through the MusicBrainz release group's Discogs URL relation
(else a Discogs search) and caches its main release (track list, per-track
credits, release credits with track ranges) in `exports/discogs_releases.json`.
`python discogs_credits.py [--apply]` matches tracks (position + title, else
a unique title) and writes credits with `source = 'Discogs'` (same role names
as Genius; release credits only when their range covers the track, plus
mastering; "Dwayne Carter" = Lil Wayne; "(2)" suffixes dropped; album
version -> recording credits, other versions -> version credits; no repeats
of existing credits).

State Nov 13, 2026 (15:15): Discogs data for 79 of 143 official release
groups (singles mostly have no Discogs master) -> 951 credits on 731 tracks.
Genius details downloaded for ~1650 of 2901 songs (download interrupted on
purpose); Genius credits applied for those (11021 credits, `source =
'Genius'`; "Recorded At"/"Mixed At" studios left out). Album-based
guest_matches applied: 490 same, 169 different (66 more guest songs), 264
review. 6038 recordings, 2070 guest songs. Backup before the last step:
`lilwayne_backup_20261113_151338_pre_genius_credits2.db`.
Nov 14, 2026: Genius download finished (all 2901 songs); second pass applied:
guest_matches stable at 505 same / 209 different (38 more guest songs) /
209 review; Genius credits for all songs (20450 with `source = 'Genius'`).
Backup before: `lilwayne_backup_20261114_153702_pre_genius_pass2.db`.
To refresh later: `python genius_details.py --guest-matches
../exports/guest_matches_20261113.json --guest-survey
../exports/guest_survey_20261113.json` (continues from the cache), then
`python guest_matches.py ../exports/guest_survey_20261113.json --apply`
(repeat until the counts stop changing) and `python genius_credits.py
--apply` -- each after a backup.

### Web tracker (step 4, Nov 14, 2026)

`python tracker_server.py [--port 8765]` (from `src/`) serves a local,
read-only tracker at http://127.0.0.1:8765 (standard library only, binds
to 127.0.0.1, SQLite opened read-only). Page: `web/index.html`, `web/app.js`,
`web/style.css`; queries: `src/tracker_data.py`; API `/api/summary`,
`/api/songs`, `/api/song/<id>`, `/api/releases`, `/api/release/<id>`.
Songs grouped by era with status (released / leaked / unreleased), filters
(search incl. aliases, era, own / guest, version type, has a local file),
song panel (versions with status and releases, credits by role with source,
aliases, MusicBrainz / Genius / HQ links); releases tab (official /
unofficial, track lists). Audio files are never served -- only "a local
file exists". Tests: `tests/test_tracker.py`.
User decisions (Nov 14, 2026): the site is in English only; eras show cover
art and a short description. `python era_info.py [--apply] [--refresh]`
saves the Cover Art Archive front image of each era's album to
`web/covers/era_<id>.jpg` and the first 3 sentences of the album's English
Wikipedia article (found via MusicBrainz -> Wikidata -> enwiki; URL kept as
CC BY-SA attribution) in `eras.summary`, `eras.summary_url`,
`eras.cover_file` (eras.py never overwrites them; the open era gets a
generated text). Backup before: `lilwayne_backup_20261114_152847_pre_era_info.db`.
Improvements (user's order, Nov 14, 2026): (1) deep links `#/songs`,
`#/song/<id>`, `#/release/<id>`, `#/artist/<id>`, `#/people`, `#/review`
(back button works); People tab (`/api/people`) and artist pages
(`/api/artist/<id>`: songs and roles); clickable credits (one entry per
person and role); producers in the song list; sorting. (2) "Needs review"
tab: `src/review_queue.py` lists guest_matches 'review' songs and
split_versions ambiguous bases; the page POSTs one decision to
`/api/review`, which only appends it to `data/song_review_decisions.json`
(same-origin JSON, validated against the open questions, write lock);
`song_review.py --apply` applies them (new actions `guest_same`,
`guest_different`, `skip`; `make_version` / `keep_separate` as before).
User decisions (Nov 14, 2026, later): the Songs tab is a flat song list (the
era is a column and a filter; era blocks with cover and description move to
the era page); views List / Grid / Compact (remembered in localStorage,
`?view=grid` in the URL); sources are shown as one badge per service
(monogram in the service colour, no copied logos; further links of the same
service under "+N").
Nov 14, 2026 (later): filters (era, own/guest/all, version type, sort) sit
in one "Filters" menu and the view in one menu button ("has local file"
removed). (3) `python release_media.py [--apply]` (resumable): official
releases get the Cover Art Archive front image (release, else release
group) in `web/covers/release_<id>.jpg` (`releases.cover_file`) and the
streaming links of every edition in the release group (one per service,
US Apple Music preferred) in the new table `release_links` (source
'MusicBrainz'); library releases (bootlegs, The Dedication) get the cover
image copied from their own folder. Result: covers for 152/180 official and
145/146 unofficial releases (singles without CAA art are missing); Spotify
links for 53 releases, Apple Music 63, Deezer 54, Tidal 31. A song shows the
cover of the release it came out on (earliest official, else any; era cover
as fallback) and "Listen on" badges from its official releases. Backup
before: `lilwayne_backup_20261114_160428_pre_release_media.db`.
The user's "Spotify key" (`spak_...`, stored only in the user environment
variable `SPOTIFY_API_KEY`) is rejected by the Spotify Web API (401); track
links need a Client ID + Client Secret from the Spotify Developer Dashboard.
`tracker_server.py` refuses a port that is already in use (Windows
SO_REUSEADDR let a second server share it silently).
Wrongly linked versions (user report: Funeral's "Help" held the 2002 SQ2
"Help (remix)"): a mixtape remix had been attached by the "unique catalogued
title" rule to a song released later. Fixed with `song_review.py` action
`detach_version` (Help (remix) -> own song; bootleg "Lost (Remix)" -> guest
song Gorilla Zoe "Lost (Remix)"; bootleg "Someone To Love Me (Remix)
(Alternate Version)" -> guest song Diddy - Dirty Money "Someone To Love Me
(Bad Boy Remix)", the empty old song merged into it). `verify_catalog.py`
section 3.6 now warns about remixes released years before their original.
"Needs review" got a third kind, `spread`: one song on a bootleg 6+ years
before its official release (White Girl 2008/2015, HOLLYWOOD 2006/2020);
answer `split_off` moves the bootleg tracks (and matching files) to a new
song. Promotion of guest songs (`--promote`) now needs an official release.
Backup before: `lilwayne_backup_20261114_162024_pre_version_fixes.db`.
Nov 14, 2026 (evening), user decisions: totals and an "about" text in the
footer (personal non-commercial project 2026, data sources, not affiliated,
no audio hosted); a back-to-top button; the sections (Songs, Releases,
People, Needs review) moved to a drawer opened from a menu button on the
right; notification and settings icons next to it (placeholders for later).
Eras before the solo career: `eras.py` also makes eras of the group albums
released before the first solo studio album (Hot Boys "Get It How U Live!!"
-1997-10-28, "Guerrilla Warfare" 1997-10-29 - 1999-07-27); era_info.py
fetched their covers and summaries. Backup before:
`lilwayne_backup_20261114_162945_pre_group_eras.db`.
Spotify: Client ID / Secret in the user environment variables
`SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` (client-credentials flow; never
in project files). `python spotify_source.py [--apply]` fetches the tracks of
every Spotify album linked to an official release (cache
`exports/spotify_albums.json`), matches them (position + title, else a
unique title) and stores track links per version in the new table
`version_links`; the song panel then links straight to the track
("▶ Spotify" on the version). Result: 325 versions with a Spotify track link
(27 albums). Backup before: `lilwayne_backup_20261114_163247_pre_spotify.db`.
Note: Python's TLS failed once for accounts.spotify.com until Windows had
fetched the intermediate certificate (a PowerShell request did that);
verification is never disabled.
(4) done Nov 14, 2026: Eras tab (cover grid) and era page `#/era/<id>`
(`/api/era/<id>`: summary, official releases of the period, songs by status,
top producers / featured artists); Statistics tab (`/api/stats`: songs by
era and by year of first appearance, top producers / featured / writers,
version types) drawn as HTML bars with legend, hover tooltip and a table
view; "⤓ CSV" exports the songs shown. Status colours (released aqua /
leaked orange / unreleased blue) were checked with the dataviz palette
validator (all pairs, light on #ffffff and dark on #1d1c1a: PASS; light aqua
< 3:1 -> values are labelled and a table view exists); badges show a
coloured dot with text in the text colour.
Streaming search (`src/streaming_search.py`): MusicBrainz links cover only
part of the discography (e.g. no Hot Boys albums), so the discographies of
Lil Wayne, Hot Boys, Young Money, Rich Gang, Birdman, ... are read from
Spotify (artist albums: max 10 per page now) and Apple Music (iTunes Search /
Lookup API, no key, ~20 requests/min); an album is accepted for an official
release only with the same title and >= 60% of its tracks; cache
`exports/streaming_catalog.json`.
UI (user, Nov 14, 2026): the menu button and the drawer are on the left;
the Songs list renders 50 songs and appends the next 50 while scrolling
(IntersectionObserver + viewport fill, no "Show more"); an "About Lil Wayne"
page (`#/about`, `/api/profile`) built by `python artist_profile.py` into
`data/artist_profile.json`: MusicBrainz artist URL relations (social,
streaming, official sites, reference sites; dead services dropped), the
English Wikipedia intro (6 sentences, CC BY-SA attribution) and the
MusicBrainz image (Wikimedia Commons "Lil Wayne in Concert", Michael
Williams, CC BY 2.0, credited under the photo) saved as web/covers/artist.jpg.
Added Nov 14, 2026 (user: "add everything you proposed, in order"):
(1) Timeline `#/timeline` (`/api/timeline`: official releases per year, one
per release group, era ends, bootleg count + list, unreleased versions first
known that year). (2) Bell = "Today" popover: releases on this day in
earlier years (`/api/on-this-day/MM-DD`, badge count), a random song, a link
to the open questions. (3) "Compare N versions" table in the song panel.
(4) "Lyrics on Genius" link (lyrics themselves are copyrighted, never shown).
(5) Settings dialog (gear; localStorage only): theme system/light/dark
(`data-theme` on <html> overrides the OS), default view, songs shown at
start, date style ISO/long. (6) Collaborations: people list shows the years
of working together (`people[].years` from `song_years`), the person panel a
"Songs with Lil Wayne by year" chart. (7) `python export_static.py [--out
dist]` writes a static copy (page + every API answer as api/*.json; the page
reads them when `window.LWT_STATIC`): no "Needs review" tab and no
local-library facts (file marks/counts) -- 6076 songs, 5043 artists, ~66 MB.
Publishing it is an outward-facing step: only after the user confirms where.
User request: songs without an era cover and releases without a cover show
the Lil Wayne photo (covers/artist.jpg) instead of an empty placeholder.
UI (user, Nov 14, 2026, later): song / release / person details open in a
centred modal window (`#panel` is the backdrop; click outside, × or Esc
closes); a release shows its music services (Spotify, Apple Music, Deezer,
Tidal, YouTube, Amazon, SoundCloud) under the title and the other sources
(MusicBrainz, Discogs, ...) under the track list; opening the bell marks
the day's notifications as read (localStorage `lwt-notify-read`, badge
comes back the next day).
Player (user, Nov 14, 2026; reference player.juicewrldapi.com): `web/player.js`.
"▶ Listen" under every playable song (list / grid / compact), per track and
"▶ Play all" on releases, per version in the song window; a bar at the bottom
(prev / play / next, seek, source) opens a full-screen player (blurred cover
behind a centred window, the service embed, "Up next" queue). Sources per
version (`tracker_data.playables`): Spotify track id (version_links, Spotify
iFrame API: full tracks when signed in to Spotify, 30 s previews otherwise),
Apple Music track page (embed.music.apple.com, no script control), and on
the local server only a file of the owner's library (`/api/audio/<file id>`,
files table only, Range requests; `export_static.py` drops `file_id`).
Settings "Play with": Spotify (default) / Apple Music / Local files, then the
next available source. Decision: leaked / unreleased audio is never
uploaded or hosted publicly (no rights); the public copy plays official
streaming embeds only.
Player, second round (user, Nov 14, 2026): queue ("+" next to every ▶ adds
to the queue; remove / clear in "Up next"), shuffle (restores the order when
switched off), repeat off / queue / song forever / song once, volume + mute
(local files only: the Spotify and Apple embeds have no volume API), state in
localStorage `lwt-player`; monochrome SVG icons; the full player has the
player on the left and "Up next" / "Lyrics" tabs on the right. Song grid: 4
per row (3 / 2 on narrow screens).
Synced lyrics (experiment, local server only): `src/lyrics_source.py` asks
LRCLIB (lrclib.net search, chosen by length within 10 s, synced first) when
the Lyrics tab is open; `/api/lyrics/<version id>`; kept in memory only,
never stored in SQLite / files / the static export (lyrics are copyrighted;
a public site would need a licensed provider such as Musixmatch's commercial
API). Lines follow the position of local files and Spotify (not Apple: no
position); clicking a line seeks. Tests: `tests/test_lyrics_source.py`.
Round three (user, Nov 14, 2026): every song shows its album / project
(`tracker_data.song_albums`: official before unofficial, albums / mixtapes
before singles, then the earliest) and its era; "▶ Listen" spans the card /
title cell with "+" at the right edge; shuffle and repeat look like the
other buttons (on = accent colour + dot); lyrics icon = speech bubble.
Filters and Settings use pill chips (`select[data-chips]` stays the source
of truth, `refreshChips()` draws the pills) and styled selects. Settings:
sections on the left (Appearance, Songs, Player, About) with the GitHub link
at the bottom, options on the right. Colour styles: purple (default), gold,
red, blue, green (light / dark variants, >= 4.5:1 on the surface) and "From
the playing cover" (player.js: dominant colour of the cover -> accent with
the lightness chosen for 4.5:1 contrast, plus a page tint).

### GitHub (Nov 14, 2026)

Public repository https://github.com/Poctyllla/LilWayneTracker -- code only
(owner's decision): `src/`, `tests/`, the page code in `web/`,
`requirements.txt`, this README. `.gitignore` keeps out `data/` (database
with library paths, backups, decisions), `exports/` (API caches whose terms
forbid re-publishing), `dist/`, cover art (only the CC BY 2.0 photo
`web/covers/artist.jpg` is included) and any audio. Commits use the GitHub
no-reply address. The project folder belongs to the Administrators group,
so git needs `safe.directory` for it (set globally once).
AGENTS.md was renamed to README.md (owner's decision): coding agents must be
told to read README.md at the start of a session.
Next:
Spotify / Apple Music links for official released songs; a settings page
later. The page can be checked with headless Edge
(`msedge --headless=new --screenshot=... http://127.0.0.1:8765/#/...`).
Known data issues seen on the site: an artist under two names (Genius
"Bangladesh" vs Discogs "Shondrae Crawford") -- needs artist aliases; a few
guest songs duplicate an own track (e.g. "AIN'T GONNA ANSWER").

### `library_scanner.py`, `library_matching.py` (dependency: mutagen, see `requirements.txt`)

`python library_scanner.py "<library folder>"` reads tags, durations, sizes and
SHA-256 (read-only) into `exports/library_scan_<date>.json`;
`--retitle <scan.json>` recomputes track titles / featuring / other main
artist from the stored tags after parser fixes.
`python library_packages.py <scan.json> [--kinds bootleg,unreleased]` builds
the unofficial bootleg packages from unmatched folders (never writes SQLite).
`python library_matching.py <scan.json> [--apply]` links files to catalogued
release tracks (folder -> edition by comparable title, file -> track by
position + title; censored/joined spellings handled) and writes the `files`
table (idempotent by path). Nov 12, 2026: 367 files linked (20 releases,
351 of 1194 recordings have a local file); 165 folders are not in the
catalogue (108 bootlegs, 3 unreleased collections, other compilations) —
input for the unofficial discography.

### `lilwaynehq_source.py`, `hq_packages.py`

Parses LilWayneHQ list pages saved by the user (local HTML only) and lists
entries missing from the catalogue (`TITLE_ALIASES` maps HQ names):
`python lilwaynehq_source.py singles|mixtapes|collabs "<saved page.html>"`.
`python hq_packages.py "<saved singles page>" single|group_single "Title" ...`
builds source-only packages for singles missing from MusicBrainz.

Cleanup (Nov 12, 2026): old one-off scripts, the `archive/` folder, the two
handover/overview documents and all but the 3 latest database backups were
deleted; only `README.md` (formerly AGENTS.md) documents the project now.

## 8. Latest Discography Scan

The latest successful scan was performed on November 10, 2026.

Results:

- Total MusicBrainz release groups: 381
- Existing release groups in the database: 5
- New candidates: 376

Preliminary classification of the 376 new candidates:

| Category | Count |
|---|---:|
| Studio album candidates | 17 |
| Mixtape candidates | 90 |
| EP candidates | 11 |
| Single candidates | 198 |
| Remix candidates | 34 |
| Other candidates | 21 |
| Unclassified / manual review | 5 |
| Total | 376 |

These are preliminary classifications, not verified facts.

Important examples:
- Instrumental editions should not automatically be treated as studio albums.
- Collaboration albums must be distinguished from Lil Wayne solo albums.
- Mixtapes and official albums can have similar titles.
- Different release groups with similar titles must not be merged automatically.
- A single and its remix are separate releases, but may have relationships at recording/version level.
- Bootlegs, unofficial compilations and dubious entries require review.

## 9. Current Development Stage

As of November 10, 2026 the scanner, review queue, package builder and
package importer exist and are covered by unit tests (`tests/`).
Tha Carter III is imported; its package is
`data/release_groups/studio_albums/tha_carter_iii.json`.
All existing releases are stored as packages in `data/release_groups/studio_albums/`.

The `release_tracks.track_title` migration was applied to the working database
on November 10, 2026 (backup: `lilwayne_backup_20261110_144558_pre_track_title.db`);
all 137 existing rows were backfilled by re-importing the packages.

Package schema v2 is implemented (`src/edition_delta.py`): canonical edition
keeps full `tracks`; additional editions keep `track_changes`
(`replace` / `add` / `remove`) and `track_metadata` (per-edition Track ID,
duration and differing spelling). All packages were upgraded to v2
(v1 copies: `archive/release_groups_v1_20261110.zip`).
Tha Carter III was imported on November 10, 2026
(backup: `lilwayne_backup_20261110_145359_pre_carter_iii.db`).

An edition survey of the earlier release groups is in
`exports/edition_survey_20261110.json`.

Version model, remix model and the official/unofficial split are in place
(section 6). Next: singles and mixtapes, then a design for singles that are
part of an album.

Open items:
- Next: guest appearances (121 singles "X feat. Lil Wayne" and 45 "X & Lil
  Wayne"), unofficial discography (bootlegs, leaks, unofficial mixtapes and
  compilations), maybe Genius as a further source (API token needed).
- ColleGrove (2016) is credited only to 2 Chainz in MusicBrainz and is not in
  the review queue.
- Edition survey candidates: Tha Carter digital edition with "Shine" instead
  of "Earthquake" (position 19); digital reissues differing only in spelling.
- Remaining pending candidates in the review queue.

Workflow:

1. Scan MusicBrainz for release groups.
2. Compare discovered groups with the database.
3. Assign preliminary categories.
4. Put new candidates into a review queue.
5. Allow the user to confirm, change or exclude candidates.
6. Save review decisions in a separate JSON file.
7. Only after confirmation, retrieve detailed release editions and tracklists.
8. Validate the data.
9. Import approved releases into SQLite.

The review process must not modify SQLite. Review decisions are saved in
`data/reviewed_candidates.json`.

## 10. Development Rules

Follow these rules throughout the project:

1. Read existing files before modifying them.
2. Do not rewrite working functionality without a clear reason.
3. Do not introduce unnecessary dependencies.
4. Do not duplicate existing functions or logic.
5. Preserve existing database records and relationships.
6. Do not automatically import unverified MusicBrainz candidates.
7. Do not merge releases based only on title similarity.
8. Preserve source provenance and external identifiers.
9. Use transactions for database modifications.
10. Add validation before importing new data.
11. Keep scripts modular and reusable.
12. Prefer automation over manually hardcoding individual releases.
13. Test each meaningful change before moving to the next task.
14. Explain what changed, which files were affected and how to test it.
15. Never claim that a test passed unless it was actually executed and its output checked.

When modifying a file, provide the complete updated file when that is the clearest and safest approach.

Do not make several unrelated changes at once.

If existing code differs from this document, inspect the code and report the discrepancy before making assumptions.

## 11. Long-Term Architecture

The intended architecture should eventually support:

- Automated discography discovery
- Multi-source metadata collection
- Release classification
- Human review of uncertain candidates
- Edition selection
- Tracklist retrieval
- Recording and version deduplication
- Credits and contributor relationships
- Source provenance
- Unreleased music catalogue
- Local music library scanning
- Matching local audio files to catalogue recordings
- Exporting and browsing the catalogue

The local music library should remain under the user's control.

Do not design the project around hosting or distributing leaked or otherwise unauthorized audio.

The main priority is a reliable metadata catalogue, not simply collecting the largest possible number of entries.

## 12. How to Start Working

At the beginning of a new session:

1. Read this file.
2. Inspect the current project structure.
3. Inspect relevant existing scripts before proposing changes.
4. Check the actual database schema when database work is involved.
5. Identify the current development stage.
6. Continue from the next unfinished task.

Do not start rebuilding the project from scratch.

The immediate priority is listed in section 9 (Current Development Stage).