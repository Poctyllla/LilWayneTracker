import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from audio_service import db, rights  # noqa: E402
from audio_service.app import create_app  # noqa: E402
from audio_service.config import Settings  # noqa: E402
from audio_service.storage import LocalStorage  # noqa: E402

AUDIO = bytes(range(256)) * 40  # 10 240 bytes of "audio"


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.storage = LocalStorage(Path(self.directory.name))

    def tearDown(self):
        self.directory.cleanup()

    def test_same_content_is_stored_once(self):
        first = self.storage.put(io.BytesIO(AUDIO))
        second = self.storage.put(io.BytesIO(AUDIO))
        self.assertEqual(first, second)
        self.assertEqual(first[1], len(AUDIO))
        files = [p for p in Path(self.directory.name).rglob("*") if p.is_file()]
        self.assertEqual(len(files), 1)  # no leftover temporary file either

    def test_size_limit_and_bad_names(self):
        with self.assertRaises(ValueError):
            self.storage.put(io.BytesIO(AUDIO), max_bytes=100)
        self.assertEqual([p for p in Path(self.directory.name).rglob("*") if p.is_file()], [])
        with self.assertRaises(ValueError):
            self.storage.path_for("../../etc/passwd")


class RightsTests(unittest.TestCase):
    def test_public_needs_rights_and_approval(self):
        self.assertTrue(rights.may_stream_publicly("licensed", "approved"))
        self.assertFalse(rights.may_stream_publicly("licensed", "pending"))
        self.assertFalse(rights.may_stream_publicly("private", "approved"))  # the owner's library, leaks
        self.assertFalse(rights.may_stream_publicly(None, "approved"))
        self.assertFalse(rights.may_download_publicly("licensed", False, "approved"))
        self.assertTrue(rights.may_download_publicly("public_domain", True, "approved"))


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings = Settings(data_dir=Path(self.directory.name))
        connection = db.connect(self.settings.database_path)
        db.initialize(connection)
        storage = LocalStorage(self.settings.storage_dir)
        sha, size = storage.put(io.BytesIO(AUDIO))
        with connection:
            private = connection.execute("INSERT INTO assets (sha256, size, format, bitrate) VALUES (?, ?, 'mp3', 128000)",
                                         (sha, size)).lastrowid
            connection.execute("INSERT INTO rights (asset_id, status) VALUES (?, 'private')", (private,))
            connection.execute("INSERT INTO asset_versions (asset_id, version_id) VALUES (?, 42)", (private,))
            sha2, size2 = storage.put(io.BytesIO(AUDIO[::-1]))
            cleared = connection.execute("INSERT INTO assets (sha256, size, format, bitrate) VALUES (?, ?, 'mp3', 320000)",
                                         (sha2, size2)).lastrowid
            connection.execute("INSERT INTO rights (asset_id, status, license) VALUES (?, 'licensed', 'CC BY 4.0')", (cleared,))
            connection.execute("INSERT INTO moderation (asset_id, state, actor) VALUES (?, 'approved', 'test')", (cleared,))
            connection.execute("INSERT INTO asset_versions (asset_id, version_id) VALUES (?, 42)", (cleared,))
        connection.close()
        self.private, self.cleared = private, cleared

    def tearDown(self):
        for client in getattr(self, "_clients", []):
            client.app.state.connection.close()
        self.directory.cleanup()

    def client(self, public=False):
        client = TestClient(create_app(self.settings, public=public))
        self._clients = getattr(self, "_clients", []) + [client]
        return client

    def test_health(self):
        self.assertEqual(self.client().get("/health").json()["assets"], 2)

    def test_local_mode_serves_everything_best_first(self):
        assets = self.client().get("/v1/versions/42/assets").json()
        self.assertEqual([a["id"] for a in assets], [self.cleared, self.private])  # 320 kbps first
        self.assertEqual(self.client().get("/v1/versions/7/assets").json(), [])

    def test_public_mode_hides_private_assets(self):
        public = self.client(public=True)
        self.assertEqual([a["id"] for a in public.get("/v1/versions/42/assets").json()], [self.cleared])
        self.assertEqual(public.get(f"/v1/assets/{self.private}").status_code, 404)
        self.assertEqual(public.get(f"/v1/assets/{self.private}/stream").status_code, 404)
        self.assertEqual(public.get(f"/v1/assets/{self.cleared}/stream").status_code, 200)

    def test_stream_and_ranges(self):
        client = self.client()
        whole = client.get(f"/v1/assets/{self.private}/stream")
        self.assertEqual((whole.status_code, whole.content, whole.headers["accept-ranges"]), (200, AUDIO, "bytes"))
        part = client.get(f"/v1/assets/{self.private}/stream", headers={"Range": "bytes=100-199"})
        self.assertEqual(part.status_code, 206)
        self.assertEqual(part.content, AUDIO[100:200])
        self.assertEqual(part.headers["content-range"], f"bytes 100-199/{len(AUDIO)}")
        tail = client.get(f"/v1/assets/{self.private}/stream", headers={"Range": "bytes=-10"})
        self.assertEqual(tail.content, AUDIO[-10:])
        self.assertEqual(client.get(f"/v1/assets/{self.private}/stream", headers={"Range": "bytes=99999-"}).status_code, 416)
        self.assertEqual(client.get("/v1/assets/999/stream").status_code, 404)


if __name__ == "__main__":
    unittest.main()


