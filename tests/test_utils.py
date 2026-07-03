import unittest

import lattice.utils as utils
from lattice.utils import (
    normalize_rating,
    format_rating,
    clean_song_name,
    parse_layout,
    _looks_numeric,
    color,
    green,
)


class NormalizeRatingTests(unittest.TestCase):
    def test_zero_to_five_scale_passes_through(self):
        self.assertEqual(normalize_rating(0), 0.0)
        self.assertEqual(normalize_rating(3), 3.0)
        self.assertEqual(normalize_rating(5), 5.0)

    def test_ten_scale_halves(self):
        self.assertEqual(normalize_rating(10), 5.0)
        self.assertEqual(normalize_rating(8), 4.0)

    def test_hundred_scale(self):
        self.assertEqual(normalize_rating(100), 5.0)
        self.assertEqual(normalize_rating(50), 2.5)

    def test_two_fifty_five_scale(self):
        self.assertEqual(normalize_rating(255), 5.0)
        r = normalize_rating(196)
        assert r is not None
        self.assertAlmostEqual(r, 196 / 255 * 5.0)

    def test_out_of_range_and_garbage(self):
        self.assertIsNone(normalize_rating(300))
        self.assertIsNone(normalize_rating("abc"))
        self.assertIsNone(normalize_rating(None))

    def test_numeric_string(self):
        self.assertEqual(normalize_rating("4"), 4.0)


class FormatRatingTests(unittest.TestCase):
    def test_none_is_empty(self):
        self.assertEqual(format_rating(None), "")

    def test_full_five(self):
        self.assertEqual(format_rating(5.0), " [★★★★★ 5.0/5]")

    def test_half_star(self):
        self.assertEqual(format_rating(4.8), " [★★★★☆ 4.8/5]")

    def test_three(self):
        self.assertEqual(format_rating(3.0), " [★★★☆☆ 3.0/5]")

    def test_zero(self):
        self.assertEqual(format_rating(0.0), " [☆☆☆☆☆ 0.0/5]")


class CleanSongNameTests(unittest.TestCase):
    def test_dash_separated_track(self):
        self.assertEqual(clean_song_name("01 - Song A.flac"), "01. Song A")

    def test_space_separated_track(self):
        self.assertEqual(clean_song_name("02 Track.mp3"), "02. Track")

    def test_track_keyword(self):
        self.assertEqual(clean_song_name("Track 5 - Hello.flac"), "05. Hello")

    def test_artist_dash_prefix_stripped(self):
        self.assertEqual(clean_song_name("Artist - Song.mp3"), "Song")

    def test_plain_title_untouched(self):
        self.assertEqual(clean_song_name("Just A Title.flac"), "Just A Title")


class ParseLayoutTests(unittest.TestCase):
    def test_artist_album(self):
        got = parse_layout("Artist/Album/01.flac", "{artist}/{album}")
        self.assertEqual(got, {"artist": "Artist", "album": "Album"})

    def test_deeper_tree_takes_leading_components(self):
        got = parse_layout("X/Y/Z/t.mp3", "{artist}/{album}")
        self.assertEqual(got, {"artist": "X", "album": "Y"})

    def test_flat_file_partial(self):
        got = parse_layout("song.mp3", "{artist}/{album}")
        self.assertEqual(got.get("artist"), "")
        self.assertNotIn("album", got)


class LooksNumericTests(unittest.TestCase):
    def test_truthy(self):
        self.assertTrue(_looks_numeric("5"))
        self.assertTrue(_looks_numeric("4.5"))

    def test_falsy(self):
        self.assertFalse(_looks_numeric(""))
        self.assertFalse(_looks_numeric(None))
        self.assertFalse(_looks_numeric("abc"))
        self.assertFalse(_looks_numeric("3stars"))


class ColorTests(unittest.TestCase):
    def test_plain_when_not_a_tty(self):
        # The test runner's stdout is not a tty, so output stays uncolored,
        # which keeps report files and pipes clean.
        orig = utils._use_color
        utils._use_color = lambda: False
        try:
            self.assertEqual(color("x", "32"), "x")
            self.assertEqual(green("ok"), "ok")
        finally:
            utils._use_color = orig

    def test_codes_when_enabled(self):
        orig = utils._use_color
        utils._use_color = lambda: True
        try:
            self.assertEqual(utils.color("x", "32"), "\033[32mx\033[0m")
            self.assertEqual(utils.green("ok"), "\033[32mok\033[0m")
            self.assertEqual(utils.red("bad"), "\033[31mbad\033[0m")
            self.assertEqual(utils.yellow("warn"), "\033[33mwarn\033[0m")
        finally:
            utils._use_color = orig


class TUIPbarSharedScreenTests(unittest.TestCase):
    """T7: with a session screen published, the TUI progress bar draws into
    it — no initscr() of its own, and close() leaves the session's screen
    alone instead of endwin()'ing the whole terminal state."""

    def test_draws_into_shared_screen_and_close_leaves_it(self):
        from unittest import mock

        scr = mock.Mock()
        scr.getmaxyx.return_value = (24, 80)
        utils.set_shared_screen(scr)
        try:
            with (
                mock.patch("curses.initscr") as initscr,
                mock.patch("curses.endwin") as endwin,
                mock.patch("curses.color_pair", return_value=0),
            ):
                bar = utils._TUIPbar(10, "Scanning")
                bar.update(10)
                bar.close()
            initscr.assert_not_called()
            endwin.assert_not_called()
            self.assertTrue(scr.erase.called)
            self.assertTrue(scr.refresh.called)
        finally:
            utils.set_shared_screen(None)


class ResetTerminalSessionGuardTests(unittest.TestCase):
    """With a session screen published, _reset_terminal must not run stty
    sane: it would re-enable echo/canonical mode under the live curses screen
    and break every later getch() (found via the submenu's reset call)."""

    def test_no_stty_while_session_owns_the_terminal(self):
        from unittest import mock

        utils.set_shared_screen(mock.Mock())
        try:
            with mock.patch.object(utils.subprocess, "run") as run:
                utils._reset_terminal()
            run.assert_not_called()
        finally:
            utils.set_shared_screen(None)

    def test_stty_runs_again_once_session_ends(self):
        from unittest import mock

        utils.set_shared_screen(None)
        with (
            mock.patch.object(utils.subprocess, "run") as run,
            mock.patch.object(utils.sys.stdin, "isatty", return_value=True),
        ):
            utils._reset_terminal()
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
