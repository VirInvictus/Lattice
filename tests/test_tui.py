"""Tests for interactive_menu's wiring of vir-tui's session screen into the
mode layer: utils.IN_TUI selects the curses progress bar over captured tqdm
output, and _TUIPbar draws into the published shared screen."""

import unittest
from unittest import mock

import lattice.tui as tui
from lattice import utils


class InteractiveMenuWiringTests(unittest.TestCase):
    def tearDown(self):
        utils.IN_TUI = False
        utils.set_shared_screen(None)

    def _run_menu(self, screen):
        seen = {}

        def body():
            seen["in_tui"] = utils.IN_TUI
            seen["screen"] = utils._SHARED_SCREEN
            return 0

        with (
            mock.patch.object(tui, "open_screen", return_value=screen),
            mock.patch.object(tui, "close_screen"),
            mock.patch.object(tui, "_menu_session", side_effect=body),
        ):
            rc = tui.interactive_menu()
        return rc, seen

    def test_curses_session_publishes_flags_and_restores_them(self):
        sentinel = object()
        rc, seen = self._run_menu(sentinel)
        self.assertEqual(rc, 0)
        self.assertTrue(seen["in_tui"])
        self.assertIs(seen["screen"], sentinel)
        self.assertFalse(utils.IN_TUI)
        self.assertIsNone(utils._SHARED_SCREEN)

    def test_no_curses_keeps_cli_progress_semantics(self):
        _rc, seen = self._run_menu(None)
        self.assertFalse(seen["in_tui"])
        self.assertIsNone(seen["screen"])
        self.assertFalse(utils.IN_TUI)
        self.assertIsNone(utils._SHARED_SCREEN)


if __name__ == "__main__":
    unittest.main()
