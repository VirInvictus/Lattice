import os
import sys
import unittest
from collections import Counter, namedtuple

# genre_tidy.py lives in scripts/ (outside the lattice package); add it to the
# path so its pure helpers can be imported and unit-tested. The lattice scan and
# the retag.py subprocess are not exercised here (mirrors the integrity modes:
# the shell-out is untested, its decision logic is).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import genre_tidy as gt  # noqa: E402

FakeAD = namedtuple("FakeAD", "artist genre")


class NormTests(unittest.TestCase):
    def test_case_and_whitespace(self):
        self.assertEqual(gt.norm("  The  XX "), "the xx")

    def test_curly_quote_and_dash_fold(self):
        self.assertEqual(gt.norm("Jay‐Z"), gt.norm("Jay-Z"))
        self.assertEqual(gt.norm("Director’s Cut"), gt.norm("Director's Cut"))

    def test_empty_and_none(self):
        self.assertEqual(gt.norm(""), "")
        self.assertEqual(gt.norm(None), "")


class CanonicalGenreTests(unittest.TestCase):
    def test_most_common_wins(self):
        c = Counter({"Trap": 1})
        c.update(["Gangsta Rap"] * 3)
        self.assertEqual(gt.canonical_genre(c), "Gangsta Rap")

    def test_tie_breaks_to_first_inserted(self):
        c = Counter()
        c["Emo"] = 2
        c["Orgcore"] = 2
        self.assertEqual(gt.canonical_genre(c), "Emo")

    def test_empty(self):
        self.assertEqual(gt.canonical_genre(Counter()), "")


class ParseAllowedTests(unittest.TestCase):
    def test_split_and_strip(self):
        self.assertEqual(
            gt.parse_allowed("Conscious Hip Hop;  Jazz Rap "),
            ["Conscious Hip Hop", "Jazz Rap"],
        )

    def test_drops_empties(self):
        self.assertEqual(gt.parse_allowed("Trap; ; "), ["Trap"])


class ParseMapTests(unittest.TestCase):
    def test_skips_comments_and_blanks(self):
        entries = gt.parse_map(["# header", "", "  ", "Eminem\tHardcore Hip Hop"])
        self.assertEqual(set(entries), {gt.norm("Eminem")})

    def test_canonical_is_first_allowed(self):
        entries = gt.parse_map(["Kendrick Lamar\tConscious Hip Hop; Jazz Rap"])
        e = entries[gt.norm("Kendrick Lamar")]
        self.assertEqual(e.canonical, "Conscious Hip Hop")
        self.assertEqual(
            e.allowed_norm,
            frozenset({gt.norm("Conscious Hip Hop"), gt.norm("Jazz Rap")}),
        )

    def test_blanked_genre_means_skip(self):
        entries = gt.parse_map(["Aphex Twin\t"])
        e = entries[gt.norm("Aphex Twin")]
        self.assertEqual(e.canonical, "")
        self.assertEqual(e.allowed_norm, frozenset())

    def test_artist_only_line_no_tab(self):
        entries = gt.parse_map(["Boards of Canada"])
        e = entries[gt.norm("Boards of Canada")]
        self.assertEqual(e.canonical, "")


class IsCompliantTests(unittest.TestCase):
    def setUp(self):
        self.allowed = frozenset({gt.norm("Conscious Hip Hop"), gt.norm("Jazz Rap")})

    def test_in_set_normalized(self):
        self.assertTrue(gt.is_compliant("conscious hip hop", self.allowed))
        self.assertTrue(gt.is_compliant("Jazz Rap", self.allowed))

    def test_outside_set(self):
        self.assertFalse(gt.is_compliant("Pop", self.allowed))

    def test_empty_genre_never_compliant(self):
        self.assertFalse(gt.is_compliant("", self.allowed))
        self.assertFalse(gt.is_compliant(None, self.allowed))


class RetagArgvTests(unittest.TestCase):
    def setUp(self):
        self.retag = gt.Path("/x/scripts/retag.py")

    def test_single_genre(self):
        argv = gt.retag_argv(self.retag, "/m/A/Album", "Trap", dry_run=False)
        self.assertEqual(argv[2:], ["/m/A/Album", "Trap"])
        self.assertEqual(argv[0], sys.executable)

    def test_slash_canonical_splits_into_separate_args(self):
        argv = gt.retag_argv(self.retag, "/m/A/Album", "Emo / Orgcore", dry_run=False)
        self.assertEqual(argv[3:], ["Emo", "Orgcore"])

    def test_dry_run_appended(self):
        argv = gt.retag_argv(self.retag, "/m/A/Album", "Trap", dry_run=True)
        self.assertEqual(argv[-1], "--dry-run")


class ReduceArtistsTests(unittest.TestCase):
    def test_weights_genres_by_album_and_picks_display(self):
        dirs = [
            FakeAD("Freddie Gibbs", "Gangsta Rap"),
            FakeAD("Freddie Gibbs", "Gangsta Rap"),
            FakeAD("Freddie Gibbs", "Hardcore Hip Hop"),
        ]
        reduced = gt.reduce_artists(dirs)
        display, genres = reduced[gt.norm("Freddie Gibbs")]
        self.assertEqual(display, "Freddie Gibbs")
        self.assertEqual(genres["Gangsta Rap"], 2)
        self.assertEqual(genres["Hardcore Hip Hop"], 1)

    def test_spelling_variants_collapse_under_norm(self):
        dirs = [FakeAD("Jay-Z", "East Coast Rap"), FakeAD("Jay‐Z", "Mafioso Rap")]
        reduced = gt.reduce_artists(dirs)
        self.assertEqual(len(reduced), 1)

    def test_skips_empty_artist(self):
        self.assertEqual(gt.reduce_artists([FakeAD("", "Trap")]), {})


class BuildRowsTests(unittest.TestCase):
    def test_single_genre_no_comment(self):
        reduced = gt.reduce_artists([FakeAD("AFI", "Post-Hardcore")])
        rows = gt.build_rows(reduced)
        self.assertEqual(rows, ["AFI\tPost-Hardcore"])

    def test_multi_genre_gets_review_comment(self):
        reduced = gt.reduce_artists(
            [FakeAD("Kendrick Lamar", "Conscious Hip Hop")] * 7
            + [FakeAD("Kendrick Lamar", "Jazz Rap")] * 2
        )
        rows = gt.build_rows(reduced)
        self.assertTrue(rows[0].startswith("# Kendrick Lamar:"))
        self.assertEqual(rows[1], "Kendrick Lamar\tConscious Hip Hop")

    def test_no_genre_artist_flagged(self):
        reduced = gt.reduce_artists([FakeAD("Mystery Act", "")])
        rows = gt.build_rows(reduced)
        self.assertIn("no genre tags found", rows[0])
        self.assertEqual(rows[1], "Mystery Act\t")

    def test_round_trips_through_parse_map(self):
        reduced = gt.reduce_artists(
            [FakeAD("Aesop Rock", "Abstract Hip Hop")] * 3
            + [FakeAD("Deftones", "Nu Metal"), FakeAD("Deftones", "Alternative Rock")]
        )
        entries = gt.parse_map(gt.build_rows(reduced))
        self.assertEqual(entries[gt.norm("Aesop Rock")].canonical, "Abstract Hip Hop")
        # Deftones tie (1 each): canonical is whichever the Counter saw first.
        self.assertEqual(entries[gt.norm("Deftones")].canonical, "Nu Metal")


if __name__ == "__main__":
    unittest.main()
