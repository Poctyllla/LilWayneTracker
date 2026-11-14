"""Format, validation and path helpers for release-group packages.

Schema 1: every edition stores full ``tracks``.
Schema 2: the canonical edition stores full ``tracks``; additional editions
store ``track_changes`` and ``track_metadata`` (see edition_delta.py).
"""

import copy
import json
import re
from pathlib import Path

from edition_delta import DeltaError, make_delta_edition, resolve_edition_tracks
from remix_links import remix_issues
from validator import get_release_validation_issues
from version_policy import excluded_edition_reason, excluded_track_reason


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RELEASE_GROUPS_FOLDER = PROJECT_ROOT / "data" / "release_groups"

CURRENT_SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = (1, 2)

# Catalog categories confirmed by the user during review.  The same values are
# stored in releases.release_type, so they must match the existing database.
CATEGORIES = (
    "studio_album",
    # Albums credited jointly to Lil Wayne and other artists (e.g. Birdman &
    # Lil Wayne); kept apart from solo studio albums.
    "collaboration_album",
    # Releases of groups Lil Wayne belongs to (Hot Boys, Young Money, Rich Gang).
    "group_album",
    "group_single",
    "mixtape",
    "ep",
    "single",
    "remix",
    "compilation",
    "bootleg",
    "other",
)

# One package folder per category inside data/release_groups/.
CATEGORY_FOLDERS = {
    "studio_album": "studio_albums",
    "collaboration_album": "collaboration_albums",
    "group_album": "group_albums",
    "group_single": "group_singles",
    "mixtape": "mixtapes",
    "ep": "eps",
    "single": "singles",
    "remix": "remixes",
    "compilation": "compilations",
    "bootleg": "bootlegs",
    "other": "other",
}

# Older review files used "album" before the vocabulary was unified.
LEGACY_CATEGORY_ALIASES = {
    "album": "studio_album",
}

# Official vs unofficial discography (user decision, November 11, 2026).  It is
# independent of the category: a mixtape or compilation can be either.
DISCOGRAPHIES = ("official", "unofficial")

# Categories whose discography is fixed by definition.
FIXED_DISCOGRAPHY = {
    "studio_album": "official",
    "collaboration_album": "official",
    "bootleg": "unofficial",
}

# Defaults that may be overridden per release (e.g. an unofficial "single").
DEFAULT_DISCOGRAPHY = {
    **FIXED_DISCOGRAPHY,
    "ep": "official",
    "single": "official",
    "group_album": "official",
    "group_single": "official",
}


# Relations between release groups stored in release_group.relations,
# e.g. {"type": "single_from", "musicbrainz_release_group_id": "...",
#       "title": "Tha Carter III", "source": "MusicBrainz"}.
RELATION_TYPES = ("single_from",)

# Sources that may provide a whole package without MusicBrainz IDs.
SOURCE_ONLY_SOURCES = ("LilWayneHQ", "LocalLibrary")


def relation_issues(relations):
    """Validation errors for release_group.relations (missing is fine)."""
    if relations is None:
        return []
    if not isinstance(relations, list):
        return ["release_group.relations must be a list."]
    errors = []
    for index, relation in enumerate(relations, start=1):
        if not isinstance(relation, dict):
            errors.append(f"relation {index} must be an object.")
            continue
        if relation.get("type") not in RELATION_TYPES:
            errors.append(f"relation {index} has unknown type {relation.get('type')!r}.")
        related = relation.get("musicbrainz_release_group_id")
        if not isinstance(related, str) or not related.strip():
            errors.append(f"relation {index} has no musicbrainz_release_group_id.")
    return errors


def resolve_discography(category, discography=None):
    """Return the discography for a category; raises ValueError if unknown/invalid.

    mixtape, remix, compilation and other have no default and must be given.
    """
    category = normalize_category(category)
    if discography is None:
        discography = DEFAULT_DISCOGRAPHY.get(category)
    if discography is None:
        raise ValueError(
            f"Discography (official/unofficial) must be chosen for category "
            f"'{category}'."
        )
    if discography not in DISCOGRAPHIES:
        raise ValueError(
            f"Unknown discography '{discography}'; use one of: {', '.join(DISCOGRAPHIES)}."
        )
    fixed = FIXED_DISCOGRAPHY.get(category)
    if fixed and discography != fixed:
        raise ValueError(f"Category '{category}' is always {fixed}.")
    return discography


def package_discography(package):
    """Discography of a package (stored value, or the category default).

    Returns None only for minimal packages without catalog_category and
    discography; validation reports any invalid combination.
    """
    release_group = package.get("release_group") or {}
    category = release_group.get("catalog_category")
    discography = release_group.get("discography")
    if category is None:
        return discography
    return resolve_discography(category, discography)


def normalize_category(category):
    """Return the current category name for a current or legacy value."""
    if category is None:
        return None
    return LEGACY_CATEGORY_ALIASES.get(category, category)


