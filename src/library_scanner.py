"""Scan the user's local music library (read-only).

The library stays where it is and under the user's control: the scanner only
reads tags, durations, sizes and hashes.  Folder names of the user's
discography look like
    "Lil Wayne-Tha.Block.Is.Hot.Retail-(1999)-[NoFS]"
    "Lil.Wayne-The.Leak.4.(Bootleg)-2008-[NoFS]"
    "Lil.Wayne-Tha.Fix-(2006)-(Unreleased)-[NoFS]"

usage:
    python library_scanner.py "<library folder>" [--no-hash]
writes exports/library_scan_<date>.json
    python library_scanner.py --retitle exports/library_scan_<date>.json
recomputes titles from the stored tags (after parser fixes)
"""

import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3NoHeaderError
from mutagen.mp3 import MP3, HeaderNotFoundError


PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPORTS_FOLDER = PROJECT_ROOT / "exports"
AUDIO_EXTENSIONS = {".mp3"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".jpe"}

KIND_MARKERS = (
    ("unreleased", r"unreleased|stolen|leaks? of|the leak\b.*bootleg"),
    ("bootleg", r"bootleg"),
    ("official_mixtape", r"official\.?mixtape|collectors\.edition|gangsta\.grillz"),
    ("retail", r"retail|proper"),
)
NOISE = re.compile(
    r"\[nofs\]|\(?\b(retail|proper|bootleg(\.bonus\.disc)?|official\.?mixtape|unreleased|"
    r"collectors\.edition|deluxe\.edition|retail\.deluxe\.edition|bootleg-\d{4})\b\)?",
    re.IGNORECASE,
)


