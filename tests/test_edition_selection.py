import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from edition_selection import (
    DUPLICATE,
    MANUAL_REVIEW,
    NO_TRACKLIST,
    NOT_OFFICIAL,
    OUT_OF_SCOPE,
    SAME_RECORDINGS,
    SAME_TRACKLIST,
    SELECTED,
    normalized_title,
    select_editions,
)
from review_candidates import edition_sort_key


def edition(edition_id, date="2011-08-29", country="US", status="Official",
            title="Tha Carter IV", disambiguation=""):
    return {"id": edition_id, "title": title, "date": date, "country": country,
            "status": status, "disambiguation": disambiguation}


def tracks(*items):
    """items: (title, recording_id[, disc_number]); numbered per disc."""
    result, numbers = [], {}
    for item in items:
        title, recording_id, disc = (*item, 1)[:3]
        numbers[disc] = numbers.get(disc, 0) + 1
        result.append({"number": numbers[disc], "disc_number": disc, "title": title,
                       "musicbrainz_track_id": f"{recording_id}-{disc}-{numbers[disc]}-{title}",
                       "musicbrainz_recording_id": recording_id, "duration_ms": 1000,
                       "credits": []})
    return result


STANDARD = (("Intro", "r1"), ("Blunt Blowin", "r2"), ("6 Foot 7 Foot", "r3"))


def statuses(decisions):
    return {edition["id"]: (status, detail) for edition, status, detail in decisions}


