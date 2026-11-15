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
