"""Review MusicBrainz release groups before any edition is imported.

This script never writes to SQLite.  It stores the human review and the
selected editions in data/reviewed_candidates.json.  A later importer can use
that file to retrieve tracklists and create a release-group package.
"""

import json
import os
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from classify_release import classify_releases
from musicbrainz_catalog import BASE_URL, api_request, display_tracklist, get_tracklist
from release_group_package import (
    CATEGORIES,
    DEFAULT_DISCOGRAPHY,
    DISCOGRAPHIES,
    FIXED_DISCOGRAPHY,
    normalize_category,
)
from version_policy import excluded_edition_reason
from scan_discography import (
    ARTIST_NAME,
    get_existing_release_groups,
    get_new_candidates,
    get_release_groups,
)


ARTIST_MUSICBRAINZ_ID = "ac9a487a-d9d2-4f27-bb23-0f4686488345"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REVIEW_FILE = PROJECT_ROOT / "data" / "reviewed_candidates.json"
EDITION_LIMIT = 100

DECISIONS = {"pending", "approved", "excluded"}

DEFAULT_CATEGORIES = {
    "album_candidate": "studio_album",
    "mixtape_candidate": "mixtape",
    "ep_candidate": "ep",
    "single_candidate": "single",
    "remix_candidate": "remix",
    "other_candidate": "other",
    "review": "other",
}

NON_STANDARD_EDITION_WORDS = (
    "deluxe",
    "expanded",
    "reissue",
    "remaster",
    "anniversary",
    "bonus",
    "special",
    "limited",
    "exclusive",
    "target",
    "complete",
    "atmos",
    "instrumental",
    "clean",
    "edited",
    "chopped",
    "screwed",
)


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def empty_review_data():
    return {
        "schema_version": 1,
        "artist_name": ARTIST_NAME,
        "artist_musicbrainz_id": ARTIST_MUSICBRAINZ_ID,
        "candidates": [],
    }


def load_review_data(path=REVIEW_FILE):
    """Load persisted decisions without silently replacing invalid data."""
    if not path.exists():
        return empty_review_data()

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not load review file {path}: {error}") from error

    if not isinstance(data, dict) or not isinstance(data.get("candidates"), list):
        raise ValueError("Review file must contain a 'candidates' list.")

    # Upgrade legacy category names in memory; the file changes on next save.
    for candidate in data["candidates"]:
        if isinstance(candidate, dict) and candidate.get("confirmed_category"):
            candidate["confirmed_category"] = normalize_category(
                candidate["confirmed_category"]
            )
            # Decisions made before the official/unofficial split get the
            # category default (only where one exists).
            if candidate.get("decision") == "approved" and not candidate.get(
                "confirmed_discography"
            ):
                default = DEFAULT_DISCOGRAPHY.get(candidate["confirmed_category"])
                if default:
                    candidate["confirmed_discography"] = default

    return data


