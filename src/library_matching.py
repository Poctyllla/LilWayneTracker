"""Match a library scan (library_scanner.py) to catalogued releases and link
the audio files to their versions in the `files` table.

* Folder -> release: comparable release titles ("The Carter" = "Tha Carter",
  "Da Drought II" = "Da Drought 2"); of several editions the one whose track
  list matches most files wins.
* File -> release track: same position (disc, number) with a matching title,
  otherwise a unique title match inside that edition.
* Only folders where at least MIN_SHARE of the files match are linked.
  Unmatched folders (bootlegs, unreleased collections) are only reported.

usage:
    python library_matching.py exports/library_scan_<date>.json          # dry run
    python library_matching.py exports/library_scan_<date>.json --apply  # write files
"""

import json
import re
import sqlite3
import sys
from pathlib import Path

from import_release import DATABASE_PATH
from titles import normalized_title


MIN_SHARE = 0.7      # of the folder's files matched, or
MIN_COVERAGE = 0.9   # of the edition's tracks found in the folder
ROMAN = {"ii": "2", "iii": "3", "iv": "4", "vi": "6"}


def release_key(title):
    """Comparable release title: 'Tha'/'Da' spelling and roman numerals ignored."""
    words = []
    for word in normalized_title(title).split():
        word = {"tha": "the"}.get(word, word)
        words.append(ROMAN.get(word, word))
    return " ".join(words)


# Words that describe a folder rather than the release ("Gangsta Grillz
# Edition", "And BONUS", "EP" ...); removed for a second lookup.
FOLDER_DESCRIPTORS = re.compile(
    r"\b(and bonus|bonus|ep|gangsta grillz edition|buck d up edition|"
    r"presented by .*|collectors edition|edition)\b")


def folder_keys(title):
    """Lookup keys for a folder title, most specific first."""
    key = release_key(title)
    stripped = re.sub(r"\s+", " ", FOLDER_DESCRIPTORS.sub(" ", key)).strip()
    keys = [key] + ([stripped] if stripped and stripped != key else [])
    # "SQ6 Remixz" -> "SQ6 The Remix"
    spelled = re.sub(r"\b(?:the )?remix(?:z|es)?$", "the remix", key)
    return keys + ([spelled] if spelled not in keys else [])


def squash(text):
    """Letters, digits and censor stars only: "Young'n Blues" == "Young n Blues"."""
    return re.sub(r"[^a-z0-9*]", "", str(text or "").casefold().replace("&", "and"))


def censored_match(a, b):
    """'F**k Wit Me Now' matches 'Fuck Wit Me Now' (each * = one letter)."""
    a, b = squash(a), squash(b)
    if "*" not in a + b or len(a) != len(b):
        return False
    return all(x == y or "*" in (x, y) for x, y in zip(a, b))


# Labels in file tags that are not part of the song title.
TAG_NOISE = re.compile(r"\s*[(\[](?:prod\.?|produced|feat\.?|ft\.?|featuring)\b[^)\]]*[)\]]", re.IGNORECASE)


def comparable(title):
    """Tag title cleaned for comparison: "I\x92m Single (Prod. by 40)" -> "I'm Single",
    "We Ready Freestyle" -> "We Ready", "Carter Talk pt. 1" -> "Carter Talk part 1"."""
    text = str(title or "").replace("\x92", "'").replace("’", "'")
    text = TAG_NOISE.sub("", text)
    text = re.sub(r"\bpt\.?\s*(?=\d)", "part ", text, flags=re.IGNORECASE)
    text = re.sub(r"\brmx\b", "remix", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*freestyle\s*[(\[]([^)\]]+)[)\]]\s*$", r"\1", text, flags=re.IGNORECASE)  # "Freestyle (Damn)"
    stripped = re.sub(r"\s+freestyle\s*$", "", text.strip(), flags=re.IGNORECASE)
    return stripped or text


def titles_match(a, b):
    a, b = comparable(a), comparable(b)
    na, nb = normalized_title(a), normalized_title(b)
    if not (na and nb):
        return False
    if na == nb or na.startswith(nb + " ") or nb.startswith(na + " "):
        return True
    sa, sb = squash(normalized_title(a)), squash(normalized_title(b))
    return sa == sb or censored_match(a, b)


def catalogue_editions(connection):
    """{release_key: [edition]}; edition = {id, title, tracks: {(disc, num): track}}."""
    editions = {}
    for release_id, title in connection.execute("SELECT id, title FROM releases"):
        tracks = {}
        for rt_id, disc, num, version_id, track_title, rec_title in connection.execute("""
            SELECT rt.id, rt.disc_number, rt.track_number, rt.version_id,
                   COALESCE(rt.track_title, rec.title), rec.title
            FROM release_tracks rt
            JOIN versions v ON v.id = rt.version_id
            JOIN recordings rec ON rec.id = v.recording_id
            WHERE rt.release_id = ?
        """, (release_id,)):
            tracks[(disc or 1, num)] = {"release_track_id": rt_id, "version_id": version_id,
                                        "titles": {track_title, rec_title}}
        editions.setdefault(release_key(title), []).append(
            {"id": release_id, "title": title, "tracks": tracks})
    return editions


