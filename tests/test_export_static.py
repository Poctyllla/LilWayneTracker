import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from export_static import without_local_files


class ExportStaticTests(unittest.TestCase):
    def test_local_library_facts_are_dropped(self):
        data = {"songs": [{"id": 1, "file": True, "title": "A Milli"}],
                "versions": [{"files": 3, "tracks": [{"release": "Tha Carter III"}]}],
                "files": [{"file_path": "C:/Users/x/Downloads/a.mp3"}]}
        self.assertEqual(without_local_files(data), {
            "songs": [{"id": 1, "file": False, "title": "A Milli"}],
            "versions": [{"files": 0, "tracks": [{"release": "Tha Carter III"}]}]})

    def test_player_file_ids_are_dropped(self):
        data = {"play": {"v": 7, "spotify": "abc1234567", "file_id": 271}}
        self.assertEqual(without_local_files(data), {"play": {"v": 7, "spotify": "abc1234567"}})


if __name__ == "__main__":
    unittest.main()
