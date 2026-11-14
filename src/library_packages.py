"""Unofficial-discography packages from the user's local library.

Bootlegs and unreleased collections in the library are usually not in
MusicBrainz.  For every such folder (grouped by title, so a bonus-disc folder
becomes disc 2 of the same release) a package is built from the scanned tags:
category bootleg, discography unofficial, release_group.source "LocalLibrary",
no MusicBrainz IDs, durations from the files.  Catalogue rules apply:
clean / instrumental / Screwed & Chopped tracks are left out, remixes link to
catalogued originals or are marked as remixes of other artists' songs.

usage:
    python library_packages.py exports/library_scan_<date>.json [--kinds bootleg,unreleased]
        [--exclude "Title|Title"]
    # an official release known only from the library:
    python library_packages.py <scan> --only "Dedication" --category mixtape --discography official
        --title "The Dedication" --release-date 2005-12-13 --label "On the Low" --evidence "..."
"""

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

from build_release_group_package import output_path_status, save_package
from duplicate_guard import load_catalogue
from import_release import DATABASE_PATH
from library_matching import plan, release_key
from release_group_package import package_path
from remix_links import link_remixes, mark_external_remixes
from version_policy import excluded_track_reason


SOURCE_NAME = "LocalLibrary"
ARTIST = "Lil Wayne"


def split_artists(text):
    """'Tyga & Gata, Kidd Kidd' -> ['Tyga', 'Gata', 'Kidd Kidd']."""
    parts = re.split(r"\s*(?:,|&|\band\b|\bx\b)\s*", text or "", flags=re.IGNORECASE)
    return [part.strip() for part in parts if part.strip()]


WAYNE_NAME = re.compile(r"^\s*lil'?\.?\s*wa[yn]{2}e\s*$", re.IGNORECASE)
TAG_FEAT = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+", re.IGNORECASE)


def artist_tag_credits(artist_tag):
    """Credits from a per-file artist tag of a mixed compilation:
    'Juelz Santana Feat. Lil Wayne' -> [Juelz Santana: main_artist];
    'Birdman Ft. Lil Wayne & Tyga' -> [Birdman: main, Tyga: featured].
    A tag that starts with Lil Wayne gives no other main artist."""
    tag = str(artist_tag or "").strip()
    if not tag or re.match(r"^\s*lil'?\.?\s*wa[yn]{2}e\b", tag, re.IGNORECASE):
        return []
    parts = TAG_FEAT.split(tag, maxsplit=1)
    main, featured = parts[0], parts[1] if len(parts) > 1 else ""
    credits =[{"artist": name, "role": "main_artist"} for name in split_artists(main) if not WAYNE_NAME.match(name)]
    credits += [{"artist": name, "role": "featured_artist"}
                for name in split_artists(featured) if not WAYNE_NAME.match(name)]
    return credits


def folder_tracks(folder, disc, tag_credits=False):
    """Track dicts of one folder; out-of-scope tracks removed, numbering fixed.
    tag_credits: the folder mixes artists, so each file's artist tag names
    the main artist of that track (unless it starts with Lil Wayne)."""
    files = sorted(folder["tracks"], key=lambda f: (f.get("track_number") or 9999, f["file_name"]))
    numbers = [f.get("track_number") for f in files]
    renumber = None in numbers or len(set(numbers)) != len(numbers)
    tracks, removed = [], []
    for index, file in enumerate(files, start=1):
        if excluded_track_reason(file.get("tag_title")) or excluded_track_reason(file["title"]):
            removed.append(file["title"])
            continue
        # Another artist's song on a bootleg ("Rich Boy - Ghetto Rich"):
        # that artist is the main artist, Lil Wayne a guest.
        credits = [{"artist": name, "role": "main_artist"} for name in split_artists(file.get("track_artist"))]
        if not credits and tag_credits:
            credits = artist_tag_credits(file.get("artist"))
        for name in split_artists(file.get("featuring")):
            if {"artist": name, "role": "featured_artist"} not in credits:
                credits.append({"artist": name, "role": "featured_artist"})
        tracks.append({
            "number": index if renumber else file["track_number"],
            "disc_number": disc,
            "title": file["title"],
            "duration_ms": file.get("duration_ms"),
            "credits": credits,
            "local_file": file["file_name"],
        })
    return tracks, removed


def group_folders(folders):
    """{title: [folders]} — bonus discs join the release with the same title."""
    groups = {}
    for folder in folders:
        groups.setdefault(release_key(folder["title"]), []).append(folder)
    return groups


