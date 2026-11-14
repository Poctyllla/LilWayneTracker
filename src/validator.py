"""Validation of one release edition (release + full tracks).

Used by release_group_package.py for every edition of a package.
"""



def get_release_validation_issues(data):
    """Return validation errors and warnings for one release JSON object."""
    errors = []
    warnings = []

    if not isinstance(data, dict):
        return ["Root must be a JSON object."], warnings

    release = data.get("release")
    tracks = data.get("tracks")

    if not isinstance(release, dict):
        errors.append("Missing or invalid 'release' object.")

    if not isinstance(tracks, list) or not tracks:
        errors.append("'tracks' must be a non-empty list.")

    if errors:
        return errors, warnings

    # 3. Check release information.
    for field in ["title", "artist"]:
        value = release.get(field)

        if not isinstance(value, str) or not value.strip():
            errors.append(f"Release field '{field}' is missing or empty.")

    if "sources" in release:
        if not isinstance(release["sources"], list):
            errors.append("'sources' must be a list.")
        else:
            for index, source in enumerate(release["sources"], start=1):
                if not isinstance(source, dict):
                    errors.append(f"Source {index} must be an object.")
                    continue

                if not isinstance(source.get("name"), str) or not source["name"].strip():
                    errors.append(f"Source {index} has no valid name.")

                url = source.get("url")
                if url is not None and not isinstance(url, str):
                    errors.append(f"Source {index} has an invalid URL.")

    # 4. Check tracks.
    track_numbers = set()

    for index, track in enumerate(tracks, start=1):
        if not isinstance(track, dict):
            errors.append(f"Track entry {index} must be an object.")
            continue

        number = track.get("number")
        disc_number = track.get("disc_number", 1)
        title = track.get("title")

        if not isinstance(disc_number, int) or isinstance(disc_number, bool) or disc_number <= 0:
            errors.append(f"Track entry {index} has an invalid disc number.")

        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            errors.append(f"Track entry {index} has an invalid track number.")

        elif isinstance(disc_number, int) and not isinstance(disc_number, bool) and disc_number > 0:
            track_key = (disc_number, number)

            if track_key in track_numbers:
                errors.append(
                    f"Duplicate track number: Disc {disc_number}, Track {number}."
                )
            else:
                track_numbers.add(track_key)

        if not isinstance(title, str) or not title.strip():
            errors.append(f"Track entry {index} has no valid title.")

        credits = track.get("credits", [])

        if not isinstance(credits, list):
            errors.append(f"Credits for track {index} must be a list.")
            continue

        seen_credits = set()

        for credit in credits:
            if not isinstance(credit, dict):
                errors.append(f"Invalid credit entry in track {index}.")
                continue

            artist = credit.get("artist")
            role = credit.get("role")

            if not isinstance(artist, str) or not artist.strip():
                errors.append(f"Missing artist name in track {index}.")

            if not isinstance(role, str) or not role.strip():
                errors.append(f"Missing credit role in track {index}.")

            if isinstance(artist, str) and isinstance(role, str):
                credit_key = (artist.casefold(), role.casefold())

                if credit_key in seen_credits:
                    warnings.append(
                        f"Duplicate credit: {artist} ({role}) "
                        f"in track {index}."
                    )

                seen_credits.add(credit_key)

    # 5. Check track numbering.
    if track_numbers:
        tracks_by_disc = {}

        for disc_number, track_number in track_numbers:
            tracks_by_disc.setdefault(disc_number, set()).add(track_number)

        for disc_number, numbers in tracks_by_disc.items():
            expected = set(range(1, len(numbers) + 1))

            if numbers != expected:
                warnings.append(
                    f"Track numbers on Disc {disc_number} "
                    "are not continuous starting from 1."
                )

    return errors, warnings
