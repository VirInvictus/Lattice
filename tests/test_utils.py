import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import lattice.config as config
import lattice.utils as utils
from lattice.utils import (
    normalize_rating,
    format_rating,
    clean_song_name,
    parse_layout,
    relpath_under,
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
        # A root-level file fills no slots at all: a present-but-empty artist
        # would defeat callers' .get("artist", "Unknown Artist") fallbacks.
        got = parse_layout("song.mp3", "{artist}/{album}")
        self.assertEqual(got, {})


class RelpathUnderTests(unittest.TestCase):
    def test_single_root(self):
        self.assertEqual(relpath_under("/lib/Artist/Album", "/lib"), "Artist/Album")

    def test_filesystem_root(self):
        # A root of "/" already ends with the separator; the prefix must not
        # become "//" (which no ordinary path starts with).
        self.assertEqual(relpath_under("/lib/Artist", "/"), "lib/Artist")

    def test_unowned_path_returned_verbatim(self):
        self.assertEqual(relpath_under("/other/x", "/lib"), "/other/x")


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


class TagWorkersTests(unittest.TestCase):
    """Tag reads are serial by default (mutagen parses under the GIL, so a pool
    measured slower on local storage) but stay tunable for volumes where an
    open really blocks."""

    def setUp(self):
        self._saved = os.environ.pop("LATTICE_TAG_WORKERS", None)

    def tearDown(self):
        os.environ.pop("LATTICE_TAG_WORKERS", None)
        if self._saved is not None:
            os.environ["LATTICE_TAG_WORKERS"] = self._saved

    def test_default_is_serial(self):
        with mock.patch.object(config, "load_config", return_value={}):
            self.assertEqual(config.get_tag_workers(), 1)

    def test_env_overrides_config(self):
        os.environ["LATTICE_TAG_WORKERS"] = "6"
        with mock.patch.object(config, "load_config", return_value={"tag_workers": 2}):
            self.assertEqual(config.get_tag_workers(), 6)

    def test_config_key_used_when_no_env(self):
        with mock.patch.object(config, "load_config", return_value={"tag_workers": 4}):
            self.assertEqual(config.get_tag_workers(), 4)

    def test_auto_scales_with_cpus_and_caps(self):
        os.environ["LATTICE_TAG_WORKERS"] = "auto"
        with mock.patch.object(config.os, "cpu_count", return_value=2):
            self.assertEqual(config.get_tag_workers(), 4)
        with mock.patch.object(config.os, "cpu_count", return_value=64):
            self.assertEqual(config.get_tag_workers(), 16)

    def test_garbage_falls_back_to_default(self):
        os.environ["LATTICE_TAG_WORKERS"] = "not-a-number"
        with mock.patch.object(config, "load_config", return_value={}):
            self.assertEqual(config.get_tag_workers(), 1)

    def test_zero_and_negative_mean_serial(self):
        with mock.patch.object(config, "load_config", return_value={}):
            for v in ("0", "-4"):
                os.environ["LATTICE_TAG_WORKERS"] = v
                self.assertEqual(config.get_tag_workers(), 1)

    def test_map_concurrent_is_correct_either_way(self):
        paths = [f"p{i}" for i in range(20)]
        expected = {p: p.upper() for p in paths}
        for workers in (1, 8):
            self.assertEqual(
                utils.map_concurrent(str.upper, paths, workers=workers), expected
            )


class FindCoverFileTests(unittest.TestCase):
    """_has_cover_file is the boolean form of _find_cover_file; the art-quality
    audit needs the path, so the COVER_NAMES lookup lives in one place."""

    def _dir(self, td, *names):
        for n in names:
            (Path(td) / n).write_bytes(b"")
        return td

    def test_matches_case_insensitively(self):
        with tempfile.TemporaryDirectory() as td:
            self._dir(td, "Folder.JPG")
            self.assertEqual(utils._find_cover_file(td), os.path.join(td, "Folder.JPG"))
            self.assertTrue(utils._has_cover_file(td))

    def test_none_when_no_cover(self):
        with tempfile.TemporaryDirectory() as td:
            self._dir(td, "01.mp3", "notes.txt")
            self.assertIsNone(utils._find_cover_file(td))
            self.assertFalse(utils._has_cover_file(td))

    def test_pick_is_deterministic_across_several_covers(self):
        with tempfile.TemporaryDirectory() as td:
            self._dir(td, "front.png", "cover.jpg", "album.jpeg")
            # Sorted, so a directory carrying more than one recognized name
            # does not depend on readdir order.
            self.assertEqual(os.path.basename(utils._find_cover_file(td)), "album.jpeg")

    def test_unreadable_directory_is_none(self):
        self.assertIsNone(utils._find_cover_file("/nonexistent-lattice-dir"))
        self.assertFalse(utils._has_cover_file("/nonexistent-lattice-dir"))


if __name__ == "__main__":
    unittest.main()
