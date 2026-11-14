import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from wikipedia_source import parse_release_table, strip_markup


SAMPLE = """==Mixtapes==
{| class="wikitable plainrowheaders"
! scope="col" | Title
|-
! scope="row"| ''SQ6: Tha Remix''<br /><span style="font-size:85%;">(with [[Sqad Up]])</span>
|
* Released: 2003<ref>{{cite web|url=http://x|title=SQ6}}</ref>
* Format: Digital download
| — || — || — || —
|-
! scope="row"| ''[[Da Drought 3]]''
|
* Released: May 11, 2007<ref name="d3"/>
* Label: [[Young Money Entertainment|Young Money]]
* Format: Digital download
| — || — || — || —
|-
! scope="row"| ''[[Trust Fund Babies]]''<br /><span>(with [[Rich the Kid]])</span>
|
* Released: October 1, 2021
* Labels: Young Money, Rostrum, [[Republic Records|Republic]]
* Formats: CD, digital download
| 31 || — || 18 || 15
|}
"""


class WikipediaParserTests(unittest.TestCase):
    def test_rows(self):
        self.assertEqual(parse_release_table(SAMPLE), [
            {"title": "SQ6: Tha Remix", "with": "Sqad Up", "released": "2003",
             "label": None, "format": "Digital download"},
            {"title": "Da Drought 3", "with": None, "released": "May 11, 2007",
             "label": "Young Money", "format": "Digital download"},
            {"title": "Trust Fund Babies", "with": "Rich the Kid", "released": "October 1, 2021",
             "label": "Young Money, Rostrum, Republic", "format": "CD, digital download"},
        ])

    def test_strip_markup(self):
        self.assertEqual(strip_markup("''[[No Ceilings (mixtape)|No Ceilings]]''<ref>x</ref>"),
                         "No Ceilings")
        self.assertEqual(parse_release_table(""), [])


if __name__ == "__main__":
    unittest.main()
