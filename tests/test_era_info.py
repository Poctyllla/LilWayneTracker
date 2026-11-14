import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from era_info import first_sentences, generated_summary


class EraInfoTests(unittest.TestCase):
    def test_first_sentences(self):
        text = ("Tha Carter III is the sixth studio album by Lil Wayne, released on June 10, 2008.[1] "
                "It follows a string of mixtapes. It sold 1 million copies. A fourth sentence.")
        self.assertEqual(first_sentences(text),
                         "Tha Carter III is the sixth studio album by Lil Wayne, released on June 10, 2008. "
                         "It follows a string of mixtapes. It sold 1 million copies.")
        self.assertEqual(first_sentences("No end mark"), "No end mark")
        self.assertEqual(first_sentences(""), "")

    def test_generated_summary(self):
        eras = [{"name": "Tha Carter V", "end_date": "2018-09-28", "release_group_id": "a"},
                {"name": "Tha Carter VI", "end_date": "2025-06-06", "release_group_id": "b"},
                {"name": "After Tha Carter VI", "end_date": None, "release_group_id": None}]
        self.assertIn("after Tha Carter VI", generated_summary(eras[2], eras))


if __name__ == "__main__":
    unittest.main()
