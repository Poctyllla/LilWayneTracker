"""Build one multi-edition JSON package from an approved review decision."""

import json
import time
from pathlib import Path

from edition_delta import make_delta_edition
from remix_links import link_remixes, mark_external_remixes, mark_unlinked_as_songs
from musicbrainz_catalog import create_release_json, get_tracklist
from release_group_package import (
    CURRENT_SCHEMA_VERSION,
    existing_package_release_group_id,
    package_path,
    resolve_discography,
    validate_release_group_package_data,
)
from review_candidates import REVIEW_FILE, load_review_data


TRACKLIST_ATTEMPTS = 3


def edition_name(selection, kind):
    parts = [selection.get("country") or "Unknown country"]
    if selection.get("status"):
        parts.append(selection["status"])
    if selection.get("date"):
        parts.append(selection["date"])
    if selection.get("disambiguation"):
        parts.append(selection["disambiguation"])
    prefix = "Canonical" if kind == "canonical" else "Additional"
    return f"{prefix}: {' — '.join(parts)}"


def build_edition(candidate, selection, kind, details=None):
    """Build one full edition; details may be a MusicBrainz release already fetched."""
    release_id = selection["musicbrainz_release_id"]
    for attempt in range(1, TRACKLIST_ATTEMPTS + 1):
        if details:
            break
        details = get_tracklist(release_id)
        if details:
            break
        if attempt < TRACKLIST_ATTEMPTS:
            wait_seconds = attempt * 3
            print(
                f"Tracklist unavailable; retrying in {wait_seconds} seconds "
                f"({attempt}/{TRACKLIST_ATTEMPTS})."
            )
            time.sleep(wait_seconds)
    if not details:
        raise ValueError(
            f"Could not retrieve release details after "
            f"{TRACKLIST_ATTEMPTS} attempts: {release_id}"
        )

    selected_release = {
        "id": release_id,
        "release-group": {
            "id": candidate["release_group_id"],
            "primary-type": candidate.get("musicbrainz_primary_type"),
            "secondary-types": candidate.get("musicbrainz_secondary_types", []),
        },
    }
    release_json = create_release_json(
        details, candidate["artist_name"], selected_release
    )
    release_json["release"]["release_type"] = candidate["confirmed_category"]
    if details.get("status"):
        release_json["release"]["musicbrainz_status"] = details["status"]
    if details.get("_clean_only"):
        # User decision: a single that exists only as a clean version is added.
        release_json["release"]["clean_only"] = True
    if details.get("_removed_out_of_scope_tracks"):
        # Clean / radio edit / instrumental tracks were left out (catalogue scope).
        release_json["release"]["removed_out_of_scope_tracks"] = list(
            details["_removed_out_of_scope_tracks"]
        )
    return {
        "edition_name": edition_name(selection, kind),
        **release_json,
    }


def build_package(candidate, details_by_id=None, relations=None, catalogue_originals=None):
    """Retrieve selected editions and build an in-memory validated package.

    details_by_id optionally maps MusicBrainz Release IDs to release details
    that were already fetched, so they are not requested again.  relations
    are release-group links such as 'single_from'.
    """
    details_by_id = details_by_id or {}
    canonical = candidate.get("canonical_edition")
    if not canonical:
        raise ValueError("Candidate has no canonical edition selection.")

    canonical_edition = build_edition(
        candidate, canonical, "canonical",
        details_by_id.get(canonical["musicbrainz_release_id"]),
    )
    full_additional = [
        build_edition(
            candidate, selection, "additional",
            details_by_id.get(selection["musicbrainz_release_id"]),
        )
        for selection in candidate.get("additional_editions", [])
    ]

    # Link remixes to their originals across all editions of the package.
    linked = link_remixes(
        [canonical_edition["tracks"]] + [edition["tracks"] for edition in full_additional],
        catalogue_originals,
    )
    if candidate["confirmed_category"] in ("mixtape", "bootleg", "compilation"):  # remixes of other artists' songs
        linked = mark_external_remixes(linked)
    elif candidate["confirmed_category"] in ("single", "group_single"):
        linked = mark_unlinked_as_songs(linked)
    canonical_edition["tracks"] = linked[0]
    for edition, tracks in zip(full_additional, linked[1:]):
        edition["tracks"] = tracks

    # Additional editions are stored only as their differences from the
    # canonical edition (schema 2, round-trip checked).
    additional_editions = [
        make_delta_edition(canonical_edition["tracks"], edition)
        for edition in full_additional
    ]

    package = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "release_group": {
            "title": candidate["title"],
            "artist": candidate["artist_name"],
            "musicbrainz_release_group_id": candidate["release_group_id"],
            "catalog_category": candidate["confirmed_category"],
            "discography": resolve_discography(
                candidate["confirmed_category"], candidate.get("confirmed_discography")
            ),
        },
        "canonical_edition": canonical_edition,
        "additional_editions": additional_editions,
    }
    if relations:
        package["release_group"]["relations"] = relations
    return package


