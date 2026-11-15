"""Stage 2: bring the owner's library (the tracker's `files` table) into the service.

Every file is copied into the content-addressed storage (its sha256 is checked
against the catalogue on the way), registered as a *private* asset and linked to
its catalogue version.  Safe to run again: stored content is not copied twice and
the library links are rebuilt from the catalogue (so merges in the tracker carry
over).  The catalogue database is opened read-only.

    python -m audio_service.library_import            # dry run
    python -m audio_service.library_import --apply
"""

import argparse
import sqlite3
from pathlib import Path

from . import db
from .config import SERVICE_ROOT, load_settings
from .storage import LocalStorage

CATALOGUE = SERVICE_ROOT.parent / "data" / "lilwayne.db"
CONTENT_TYPES = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "flac": "audio/flac", "ogg": "audio/ogg", "wav": "audio/wav"}


def catalogue_files(path: Path):
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in connection.execute(
            "SELECT id, version_id, file_path, format, duration, file_size, bitrate, sample_rate, sha256 FROM files ORDER BY id")]
    finally:
        connection.close()


def run(apply: bool, catalogue: Path = CATALOGUE, settings=None, progress=print):
    settings = settings or load_settings()
    files = catalogue_files(catalogue)
    storage = LocalStorage(settings.storage_dir)
    connection = db.connect(settings.database_path)
    db.initialize(connection)
    report = {"files": len(files), "copied": 0, "already_stored": 0, "missing": [], "hash_mismatch": [], "linked": 0}
    try:
        to_copy = [f for f in files if not (f["sha256"] and storage.exists(f["sha256"]))]
        report["already_stored"] = len(files) - len(to_copy)
        report["missing"] = [f["file_path"] for f in to_copy if not Path(f["file_path"]).is_file()]
        report["bytes_to_copy"] = sum(f["file_size"] or 0 for f in to_copy if Path(f["file_path"]).is_file())
        if not apply:
            return report
        for index, f in enumerate(files, 1):
            sha = f["sha256"]
            if not (sha and storage.exists(sha)):
                source = Path(f["file_path"])
                if not source.is_file():
                    continue
                sha, size = storage.put_file(source)
                report["copied"] += 1
                if f["sha256"] and sha != f["sha256"]:
                    report["hash_mismatch"].append(f["file_path"])  # changed since the scan: stored as it is now
            fmt = (f["format"] or Path(f["file_path"]).suffix.lstrip(".")).lower()
            with connection:
                connection.execute("""
                    INSERT INTO assets (sha256, size, format, content_type, duration, bitrate, sample_rate, original_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(sha256) DO NOTHING""",
                    (sha, storage.path_for(sha).stat().st_size, fmt, CONTENT_TYPES.get(fmt), f["duration"],
                     f["bitrate"], f["sample_rate"], Path(f["file_path"]).name))
                asset_id = connection.execute("SELECT id FROM assets WHERE sha256 = ?", (sha,)).fetchone()[0]
                connection.execute("""INSERT INTO rights (asset_id, status, decided_by, decided_at)
                    VALUES (?, 'private', 'library import', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                    ON CONFLICT(asset_id) DO NOTHING""", (asset_id,))
                f["asset_id"] = asset_id
            if index % 250 == 0:
                progress(f"  ... {index}/{len(files)}")
        with connection:  # the catalogue owns the links: rebuild the library ones
            connection.execute("DELETE FROM asset_versions WHERE linked_by = 'library'")
            for f in files:
                if f.get("asset_id") and f["version_id"]:
                    report["linked"] += connection.execute(
                        "INSERT OR IGNORE INTO asset_versions (asset_id, version_id, confidence, linked_by) "
                        "VALUES (?, ?, 1.0, 'library')", (f["asset_id"], f["version_id"])).rowcount
        return report
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--catalogue", type=Path, default=CATALOGUE)
    args = parser.parse_args()
    report = run(args.apply, args.catalogue)
    print(f"Library files: {report['files']}; already stored: {report['already_stored']}; "
          f"missing on disk: {len(report['missing'])}")
    if not args.apply:
        print(f"To copy: {report['bytes_to_copy'] / 1e9:.1f} GB (dry run; --apply copies)")
        return
    print(f"Copied: {report['copied']}; linked to versions: {report['linked']}; "
          f"changed since the scan: {len(report['hash_mismatch'])}")
    for path in report["hash_mismatch"][:10]:
        print("  changed:", path)


if __name__ == "__main__":
    main()