def save_review_data(data, path=REVIEW_FILE):
    """Atomically replace the review file after a successful user decision."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")

    with open(temporary_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")

    os.replace(temporary_path, path)


def candidate_from_classification(item, timestamp):
    return {
        "release_group_id": item["release_group_id"],
        "title": item["title"],
        "first_release_date": item["date"],
        "musicbrainz_primary_type": item["musicbrainz_primary_type"],
        "musicbrainz_secondary_types": item["musicbrainz_secondary_types"],
        "preliminary_classification": item["classification"],
        "classification_reasons": item["reasons"],
        "decision": "pending",
        "confirmed_category": None,
        "confirmed_discography": None,
        "review_note": None,
        "canonical_edition": None,
        "additional_editions": [],
        "first_seen_at": timestamp,
        "last_seen_at": timestamp,
    }


def merge_candidates(review_data, classified, timestamp=None):
    """Add candidates discovered by a scan while preserving manual decisions."""
    timestamp = timestamp or utc_now()
    by_id = {
        item.get("release_group_id"): item
        for item in review_data["candidates"]
        if item.get("release_group_id")
    }
    added = 0
    updated = 0

    for item in classified:
        release_group_id = item.get("release_group_id")
        if not release_group_id:
            continue

        existing = by_id.get(release_group_id)
        if existing is None:
            candidate = candidate_from_classification(item, timestamp)
            review_data["candidates"].append(candidate)
            by_id[release_group_id] = candidate
            added += 1
            continue

        # MusicBrainz metadata and heuristic output may change.  Decisions,
        # notes and selected editions are deliberately retained.
        existing.update({
            "title": item["title"],
            "first_release_date": item["date"],
            "musicbrainz_primary_type": item["musicbrainz_primary_type"],
            "musicbrainz_secondary_types": item["musicbrainz_secondary_types"],
            "preliminary_classification": item["classification"],
            "classification_reasons": item["reasons"],
            "last_seen_at": timestamp,
        })
        updated += 1

    review_data["last_scan_at"] = timestamp
    return added, updated


def refresh_from_musicbrainz(review_data):
    """Read new release groups; this operation does not modify SQLite."""
    print("Loading Lil Wayne release groups from MusicBrainz...")
    groups = get_release_groups(ARTIST_MUSICBRAINZ_ID)

    if not groups:
        print("No release groups were retrieved; review file was not changed.")
        return False

    existing_groups = get_existing_release_groups()
    new_groups = get_new_candidates(groups, existing_groups)
    classified = classify_releases(new_groups)
    added, updated = merge_candidates(review_data, classified)

    print(f"MusicBrainz release groups: {len(groups)}")
    print(f"Already represented in SQLite: {len(groups) - len(new_groups)}")
    print(f"Candidates from current scan: {len(classified)}")
    print(f"Added to review file: {added}")
    print(f"Updated in review file: {updated}")
    return True


def candidate_summary(candidate):
    date = candidate.get("first_release_date") or "unknown date"
    category = candidate.get("confirmed_category") or candidate.get(
        "preliminary_classification", "unknown"
    )
    return f"{candidate['title']} ({date}) — {category}"


def display_candidate(candidate):
    print("\n" + "=" * 75)
    print(candidate_summary(candidate))
    print(f"Release group ID: {candidate['release_group_id']}")
    print(
        "MusicBrainz type: "
        f"{candidate.get('musicbrainz_primary_type', 'Unknown')}"
    )
    secondary = candidate.get("musicbrainz_secondary_types") or []
    print(f"Secondary types: {', '.join(secondary) if secondary else 'None'}")
    print(f"Decision: {candidate.get('decision', 'pending')}")
    print("Reasons:")
    for reason in candidate.get("classification_reasons", []):
        print(f"- {reason}")
    if candidate.get("review_note"):
        print(f"Note: {candidate['review_note']}")


def prompt_category(candidate):
    default = candidate.get("confirmed_category") or DEFAULT_CATEGORIES.get(
        candidate.get("preliminary_classification"), "other"
    )
    options = "/".join(CATEGORIES)

    while True:
        value = input(f"Category [{default}] ({options}): ").strip().lower()
        if not value:
            return default
        if value in CATEGORIES:
            return value
        print("Use one of the listed categories.")


def prompt_discography(category):
    """Ask official/unofficial; categories with a fixed value are not asked."""
    if category in FIXED_DISCOGRAPHY:
        return FIXED_DISCOGRAPHY[category]
    default = DEFAULT_DISCOGRAPHY.get(category)
    hint = f" [{default}]" if default else ""
    while True:
        value = input(f"Discography{hint} (official/unofficial): ").strip().lower()
        if not value and default:
            return default
        if value in DISCOGRAPHIES:
            return value
        print("Use 'official' or 'unofficial'.")


def review_candidate(candidate):
    """Review one candidate and return whether its saved state changed."""
    display_candidate(candidate)
    choice = input(
        "[a]pprove, [e]xclude, [p]ending, [s]kip: "
    ).strip().lower()

    if choice == "s" or not choice:
        return False
    if choice == "a":
        candidate["decision"] = "approved"
        candidate["confirmed_category"] = prompt_category(candidate)
        candidate["confirmed_discography"] = prompt_discography(
            candidate["confirmed_category"]
        )
    elif choice == "e":
        candidate["decision"] = "excluded"
        candidate["confirmed_category"] = None
        candidate["confirmed_discography"] = None
    elif choice == "p":
        candidate["decision"] = "pending"
        candidate["confirmed_category"] = None
        candidate["confirmed_discography"] = None
    else:
        print("Unknown choice; candidate was not changed.")
        return False

    note = input("Optional note (leave empty to keep current): ").strip()
    if note:
        candidate["review_note"] = note
    candidate["reviewed_at"] = utc_now()
    return True


def review_pending_candidates(review_data):
    pending = sorted(
        (
            item for item in review_data["candidates"]
            if item.get("decision", "pending") == "pending"
        ),
        key=lambda item: (
            item.get("first_release_date") or "9999",
            item.get("title", "").casefold(),
        ),
    )
    if not pending:
        print("There are no pending candidates.")
        return False

    changed = False
    for candidate in pending:
        changed = review_candidate(candidate) or changed
    return changed


def find_candidates(review_data, query):
    query = query.casefold().strip()
    return [
        candidate for candidate in review_data["candidates"]
        if query in candidate.get("title", "").casefold()
        or query in candidate.get("release_group_id", "").casefold()
    ]


def find_and_review_candidate(review_data):
    query = input("Title or release group ID: ").strip()
    if not query:
        return False

    matches = find_candidates(review_data, query)
    if not matches:
        print("No matching candidates.")
        return False
    for index, candidate in enumerate(matches, start=1):
        print(f"{index}. {candidate_summary(candidate)}")

    choice = input("Candidate number (or Enter to cancel): ").strip()
    if not choice:
        return False
    if not choice.isdigit() or not 1 <= int(choice) <= len(matches):
        print("Invalid candidate number.")
        return False
    return review_candidate(matches[int(choice) - 1])


def get_release_editions(release_group_id):
    """Fetch all MusicBrainz editions for one release group with pagination."""
    editions = []
    offset = 0

    while True:
        params = urllib.parse.urlencode({
            "release-group": release_group_id,
            "fmt": "json",
            "limit": EDITION_LIMIT,
            "offset": offset,
        })
        time.sleep(1)
        data = api_request(f"{BASE_URL}release/?{params}")
        if not data:
            break

        page = data.get("releases", [])
        if not page:
            break

        editions.extend(page)
        total = data.get("release-count", 0)
        if len(editions) >= total or len(page) < EDITION_LIMIT:
            break
        offset += EDITION_LIMIT

    return editions


def edition_is_non_standard(edition):
    text = " ".join(
        str(edition.get(field) or "")
        for field in ("title", "disambiguation")
    ).casefold()
    return any(word in text for word in NON_STANDARD_EDITION_WORDS)


COUNTRY_PREFERENCE = {"US": 0, "XW": 1}  # XW = worldwide (digital) releases


def edition_sort_key(edition):
    """Rank official standard editions first, then US, worldwide, others, by date.

    A standard edition from another country ranks before a US deluxe or
    bonus edition, so a canonical edition is never a special edition when a
    standard one exists.
    """
    return (
        edition.get("status") != "Official",
        edition_is_non_standard(edition),
        COUNTRY_PREFERENCE.get(edition.get("country"), 2),
        edition.get("date") or "9999-99-99",
        edition.get("title", "").casefold(),
        edition.get("id", ""),
    )


def edition_excluded_reason(edition):
    """Clean, Screwed & Chopped and instrumental editions are not catalogued."""
    return excluded_edition_reason(edition.get("title"), edition.get("disambiguation"))


def compact_edition(edition):
    return {
        "musicbrainz_release_id": edition["id"],
        "title": edition.get("title", "Unknown"),
        "date": edition.get("date"),
        "country": edition.get("country"),
        "status": edition.get("status"),
        "disambiguation": edition.get("disambiguation") or None,
    }


def select_editions(review_data):
    approved = [
        item for item in review_data["candidates"]
        if item.get("decision") == "approved"
    ]
    if not approved:
        print("Approve a release group before selecting editions.")
        return False

    for index, candidate in enumerate(approved, start=1):
        marker = "selected" if candidate.get("canonical_edition") else "not selected"
        print(f"{index}. {candidate_summary(candidate)} [{marker}]")

    selected = input("Release group number (or Enter to cancel): ").strip()
    if not selected:
        return False
    if not selected.isdigit() or not 1 <= int(selected) <= len(approved):
        print("Invalid release group number.")
        return False

    candidate = approved[int(selected) - 1]
    editions = sorted(
        get_release_editions(candidate["release_group_id"]),
        key=edition_sort_key,
    )
    if not editions:
        print("No editions were retrieved.")
        return False

    print("\nEditions (the first is the automatic recommendation):")
    for index, edition in enumerate(editions, start=1):
        flags = []
        if edition.get("country") == "US":
            flags.append("US")
        if edition.get("status") == "Official":
            flags.append("Official")
        if edition_is_non_standard(edition):
            flags.append("alternative")
        excluded = edition_excluded_reason(edition)
        if excluded:
            flags.append(f"EXCLUDED ({excluded})")
        description = edition.get("disambiguation") or ""
        print(
            f"{index}. {edition.get('title', 'Unknown')} | "
            f"{edition.get('date') or 'Unknown date'} | "
            f"{edition.get('country') or 'Unknown country'} | "
            f"{edition.get('status') or 'Unknown status'} | "
            f"{', '.join(flags) or 'unclassified'} {description}\n"
            f"   Release ID: {edition.get('id', 'Unknown')}"
        )

    while True:
        value = input(
            "Canonical edition [1], or p<number> to preview its tracklist: "
        ).strip().lower()
        if value.startswith("p"):
            preview = value[1:]
            if not preview.isdigit() or not 1 <= int(preview) <= len(editions):
                print("Invalid preview number.")
                continue
            details = get_tracklist(editions[int(preview) - 1]["id"])
            if details:
                display_tracklist(details)
            else:
                print("Could not retrieve this edition's tracklist.")
            continue
        if not value or value.isdigit() and 1 <= int(value) <= len(editions):
            canonical_index = int(value or "1") - 1
            excluded = edition_excluded_reason(editions[canonical_index])
            if excluded:
                print(f"This is a {excluded} edition, which is not catalogued.")
                continue
            break
        print("Invalid edition number.")

    candidate["canonical_edition"] = compact_edition(editions[canonical_index])

    additional = input(
        "Additional edition numbers, comma-separated (optional): "
    ).strip()
    additional_editions = []
    if additional:
        try:
            indexes = [int(item.strip()) - 1 for item in additional.split(",")]
        except ValueError:
            print("Additional editions were not changed: use numeric values.")
        else:
            if any(index < 0 or index >= len(editions) for index in indexes):
                print("Additional editions were not changed: number outside range.")
            elif any(edition_excluded_reason(editions[index]) for index in indexes):
                print(
                    "Additional editions were not changed: clean, Screwed & "
                    "Chopped and instrumental editions are not catalogued."
                )
            else:
                additional_editions = [
                    compact_edition(editions[index])
                    for index in dict.fromkeys(indexes)
                    if index != canonical_index
                ]
                candidate["additional_editions"] = additional_editions

    candidate["edition_selection_reviewed_at"] = utc_now()
    print("Edition selection saved in memory; choose Save from the main menu.")
    return True


def display_statistics(review_data):
    counts = {decision: 0 for decision in DECISIONS}
    for candidate in review_data["candidates"]:
        counts[candidate.get("decision", "pending")] = (
            counts.get(candidate.get("decision", "pending"), 0) + 1
        )

    print("\nREVIEW QUEUE")
    print(f"Total: {len(review_data['candidates'])}")
    for decision in ("pending", "approved", "excluded"):
        print(f"{decision.title()}: {counts[decision]}")


def main():
    print("=" * 75)
    print("LIL WAYNE TRACKER — RELEASE GROUP REVIEW")
    print("This script never imports releases or modifies SQLite.")
    print("=" * 75)

    try:
        review_data = load_review_data()
    except ValueError as error:
        print(f"ERROR: {error}")
        return

    dirty = False
    while True:
        display_statistics(review_data)
        print("\n[r]efresh from MusicBrainz")
        print("[v]iew/review pending candidates")
        print("[f]ind and review a candidate")
        print("[e]dition selection for approved candidates")
        print("[s]ave decisions")
        print("[q]uit")
        choice = input("Choose an action: ").strip().lower()

        if choice == "r":
            dirty = refresh_from_musicbrainz(review_data) or dirty
        elif choice == "v":
            dirty = review_pending_candidates(review_data) or dirty
        elif choice == "f":
            dirty = find_and_review_candidate(review_data) or dirty
        elif choice == "e":
            dirty = select_editions(review_data) or dirty
        elif choice == "s":
            save_review_data(review_data)
            dirty = False
            print(f"Saved: {REVIEW_FILE}")
        elif choice == "q":
            if dirty:
                answer = input("Save unsaved decisions before exit? (y/n): ").strip().lower()
                if answer == "y":
                    save_review_data(review_data)
                    print(f"Saved: {REVIEW_FILE}")
            return
        else:
            print("Unknown action.")


if __name__ == "__main__":
    main()
