"""Approve release groups from the review queue, select editions automatically
and build their schema 2 packages.  Never writes to SQLite.

usage:
    python add_release_groups.py --category studio_album RELEASE_GROUP_ID [...]
    python add_release_groups.py --dry-run ...   # show decisions, save nothing
    python add_release_groups.py --replace ...   # rebuild a package of the same group

Edition rules are in edition_selection.py.  Import the saved packages
afterwards with import_release_group.py.
"""

import argparse
import sys
import time

from build_release_group_package import (
    TRACKLIST_ATTEMPTS,
    build_package,
    output_path_status,
    save_package,
)
import import_release
from duplicate_guard import classify_tracks, load_catalogue
from edition_selection import SELECTED, select_editions
from musicbrainz_catalog import (
    BASE_URL,
    api_request,
    create_release_json,
    format_artist_credit,
    get_single_from_relations,
    get_tracklist,
)
from remix_links import link_remixes
from release_group_package import (
    CATEGORIES,
    DISCOGRAPHIES,
    package_path,
    resolve_discography,
)
from review_candidates import (
    REVIEW_FILE,
    compact_edition,
    edition_sort_key,
    get_release_editions,
    load_review_data,
    save_review_data,
    utc_now,
)
from version_policy import excluded_edition_reason, without_excluded_tracks


AUTO_NOTE = "Editions selected automatically by edition_selection.py."

# MusicBrainz release statuses usable for each discography.  Official
# discography uses only official releases; unofficial material is often marked
# Bootleg or has no status.  Promotion releases are never used.
ALLOWED_STATUSES = {
    "official": ("Official",),
    "unofficial": ("Official", "Bootleg", None),
}

# User decision (November 11, 2026): an official single that exists only as a
# promo (e.g. an old radio single) is still part of the official discography.
PROMO_SINGLE_STATUSES = ("Promotion",)


def fetch_details(release_id):
    """Fetch release details with retries; returns None after the last attempt."""
    for attempt in range(1, TRACKLIST_ATTEMPTS + 1):
        details = get_tracklist(release_id)
        if details:
            return details
        if attempt < TRACKLIST_ATTEMPTS:
            time.sleep(attempt * 3)
    return None


def without_out_of_scope_tracks(pairs, details_by_id, artist_name, release_group_id,
                                keep_reasons=()):
    """Pairs and details with out-of-scope tracks removed (keep_reasons kept)."""
    new_pairs, new_details = [], {}
    for edition, tracks in pairs:
        details = details_by_id.get(edition["id"])
        if details is None:
            new_pairs.append((edition, tracks))
            continue
        filtered, _ = without_excluded_tracks(details, keep_reasons)
        if keep_reasons:
            filtered["_clean_only"] = True
        new_details[edition["id"]] = filtered
        new_tracks = create_release_json(
            filtered, artist_name, {"release-group": {"id": release_group_id}}
        )["tracks"]
        new_pairs.append((edition, new_tracks or None))
    return new_pairs, new_details


def fetch_relations(release_group_id):
    """'single from' relations with retries; incomplete data aborts the group."""
    for attempt in range(1, TRACKLIST_ATTEMPTS + 1):
        relations = get_single_from_relations(release_group_id)
        if relations is not None:
            return relations
        if attempt < TRACKLIST_ATTEMPTS:
            time.sleep(attempt * 3)
    raise ValueError("release-group relations could not be retrieved; try again later")


def fetch_editions(candidate, artist_name, statuses=("Official",)):
    """Return [(edition, tracks_or_None)] and {release_id: details}.

    Track lists are fetched only for in-scope editions with an allowed status.
    If one of them cannot be retrieved, the selection would be made on
    incomplete data, so ValueError is raised and nothing is saved.
    """
    editions = get_release_editions(candidate["release_group_id"])
    if not editions:
        raise ValueError("no editions could be retrieved from MusicBrainz")
    pairs, details_by_id = [], {}
    for edition in editions:
        tracks = None
        if edition.get("status") in statuses and not excluded_edition_reason(
            edition.get("title"), edition.get("disambiguation")
        ):
            details = fetch_details(edition["id"])
            if not details:
                raise ValueError(
                    f"track list of edition {edition['id']} could not be retrieved "
                    f"after {TRACKLIST_ATTEMPTS} attempts; try again later"
                )
            details_by_id[edition["id"]] = details
            tracks = create_release_json(
                details, artist_name,
                {"release-group": {"id": candidate["release_group_id"]}},
            )["tracks"]
        pairs.append((edition, tracks))
    return pairs, details_by_id


