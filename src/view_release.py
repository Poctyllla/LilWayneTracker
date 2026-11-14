import sqlite3
from pathlib import Path


PROJECT_FOLDER = Path(__file__).resolve().parent.parent
DATABASE_PATH = PROJECT_FOLDER / "data" / "lilwayne.db"


def view_release():
    connection = sqlite3.connect(DATABASE_PATH)
    cursor = connection.cursor()

    album = input("Enter album title: ").strip()

    cursor.execute("""
        SELECT
            releases.id,
            releases.title,
            releases.release_date,
            artists.name,
            releases.release_type,
            releases.discography
        FROM releases
        JOIN artists
            ON artists.id = releases.artist_id
        WHERE releases.title LIKE ?
    """, (f"%{album}%",))

    releases = cursor.fetchall()

    if not releases:
        print("Release not found.")
        connection.close()
        return

    for release in releases:
        release_id, title, release_date, artist, release_type, discography = release

        print(f"\nALBUM: {title}")
        print(f"ARTIST: {artist}")
        print(f"DATE: {release_date}")
        print(f"TYPE: {release_type} ({discography or 'discography not set'} discography)")
        print("\nTRACKLIST:\n")

        cursor.execute("""
            SELECT
                rt.disc_number,
                rt.track_number,
                COALESCE(rt.track_title, recordings.title),
                recordings.title,
                versions.version_name
            FROM release_tracks AS rt
            JOIN versions
                ON versions.id = rt.version_id
            JOIN recordings
                ON recordings.id = versions.recording_id
            WHERE rt.release_id = ?
            ORDER BY rt.disc_number, rt.track_number
        """, (release_id,))

        tracks = cursor.fetchall()

        for disc, number, track_title, main_title, version_name in tracks:
            # Show the main recording title when this edition spells it differently.
            main_note = f" (main title: {main_title})" if track_title != main_title else ""
            print(
                f"Disc {disc} | {number}. {track_title}{main_note} "
                f"[{version_name}]"
            )

        print(f"\nTOTAL TRACKS: {len(tracks)}")

    connection.close()


if __name__ == "__main__":
    view_release()