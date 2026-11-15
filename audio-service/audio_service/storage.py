"""Content-addressed storage: a file is kept once, under its sha256 (objects/ab/cd/<sha256>).

Local disk now; the same interface can later sit on S3-compatible storage.
"""

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import BinaryIO

CHUNK = 1024 * 1024


class LocalStorage:
    def __init__(self, root: Path):
        self.root = Path(root)

    def path_for(self, sha256: str) -> Path:
        if len(sha256) != 64 or any(ch not in "0123456789abcdef" for ch in sha256):
            raise ValueError("not a sha256")
        return self.root / sha256[:2] / sha256[2:4] / sha256

    def exists(self, sha256: str) -> bool:
        return self.path_for(sha256).is_file()

    def put(self, stream: BinaryIO, max_bytes: int | None = None) -> tuple[str, int]:
        """Store a stream; returns (sha256, size).  An identical file is stored once."""
        self.root.mkdir(parents=True, exist_ok=True)
        digest, size = hashlib.sha256(), 0
        handle, temporary = tempfile.mkstemp(dir=self.root, prefix="upload-")
        try:
            with os.fdopen(handle, "wb") as out:
                while chunk := stream.read(CHUNK):
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise ValueError("file too large")
                    digest.update(chunk)
                    out.write(chunk)
            sha256 = digest.hexdigest()
            target = self.path_for(sha256)
            if target.exists():
                os.remove(temporary)  # already stored: the same content
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(temporary, target)
            return sha256, size
        except BaseException:
            if os.path.exists(temporary):
                os.remove(temporary)
            raise

    def put_file(self, path: Path) -> tuple[str, int]:
        with open(path, "rb") as stream:
            return self.put(stream)