def print_decisions(decisions):
    for edition, status, detail in decisions:
        marker = "*" if status == SELECTED else " "
        print(
            f"  {marker} {edition.get('date') or '????':10} {edition.get('country') or '??':3} "
            f"{(edition.get('status') or '?'):10} {status:42} {detail} "
            f"| {edition.get('disambiguation') or ''} [{edition['id']}]"
        )


def queue_candidate_from_musicbrainz(review_data, release_group_id):
    """Add a release group that is not in the review queue (e.g. a mixtape
    credited to 'Lil Wayne and The Sqad' or found via Wikipedia)."""
    time.sleep(1)
    data = api_request(f"{BASE_URL}release-group/{release_group_id}?inc=artist-credits&fmt=json")
    if not data:
        print(f"Release group {release_group_id} could not be retrieved.")
        return None
    timestamp = utc_now()
    candidate = {
        "release_group_id": release_group_id,
        "title": data.get("title"),
        "first_release_date": data.get("first-release-date"),
        "musicbrainz_primary_type": data.get("primary-type"),
        "musicbrainz_secondary_types": data.get("secondary-types", []),
        "preliminary_classification": "review",
        "classification_reasons": ["Added manually (not in Lil Wayne's MusicBrainz release groups)."],
        "decision": "pending",
        "confirmed_category": None,
        "confirmed_discography": None,
        "review_note": "Added via add_release_groups.py --add-missing; credit: "
                       + (format_artist_credit(data.get("artist-credit")) or "?"),
        "canonical_edition": None,
        "additional_editions": [],
        "first_seen_at": timestamp,
        "last_seen_at": timestamp,
    }
    review_data["candidates"].append(candidate)
    print(f"Added to review queue: {candidate['title']} ({candidate['review_note']})")
    return candidate


