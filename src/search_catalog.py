
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = PROJECT_ROOT / "data" / "lilwayne.db"


def search_catalog(search_term):
    with sqlite3.connect(DATABASE_PATH) as connection:
        cursor = connection.cursor()

        cursor.execute("""
            SELECT
                releases.title,
                release_tracks.track_number,
                COALESCE(release_tracks.track_title, recordings.title),
                GROUP_CONCAT(
                    DISTINCT artists.name || ' (' || credits.role || ')'
                )
            FROM release_tracks
            JOIN releases
                ON release_tracks.release_id = releases.id
            JOIN versions
                ON release_tracks.version_id = versions.id
            JOIN recordings
                ON versions.recording_id = recordings.id
            LEFT JOIN credits
                ON credits.version_id = versions.id
            LEFT JOIN artists
                ON credits.artist_id = artists.id
            WHERE
                -- Main title and edition spelling; LIKE ignores ASCII case.
                recordings.title LIKE ?
                OR release_tracks.track_title LIKE ?
                OR releases.title LIKE ?
                OR EXISTS (
                    SELECT 1
                    FROM credits AS c
                    JOIN artists AS a ON c.artist_id = a.id
                    WHERE c.version_id = versions.id
                    AND a.name LIKE ?
                )
            GROUP BY
                releases.id,
                release_tracks.id
            ORDER BY
                releases.title,
                release_tracks.track_number
        """, (
            f"%{search_term}%",
            f"%{search_term}%",
            f"%{search_term}%",
            f"%{search_term}%"
        ))

        results = cursor.fetchall()

        print(f"\nSEARCH RESULTS: {search_term}\n")

        if not results:
            print("Nothing found.")
            return

        for release, number, track, credits in results:
            print(f"{release} | Track {number}: {track}")

            if credits:
                print(f"    Credits: {credits}")
            else:
                print("    Credits: Not available")

        print(f"\nTotal results: {len(results)}")


if __name__ == "__main__":
    search_term = input("Enter album, track or artist: ").strip()

    if search_term:
        search_catalog(search_term)
    else:
        print("Search term cannot be empty.")
