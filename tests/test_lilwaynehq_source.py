import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from lilwaynehq_source import compare, parse_list_page


PAGE = """<html><head><script>var x = "<p>not this</p>";</script></head><body>
<ul><li><a href="/">Home</a></li></ul>
<h1>Lil Wayne Singles</h1>
<p>Here, you will find every single Lil Wayne has officially released since he began.</p>
<p><img src="a.jpg"></p>
<p><a href="/x">Grindin (feat. Drake)</a></p>
<p>Sucker For Pain (with Wiz Khalifa &amp; Imagine Dragons feat. Logic)</p>
<p><a>T-Wayne</a> <span>Lil Wayne &amp; T-Pain</span></p>
<p><a>Senile</a> <span>(with Young Money)</span></p>
<p>If you have any problems with the mixtape downloads, please <a>contact me</a>.</p>
<p>» <a href="https://www.lilwaynehq.com/discography/">Lil Wayne Discography</a></p>
<p>Footer paragraph</p>
<h2>Website</h2>
</body></html>"""


class ParserTests(unittest.TestCase):
    def test_list_page(self):
        heading, entries = parse_list_page(PAGE)
        self.assertEqual(heading, "Lil Wayne Singles")
        self.assertEqual(entries, [
            {"title": "Grindin", "with": None, "featuring": "Drake", "credit": None},
            {"title": "Sucker For Pain", "with": "Wiz Khalifa & Imagine Dragons feat. Logic",
             "featuring": None, "credit": None},
            {"title": "T-Wayne", "with": None, "featuring": None, "credit": "Lil Wayne & T-Pain"},
            {"title": "Senile", "with": "Young Money", "featuring": None, "credit": None},
        ])

    def test_compare(self):
        titles = {"grindin": [("Grindin’", "single", "official")],
                  "dedication 6 reloaded": [("Dedication 6: Reloaded", "mixtape", "official")]}
        entries = [{"title": "Grindin"}, {"title": "Dedication 6"}, {"title": "Glory"},
                   {"title": "D6: Reloaded"}]
        found, missing = compare(entries, titles)
        self.assertEqual([e["title"] for e, _ in found], ["Grindin", "Dedication 6", "D6: Reloaded"])
        self.assertEqual([e["title"] for e, _ in missing], ["Glory"])


if __name__ == "__main__":
    unittest.main()