def process(review_data, release_group_id, category, dry_run, replace=False,
            discography=None, add_missing=False, evidence=()):
    candidate = next(
        (item for item in review_data["candidates"]
         if item.get("release_group_id") == release_group_id),
        None,
    )
    if candidate is None and add_missing:
        candidate = queue_candidate_from_musicbrainz(review_data, release_group_id)
    if candidate is None:
        print(f"\nNOT IN REVIEW QUEUE: {release_group_id} (use --add-missing)")
        return False

    discography = resolve_discography(category, discography)
    statuses = ALLOWED_STATUSES[discography]
    # Official singles and mixtapes may exist only as promo / street releases.
    promo_fallback = category in ("single", "group_single", "mixtape") and discography == "official"
    fetch_statuses = statuses + (PROMO_SINGLE_STATUSES if promo_fallback else ())

    print("\n" + "=" * 75)
    print(f"{candidate['title']} ({candidate.get('first_release_date')}) "
          f"[{release_group_id}] — {discography} {category}")
    artist_name = review_data["artist_name"]
    pairs, details_by_id = fetch_editions(candidate, artist_name, fetch_statuses)
    raw_pairs, raw_details = pairs, details_by_id
    canonical, additional, decisions = select_editions(pairs, edition_sort_key, statuses)
    print(f"MusicBrainz editions: {len(pairs)}")
    print_decisions(decisions)
    if canonical is None and promo_fallback:
        # Official single without a usable official edition (e.g. a radio
        # single that exists only as a promo CD): use official or promotion
        # editions without clean / radio edit / instrumental tracks.
        print("No usable official edition; trying editions without out-of-scope tracks "
              "(promotion allowed):")
        pairs, details_by_id = without_out_of_scope_tracks(
            pairs, details_by_id, artist_name, release_group_id
        )
        canonical, additional, decisions = select_editions(
            pairs, edition_sort_key, statuses + PROMO_SINGLE_STATUSES
        )
        print_decisions(decisions)
    if canonical is None and promo_fallback:
        # User decision (Nov 12, 2026): a single that exists only as a clean /
        # radio edit version is added too (instrumentals stay out).
        print("Still no usable edition; trying clean-only editions:")
        pairs, details_by_id = without_out_of_scope_tracks(
            raw_pairs, raw_details, artist_name, release_group_id, keep_reasons=("clean",)
        )
        canonical, additional, decisions = select_editions(
            pairs, edition_sort_key, statuses + PROMO_SINGLE_STATUSES,
            allowed_track_reasons=("clean",),
        )
        print_decisions(decisions)
    if canonical is None:
        print("NO USABLE EDITION; nothing saved.")
        return False

    # Same-song rule against the catalogue: <= 5 s -> the import links the
    # track to the catalogued song; 5-15 s -> manual review, nothing saved.
    catalogue = load_catalogue(import_release.DATABASE_PATH)
    selected_tracks = link_remixes(
        [canonical[1]] + [tracks for _, tracks in additional], catalogue["originals"]
    )
    matches = classify_tracks(
        [track for tracks in selected_tracks for track in tracks], catalogue,
        allow_unknown_duration=category in ("single", "group_single"),
    )
    for title, recording_id in matches["same"]:
        print(f"Same song as catalogued recording {recording_id}: '{title}' "
              "(other MusicBrainz ID will be stored as an alternate)")
    if matches["review"]:
        print("MANUAL REVIEW: same title as catalogued recordings, duration differs "
              "5-15 s (or unknown); nothing saved:")
        for title, recording_id in matches["review"]:
            print(f"   '{title}' ~ catalogued recording {recording_id}")
        return False

    relations = fetch_relations(release_group_id)
    for relation in relations:
        print(f"Single from: {relation.get('title')} [{relation['musicbrainz_release_group_id']}]")

    output_path = package_path(category, candidate["title"], discography=discography)
    status = output_path_status(output_path, release_group_id)
    if status == "conflict" or (status == "same_release_group" and not replace):
        hint = " (use --replace to rebuild it)" if status == "same_release_group" else ""
        print(f"PACKAGE EXISTS ({status}): {output_path}; nothing saved{hint}.")
        return False
    if dry_run:
        print(f"Dry run: would save {output_path}")
        return False

    timestamp = utc_now()
    updates = {
        "decision": "approved",
        "confirmed_category": category,
        "confirmed_discography": discography,
        "canonical_edition": compact_edition(canonical[0]),
        "additional_editions": [compact_edition(edition) for edition, _ in additional],
        "review_note": candidate.get("review_note") or AUTO_NOTE,
        "reviewed_at": timestamp,
        "edition_selection_reviewed_at": timestamp,
    }
    package_candidate = dict(candidate, **updates, artist_name=review_data["artist_name"])
    package = build_package(package_candidate, details_by_id, relations, catalogue["originals"])
    if evidence:
        package["release_group"]["evidence"] = list(evidence)
    save_package(package, output_path)
    # The review decision changes only after the package was saved.
    candidate.update(updates)
    print(f"Saved: {output_path}")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("release_group_ids", nargs="+")
    parser.add_argument("--category", required=True, choices=CATEGORIES)
    parser.add_argument(
        "--discography", choices=DISCOGRAPHIES,
        help="official or unofficial; required for mixtape, remix, compilation, other",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--replace", action="store_true",
        help="rebuild an existing package of the same release group",
    )
    parser.add_argument(
        "--add-missing", action="store_true",
        help="add release groups that are not in the review queue yet",
    )
    parser.add_argument(
        "--evidence", action="append", default=[],
        help="basis of the decision stored in the package, e.g. 'LilWayneHQ official mixtapes page'",
    )
    args = parser.parse_args(argv)
    try:
        resolve_discography(args.category, args.discography)
    except ValueError as error:
        parser.error(str(error))

    review_data = load_review_data(REVIEW_FILE)
    saved = 0
    for release_group_id in args.release_group_ids:
        try:
            if process(review_data, release_group_id, args.category,
                       args.dry_run, args.replace, args.discography, args.add_missing,
                       args.evidence):
                saved += 1
                # Persist after every package so finished work is never lost.
                save_review_data(review_data, REVIEW_FILE)
        except ValueError as error:
            print(f"FAILED: {error}; nothing saved for this release group.")

    print(f"\nPackages saved: {saved} of {len(args.release_group_ids)}")
    print("SQLite was not modified. Import with import_release_group.py.")
    return 0 if saved or args.dry_run else 1


if __name__ == "__main__":
    sys.exit(main())
