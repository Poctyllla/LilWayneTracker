import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from library_scanner import clean_track_title, number, parse_folder_name


class FolderNameTests(unittest.TestCase):
    def test_folder_names(self):
        cases = {
            "Lil Wayne-Tha.Block.Is.Hot.Retail-(1999)-[NoFS]": ("Tha Block Is Hot", 1999, "retail", None),
            "Lil.Wayne-The.Leak.4.(Bootleg)-2008-[NoFS]": ("The Leak 4", 2008, "bootleg", None),
            "Lil.Wayne-Tha.Fix-(2006)-(Unreleased)-[NoFS]": ("Tha Fix", 2006, "unreleased", None),
            "Lil.Wayne-Da.Drought.3-CD.2-(Official.mixtape)-2007-[NoFS]": ("Da Drought 3", 2007, "official_mixtape", 2),
            "Lil.Wayne-SQ6.(Collectors.Edition)-2003-[NoFS]": ("SQ6", 2003, "official_mixtape", None),
            "Birdman.And.Lil Wayne-Like.Father.Like.Son.Retail-(2006-Bonus.CD)-[NoFS]":
                ("Like Father Like Son", 2006, "retail", 2),
            "Lil.Wayne-Rebirth-Retail.Deluxe.Edition)-2010-[NoFS]": ("Rebirth", 2010, "retail", None),
            "Lil.Wayne.And.Drake.The.Carter.Meets.The.Cartel.II-(Bootleg)-2009-[NoFS]":
                ("The Carter Meets The Cartel II", 2009, "bootleg", None),
            "Lil.Wayne.&.The.Game.4.Blood.Sake-(Bootleg)-2008-[NoFS]": ("4 Blood Sake", 2008, "bootleg", None),
            "Lil.Wayne.Ringtones.VR.7-[NoFS]": ("Ringtones VR 7", None, "other", None),
        }
        for name, (title, year, kind, disc) in cases.items():
            with self.subTest(name):
                parsed = parse_folder_name(name)
                self.assertEqual((parsed["title"], parsed["year"], parsed["kind"], parsed["disc"]),
                                 (title, year, kind, disc))


class TrackTitleTests(unittest.TestCase):
    def test_clean_titles(self):
        self.assertEqual(clean_track_title("Lil Wayne feat. Tyga & Gata - Exquisite", "Lil Wayne"),
                         ("Exquisite", "Tyga & Gata"))
        self.assertEqual(clean_track_title("Lil Wayne - Pimpin'", "Lil Wayne"), ("Pimpin'", None))
        self.assertEqual(clean_track_title("Intro (feat. Big Tymers)", "Lil Wayne"), ("Intro", "Big Tymers"))
        self.assertEqual(clean_track_title("Flashy And Fly (Feat Nikki)", "Lil Wayne"), ("Flashy And Fly", "Nikki"))
        self.assertEqual(clean_track_title("Mr. Carter (Alternate Version)", "Lil Wayne"),
                         ("Mr. Carter (Alternate Version)", None))

    def test_dash_without_spaces(self):
        self.assertEqual(clean_track_title("The Game Ft. Cam'ron & Glasses Malone-Where It At", "Lil Wanye & The Game"),
                         ("Where It At", "Cam'ron & Glasses Malone"))
        self.assertEqual(clean_track_title("Lil Wayne Ft. The Game-My Life-[NoFS]", "x"),
                         ("My Life", "The Game"))
        self.assertEqual(clean_track_title("Lil Wayne Ft. Nutt Da Kid- EXclusive (HOT!!!)", "Lil.Wayne"),
                         ("EXclusive (HOT!!!)", "Nutt Da Kid"))
        self.assertEqual(clean_track_title("Lil Wayne Ft. Bobby Valentino And Jers -Your Smile (Remix)", "Lil Wayne"),
                         ("Your Smile (Remix)", "Bobby Valentino And Jers"))

    def test_bootleg_tag_variants(self):
        self.assertEqual(clean_track_title("Nothing Can Stop Me - Lil' Wayne", "Lil Wayne"),
                         ("Nothing Can Stop Me", None))
        self.assertEqual(clean_track_title("Jay-Z, Kanye West - Swagga Like Us", "Lil Wayne", with_artist=True),
                         ("Swagga Like Us", None, "Jay-Z, Kanye West"))
        self.assertEqual(clean_track_title("T Pain - Fire", "Lil Wayne", with_artist=True),
                         ("Fire", None, "T Pain"))
        self.assertEqual(clean_track_title("Lil Wayne - Ft. Nutt Da Kid-Datz My Nigga (HOT!!!)", "Lil Wayne"),
                         ("Datz My Nigga (HOT!!!)", "Nutt Da Kid"))
        self.assertEqual(clean_track_title("Interlude - Lil' Wayne Speaks On Ladies", "Lil Wayne"),
                         ("Interlude", None))
        self.assertEqual(clean_track_title("Lil Wayne Ft. Nas - Rich Boy & John Legend - Ghetto Rich (Remix)",
                                           "Lil.Wayne DJ Whiteowl", with_artist=True),
                         ("Ghetto Rich (Remix)", "Nas", "Rich Boy & John Legend"))
        self.assertEqual(clean_track_title("Lil Wayne Ft. T Pain \x96 Live (BONUS)", "DJ Testarosa"),
                         ("Live (BONUS)", "T Pain"))
        self.assertEqual(clean_track_title("Winding On Me Ft.Fat Joe & Ron Browz", "Lil Wayne"),
                         ("Winding On Me", "Fat Joe & Ron Browz"))

    def test_titles_with_dashes_stay_whole(self):
        self.assertEqual(clean_track_title("G-Code", "Lil Wayne"), ("G-Code", None))
        self.assertEqual(clean_track_title("Lil Wayne - G-Code", "Lil Wayne"), ("G-Code", None))
        self.assertEqual(clean_track_title("Lil Wayne - I", "Lil Wayne"), ("I", None))
        self.assertEqual(clean_track_title("LIL WAYNE FT JUNIOR REID FREESTYLES", "Lil Wayne"),
                         ("LIL WAYNE FT JUNIOR REID FREESTYLES", None))

    def test_number(self):
        self.assertEqual((number("2/17"), number("01/22"), number(None), number("x")), (2, 1, None, None))


if __name__ == "__main__":
    unittest.main()