def output_path_status(output_path, release_group_id):
    """Return 'new', 'same_release_group' or 'conflict' for a package path.

    Filenames contain only the release title, so two different release groups
    with the same title would map to the same file.  Such a collision must be
    resolved manually and is never offered as an overwrite.
    """
    if not output_path.exists():
        return "new"
    try:
        existing_id = existing_package_release_group_id(output_path)
    except ValueError:
        return "conflict"
    if existing_id == release_group_id:
        return "same_release_group"
    return "conflict"


def save_package(package, output_path):
    if not validate_release_group_package_data(package):
        raise ValueError("Generated package validation failed.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(package, file, ensure_ascii=False, indent=2)
        file.write("\n")


def select_candidate(review_data):
    candidates = [
        item for item in review_data["candidates"]
        if item.get("decision") == "approved" and item.get("canonical_edition")
    ]
    if not candidates:
        print("No approved candidate has a canonical edition selection.")
        return None

    for index, candidate in enumerate(candidates, start=1):
        print(
            f"{index}. {candidate['title']} | "
            f"{candidate.get('confirmed_category', 'unknown')} | "
            f"{candidate['canonical_edition']['musicbrainz_release_id']}"
        )

    choice = input("Release group number (or Enter to cancel): ").strip()
    if not choice:
        return None
    if not choice.isdigit() or not 1 <= int(choice) <= len(candidates):
        print("Invalid release group number.")
        return None
    return candidates[int(choice) - 1]


def main():
    try:
        review_data = load_review_data(REVIEW_FILE)
    except ValueError as error:
        print(f"ERROR: {error}")
        return

    candidate = select_candidate(review_data)
    if candidate is None:
        return

    # The review file is artist-specific, but keep the value with the candidate
    # so build_edition does not depend on a hard-coded artist string.
    candidate = dict(candidate)
    candidate["artist_name"] = review_data["artist_name"]

    try:
        output_path = package_path(
            candidate.get("confirmed_category"), candidate["title"],
            discography=candidate.get("confirmed_discography"),
        )
    except ValueError as error:
        print(f"PACKAGE CREATION FAILED: {error}")
        return

    status = output_path_status(output_path, candidate["release_group_id"])
    if status == "conflict":
        print(
            f"PACKAGE CREATION FAILED: {output_path} already exists and does "
            "not belong to this release group (or cannot be read).\n"
            "Two release groups have the same title; resolve the filename "
            "manually before building."
        )
        return
    if status == "same_release_group":
        overwrite = input(
            f"{output_path.name} exists for this release group. Overwrite? (y/n): "
        ).strip().lower()
        if overwrite != "y":
            print("Package creation cancelled.")
            return

    try:
        package = build_package(candidate)
        save_package(package, output_path)
    except ValueError as error:
        print(f"PACKAGE CREATION FAILED: {error}")
        return

    print(f"\nPackage saved: {output_path}")
    print("It has not been imported into SQLite.")


if __name__ == "__main__":
    main()
