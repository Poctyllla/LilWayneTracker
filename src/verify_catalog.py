
import sqlite3
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_DIR / "data" / "lilwayne.db"


def print_section(title):
    print()
    print("=" * 65)
    print(title)
    print("=" * 65)


def verify_catalog():
    if not DB_PATH.exists():
        print(f"ERROR: Database not found: {DB_PATH}")
        return

    errors = 0
    warnings = 0

    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row

        print("DATABASE:", DB_PATH)

        # 1. Get all Lil Wayne releases
        print_section("1. RELEASES")

        releases = connection.execute("""
            SELECT
                r.id,
                r.title,
                r.release_date,
                r.musicbrainz_release_id,
                r.musicbrainz_release_group_id,
                a.name AS artist_name
            FROM releases r
            JOIN artists a ON a.id = r.artist_id
            WHERE a.name = 'Lil Wayne'
            ORDER BY r.release_date, r.title
        """).fetchall()

        print(f"Total Lil Wayne releases: {len(releases)}")

        # Releases known only from another source (no MusicBrainz release):
        # missing MusicBrainz IDs, durations and dates are expected there.
        source_only_ids = {row[0] for row in connection.execute("""
            SELECT DISTINCT r.id FROM releases r
            JOIN recording_sources rs ON rs.release_id = r.id
            JOIN sources s ON s.id = rs.source_id
            WHERE r.musicbrainz_release_id IS NULL AND s.name IN ('LilWayneHQ', 'LocalLibrary')
        """)}
        print(f"Releases known only from LilWayneHQ / the local library: {len(source_only_ids)}")

        if not releases:
            print("WARNING: No Lil Wayne releases found.")
            warnings += 1


        # 1.5. Check release date precision
        print_section("1.5. RELEASE DATE PRECISION")

        for release in releases:
            release_date = release["release_date"]
            musicbrainz_id = release["musicbrainz_release_id"]

            if not release_date:
                if release["id"] in source_only_ids:
                    print(f"INFO: {release['title']} (LilWayneHQ only) has no release date.")
                    continue
                print(
                    f"WARNING: {release['title']} "
                    "has no release date."
                )
                warnings += 1
                continue

            date_parts = release_date.split("-")

            if len(date_parts) == 1:
                precision = "year"
            elif len(date_parts) == 2:
                precision = "month"
            elif len(date_parts) == 3:
                precision = "day"
            else:
                print(
                    f"ERROR: Invalid release date format: "
                    f"{release_date}"
                )
                errors += 1
                continue

            print(
                f"{release['title']}: "
                f"{release_date} (precision: {precision})"
            )

            if precision == "year" and musicbrainz_id:
                print(
                    "WARNING: Release has MusicBrainz ID "
                    "but only year-level date precision."
                )
                warnings += 1

        # 2. Check each release
        print_section("2. TRACK AND METADATA CHECKS")

        for release in releases:
            release_id = release["id"]
            title = release["title"]

            tracks = connection.execute("""
                SELECT
                    rt.id AS release_track_id,
                    rt.disc_number,
                    rt.track_number,
                    rt.musicbrainz_track_id,
                    rt.duration_ms,
                    v.id AS version_id,
                    rec.id AS recording_id,
                    rec.musicbrainz_recording_id
                FROM release_tracks rt
                LEFT JOIN versions v
                    ON v.id = rt.version_id
                LEFT JOIN recordings rec
                    ON rec.id = v.recording_id
                WHERE rt.release_id = ?
                ORDER BY rt.disc_number, rt.track_number
            """, (release_id,)).fetchall()

            track_count = len(tracks)

            missing_track_ids = sum(
                1 for t in tracks
                if not t["musicbrainz_track_id"]
            )

            missing_recording_ids = sum(
                1 for t in tracks
                if not t["musicbrainz_recording_id"]
            )

            missing_durations = sum(
                1 for t in tracks
                if t["duration_ms"] is None
            )

            missing_versions = sum(
                1 for t in tracks
                if t["version_id"] is None
            )

            missing_recordings = sum(
                1 for t in tracks
                if t["recording_id"] is None
            )

            recording_ids = {
                t["recording_id"]
                for t in tracks
                if t["recording_id"] is not None
            }

            duplicate_positions = connection.execute("""
                SELECT disc_number, track_number, COUNT(*) AS amount
                FROM release_tracks
                WHERE release_id = ?
                GROUP BY disc_number, track_number
                HAVING COUNT(*) > 1
            """, (release_id,)).fetchall()

            print()
            print(f"ALBUM: {title}")
            print(f"Database ID: {release_id}")
            print(f"Release date: {release['release_date']}")
            print(f"MusicBrainz Release ID: "
                  f"{release['musicbrainz_release_id']}")
            print(f"MusicBrainz Release Group ID: "
                  f"{release['musicbrainz_release_group_id']}")
            print(f"Tracks: {track_count}")
            print(f"Unique recordings: {len(recording_ids)}")
            print(f"Missing Track IDs: {missing_track_ids}")
            print(f"Missing Recording IDs: {missing_recording_ids}")
            print(f"Missing recordings: {missing_recordings}")
            print(f"Missing durations: {missing_durations}")
            print(f"Missing versions: {missing_versions}")

            if track_count == 0:
                print("ERROR: Release has no tracks")
                errors += 1

            if release_id in source_only_ids:
                # Only the structure is checked; no MusicBrainz data exists.
                print("INFO: release known only from LilWayneHQ "
                      "(no MusicBrainz IDs or durations expected)")
                if missing_recordings:
                    print("ERROR: Some tracks have no linked recording")
                    errors += 1
                if missing_versions:
                    print("ERROR: Some tracks have no linked version")
                    errors += 1
                continue

            if not release["musicbrainz_release_id"]:
                print("WARNING: Missing MusicBrainz Release ID")
                warnings += 1

            if not release["musicbrainz_release_group_id"]:
                print("WARNING: Missing MusicBrainz Release Group ID")
                warnings += 1

            if missing_track_ids:
                print("WARNING: Some tracks have no MusicBrainz Track ID")
                warnings += 1

            if missing_recording_ids:
                print("WARNING: Some recordings have no MusicBrainz ID")
                warnings += 1

            if missing_recordings:
                print("ERROR: Some tracks have no linked recording")
                errors += 1

            if missing_durations:
                print("WARNING: Some tracks have no duration")
                warnings += 1

            if missing_versions:
                print("ERROR: Some tracks have no linked version")
                errors += 1

            if duplicate_positions:
                print("ERROR: Duplicate track positions found")
                errors += 1

            # Check track numbering separately for each disc.
            tracks_by_disc = {}

            for track in tracks:
                disc = track["disc_number"]
                number = track["track_number"]

                if disc is None or number is None:
                    print("ERROR: Track has an invalid disc or track number")
                    errors += 1
                    continue

                tracks_by_disc.setdefault(disc, []).append(number)

            for disc, numbers in tracks_by_disc.items():
                expected_numbers = list(range(1, len(numbers) + 1))

                if sorted(numbers) != expected_numbers:
                    print(
                        f"WARNING: Track numbering on Disc {disc} "
                        "is not continuous"
                    )
                    warnings += 1

        # 3. Check duplicate MusicBrainz IDs
        print_section("3. DUPLICATE MUSICBRAINZ IDS")

        duplicate_releases = connection.execute("""
            SELECT musicbrainz_release_id, COUNT(*) AS amount
            FROM releases
            WHERE musicbrainz_release_id IS NOT NULL
            GROUP BY musicbrainz_release_id
            HAVING COUNT(*) > 1
        """).fetchall()

        duplicate_recordings = connection.execute("""
            SELECT musicbrainz_recording_id, COUNT(*) AS amount
            FROM recordings
            WHERE musicbrainz_recording_id IS NOT NULL
            GROUP BY musicbrainz_recording_id
            HAVING COUNT(*) > 1
        """).fetchall()

        print(f"Duplicate release IDs: {len(duplicate_releases)}")
        print(f"Duplicate recording IDs: {len(duplicate_recordings)}")

        if duplicate_releases:
            errors += len(duplicate_releases)
            for item in duplicate_releases:
                print("ERROR:", item["musicbrainz_release_id"])

        if duplicate_recordings:
            errors += len(duplicate_recordings)
            for item in duplicate_recordings:
                print("ERROR:", item["musicbrainz_recording_id"])

        # 3.5. Official / unofficial discography
        print_section("3.5. DISCOGRAPHY")

        release_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(releases)")
        }
        if "discography" not in release_columns:
            print("ERROR: releases.discography is missing; run database.py")
            errors += 1
        else:
            for row in connection.execute("""
                SELECT COALESCE(discography, '(missing)') AS discography,
                       release_type, COUNT(*) AS amount
                FROM releases
                GROUP BY 1, 2
                ORDER BY 1, 2
            """):
                print(f"{row['discography']:10} {row['release_type'] or '?':20} {row['amount']}")

            invalid = connection.execute("""
                SELECT id, title, discography FROM releases
                WHERE discography IS NULL
                OR discography NOT IN ('official', 'unofficial')
            """).fetchall()
            for item in invalid:
                print(f"ERROR: release {item['id']} ({item['title']}) has no valid "
                      f"discography: {item['discography']!r}")
            errors += len(invalid)

        # 3.6. Version links: a remix cannot come out years before its original
        # (catches a remix attached to a later song of the same title).
        print_section("3.6. VERSION LINKS")
        early_remixes = connection.execute("""
            WITH vdate AS (
                SELECT v.id AS version_id, v.recording_id, v.version_type,
                       MIN(CAST(substr(x.release_date, 1, 4) AS INTEGER)) AS year
                FROM versions v
                JOIN release_tracks rt ON rt.version_id = v.id
                JOIN releases x ON x.id = rt.release_id
                WHERE x.release_date IS NOT NULL
                GROUP BY v.id
            ),
            main AS (
                SELECT recording_id, MIN(year) AS year FROM vdate
                WHERE version_type = 'album' GROUP BY recording_id
            )
            SELECT r.id, r.title, d.version_id, d.year AS remix_year, m.year AS original_year
            FROM vdate d JOIN main m ON m.recording_id = d.recording_id
            JOIN recordings r ON r.id = d.recording_id
            WHERE d.version_type = 'remix' AND d.year < m.year - 1
        """).fetchall()
        print(f"Remixes released years before their original: {len(early_remixes)}")
        for item in early_remixes:
            print(f"WARNING: remix version {item['version_id']} of '{item['title']}' "
                  f"(recording {item['id']}) is from {item['remix_year']}, the original from "
                  f"{item['original_year']} -- probably linked to the wrong song")
        warnings += len(early_remixes)

        # 4. Check database foreign keys
        print_section("4. DATABASE RELATIONSHIPS")

        foreign_key_errors = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        print(f"Foreign key errors: {len(foreign_key_errors)}")

        if foreign_key_errors:
            errors += len(foreign_key_errors)
            for item in foreign_key_errors:
                print("ERROR:", tuple(item))

    # 5. Final result
    print_section("FINAL RESULT")

    print(f"Errors: {errors}")
    print(f"Warnings: {warnings}")

    if errors == 0 and warnings == 0:
        print("CATALOG STATUS: ALL CHECKS PASSED")
    elif errors == 0:
        print("CATALOG STATUS: PASSED WITH WARNINGS")
    else:
        print("CATALOG STATUS: CHECKS FAILED")


if __name__ == "__main__":
    verify_catalog()
