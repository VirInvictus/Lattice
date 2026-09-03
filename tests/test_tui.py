"""Tests for interactive_menu's wiring through vir-tui 2.2.0's
interactive_session(): utils.IN_TUI selects the vir_tui progress box over
captured tqdm output, and the session screen lifecycle is vir-tui's."""

import unittest
from unittest import mock

from lattice import tui, utils


class _FakeSession:
    """Stands in for vir_tui.interactive_session in wiring tests."""

    def __init__(self, screen):
        self.screen = screen

    def __enter__(self):
        return self.screen

    def __exit__(self, *exc):
        return False


class InteractiveMenuWiringTests(unittest.TestCase):
    def tearDown(self):
        utils.IN_TUI = False

    def _run_menu(self, screen):
        seen = {}

        def body():
            seen["in_tui"] = utils.IN_TUI
            return 0

        with (
            mock.patch.object(
                tui, "interactive_session", return_value=_FakeSession(screen)
            ),
            mock.patch.object(tui, "_menu_session", side_effect=body),
        ):
            rc = tui.interactive_menu()
        return rc, seen

    def test_curses_session_selects_tui_progress_semantics(self):
        sentinel = object()
        rc, seen = self._run_menu(sentinel)
        self.assertEqual(rc, 0)
        self.assertTrue(seen["in_tui"])
        self.assertFalse(utils.IN_TUI)

    def test_no_curses_keeps_cli_progress_semantics(self):
        _rc, seen = self._run_menu(None)
        self.assertFalse(seen["in_tui"])
        self.assertFalse(utils.IN_TUI)


if __name__ == "__main__":
    unittest.main()