def match_tracks(folder, edition):
    """[(file, catalogue track or None)] for one folder against one edition."""
    disc = folder.get("disc")
    result = []
    for file in folder["tracks"]:
        position = (disc or file.get("disc_number") or 1, file.get("track_number"))
        track = edition["tracks"].get(position)
        if track and any(titles_match(file["title"], t) for t in track["titles"]):
            result.append((file, track))
            continue
        by_title = [t for t in edition["tracks"].values()
                    if any(titles_match(file["title"], name) for name in t["titles"])]
        result.append((file, by_title[0] if len(by_title) == 1 else None))
    return result


# A folder that says it holds an official release but whose name does not
# match (the library numbers the SQ mixtapes differently from MusicBrainz) is
# matched by its track list alone -- only with this much overlap both ways.
CONTENT_KINDS = ("retail", "official_mixtape")
CONTENT_MIN = 0.8
# An official folder found by its name whose track list differs from the
# catalogued edition (SQ mixtapes, "No Ceilings And BONUS"): the files whose
# title matches a track are still linked (a file belongs to a version of a
# song, not to a position), the others are reported.
PARTIAL_MIN = 0.3


def best_edition(folder, candidates):
    """(edition, pairs, share, coverage) of the best-matching candidate."""
    best, best_pairs, best_score = None, [], 0
    for edition in candidates:
        pairs = match_tracks(folder, edition)
        score = sum(1 for _, track in pairs if track)
        if score > best_score:
            best, best_pairs, best_score = edition, pairs, score
    share = best_score / len(folder["tracks"])
    # Folders may hold extra files (skits, bonus tracks): also accept when
    # nearly all tracks of the edition were found.
    coverage = len({id(t) for _, t in best_pairs if t}) / len(best["tracks"]) if best and best["tracks"] else 0
    return best, best_pairs, share, coverage


def plan(scan, connection):
    """{'linked': [...], 'unmatched_folders': [...], 'unmatched_files': [...]}."""
    editions = catalogue_editions(connection)
    all_editions = [e for items in editions.values() for e in items]
    linked, unmatched_folders, unmatched_files = [], [], []
    for folder in scan["folders"]:
        if not folder["tracks"]:
            continue
        candidates = []
        for key in folder_keys(folder["title"]):
            candidates = editions.get(key, [])
            if not candidates:
                # "The Suffix" -> "The Suffix: Remastered"
                candidates = [e for k, items in editions.items()
                              if k.startswith(key + " ") for e in items]
            if candidates:
                break
        best, best_pairs, share, coverage = best_edition(folder, candidates)
        partial = False
        if (best is None or (share < MIN_SHARE and coverage < MIN_COVERAGE)) and folder["kind"] in CONTENT_KINDS:
            by_content = best_edition(folder, all_editions)
            if by_content[0] is not None and by_content[2] >= CONTENT_MIN and by_content[3] >= CONTENT_MIN:
                best, best_pairs, share, coverage = by_content
            elif best is not None and share >= PARTIAL_MIN:
                partial = True
        if best is None or (share < MIN_SHARE and coverage < MIN_COVERAGE and not partial):
            unmatched_folders.append({"folder": folder["folder"], "kind": folder["kind"],
                                      "title": folder["title"], "files": len(folder["tracks"]),
                                      "best_release": best and best["title"], "share": round(share, 2),
                                      "coverage": round(coverage, 2)})
            continue
        for file, track in best_pairs:
            if track:
                linked.append({"file": file, "version_id": track["version_id"],
                               "release_id": best["id"], "release_title": best["title"]})
            else:
                unmatched_files.append({"folder": folder["folder"], "file": file["file_name"],
                                        "title": file["title"], "release": best["title"]})
    return {"linked": linked, "unmatched_folders": unmatched_folders,
            "unmatched_files": unmatched_files}


def apply(connection, linked):
    """Insert file rows (idempotent by file_path); returns the number inserted."""
    inserted = 0
    for item in linked:
        file = item["file"]
        if connection.execute("SELECT 1 FROM files WHERE file_path = ?", (file["file_path"],)).fetchone():
            continue
        connection.execute("""
            INSERT INTO files (version_id, file_path, format, duration,
                               file_size, bitrate, sample_rate, sha256)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (item["version_id"], file["file_path"], file.get("format"),
              file["duration_ms"] / 1000 if file.get("duration_ms") else None,
              file.get("file_size"), file.get("bitrate"), file.get("sample_rate"), file.get("sha256")))
        inserted += 1
    return inserted


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    scan = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    connection = sqlite3.connect(DATABASE_PATH)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        result = plan(scan, connection)
        releases = sorted({item["release_title"] for item in result["linked"]})
        print(f"Files linked to catalogue tracks: {len(result['linked'])} "
              f"(in {len({i['file']['file_path'].rsplit(chr(92), 1)[0] for i in result['linked']})} folders, "
              f"{len(releases)} release titles)")
        print(f"Files in matched folders without a track: {len(result['unmatched_files'])}")
        print(f"Folders not matched to the catalogue: {len(result['unmatched_folders'])}")
        if "--apply" in sys.argv:
            with connection:
                print(f"Inserted into files: {apply(connection, result['linked'])}")
        else:
            print("Dry run; use --apply to write the files table (make a backup first).")
        return result
    finally:
        connection.close()


if __name__ == "__main__":
    main()
