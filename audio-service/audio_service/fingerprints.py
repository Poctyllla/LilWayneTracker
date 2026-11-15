"""Stage 4: audio fingerprints (Chromaprint) and near-duplicates.

Two files with different bytes can hold the same recording: another bitrate,
re-tagged, re-encoded.  Chromaprint's fpcalc turns the first two minutes of audio
into a list of 32-bit numbers; two files are the same recording when, at the best
small offset, few bits differ.

    python -m audio_service.fingerprints             # what is missing / found (no changes)
    python -m audio_service.fingerprints --apply     # compute missing fingerprints, store similar pairs

fpcalc comes from https://acoustid.org/chromaprint (winget: AcoustID.Chromaprint);
FPCALC may point to it.  Results are suggestions: the catalogue is never changed here.
"""

import argparse
import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np

from . import db
from .config import load_settings
from .storage import LocalStorage

SAME_RECORDING = 0.80   # share of equal bits at the best offset (other recordings: about 0.5)
MAX_OFFSET = 12         # fingerprint items (about 1.5 s)
MIN_OVERLAP = 80        # items (about 10 s)
LENGTH_WINDOW = 3.0     # seconds: only files of about the same length are compared


def find_fpcalc():
    candidates = [os.environ.get("FPCALC"), shutil.which("fpcalc"),
                  str(Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "fpcalc.exe")]
    packages = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if packages.is_dir():
        candidates += [str(p) for p in packages.glob("AcoustID.Chromaprint*/**/fpcalc.exe")]
    return next((c for c in candidates if c and Path(c).is_file()), None)


def compute(path: Path, fpcalc: str) -> tuple[float, np.ndarray]:
    """(duration, fingerprint) of an audio file."""
    out = subprocess.run([fpcalc, "-raw", "-json", str(path)], capture_output=True, text=True, timeout=120, check=True)
    data = json.loads(out.stdout)
    return float(data["duration"]), np.array(data["fingerprint"], dtype=np.uint32)


def encode(fingerprint: np.ndarray) -> str:
    return base64.b64encode(fingerprint.astype("<u4").tobytes()).decode("ascii")


def decode(text: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(text), dtype="<u4")


def similarity(a: np.ndarray, b: np.ndarray, max_offset: int = MAX_OFFSET) -> tuple[float, int]:
    """(share of equal bits, offset) at the best alignment; (0, 0) when they barely overlap."""
    best, best_offset = 0.0, 0
    for offset in range(-max_offset, max_offset + 1):
        x, y = (a[offset:], b) if offset >= 0 else (a, b[-offset:])
        n = min(len(x), len(y))
        if n < MIN_OVERLAP:
            continue
        differing = np.unpackbits((x[:n] ^ y[:n]).view(np.uint8)).sum()
        score = 1.0 - differing / (32.0 * n)
        if score > best:
            best, best_offset = score, offset
    return best, best_offset


def similar_pairs(items, threshold=SAME_RECORDING, window=LENGTH_WINDOW):
    """items: [(asset_id, duration, fingerprint)] -> [(a, b, score, offset)] for the same recording."""
    items = sorted(items, key=lambda item: item[1] or 0)
    found = []
    for i, (a_id, a_length, a_fp) in enumerate(items):
        for b_id, b_length, b_fp in items[i + 1:]:
            if (b_length or 0) - (a_length or 0) > window:
                break
            score, offset = similarity(a_fp, b_fp)
            if score >= threshold:
                found.append((min(a_id, b_id), max(a_id, b_id), round(score, 4), offset))
    return found


def classify(connection, pairs):
    """Split pairs into copies of one catalogue version and audio shared by different versions."""
    versions = {}
    for asset_id, version_id in connection.execute("SELECT asset_id, version_id FROM asset_versions"):
        versions.setdefault(asset_id, set()).add(version_id)
    same, different = [], []
    for a, b, score, offset in pairs:
        (same if versions.get(a, set()) & versions.get(b, set()) else different).append((a, b, score, offset))
    return same, different


def run(apply: bool, settings=None, fpcalc=None, progress=print):
    settings = settings or load_settings()
    connection = db.connect(settings.database_path)
    db.initialize(connection)
    storage = LocalStorage(settings.storage_dir)
    report = {"assets": 0, "missing": 0, "computed": 0, "failed": [], "pairs": [], "fpcalc": fpcalc or find_fpcalc()}
    try:
        rows = connection.execute("SELECT id, sha256, duration, fingerprint FROM assets ORDER BY id").fetchall()
        report["assets"] = len(rows)
        todo = [r for r in rows if not r["fingerprint"]]
        report["missing"] = len(todo)
        if apply and todo:
            if not report["fpcalc"]:
                raise SystemExit("fpcalc not found: install Chromaprint (winget install --id AcoustID.Chromaprint -e) "
                                 "or set FPCALC")
            for index, row in enumerate(todo, 1):
                try:
                    length, fingerprint = compute(storage.path_for(row["sha256"]), report["fpcalc"])
                except (subprocess.SubprocessError, OSError, ValueError, KeyError) as error:
                    report["failed"].append((row["id"], str(error)[:120]))
                    continue
                with connection:
                    connection.execute("UPDATE assets SET fingerprint = ?, duration = COALESCE(duration, ?) WHERE id = ?",
                                       (encode(fingerprint), length, row["id"]))
                report["computed"] += 1
                if index % 250 == 0:
                    progress(f"  ... {index}/{len(todo)}")
        items = [(r["id"], r["duration"], decode(r["fingerprint"])) for r in connection.execute(
            "SELECT id, duration, fingerprint FROM assets WHERE fingerprint IS NOT NULL")]
        report["pairs"] = similar_pairs(items)
        report["same_version"], report["different_versions"] = classify(connection, report["pairs"])
        if apply:
            with connection:
                connection.execute("DELETE FROM similar_assets")
                connection.executemany("INSERT INTO similar_assets (asset_a, asset_b, score, offset) VALUES (?, ?, ?, ?)",
                                       report["pairs"])
        return report
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = run(args.apply)
    print(f"Assets: {report['assets']}; without fingerprint: {report['missing']}; fpcalc: {report['fpcalc'] or 'not found'}")
    if args.apply:
        print(f"Computed: {report['computed']}; failed: {len(report['failed'])}")
    print(f"Same recording: {len(report['pairs'])} pairs -- copies of one version: {len(report['same_version'])}, "
          f"shared by different catalogue versions: {len(report['different_versions'])}")
    if not args.apply:
        print("Dry run; --apply computes fingerprints and stores the pairs.")


if __name__ == "__main__":
    main()
