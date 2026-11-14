"""MusicBrainz API helpers: requests, release details and release JSON creation.

Used by scan_discography.py, review_candidates.py and
build_release_group_package.py.  Saving is handled by the release-group
package workflow (data/release_groups/<category>/<title>.json).
"""

import json
import time
import urllib.error
import urllib.request


BASE_URL = "https://musicbrainz.org/ws/2/"

HEADERS = {
    "User-Agent": "LilWayneTracker/0.1 (local catalog project)",
    "Accept": "application/json"
}


def api_request(url):
    request = urllib.request.Request(
        url,
        headers=HEADERS
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    except urllib.error.HTTPError as error:
        print(f"MusicBrainz HTTP error: {error.code}")

    except urllib.error.URLError as error:
        print(f"Connection error: {error.reason}")

    except json.JSONDecodeError:
        print("Could not read API response.")

    return None


def get_tracklist(release_id):
    time.sleep(1)

    url = (
        f"{BASE_URL}release/{release_id}"
        "?inc=recordings+labels+artist-credits"
        "+recording-level-rels+recording-rels&fmt=json"
    )

    return api_request(url)


def get_single_from_relations(release_group_id):
    """'single from' links of a release group as package relations.

    Returns None when MusicBrainz could not be reached (unknown), [] when
    there are no links.  On the single, the album is in the forward direction.
    """
    time.sleep(1)
    data = api_request(
        f"{BASE_URL}release-group/{release_group_id}?inc=release-group-rels&fmt=json"
    )
    if data is None:
        return None
    return [
        {
            "type": "single_from",
            "musicbrainz_release_group_id": relation["release_group"]["id"],
            "title": relation["release_group"].get("title"),
            "source": "MusicBrainz",
        }
        for relation in data.get("relations", [])
        if relation.get("type") == "single from"
        and relation.get("direction") == "forward"
        and relation.get("target-type") == "release_group"
        and relation.get("release_group", {}).get("id")
    ]


def format_duration(milliseconds):
    if milliseconds is None:
        return "Unknown"

    total_seconds = int(milliseconds) // 1000

    minutes = total_seconds // 60
    seconds = total_seconds % 60

    return f"{minutes}:{seconds:02d}"


def display_tracklist(release):
    print("\n" + "=" * 60)
    print("RELEASE INFORMATION")
    print("=" * 60)

    print(f"Album: {release.get('title', 'Unknown')}")
    print(f"Date: {release.get('date', 'Unknown')}")
    print(f"Country: {release.get('country', 'Unknown')}")
    print(f"Status: {release.get('status', 'Unknown')}")
    print(f"Barcode: {release.get('barcode', 'Unknown')}")
    print(f"Label: {release.get('label-info', [{}])[0].get('label', {}).get('name', 'Unknown')}")
    print(f"Packaging: {release.get('packaging', 'Unknown')}")
    print(f"Release ID: {release.get('id', 'Unknown')}")

    media = release.get("media", [])

    print(f"Number of discs: {len(media)}")

    total_tracks = 0

    for medium in media:
        print("\n" + "-" * 60)

        print(f"DISC {medium.get('position', '?')}")
        print(f"Format: {medium.get('format', 'Unknown')}")
        print(f"Tracks on disc: {medium.get('track-count', 'Unknown')}")

        for track in medium.get("tracks", []):
            total_tracks += 1

            position = track.get("position", "?")
            title = track.get("title", "Unknown")
            duration = format_duration(track.get("length"))

            print(f"{position}. {title} [{duration}]")

            recording = track.get("recording", {})

            print(f"   Track ID: {track.get('id', 'Unknown')}")
            print(f"   Recording ID: {recording.get('id', 'Unknown')}")

    print("\n" + "=" * 60)
    print(f"TOTAL DISCS: {len(media)}")
    print(f"TOTAL TRACKS: {total_tracks}")


def format_artist_credit(artist_credit):
    """'Birdman & Lil Wayne' from a MusicBrainz artist-credit list, or None."""
    if not artist_credit:
        return None
    return "".join(
        f"{credit.get('name') or credit.get('artist', {}).get('name', '')}"
        f"{credit.get('joinphrase', '')}"
        for credit in artist_credit
    ).strip() or None


def remix_original(recording):
    """(Recording ID, title) of the original if this recording is a remix, else None.

    In a "remix" recording-recording relationship, the remix sees the
    original in the forward direction (the original sees its remixes as
    backward), as observed on real MusicBrainz data.
    """
    originals = [
        (relation["recording"]["id"], relation["recording"].get("title"))
        for relation in recording.get("relations", [])
        if relation.get("type") == "remix"
        and relation.get("direction") == "forward"
        and relation.get("target-type") == "recording"
        and relation.get("recording", {}).get("id")
    ]
    return originals[0] if len(originals) == 1 else None


def remix_of_recording_id(recording):
    original = remix_original(recording)
    return original[0] if original else None


def create_release_json(release, artist_name, selected_release):
    release_id = release.get("id")

    release_group = selected_release.get("release-group", {})

    release_group_id = release_group.get("id")
    primary_type = release_group.get("primary-type")
    secondary_types = release_group.get("secondary-types", [])

    # MusicBrainz classification.
    musicbrainz_type = {
        "primary_type": primary_type,
        "secondary_types": secondary_types
    }

    # Keep our internal classification separate.  The package builder
    # replaces this default with the category confirmed during review.
    release_type = "studio_album"

    release_data = {
        "release": {
            "title": release.get("title", "Unknown"),
            "artist": artist_name,
            "release_type": release_type,
            "musicbrainz_type": musicbrainz_type,
            "release_date": release.get("date", ""),
            "musicbrainz_release_id": release_id,
            "musicbrainz_release_group_id": release_group_id,
            # Full MusicBrainz credit, e.g. "Birdman & Lil Wayne" (provenance).
            "artist_credit": format_artist_credit(release.get("artist-credit")),
            "sources": [
                {
                    "name": "MusicBrainz",
                    "url": f"https://musicbrainz.org/release/{release_id}"
                }
            ]
        },
        "tracks": []
    }

    for medium in release.get("media", []):
        disc_number = medium.get("position", 1)

        for track in medium.get("tracks", []):
            recording = track.get("recording", {})

            track_number = track.get("position")
            duration = track.get("length")

            if duration is None:
                duration = recording.get("length")

            track_data = {
                "number": int(track_number),
                "disc_number": int(disc_number),
                "title": track.get("title", "Unknown"),
                "musicbrainz_track_id": track.get("id"),
                "musicbrainz_recording_id": recording.get("id"),
                "duration_ms": duration,
                "credits": []
            }

            original = remix_original(recording)
            if original:
                track_data["remix_of_recording_id"] = original[0]
                if original[1]:
                    track_data["remix_of_title"] = original[1]

            release_data["tracks"].append(track_data)

    return release_data