class LibraryImportTests(unittest.TestCase):
    def setUp(self):
        import hashlib
        import sqlite3
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.settings = Settings(data_dir=root / "service")
        self.catalogue = root / "catalogue.db"
        (root / "a.mp3").write_bytes(AUDIO)
        (root / "b.mp3").write_bytes(AUDIO[::-1])
        c = sqlite3.connect(self.catalogue)
        c.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, version_id INTEGER, file_path TEXT, format TEXT, "
                  "duration REAL, file_size INTEGER, bitrate INTEGER, sample_rate INTEGER, sha256 TEXT)")
        for i, (name, data, version) in enumerate([("a.mp3", AUDIO, 5), ("b.mp3", AUDIO[::-1], 6)], 1):
            c.execute("INSERT INTO files VALUES (?, ?, ?, 'mp3', 60, ?, 128000, 44100, ?)",
                      (i, version, str(root / name), len(data), hashlib.sha256(data).hexdigest()))
        c.commit()
        c.close()

    def tearDown(self):
        self.directory.cleanup()

    def run_import(self, apply=True):
        from audio_service import library_import
        return library_import.run(apply, self.catalogue, self.settings, progress=lambda *_: None)

    def test_dry_run_changes_nothing(self):
        report = self.run_import(apply=False)
        self.assertEqual((report["files"], report["bytes_to_copy"]), (2, 2 * len(AUDIO)))
        self.assertFalse(self.settings.storage_dir.exists())

    def test_import_is_private_linked_and_repeatable(self):
        first = self.run_import()
        self.assertEqual((first["copied"], first["linked"]), (2, 2))
        again = self.run_import()
        self.assertEqual((again["copied"], again["already_stored"], again["linked"]), (0, 2, 2))
        client = TestClient(create_app(self.settings))
        try:
            assets = client.get("/v1/versions/5/assets").json()
            self.assertEqual([a["rights"] for a in assets], ["private"])
            self.assertEqual(client.get(assets[0]["stream_url"]).content, AUDIO)
            public = TestClient(create_app(self.settings, public=True))
            self.assertEqual(public.get("/v1/versions/5/assets").json(), [])  # never public
            public.app.state.connection.close()
        finally:
            client.app.state.connection.close()

    def test_originals_can_go_after_import(self):
        self.run_import()
        for name in ("a.mp3", "b.mp3"):
            (Path(self.directory.name) / name).unlink()
        report = self.run_import()  # nothing lost: everything is already stored
        self.assertEqual((report["already_stored"], report["linked"], report["missing"]), (2, 2, []))


class LibraryMoveTests(LibraryImportTests):
    def test_move_and_restore(self):
        from audio_service import library_move
        self.run_import()
        root = Path(self.directory.name)
        (root / "b.mp3").write_bytes(b"changed after the import")  # not stored: must be kept
        _, removable, not_stored, _ = library_move.plan(self.catalogue, self.settings)
        self.assertEqual(([p.name for p, _ in removable], [p.name for p, _ in not_stored]), (["a.mp3"], ["b.mp3"]))
        for path, _ in removable:
            path.unlink()
        self.assertEqual(library_move.restore(self.catalogue, self.settings), (1, 0))
        self.assertEqual((root / "a.mp3").read_bytes(), AUDIO)


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        rng = np.random.default_rng(7)
        self.np = np
        self.song = rng.integers(0, 2**32, 900, dtype=np.uint32)
        noise = rng.integers(0, 2**32, 900, dtype=np.uint32) & rng.integers(0, 2**32, 900, dtype=np.uint32) \
            & rng.integers(0, 2**32, 900, dtype=np.uint32)  # about 1 bit in 8 flipped
        self.reencoded = self.song ^ noise
        self.other = rng.integers(0, 2**32, 900, dtype=np.uint32)

    def test_similarity(self):
        from audio_service import fingerprints as f
        self.assertEqual(f.similarity(self.song, self.song), (1.0, 0))
        self.assertGreater(f.similarity(self.song, self.reencoded)[0], 0.8)
        self.assertLess(f.similarity(self.song, self.other)[0], 0.6)
        score, offset = f.similarity(self.song[5:], self.song)  # starts 5 items later
        self.assertEqual((score, offset), (1.0, -5))
        self.assertTrue((f.decode(f.encode(self.song)) == self.song).all())

    def test_pairs_only_of_similar_length_and_classified(self):
        from audio_service import fingerprints as f
        items = [(1, 200.0, self.song), (2, 201.0, self.reencoded), (3, 200.5, self.other), (4, 260.0, self.song)]
        pairs = f.similar_pairs(items)
        self.assertEqual([(a, b) for a, b, *_ in pairs], [(1, 2)])  # 4 is a minute longer: not compared

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = Settings(data_dir=Path(directory.name))
        connection = db.connect(settings.database_path)
        db.initialize(connection)
        with connection:
            for i, (asset_id, length, fp) in enumerate(items):
                connection.execute("INSERT INTO assets (id, sha256, size, duration, fingerprint) VALUES (?, ?, 1, ?, ?)",
                                   (asset_id, f"{asset_id:064x}", length, f.encode(fp)))
            connection.executemany("INSERT INTO asset_versions (asset_id, version_id) VALUES (?, ?)",
                                   [(1, 10), (2, 10), (3, 11), (4, 12)])
        connection.close()
        report = f.run(apply=True, settings=settings, progress=lambda *_: None)
        self.assertEqual((len(report["same_version"]), len(report["different_versions"])), (1, 0))
        client = TestClient(create_app(settings))
        try:
            self.assertEqual([a["id"] for a in client.get("/v1/assets/1/similar").json()], [2])
            self.assertEqual(client.get("/v1/assets/3/similar").json(), [])
        finally:
            client.app.state.connection.close()