def parse_folder_name(name):
    """{artist, title, year, kind, disc} from a release folder name."""
    year = None
    years = re.findall(r"(?<!\d)(19|20)(\d{2})(?!\d)", name)
    if years:
        year = int("".join(years[-1]))
    lowered = name.casefold()
    kind = next((kind for kind, pattern in KIND_MARKERS if re.search(pattern, lowered)), "other")
    disc_match = re.search(r"\b(?:cd|disc)[.\s-]?(\d)\b", lowered)
    disc = int(disc_match.group(1)) if disc_match else None
    if "bonus" in lowered and ("cd" in lowered or "disc" in lowered):
        disc = disc or 2

    artist, _, rest = name.partition("-")
    rest = NOISE.sub(" ", rest)
    rest = re.sub(r"\(?\b(19|20)\d{2}\b\)?", " ", rest)
    rest = re.sub(r"\(?\b(?:cd|disc)[.\s-]?\d\b\)?|\bbonus\.cd\b", " ", rest, flags=re.IGNORECASE)
    title = re.sub(r"[._]+", " ", rest)
    title = re.sub(r"[()\[\]-]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    if not title:
        # No dash between artist and title: "Lil.Wayne.And.Drake.The.Carter.Meets..."
        match = re.match(r"^(lil[._ ]wayne(?:[._ ](?:and|&)[._ ](?:the[._ ])?[^._ ]+)?)[._ ](.+)$",
                         artist, re.IGNORECASE)
        if match:
            artist = match.group(1)
            title = re.sub(r"\s+", " ", re.sub(r"[._]+", " ", match.group(2))).strip()
    return {
        "artist": re.sub(r"[._]+", " ", artist).strip(),
        "title": title,
        "year": year,
        "kind": kind,
        "disc": disc,
    }


FEAT = re.compile(r"\s*[(\[]?\s*(?:feat\.?|ft\.?|featuring)\s+([^)\]]+)[)\]]?\s*$", re.IGNORECASE)


ARTIST_PREFIX = re.compile(r"lil\s*'?\s*wa[yn]{2}e|\bft\b|\bfeat\b|\bfeaturing\b", re.IGNORECASE)


def _looks_like_artist(left, artist):
    left_folded = left.casefold()
    if ARTIST_PREFIX.search(left):
        return True
    first = (artist or "").replace(".", " ").split()
    return bool(first) and left_folded.startswith(first[0].casefold()) and len(left) > 2


WAYNE_ONLY = re.compile(r"^\s*lil\s*'?\s*wa[yn]{2}e\b", re.IGNORECASE)
SPACED_DASH = re.compile(r"\s+-\s*|\s*-\s+")
TIGHT_DASH = re.compile(r"(?<=[a-z.)'\"])-(?=[A-Z0-9(])")


def split_artist_title(title, artist):
    """(title, featuring, track_artist) for one 'artist - title' split."""
    for pattern in (SPACED_DASH, TIGHT_DASH):  # spaced first: keeps "Jay-Z", "T-Pain"
        separator = pattern.search(title)
        if not separator:
            continue
        left, right = title[:separator.start()].strip(), title[separator.end():].strip()
        if not left or not right:
            return title, None, None
        if WAYNE_ONLY.match(right) and not WAYNE_ONLY.match(left):
            return left, None, None  # "Nothing Can Stop Me - Lil' Wayne"
        if _looks_like_artist(left, artist):
            match = FEAT.search(left) or re.search(r"\b(?:ft|feat)\.?\s+(.+)$", left, re.IGNORECASE)
            return right, match.group(1).strip() if match else None, None
        if pattern is SPACED_DASH and len(left.split()) <= 5 and "(" not in left:
            # Another artist's song on a bootleg: "Rich Boy & John Legend - Ghetto Rich"
            return right, None, left
        return title, None, None
    return title, None, None


def clean_track_title(raw_title, artist=None, with_artist=False):
    """(title, featuring) from tags like 'Lil Wayne feat. Tyga - Exquisite'
    or 'The Game Ft. Cam'ron-Where It At' (no spaces around the dash).
    with_artist=True also returns the other main artist, if any."""
    title = re.sub(r"[\x96–—]", "-", (raw_title or "").strip())  # cp1252 / unicode dashes
    title = re.sub(r"\s*-?\s*\[nofs\]\s*$|\s+-\s*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(ft|feat)\.(?=\S)", r"\1. ", title, flags=re.IGNORECASE)  # "Ft.Fat Joe"
    featuring = track_artist = None
    for _ in range(3):  # "Lil Wayne Ft. Nas - Rich Boy & John Legend - Ghetto Rich"
        new_title, new_featuring, new_artist = split_artist_title(title, artist)
        if new_title == title:
            break
        title = new_title
        featuring = featuring or new_featuring
        track_artist = track_artist or new_artist
        if not (ARTIST_PREFIX.match(title) or SPACED_DASH.search(title)):
            break
    match = FEAT.search(title)
    if match and not ARTIST_PREFIX.fullmatch(title[:match.start()].strip() or "x"):
        stripped = title[:match.start()].strip()
        if stripped and not re.fullmatch(r"lil\s*wayne", stripped, re.IGNORECASE):
            featuring = featuring or match.group(1).strip()
            title = stripped
    if with_artist:
        return title, featuring, track_artist
    return title, featuring


def retitle(scan):
    """Recompute title/featuring of a saved scan from its stored tags."""
    changed = 0
    for folder in scan["folders"]:
        for file in folder["tracks"]:
            raw = file.get("tag_title") or re.sub(r"^\d+[-_. ]+", "", Path(file["file_name"]).stem).replace("_", " ")
            title, featuring, track_artist = clean_track_title(raw, file.get("artist"), with_artist=True)
            if (title, featuring, track_artist) != (file.get("title"), file.get("featuring"),
                                                    file.get("track_artist")):
                file.update(title=title, featuring=featuring, track_artist=track_artist)
                changed += 1
    return changed


def number(value):
    """'2/17' -> 2."""
    match = re.match(r"\s*(\d+)", str(value or ""))
    return int(match.group(1)) if match else None


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan_file(path, compute_hash=True):
    info = {"file_path": str(path), "file_name": path.name, "format": path.suffix.lstrip(".").lower(),
            "file_size": path.stat().st_size}
    try:
        audio = MP3(path)
        info.update(duration_ms=int(round(audio.info.length * 1000)),
                    bitrate=audio.info.bitrate, sample_rate=audio.info.sample_rate)
    except (HeaderNotFoundError, OSError) as error:
        info["error"] = f"audio: {error}"
    try:
        tags = EasyID3(path)
    except (ID3NoHeaderError, OSError):
        tags = {}
    tag = lambda key: (tags.get(key) or [None])[0]
    artist = tag("artist")
    raw_title = tag("title") or re.sub(r"^\d+[-_. ]+", "", path.stem).replace("_", " ")
    title, featuring, track_artist = clean_track_title(raw_title, artist, with_artist=True)
    info.update(tag_title=tag("title"), title=title, featuring=featuring, artist=artist,
                track_artist=track_artist,
                track_number=number(tag("tracknumber")) or number(path.stem),
                disc_number=number(tag("discnumber")), year=tag("date"))
    if compute_hash:
        info["sha256"] = sha256_of(path)
    return info


def scan_library(root, compute_hash=True, progress=True):
    root = Path(root)
    folders = []
    directories = sorted(path for path in root.iterdir() if path.is_dir())
    for index, directory in enumerate(directories, start=1):
        files = sorted(path for path in directory.rglob("*") if path.is_file())
        folder = {"folder": directory.name, **parse_folder_name(directory.name),
                  "covers": [str(p) for p in files if p.suffix.lower() in IMAGE_EXTENSIONS],
                  "tracks": [scan_file(p, compute_hash) for p in files
                             if p.suffix.lower() in AUDIO_EXTENSIONS]}
        folders.append(folder)
        if progress and index % 20 == 0:
            print(f"  ... {index}/{len(directories)} folders")
    return folders


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    if sys.argv[1] == "--retitle":
        path = Path(sys.argv[2])
        scan = json.loads(path.read_text(encoding="utf-8"))
        changed = retitle(scan)
        for folder in scan["folders"]:
            folder.update(parse_folder_name(folder["folder"]))
        path.write_text(json.dumps(scan, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"Track titles recomputed: {changed}")
        return
    folders = scan_library(sys.argv[1], compute_hash="--no-hash" not in sys.argv)
    EXPORTS_FOLDER.mkdir(exist_ok=True)
    out = EXPORTS_FOLDER / f"library_scan_{date.today():%Y%m%d}.json"
    out.write_text(json.dumps({"root": sys.argv[1], "folders": folders}, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    kinds = {}
    for folder in folders:
        kinds[folder["kind"]] = kinds.get(folder["kind"], 0) + 1
    print(f"Folders: {len(folders)} {kinds}; audio files: {sum(len(f['tracks']) for f in folders)}")
    print(f"Saved: {out.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
