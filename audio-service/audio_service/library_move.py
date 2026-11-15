"""Finish moving the owner's library into the service, or undo it.

    python -m audio_service.library_move                       # dry run
    python -m audio_service.library_move --delete-originals    # remove library audio already stored
    python -m audio_service.library_move --restore             # put the files back where they came from

An original is removed only when its content (sha256, recomputed now) is in the
storage.  Only the audio files listed in the catalogue are touched: covers and
anything else in the folders stay.  The catalogue keeps every file's original
path, so --restore can always put the library back.
"""

import argparse
import hashlib
import shutil
from pathlib import Path

from .config import load_settings
from .library_import import CATALOGUE, catalogue_files
from .storage import LocalStorage


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def plan(catalogue: Path = CATALOGUE, settings=None):
    storage = LocalStorage((settings or load_settings()).storage_dir)
    removable, not_stored, gone = [], [], 0
    for f in catalogue_files(catalogue):
        original = Path(f["file_path"])
        if not original.is_file():
            gone += 1
            continue
        sha = file_sha256(original)
        (removable if storage.exists(sha) else not_stored).append((original, sha))
    return storage, removable, not_stored, gone


def restore(catalogue: Path = CATALOGUE, settings=None) -> tuple[int, int]:
    storage = LocalStorage((settings or load_settings()).storage_dir)
    restored = unavailable = 0
    for f in catalogue_files(catalogue):
        original = Path(f["file_path"])
        if original.exists():
            continue
        if not (f["sha256"] and storage.exists(f["sha256"])):
            unavailable += 1
            continue
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(storage.path_for(f["sha256"]), original)
        restored += 1
    return restored, unavailable


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--delete-originals", action="store_true")
    group.add_argument("--restore", action="store_true")
    parser.add_argument("--catalogue", type=Path, default=CATALOGUE)
    args = parser.parse_args()
    if args.restore:
        restored, unavailable = restore(args.catalogue)
        print(f"Restored: {restored}; not in the storage: {unavailable}")
        return
    _, removable, not_stored, gone = plan(args.catalogue)
    size = sum(p.stat().st_size for p, _ in removable)
    print(f"Originals safely stored: {len(removable)} ({size / 1e9:.1f} GB); not stored (kept): {len(not_stored)}; "
          f"already gone: {gone}")
    for path, _ in not_stored[:10]:
        print("  kept:", path)
    if not args.delete_originals:
        print("Dry run; --delete-originals removes the stored ones (--restore brings them back).")
        return
    for path, _ in removable:
        path.unlink()
    print(f"Removed {len(removable)} originals.")


if __name__ == "__main__":
    main()
