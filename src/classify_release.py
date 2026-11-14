
from collections import Counter


def classify_release(group):
    """
    Assign a preliminary classification to a MusicBrainz release group.

    This is a heuristic classification, not a confirmation
    of the release's official status.
    """

    primary = group.get("primary-type") or "Unclassified"
    secondary = set(group.get("secondary-types") or [])

    result = {
        "release_group_id": group.get("id"),
        "title": group.get("title", "Unknown"),
        "date": group.get("first-release-date"),
        "musicbrainz_primary_type": primary,
        "musicbrainz_secondary_types": sorted(secondary),
        "classification": None,
        "review_status": "needs_review",
        "reasons": []
    }

    # 1. Unclassified records
    if primary == "Unclassified":
        classification = "review"

        result["reasons"].append(
            "MusicBrainz has no primary release type."
        )

    # 2. Other records
    elif primary == "Other":
        classification = "review"

        result["reasons"].append(
            "MusicBrainz primary type is Other."
        )

    # 3. Albums
    elif primary == "Album":

        if "Mixtape/Street" in secondary:
            classification = "mixtape_candidate"

            result["reasons"].append(
                "Album tagged as Mixtape/Street."
            )

        elif secondary.intersection({
            "Compilation",
            "DJ-mix",
            "Remix",
            "Soundtrack"
        }):
            classification = "other_candidate"

            result["reasons"].append(
                "Album has compilation, DJ-mix, remix "
                "or soundtrack classification."
            )

        else:
            classification = "album_candidate"

            result["reasons"].append(
                "Album without conflicting secondary tags."
            )

    # 4. EPs
    elif primary == "EP":

        if secondary.intersection({
            "Compilation",
            "DJ-mix",
            "Remix",
            "Soundtrack"
        }):
            classification = "other_candidate"

            result["reasons"].append(
                "EP has additional special classification."
            )

        else:
            classification = "ep_candidate"

            result["reasons"].append(
                "MusicBrainz primary type is EP."
            )

    # 5. Singles
    elif primary == "Single":

        if "Remix" in secondary:
            classification = "remix_candidate"

            result["reasons"].append(
                "Single tagged as Remix."
            )

        elif secondary.intersection({
            "Compilation",
            "DJ-mix",
            "Soundtrack",
            "Mixtape/Street",
            "Demo"
        }):
            classification = "other_candidate"

            result["reasons"].append(
                "Single has additional secondary classifications."
            )

        else:
            classification = "single_candidate"

            result["reasons"].append(
                "MusicBrainz primary type is Single."
            )

    else:
        classification = "review"

        result["reasons"].append(
            f"Unrecognized primary type: {primary}."
        )

    result["classification"] = classification

    return result


def classify_releases(groups):
    """
    Classify a list of MusicBrainz release groups.
    """

    return [
        classify_release(group)
        for group in groups
    ]


def display_classification_statistics(classified):
    """
    Display preliminary classification statistics.
    """

    counts = Counter(
        item["classification"]
        for item in classified
    )

    print("\n" + "=" * 75)
    print("PRELIMINARY CLASSIFICATION STATISTICS")
    print("=" * 75)

    labels = [
        ("album_candidate", "Studio album candidates"),
        ("mixtape_candidate", "Mixtape candidates"),
        ("ep_candidate", "EP candidates"),
        ("single_candidate", "Single candidates"),
        ("remix_candidate", "Remix candidates"),
        ("other_candidate", "Other candidates"),
        ("review", "Unclassified / manual review")
    ]

    for key, label in labels:
        print(f"{label:<40} {counts.get(key, 0):>5}")

    print("-" * 75)
    print(f"{'TOTAL':<40} {len(classified):>5}")
    print("=" * 75)


def display_classified_releases(classified):
    """
    Display candidates grouped by preliminary classification.
    """

    categories = {}

    for item in classified:
        category = item["classification"]
        categories.setdefault(category, []).append(item)

    labels = {
        "album_candidate": "STUDIO ALBUM CANDIDATES",
        "mixtape_candidate": "MIXTAPE CANDIDATES",
        "ep_candidate": "EP CANDIDATES",
        "single_candidate": "SINGLE CANDIDATES",
        "remix_candidate": "REMIX CANDIDATES",
        "other_candidate": "OTHER CANDIDATES",
        "review": "MANUAL REVIEW"
    }

    order = [
        "album_candidate",
        "mixtape_candidate",
        "ep_candidate",
        "single_candidate",
        "remix_candidate",
        "other_candidate",
        "review"
    ]

    print("\n" + "=" * 75)
    print("NEW CANDIDATES — PRELIMINARY CLASSIFICATION")
    print("=" * 75)

    for category in order:
        items = categories.get(category, [])

        if not items:
            continue

        items.sort(
            key=lambda item: (
                item.get("date") or "9999",
                item.get("title", "").lower()
            )
        )

        print(f"\n{labels[category]} — {len(items)}")
        print("-" * 75)

        for item in items:
            date = item.get("date") or "Date unknown"

            print(
                f"{item['title']} | "
                f"{date} | "
                f"RG: {item['release_group_id']}"
            )

    print("\n" + "=" * 75)
    print(f"TOTAL CLASSIFIED: {len(classified)}")
    print("=" * 75)


if __name__ == "__main__":

    test_groups = [
        {
            "id": "test-001",
            "title": "Tha Carter III",
            "primary-type": "Album",
            "secondary-types": []
        },
        {
            "id": "test-002",
            "title": "Da Drought 3",
            "primary-type": "Album",
            "secondary-types": ["Mixtape/Street"]
        },
        {
            "id": "test-003",
            "title": "The Leak",
            "primary-type": "EP",
            "secondary-types": []
        },
        {
            "id": "test-004",
            "title": "A Milli",
            "primary-type": "Single",
            "secondary-types": []
        },
        {
            "id": "test-005",
            "title": "A Milli Remix",
            "primary-type": "Single",
            "secondary-types": ["Remix"]
        },
        {
            "id": "test-006",
            "title": "Unknown Release",
            "primary-type": None,
            "secondary-types": []
        }
    ]

    classified = classify_releases(test_groups)

    display_classification_statistics(classified)
    display_classified_releases(classified)

    print("\nTEST COMPLETED")