def build_package(folders, catalogue_originals, category="bootleg", discography="unofficial", title=None,
                  release_date=None, label=None, evidence=()):
    """One package for a group of folders with the same title.

    Defaults: an unofficial bootleg named after the folder.  An official
    release known only from the library (e.g. a mixtape missing from
    MusicBrainz) passes its category, discography, title, date and the
    evidence that it is official."""
    folders = sorted(folders, key=lambda f: (f.get("disc") or 1, f["folder"]))
    title = title or folders[0]["title"]
    artist_tags = {file.get("artist") for folder in folders for file in folder["tracks"]}
    tracks, removed, used_discs = [], [], set()
    for folder in folders:
        disc = folder.get("disc") or 1
        while disc in used_discs:
            disc += 1
        used_discs.add(disc)
        disc_tracks, disc_removed = folder_tracks(folder, disc, tag_credits=len(artist_tags) > 1)
        tracks += disc_tracks
        removed += disc_removed
    if not tracks:
        return None
    tracks = mark_external_remixes(link_remixes([tracks], catalogue_originals))[0]
    year = next((f["year"] for f in folders if f.get("year")), None)
    credit = folders[0].get("artist") or ARTIST
    release = {
        "title": title,
        "artist": ARTIST,
        "release_type": category,
        "release_date": release_date or (str(year) if year else None),
        "artist_credit": credit,
        "musicbrainz_release_id": None,
        "musicbrainz_release_group_id": None,
        "sources": [{"name": SOURCE_NAME, "url": None}],
    }
    if label:
        release["label"] = label
    if removed:
        release["removed_out_of_scope_tracks"] = removed
    return {
        "schema_version": 2,
        "release_group": {
            "title": title,
            "artist": ARTIST,
            "musicbrainz_release_group_id": None,
            "catalog_category": category,
            "discography": discography,
            "source": SOURCE_NAME,
            "evidence": [f"Local library folder: {f['folder']}" for f in folders] + list(evidence),
        },
        "canonical_edition": {
            "edition_name": f"{SOURCE_NAME}: {', '.join(f['folder'] for f in folders)}",
            "release": release,
            "tracks": tracks,
        },
        "additional_editions": [],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Packages from unmatched local library folders.")
    parser.add_argument("scan")
    parser.add_argument("--kinds", default="bootleg,unreleased", help="folder kinds (library_scanner)")
    parser.add_argument("--only", help="'|'-separated folder titles to use (exact)")
    parser.add_argument("--exclude", default="", help="'|'-separated folder titles to leave out")
    parser.add_argument("--category", default="bootleg")
    parser.add_argument("--discography", default="unofficial")
    parser.add_argument("--title", help="release title (one package from all selected folders)")
    parser.add_argument("--release-date")
    parser.add_argument("--label")
    parser.add_argument("--evidence", action="append", default=[])
    args = parser.parse_args(argv)
    kinds = set(args.kinds.split(","))
    only = set(args.only.split("|")) if args.only else None
    exclude = set(filter(None, args.exclude.split("|")))
    scan = json.loads(Path(args.scan).read_text(encoding="utf-8"))
    connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
    try:
        unmatched = {f["folder"] for f in plan(scan, connection)["unmatched_folders"]}
    finally:
        connection.close()
    folders = [f for f in scan["folders"] if f["folder"] in unmatched and f["tracks"]
               and (f["title"] in only if only else f["kind"] in kinds) and f["title"] not in exclude]
    originals = load_catalogue(DATABASE_PATH)["originals"]
    groups = {args.title: folders} if args.title and folders else group_folders(folders)
    saved, skipped = 0, []
    for _, group in sorted(groups.items()):
        package = build_package(group, originals, category=args.category, discography=args.discography,
                                title=args.title, release_date=args.release_date, label=args.label,
                                evidence=args.evidence)
        if package is None:
            skipped.append((group[0]["title"], "no tracks in scope"))
            continue
        try:
            path = package_path(args.category, package["release_group"]["title"], discography=args.discography)
        except ValueError as error:
            skipped.append((group[0]["folder"], str(error)))
            continue
        if output_path_status(path, None) != "new":
            skipped.append((package["release_group"]["title"], f"exists: {path.name}"))
            continue
        try:
            save_package(package, path)
        except ValueError as error:
            skipped.append((package["release_group"]["title"], str(error)))
            continue
        saved += 1
    print(f"Folders: {len(folders)}; packages saved: {saved}; skipped: {len(skipped)}")
    for title, reason in skipped:
        print(f"   SKIPPED {title}: {reason}")


if __name__ == "__main__":
    main()
