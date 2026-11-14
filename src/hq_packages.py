"""Packages for releases known only from LilWayneHQ (no MusicBrainz release).

User decision (November 12, 2026): old official singles that LilWayneHQ
lists but MusicBrainz has no single release for are still catalogued.  Such
a package has no MusicBrainz IDs; release_group.source = "LilWayneHQ" and the
HQ page is the release source.  Its single track carries only the title, so
the import links it to the catalogued album song by the same-song rule
(single without duration, exactly one song with that title).

usage:
    python hq_packages.py "<saved Lil Wayne Singles.html>" single "Go DJ" "Gossip" ...
    python hq_packages.py "<saved page>" group_single "Roger That" ...
"""

import sys

from build_release_group_package import output_path_status, save_package
from lilwaynehq_source import load_list_page
from release_group_package import package_path, resolve_discography
from titles import normalized_title


SOURCE_NAME = "LilWayneHQ"
PAGE_URLS = {
    "single": "https://www.lilwaynehq.com/discography/singles/",
    "group_single": "https://www.lilwaynehq.com/discography/singles/",
}


def artist_credit(entry, artist="Lil Wayne"):
    """'Lil Wayne feat. X', 'Lil Wayne & Birdman', or the group ('Young Money')."""
    partner = entry.get("with")
    if not partner:
        credit = artist
    elif partner.startswith("Young Money"):
        credit = partner  # group release, e.g. "Young Money feat. Lloyd"
    else:
        credit = f"{artist} & {partner}"
    if entry.get("featuring"):
        credit += f" feat. {entry['featuring']}"
    return credit


def build_single_package(entry, category="single", artist="Lil Wayne"):
    """Package dict for one HQ list entry {title, with, featuring}."""
    discography = resolve_discography(category)
    title = entry["title"]
    release = {
        "title": title,
        "artist": artist,
        "release_type": category,
        "artist_credit": artist_credit(entry, artist),
        "musicbrainz_release_id": None,
        "musicbrainz_release_group_id": None,
        "sources": [{"name": SOURCE_NAME, "url": PAGE_URLS[category]}],
    }
    return {
        "schema_version": 2,
        "release_group": {
            "title": title,
            "artist": artist,
            "musicbrainz_release_group_id": None,
            "catalog_category": category,
            "discography": discography,
            "source": SOURCE_NAME,
            "evidence": [f"Listed as an official single on {SOURCE_NAME}; "
                         "no single release in MusicBrainz."],
        },
        "canonical_edition": {
            "edition_name": f"{SOURCE_NAME}: single",
            "release": release,
            "tracks": [{"number": 1, "disc_number": 1, "title": title, "credits": []}],
        },
        "additional_editions": [],
    }


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return
    page, category, titles = sys.argv[1], sys.argv[2], sys.argv[3:]
    _, entries = load_list_page(page)
    by_title = {normalized_title(entry["title"]): entry for entry in entries}
    saved = 0
    for title in titles:
        entry = by_title.get(normalized_title(title))
        if entry is None:
            print(f"NOT ON THE PAGE: {title}")
            continue
        package = build_single_package(entry, category)
        path = package_path(category, entry["title"], discography=package["release_group"]["discography"])
        if output_path_status(path, None) != "new":
            print(f"EXISTS, nothing saved: {path}")
            continue
        save_package(package, path)
        saved += 1
        print(f"Saved: {path} ({package['canonical_edition']['release']['artist_credit']})")
    print(f"Packages saved: {saved} of {len(titles)}. SQLite was not modified.")


if __name__ == "__main__":
    main()