class SelectionTests(unittest.TestCase):
    def test_canonical_is_earliest_standard_us_official(self):
        pairs = [
            (edition("uk", country="GB"), tracks(*STANDARD)),
            (edition("us-late", date="2011-09-01"), tracks(*STANDARD)),
            (edition("us", date="2011-08-29"), tracks(*STANDARD)),
        ]
        canonical, additional, decisions = select_editions(pairs, edition_sort_key)

        self.assertEqual(canonical[0]["id"], "us")
        self.assertEqual(additional, [])
        self.assertEqual(statuses(decisions)["uk"][0], SAME_TRACKLIST)
        self.assertEqual(statuses(decisions)["us-late"][0], SAME_TRACKLIST)

    def test_deluxe_with_bonus_tracks_is_additional(self):
        deluxe = tracks(*STANDARD, ("Mirror", "r4"), ("Novacane", "r5"))
        pairs = [
            (edition("us"), tracks(*STANDARD)),
            (edition("deluxe", disambiguation="deluxe edition"), deluxe),
            (edition("deluxe-uk", country="GB", disambiguation="deluxe edition"), deluxe),
        ]
        canonical, additional, decisions = select_editions(pairs, edition_sort_key)

        self.assertEqual([e["id"] for e, _ in additional], ["deluxe"])
        self.assertEqual(statuses(decisions)["deluxe"], (SELECTED, "additional: add 2"))
        self.assertEqual(statuses(decisions)["deluxe-uk"][0], DUPLICATE)

    def test_out_of_scope_and_unofficial_editions_are_skipped(self):
        pairs = [
            (edition("us"), tracks(*STANDARD)),
            (edition("clean", disambiguation="clean version"), tracks(*STANDARD)),
            (edition("promo", status="Promotion"), tracks(*STANDARD)),
            (edition("bootleg", status="Bootleg"), None),
            (edition("instr", title="Tha Carter IV (Instrumentals)"), tracks(*STANDARD)),
            (edition("bonus-instr"), tracks(*STANDARD, ("6 Foot 7 Foot (Instrumental)", "r9"))),
            (edition("no-tracks"), None),
        ]
        canonical, additional, decisions = select_editions(pairs, edition_sort_key)
        result = statuses(decisions)

        self.assertEqual(canonical[0]["id"], "us")
        self.assertEqual(additional, [])
        self.assertEqual(result["clean"][0], OUT_OF_SCOPE)
        self.assertEqual(result["instr"][0], OUT_OF_SCOPE)
        self.assertEqual(result["bonus-instr"][0], OUT_OF_SCOPE)
        self.assertEqual(result["promo"][0], NOT_OFFICIAL)
        self.assertEqual(result["bootleg"][0], NOT_OFFICIAL)
        self.assertEqual(result["no-tracks"][0], NO_TRACKLIST)

    def test_canonical_skips_an_out_of_scope_first_edition(self):
        pairs = [
            (edition("clean", date="2011-08-01", disambiguation="clean"), tracks(*STANDARD)),
            (edition("explicit"), tracks(*STANDARD)),
        ]
        canonical, _, _ = select_editions(pairs, edition_sort_key)
        self.assertEqual(canonical[0]["id"], "explicit")

    def test_same_songs_under_other_recording_ids_add_nothing(self):
        """Another pressing: same titles and durations, other Recording IDs."""
        duplicate_recordings = tracks(("Intro", "x1"), ("Blunt Blowin", "x2"), ("6 Foot 7 Foot", "x3"))
        reordered = tracks(("6 Foot 7 Foot", "r3"), ("Intro", "r1"), ("Blunt Blowin", "r2"))
        pairs = [
            (edition("us"), tracks(*STANDARD)),
            (edition("other-ids", country="DE"), duplicate_recordings),
            (edition("reordered", country="FR"), reordered),
        ]
        _, additional, decisions = select_editions(pairs, edition_sort_key)

        self.assertEqual(statuses(decisions)["other-ids"][0], SAME_RECORDINGS)
        # Same Recording IDs in another order add nothing to the catalogue.
        self.assertEqual(additional, [])
        self.assertEqual(statuses(decisions)["reordered"][0], SAME_RECORDINGS)

    def test_two_disc_layout_of_the_same_recordings_is_skipped(self):
        """Tha Carter V-like: a 2-CD pressing of the same 3 recordings."""
        two_discs = tracks(("Intro", "r1"), ("Blunt Blowin", "r2"), ("6 Foot 7 Foot", "r3", 2))
        pairs = [(edition("us"), tracks(*STANDARD)), (edition("2cd", country="XW"), two_discs)]
        _, additional, decisions = select_editions(pairs, edition_sort_key)
        self.assertEqual(additional, [])
        self.assertEqual(statuses(decisions)["2cd"][0], SAME_RECORDINGS)

    def test_reordered_deluxe_with_new_tracks_is_selected(self):
        """Funeral-like: deluxe puts new songs first; old recordings are reused."""
        deluxe = tracks(("New Song", "n1"), *STANDARD)
        pairs = [(edition("us"), tracks(*STANDARD)),
                 (edition("deluxe", disambiguation="deluxe"), deluxe)]
        _, additional, decisions = select_editions(pairs, edition_sort_key)
        self.assertEqual([e["id"] for e, _ in additional], ["deluxe"])
        self.assertEqual(statuses(decisions)["deluxe"], (SELECTED, "additional: add 1, replace 3"))

    def test_real_replacement_is_selected(self):
        pairs = [
            (edition("us"), tracks(*STANDARD)),
            (edition("new", date="2011-10-01"), tracks(("Intro", "r1"), ("Blunt Blowin", "r2"), ("Pussy Monster", "r9"))),
        ]
        _, additional, decisions = select_editions(pairs, edition_sort_key)
        self.assertEqual([e["id"] for e, _ in additional], ["new"])
        self.assertEqual(statuses(decisions)["new"], (SELECTED, "additional: replace 1"))

    def test_base_tracklist_becomes_canonical(self):
        """Rebirth-like: an earlier-dated edition has two extra tracks."""
        pairs = [
            (edition("bigger", date="2009"), tracks(*STANDARD, ("On Fire", "r7"), ("Ground Zero", "r8"))),
            (edition("standard", date="2010-02-02"), tracks(*STANDARD)),
        ]
        canonical, additional, decisions = select_editions(pairs, edition_sort_key)

        self.assertEqual(canonical[0]["id"], "standard")
        self.assertEqual([e["id"] for e, _ in additional], ["bigger"])
        self.assertEqual(statuses(decisions)["bigger"], (SELECTED, "additional: add 2"))

    def test_subset_edition_of_lower_rank_does_not_become_canonical(self):
        """I Am Not a Human Being II-like: a country-less edition is a subset."""
        pairs = [
            (edition("us"), tracks(*STANDARD, ("Bonus", "r9"))),
            (edition("unknown-country", country=None), tracks(*STANDARD)),
        ]
        canonical, _, _ = select_editions(pairs, edition_sort_key)
        self.assertEqual(canonical[0]["id"], "us")

    def test_worldwide_standard_beats_us_special_edition(self):
        """Tha Carter VI-like: the only US edition is a special edition."""
        pairs = [
            (edition("us-special", country="US", disambiguation="special edition, bonus tracks"),
             tracks(*STANDARD, ("Bonus", "r9"))),
            (edition("xw", country="XW", disambiguation="explicit"), tracks(*STANDARD)),
            (edition("other", country="DE", disambiguation="explicit"), tracks(*STANDARD)),
        ]
        canonical, additional, _ = select_editions(pairs, edition_sort_key)

        self.assertEqual(canonical[0]["id"], "xw")
        self.assertEqual([e["id"] for e, _ in additional], ["us-special"])

    def test_clean_edition_without_fetched_tracks_is_out_of_scope(self):
        pairs = [(edition("us"), tracks(*STANDARD)),
                 (edition("clean", disambiguation="clean"), None)]
        _, _, decisions = select_editions(pairs, edition_sort_key)
        self.assertEqual(statuses(decisions)["clean"][0], OUT_OF_SCOPE)

    def test_same_bonus_track_with_other_recording_id_in_two_editions(self):
        """Tha Carter IV-like: two deluxe editions add 'Novacane' with different IDs
        but the same duration; it is one song, so the third edition adds nothing."""
        pairs = [
            (edition("us"), tracks(*STANDARD)),
            (edition("deluxe-a", disambiguation="deluxe"), tracks(*STANDARD, ("Novacane", "n1"))),
            (edition("deluxe-b", disambiguation="deluxe", country="XW"),
             tracks(*STANDARD, ("Novacane", "n2"), ("Mirror", "m1"))),
            (edition("deluxe-c", disambiguation="deluxe", country="DE"),
             tracks(*STANDARD, ("Novacane", "n1"), ("Mirror", "m1"))),
        ]
        _, additional, decisions = select_editions(pairs, edition_sort_key)

        self.assertEqual([e["id"] for e, _ in additional], ["deluxe-a", "deluxe-b"])
        self.assertEqual(statuses(decisions)["deluxe-c"][0], SAME_RECORDINGS)

    def test_same_title_up_to_15_seconds_apart_is_the_same_song(self):
        longer = tracks(*STANDARD, ("Novacane", "n2"))
        longer[-1]["duration_ms"] = 1000 + 10000
        unknown = tracks(*STANDARD, ("Novacane", "n3"), ("Mirror", "m1"))
        unknown[-2]["duration_ms"] = None
        pairs = [
            (edition("us"), tracks(*STANDARD)),
            (edition("deluxe-a", disambiguation="deluxe"), tracks(*STANDARD, ("Novacane", "n1"))),
            (edition("deluxe-b", disambiguation="deluxe", country="XW"), longer),
            (edition("deluxe-c", disambiguation="deluxe", country="DE"), unknown),
        ]
        _, additional, decisions = select_editions(pairs, edition_sort_key)
        # deluxe-c: unknown duration, one 'Novacane' known -> the same song; it
        # adds 'Mirror', so it is a new edition.
        self.assertEqual([e["id"] for e, _ in additional], ["deluxe-a", "deluxe-c"])
        self.assertEqual(statuses(decisions)["deluxe-b"][0], SAME_RECORDINGS)

    def test_unknown_duration_with_several_candidates_needs_manual_review(self):
        album = tracks(("Grown Man", "i1"), ("Grown Man", "i2"), ("Song", "s1"))
        album[1]["duration_ms"] = 90000
        bonus = tracks(("Grown Man", "i1"), ("Grown Man", "i2"), ("Song", "s1"), ("Grown Man", "i3"))
        bonus[1]["duration_ms"] = 90000
        bonus[3]["duration_ms"] = None
        pairs = [(edition("us"), album), (edition("bonus", disambiguation="bonus"), bonus)]
        _, additional, decisions = select_editions(pairs, edition_sort_key)
        self.assertEqual(additional, [])
        self.assertEqual(statuses(decisions)["bonus"][0], MANUAL_REVIEW)

    def test_same_title_more_than_15_seconds_apart_is_another_song(self):
        other = tracks(*STANDARD, ("Intro", "i2"))
        other[-1]["duration_ms"] = 1000 + 60000
        pairs = [(edition("us"), tracks(*STANDARD)),
                 (edition("bonus", disambiguation="bonus"), other)]
        _, additional, _ = select_editions(pairs, edition_sort_key)
        self.assertEqual([e["id"] for e, _ in additional], ["bonus"])

    def test_ampersand_and_and_are_the_same_title(self):
        pairs = [
            (edition("us"), tracks(("Up Up and Away", "u1"), *STANDARD)),
            (edition("other", country="XW"), tracks(("Up Up & Away", "u2"), *STANDARD)),
        ]
        _, additional, decisions = select_editions(pairs, edition_sort_key)
        self.assertEqual(additional, [])
        self.assertEqual(statuses(decisions)["other"][0], SAME_RECORDINGS)

    def test_no_usable_edition(self):
        canonical, additional, _ = select_editions(
            [(edition("promo", status="Promotion"), tracks(*STANDARD))], edition_sort_key)
        self.assertIsNone(canonical)
        self.assertEqual(additional, [])

    def test_normalized_title(self):
        self.assertEqual(normalized_title("Mo’ Fire"), normalized_title("Mo' Fire"))
        self.assertEqual(normalized_title("Bring It Back (feat. Mannie Fresh)"),
                         normalized_title("Bring It Back"))
        self.assertNotEqual(normalized_title("Lollipop (Remix)"), normalized_title("Lollipop"))


if __name__ == "__main__":
    unittest.main()