def iter_package_editions(package):
    """Yield the canonical edition first, then each additional edition (as stored)."""
    yield "canonical", package["canonical_edition"]
    for edition in package.get("additional_editions", []):
        yield "additional", edition


def resolve_package_edition_tracks(package, kind, edition):
    """Return the full track list of one edition; raises DeltaError for bad deltas."""
    if package.get("schema_version") == 1 or kind == "canonical":
        return edition.get("tracks")
    if "tracks" in edition:
        raise DeltaError(
            "schema 2 additional editions must use track_changes/track_metadata, "
            "not a full 'tracks' list."
        )
    return resolve_edition_tracks(
        package["canonical_edition"].get("tracks"),
        edition.get("track_changes"),
        edition.get("track_metadata"),
    )


def iter_resolved_editions(package):
    """Yield (kind, {edition_name, release, tracks}) with full track lists.

    Validate the package first; an invalid delta raises DeltaError.
    """
    discography = package_discography(package)
    for kind, edition in iter_package_editions(package):
        release = dict(edition.get("release") or {}, discography=discography)
        yield kind, {
            "edition_name": edition.get("edition_name"),
            "release": release,
            "tracks": resolve_package_edition_tracks(package, kind, edition),
        }


def upgrade_package_to_v2(package):
    """Return a schema 2 copy of a valid schema 1 package (input unchanged)."""
    if package.get("schema_version") == 2:
        return copy.deepcopy(package)
    if package.get("schema_version") != 1:
        raise ValueError("Only schema 1 packages can be upgraded.")
    canonical_tracks = package["canonical_edition"]["tracks"]
    upgraded = copy.deepcopy(package)
    upgraded["schema_version"] = 2
    upgraded["additional_editions"] = [
        make_delta_edition(canonical_tracks, edition)
        for edition in package.get("additional_editions", [])
    ]
    return upgraded


def get_package_validation_issues(package):
    errors = []
    warnings = []

    if not isinstance(package, dict):
        return ["Package root must be a JSON object."], warnings

    if package.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append(
            "Package schema_version must be one of: "
            f"{', '.join(str(version) for version in SUPPORTED_SCHEMA_VERSIONS)}."
        )

    release_group = package.get("release_group")
    if not isinstance(release_group, dict):
        return errors + ["Missing or invalid 'release_group' object."], warnings

    # Packages built from another source (e.g. LilWayneHQ) may lack
    # MusicBrainz IDs; they must name that source.
    source_only = release_group.get("source") in SOURCE_ONLY_SOURCES
    required = ("title", "artist") if source_only else ("title", "artist", "musicbrainz_release_group_id")
    for field in required:
        value = release_group.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"Release group field '{field}' is missing or empty.")
    if release_group.get("source") is not None and not source_only:
        errors.append(f"Unknown release group source {release_group.get('source')!r}.")

    catalog_category = release_group.get("catalog_category")
    if catalog_category is not None and catalog_category not in CATEGORIES:
        errors.append(
            f"Unknown catalog_category '{catalog_category}'. "
            f"Use one of: {', '.join(CATEGORIES)}."
        )
    elif catalog_category is not None:
        try:
            resolve_discography(catalog_category, release_group.get("discography"))
        except ValueError as error:
            errors.append(f"Release group discography: {error}")
    errors.extend(relation_issues(release_group.get("relations")))

    canonical = package.get("canonical_edition")
    if not isinstance(canonical, dict):
        errors.append("Missing or invalid 'canonical_edition' object.")

    additional = package.get("additional_editions", [])
    if not isinstance(additional, list):
        errors.append("'additional_editions' must be a list.")
        additional = []

    if errors:
        return errors, warnings

    release_group_id = release_group.get("musicbrainz_release_group_id")
    seen_release_ids = set()

    for position, (kind, edition) in enumerate(iter_package_editions(package), start=1):
        label = f"{kind} edition {position}"
        if not isinstance(edition, dict):
            errors.append(f"{label} must be an object.")
            continue

        name = edition.get("edition_name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{label} has no valid edition_name.")

        try:
            tracks = resolve_package_edition_tracks(package, kind, edition)
        except DeltaError as error:
            errors.append(f"{label}: {error}")
            tracks = None

        if tracks is not None:
            release_data = {"release": edition.get("release"), "tracks": tracks}
            edition_errors, edition_warnings = get_release_validation_issues(release_data)
            errors.extend(f"{label}: {error}" for error in edition_errors)
            warnings.extend(f"{label}: {warning}" for warning in edition_warnings)

            if isinstance(tracks, list):
                # Catalogue scope: no Screwed & Chopped, clean or instrumental tracks.
                for track in tracks:
                    if not isinstance(track, dict):
                        continue
                    reason = excluded_track_reason(track.get("title"))
                    clean_only = isinstance(edition.get("release"), dict) and edition[
                        "release"].get("clean_only")
                    if reason and not (reason == "clean" and clean_only):
                        errors.append(
                            f"{label}: track '{track.get('title')}' is a {reason} "
                            "variant, which is not catalogued."
                        )
                    # Remixes must be linked to the recording they remix.
                    errors.extend(f"{label}: {issue}" for issue in remix_issues(track))
                missing_track_ids = sum(
                    1 for track in tracks
                    if isinstance(track, dict) and not track.get("musicbrainz_track_id")
                )
                if missing_track_ids:
                    warnings.append(
                        f"{label}: {missing_track_ids} track(s) have no "
                        "MusicBrainz Track ID."
                    )

        release = edition.get("release")
        if not isinstance(release, dict):
            continue

        reason = excluded_edition_reason(name, release.get("title"))
        if reason:
            errors.append(
                f"{label} is a {reason} edition, which is not catalogued."
            )

        # Solo vs collaboration: compare the category with the MusicBrainz credit.
        credit = release.get("artist_credit")
        solo = not credit or credit == release.get("artist")
        if catalog_category == "collaboration_album" and solo:
            warnings.append(
                f"{label}: catalogued as collaboration_album, but the artist "
                f"credit is {credit or 'missing'!r}."
            )
        elif catalog_category == "studio_album" and not solo:
            warnings.append(
                f"{label}: artist credit {credit!r} looks like a collaboration; "
                "consider collaboration_album."
            )

        release_type = release.get("release_type")
        if release_type is not None and release_type not in CATEGORIES:
            errors.append(
                f"{label} has unknown release_type '{release_type}'."
            )

        if release.get("musicbrainz_release_group_id") != release_group_id:
            errors.append(
                f"{label} does not belong to the package release group."
            )

        release_id = release.get("musicbrainz_release_id")
        if source_only and release_id is None:
            if not release.get("sources"):
                errors.append(f"{label} has neither a MusicBrainz release ID nor sources.")
        elif not isinstance(release_id, str) or not release_id.strip():
            errors.append(f"{label} has no MusicBrainz release ID.")
        elif release_id in seen_release_ids:
            errors.append(f"Duplicate MusicBrainz release ID: {release_id}.")
        else:
            seen_release_ids.add(release_id)

    return errors, warnings


