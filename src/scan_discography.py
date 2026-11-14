
import time
import sqlite3
import urllib.parse

from pathlib import Path
from collections import Counter

from musicbrainz_catalog import api_request, BASE_URL
from classify_release import (
    classify_releases,
    display_classification_statistics,
    display_classified_releases
)


ARTIST_NAME = "Lil Wayne"
LIMIT = 100

PROJECT_FOLDER = Path(__file__).resolve().parent.parent
DATABASE_PATH = PROJECT_FOLDER / "data" / "lilwayne.db"


def find_artist(artist_name):
    params = urllib.parse.urlencode({
        "query": f'artist:"{artist_name}"',
        "fmt": "json",
        "limit": 10
    })

    url = f"{BASE_URL}artist/?{params}"
    print(f"Searching for artist: {artist_name}")

    data = api_request(url)

    if not data:
        return None

    artists = data.get("artists", [])

    if not artists:
        return None

    for artist in artists:
        if artist.get("name", "").lower() == artist_name.lower():
            return artist

    return artists[0]


def get_release_groups(artist_id):
    all_groups = []
    offset = 0

    while True:
        params = urllib.parse.urlencode({
            "artist": artist_id,
            "fmt": "json",
            "limit": LIMIT,
            "offset": offset
        })

        url = f"{BASE_URL}release-group/?{params}"

        print(f"Loading release groups... Offset: {offset}")

        data = api_request(url)

        if not data:
            print("Could not retrieve release groups.")
            break

        groups = data.get("release-groups", [])

        if not groups:
            break

        all_groups.extend(groups)

        total = data.get("release-group-count", 0)

        print(f"Retrieved: {len(all_groups)} / {total}")

        if len(all_groups) >= total or len(groups) < LIMIT:
            break

        offset += LIMIT
        time.sleep(1)

    return all_groups


def get_existing_release_groups():
    if not DATABASE_PATH.exists():
        print(f"Database not found: {DATABASE_PATH}")
        return {}

    connection = sqlite3.connect(DATABASE_PATH)

    try:
        cursor = connection.cursor()

        cursor.execute("""
            SELECT
                r.musicbrainz_release_group_id,
                r.title
            FROM releases r
            JOIN artists a ON a.id = r.artist_id
            WHERE LOWER(a.name) = LOWER(?)
              AND r.musicbrainz_release_group_id IS NOT NULL
        """, (ARTIST_NAME,))

        rows = cursor.fetchall()

        existing = {}

        for release_group_id, title in rows:
            existing.setdefault(release_group_id, []).append(title)

        return existing

    except sqlite3.Error as error:
        print(f"Database error: {error}")
        return {}

    finally:
        connection.close()


def get_primary_type(group):
    return group.get("primary-type") or "Unclassified"


def get_secondary_types(group):
    return group.get("secondary-types", [])


def get_release_type(group):
    primary = get_primary_type(group)
    secondary = get_secondary_types(group)

    if secondary:
        return f"{primary} ({', '.join(secondary)})"

    return primary


def get_new_candidates(groups, existing_groups):
    return [
        group for group in groups
        if group.get("id") not in existing_groups
    ]


def display_category_statistics(groups, title):
    primary_counts = Counter()
    secondary_counts = Counter()
    combined_counts = Counter()

    for group in groups:
        primary = get_primary_type(group)
        secondary = get_secondary_types(group)

        primary_counts[primary] += 1
        combined_counts[get_release_type(group)] += 1

        for category in secondary:
            secondary_counts[category] += 1

    print("\n" + "=" * 75)
    print(title)
    print("=" * 75)

    print("\nPRIMARY RELEASE TYPES")
    print("-" * 75)

    for category, count in primary_counts.most_common():
        print(f"{category:<30} {count:>5}")

    print("\nSECONDARY RELEASE TYPES")
    print("-" * 75)

    if secondary_counts:
        for category, count in secondary_counts.most_common():
            print(f"{category:<30} {count:>5}")
    else:
        print("No secondary types found.")

    print("\nCOMBINED CLASSIFICATIONS")
    print("-" * 75)

    for category, count in combined_counts.most_common():
        print(f"{category:<55} {count:>5}")

    print("-" * 75)
    print(f"TOTAL: {len(groups)}")
    print("=" * 75)


def display_database_comparison(groups, existing_groups):
    already_in_database = [
        group for group in groups
        if group.get("id") in existing_groups
    ]

    new_candidates = get_new_candidates(groups, existing_groups)

    print("\n" + "=" * 75)
    print("DATABASE COMPARISON")
    print("=" * 75)

    print(f"Total release groups: {len(groups)}")
    print(f"Already in database: {len(already_in_database)}")
    print(f"New candidates: {len(new_candidates)}")

    print("\nALREADY IN DATABASE:")

    for group in already_in_database:
        group_id = group.get("id")
        titles = existing_groups[group_id]

        print(
            f"- {group.get('title', 'Unknown')} "
            f"({', '.join(titles)})"
        )

    print("=" * 75)


def main():
    print("=" * 75)
    print("LIL WAYNE TRACKER — DISCOGRAPHY SCANNER")
    print("=" * 75)

    artist = find_artist(ARTIST_NAME)

    if not artist:
        print("Artist not found.")
        return

    print(f"\nArtist found: {artist.get('name')}")
    print(f"Artist ID: {artist.get('id')}")

    time.sleep(1)

    groups = get_release_groups(artist["id"])

    if not groups:
        print("No release groups found.")
        return

    print("\nLoading existing release groups from database...")

    existing_groups = get_existing_release_groups()
    new_candidates = get_new_candidates(groups, existing_groups)

    display_database_comparison(groups, existing_groups)

    display_category_statistics(
        groups,
        "FULL DISCOGRAPHY — MUSICBRAINZ CATEGORY STATISTICS"
    )

    display_category_statistics(
        new_candidates,
        "NEW CANDIDATES — MUSICBRAINZ CATEGORY STATISTICS"
    )

    # Preliminary classification of new candidates.
    print("\nClassifying new candidates...")

    classified = classify_releases(new_candidates)

    display_classification_statistics(classified)
    display_classified_releases(classified)

    print("\nSCAN COMPLETED")


if __name__ == "__main__":
    main()
