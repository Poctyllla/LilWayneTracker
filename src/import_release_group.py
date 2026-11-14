"""Import one validated release-group package in a single SQLite transaction."""

import sqlite3
from pathlib import Path

from import_release import DATABASE_PATH, import_release_data
from release_group_package import (
    RELEASE_GROUPS_FOLDER,
    find_package_files,
    iter_resolved_editions,
    load_release_group_package,
    validate_release_group_package_data,
)


def import_relations(connection, release_group):
    """Store release_group.relations (e.g. single_from) inside the transaction."""
    for relation in release_group.get("relations") or []:
        connection.execute("""
            INSERT OR IGNORE INTO release_group_relations (
                musicbrainz_release_group_id,
                related_musicbrainz_release_group_id,
                relation_type,
                related_title,
                source
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            release_group["musicbrainz_release_group_id"],
            relation["musicbrainz_release_group_id"],
            relation["type"],
            relation.get("title"),
            relation.get("source"),
        ))


def import_release_group_package(json_path):
    package = load_release_group_package(json_path)
    if not validate_release_group_package_data(package):
        raise ValueError("Release group package validation failed.")

    # Rebuild every full track list in memory before touching SQLite, so a
    # delta problem can never leave a partially imported package.
    editions = list(iter_resolved_editions(package))

    results = []
    connection = sqlite3.connect(DATABASE_PATH)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            for kind, edition in editions:
                print("\n" + "=" * 75)
                print(f"IMPORTING {kind.upper()} EDITION: {edition['edition_name']}")
                print("=" * 75)
                results.append((kind, import_release_data(connection, edition)))
            import_relations(connection, package["release_group"])
    finally:
        connection.close()

    print("\nRELEASE GROUP IMPORT COMPLETED")
    print(f"Release group: {package['release_group']['title']}")
    print(f"Imported editions: {len(results)}")
    return results


def main():
    filename = input(
        "Enter release-group JSON filename: "
    ).strip()
    if not filename.lower().endswith(".json"):
        filename += ".json"

    matches = find_package_files(filename)
    if not matches:
        print(f"File not found in {RELEASE_GROUPS_FOLDER} or its category folders: {filename}")
        return

    if len(matches) == 1:
        json_path = matches[0]
    else:
        print("Several packages have this filename:")
        for index, path in enumerate(matches, start=1):
            print(f"{index}. {path.relative_to(RELEASE_GROUPS_FOLDER)}")
        choice = input("Package number (or Enter to cancel): ").strip()
        if not choice.isdigit() or not 1 <= int(choice) <= len(matches):
            print("Import cancelled.")
            return
        json_path = matches[int(choice) - 1]

    print(f"Package: {json_path}")

    try:
        import_release_group_package(json_path)
    except (ValueError, sqlite3.Error) as error:
        print(f"\nIMPORT FAILED: {error}")
        print("All changes for this release group have been rolled back.")


if __name__ == "__main__":
    main()