def validate_release_group_package_data(package, label="release group package", display=True):
    errors, warnings = get_package_validation_issues(package)
    if not display:
        return not errors

    print(f"\nVALIDATING PACKAGE: {label}\n")
    print("VALIDATION PASSED" if not errors else "VALIDATION FAILED")
    for error in errors:
        print(f"ERROR: {error}")
    for warning in warnings:
        print(f"WARNING: {warning}")
    print(f"\nErrors: {len(errors)}")
    print(f"Warnings: {len(warnings)}")
    return not errors


def load_release_group_package(json_path):
    try:
        with open(json_path, "r", encoding="utf-8") as file:
            return json.load(file)
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON: {error}") from error
    except OSError as error:
        raise ValueError(f"Could not read package: {error}") from error


def package_filename(title):
    """Build a filename from the release title only, e.g. tha_carter_iii.json."""
    if not isinstance(title, str):
        raise ValueError("Release title must be a string.")

    text = title.casefold().replace("'", "").replace("’", "")
    safe_title = re.sub(r"[\W_]+", "_", text).strip("_")
    if not safe_title:
        raise ValueError(f"Cannot build a filename from title: {title!r}")
    return f"{safe_title}.json"


def package_path(category, title, base_folder=None, discography=None):
    """Return data/release_groups/<official|unofficial>/<category folder>/<title>.json."""
    category = normalize_category(category)
    if category not in CATEGORY_FOLDERS:
        raise ValueError(
            f"Unknown category '{category}'. "
            f"Use one of: {', '.join(CATEGORIES)}."
        )
    discography = resolve_discography(category, discography)
    base_folder = Path(base_folder) if base_folder else RELEASE_GROUPS_FOLDER
    return base_folder / discography / CATEGORY_FOLDERS[category] / package_filename(title)


def iter_package_files(base_folder=None):
    """All package files below data/release_groups/, sorted."""
    base_folder = Path(base_folder) if base_folder else RELEASE_GROUPS_FOLDER
    return sorted(base_folder.glob("**/*.json"))


def existing_package_release_group_id(json_path):
    """Return the Release Group ID stored in an existing package file, or None."""
    package = load_release_group_package(json_path)
    release_group = package.get("release_group") if isinstance(package, dict) else None
    if not isinstance(release_group, dict):
        return None
    return release_group.get("musicbrainz_release_group_id")


def find_package_files(filename, base_folder=None):
    """Find a package by filename anywhere below data/release_groups/."""
    base_folder = Path(base_folder) if base_folder else RELEASE_GROUPS_FOLDER
    return sorted(path for path in base_folder.glob(f"**/{filename}") if path.is_file())
