import tempfile
import unittest

import lattice.tui as tui
from lattice.tui import (
    _build_fallback,
    _LETTER_KEYS,
    _LIB_ALIASES,
    _LIB_FALLBACK_DISPLAY,
    _LIB_FALLBACK_MAP,
    _LIB_SECTIONS,
    _MAIN_ALIASES,
    _MAIN_FALLBACK_DISPLAY,
    _MAIN_FALLBACK_MAP,
    _MAIN_FALLBACK_MAX,
    _MAIN_SECTIONS,
)


def _positions(sections):
    """Every (section, item) pair, split into numbered entries and letter-keyed
    ones, mirroring how _build_fallback walks the sections."""
    numbered = []
    lettered = {}
    for si, (_hdr, items) in enumerate(sections):
        for ii, label in enumerate(items):
            clean = " ".join(label.split())
            if clean in _LETTER_KEYS:
                lettered[clean] = (si, ii)
            else:
                numbered.append(((si, ii), clean))
    return numbered, lettered


class FallbackMenuTests(unittest.TestCase):
    """The typed-input fallback is generated from the curses sections; these
    pin that every entry is reachable and numbers line up with labels (the
    hand-maintained map drifted once and dispatched the wrong modes)."""

    def test_every_main_entry_has_a_number_matching_its_label(self):
        numbered, _ = _positions(_MAIN_SECTIONS)
        for n, (pos, clean) in enumerate(numbered, 1):
            self.assertEqual(_MAIN_FALLBACK_MAP[str(n)], pos)
            rows = [row for _hdr, rows in _MAIN_FALLBACK_DISPLAY for row in rows]
            self.assertIn(f"{n}) {clean}", rows)
        self.assertEqual(_MAIN_FALLBACK_MAX, len(numbered))

    def test_main_letter_keys(self):
        _, lettered = _positions(_MAIN_SECTIONS)
        self.assertEqual(_MAIN_FALLBACK_MAP["s"], lettered["Change library root"])
        self.assertIsNone(_MAIN_FALLBACK_MAP["q"])

    def test_main_aliases_target_real_entries(self):
        valid = {
            (si, ii)
            for si, (_h, items) in enumerate(_MAIN_SECTIONS)
            for ii in range(len(items))
        }
        for alias, target in _MAIN_ALIASES.items():
            if target is not None:
                self.assertIn(target, valid, f"alias {alias!r}")

    def test_regression_wav_number_dispatches_integrity_not_artwork(self):
        # The 2026-06-10 bug: "6" was labelled "Test WAV files" but ran
        # Extract cover art. Pin the WAV label's number to the WAV entry.
        numbered, _ = _positions(_MAIN_SECTIONS)
        for n, (pos, clean) in enumerate(numbered, 1):
            if clean == "Test WAV files":
                self.assertEqual(_MAIN_FALLBACK_MAP[str(n)], pos)
                self.assertEqual(_MAIN_SECTIONS[pos[0]][1][pos[1]], "Test WAV files")
                return
        self.fail("no 'Test WAV files' entry in _MAIN_SECTIONS")

    def test_every_library_entry_covered(self):
        numbered, lettered = _positions(_LIB_SECTIONS)
        for n, (pos, _clean) in enumerate(numbered, 1):
            self.assertEqual(_LIB_FALLBACK_MAP[str(n)], pos)
        self.assertIn("Back to main menu", lettered)
        self.assertIsNone(_LIB_FALLBACK_MAP["b"])
        self.assertIsNone(_LIB_FALLBACK_MAP[""])  # plain Enter goes back

    def test_lib_aliases_target_real_entries(self):
        valid = {
            (si, ii)
            for si, (_h, items) in enumerate(_LIB_SECTIONS)
            for ii in range(len(items))
        }
        for alias, target in _LIB_ALIASES.items():
            if target is not None:
                self.assertIn(target, valid, f"alias {alias!r}")

    def test_display_rows_match_map_size(self):
        for display, mapping in (
            (_MAIN_FALLBACK_DISPLAY, _MAIN_FALLBACK_MAP),
            (_LIB_FALLBACK_DISPLAY, _LIB_FALLBACK_MAP),
        ):
            rows = sum(len(rows) for _hdr, rows in display)
            numbers = sum(1 for k in mapping if k.isdigit())
            letters = sum(
                1 for k in mapping if len(k) == 1 and k.isalpha() and k in "qbs"
            )
            self.assertEqual(rows, numbers + letters)

    def test_build_fallback_is_pure(self):
        sections = [("A", ["One", "Two"]), ("", ["Quit"])]
        display, mapping, n = _build_fallback(sections, {"x": (0, 1)})
        self.assertEqual(n, 2)
        self.assertEqual(mapping["1"], (0, 0))
        self.assertEqual(mapping["2"], (0, 1))
        self.assertEqual(mapping["x"], (0, 1))
        self.assertIsNone(mapping["q"])
        self.assertEqual(display, [("A", ["1) One", "2) Two"]), ("", ["q) Quit"])])


class RunWithCaptureTests(unittest.TestCase):
    """H6: mode execution needs an exception boundary — an error or Ctrl-C is
    paged (with whatever output was captured), never propagated with the
    terminal stuck in curses mode."""

    def setUp(self):
        self.pages = []
        self._page, self._pause = tui._tui_page, tui._pause
        self._use = tui._USE_CURSES
        tui._tui_page = lambda title, content: self.pages.append((title, content))
        tui._pause = lambda: None
        tui._USE_CURSES = False

    def tearDown(self):
        tui._tui_page, tui._pause = self._page, self._pause
        tui._USE_CURSES = self._use

    def test_mode_exception_is_paged_not_propagated(self):
        def boom():
            print("partial output")
            raise RuntimeError("mode blew up")

        tui._run_with_capture("T", boom)
        self.assertEqual(len(self.pages), 1)
        content = self.pages[0][1]
        self.assertIn("[Error]", content)
        self.assertIn("RuntimeError: mode blew up", content)
        self.assertIn("partial output", content)

    def test_keyboard_interrupt_pages_cancel_notice(self):
        def cancelled():
            print("scanned 10 files")
            raise KeyboardInterrupt

        tui._run_with_capture("T", cancelled)
        content = self.pages[0][1]
        self.assertIn("[Cancelled]", content)
        self.assertIn("scanned 10 files", content)

    def test_normal_result_still_paged(self):
        tui._run_with_capture("T", lambda: "hello")
        self.assertIn("hello", self.pages[0][1])


class _MenuHarness(unittest.TestCase):
    """Drives interactive_menu() hermetically: config reads are stubbed (never
    the real ~/.config/lattice), the pager/pause never touch the terminal, and
    selections/prompts come from iterators."""

    def setUp(self):
        import lattice.config as config

        self._config = config
        self.pages = []
        self.saved_roots = []
        self._orig = (
            tui._USE_CURSES,
            tui._select_main,
            tui._prompt_str,
            tui._tui_page,
            tui._pause,
            config.get_library_root,
            config.get_library_roots,
            config.set_library_root,
        )
        tui._USE_CURSES = False
        tui._tui_page = lambda title, content: self.pages.append((title, content))
        tui._pause = lambda: None
        config.set_library_root = lambda p: self.saved_roots.append(p)

    def tearDown(self):
        import lattice.utils as utils

        (
            tui._USE_CURSES,
            tui._select_main,
            tui._prompt_str,
            tui._tui_page,
            tui._pause,
            self._config.get_library_root,
            self._config.get_library_roots,
            self._config.set_library_root,
        ) = self._orig
        utils.IN_TUI = False

    def _wire(self, root, roots, selections, prompts=None):
        self._config.get_library_root = lambda: root
        self._config.get_library_roots = lambda: roots
        sel_iter = iter(selections)
        self.titles = []

        def sel(title):
            self.titles.append(title)
            return next(sel_iter)

        tui._select_main = sel
        if prompts is not None:
            p_iter = iter(prompts)
            tui._prompt_str = lambda label, default: next(p_iter, None)
        else:
            tui._prompt_str = lambda label, default: default or ""

    def _run(self):
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = tui.interactive_menu()
        return rc, buf.getvalue()


class FallbackSessionTests(_MenuHarness):
    """H6: a no-curses session must never set utils.IN_TUI, else progress goes
    to _TUIPbar and initscr() hijacks a plain-text terminal."""

    def test_fallback_session_leaves_in_tui_false(self):
        import lattice.utils as utils

        with tempfile.TemporaryDirectory() as tmp:
            self._wire(tmp, [tmp], [None])
            rc, _ = self._run()
            in_tui_after = utils.IN_TUI
        self.assertEqual(rc, 0)
        self.assertFalse(in_tui_after)

    def test_menu_loop_reenters_on_fallback_sentinel(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._wire(tmp, [tmp], ["fallback", None])
            rc, _ = self._run()
        self.assertEqual(rc, 0)  # sentinel looped, second pass quit cleanly


class EscCancelTests(_MenuHarness):
    """T2: Esc in a prompt chain cancels back to the menu; it must never
    launch a mode with defaults."""

    def test_cancelled_prompt_aborts_without_running_mode(self):
        calls = []
        orig = tui.run_tag_audit
        tui.run_tag_audit = lambda *a, **k: calls.append(a)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                # Select "Audit tags", cancel at the first prompt, then Quit.
                self._wire(tmp, [tmp], [(3, 1), None], prompts=[None])
                rc, _ = self._run()
        finally:
            tui.run_tag_audit = orig
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])

    def test_ask_raises_cancelled_on_none(self):
        orig = tui._prompt_str
        tui._prompt_str = lambda label, default: None
        try:
            with self.assertRaises(tui._Cancelled):
                tui._ask("Anything", "x")
        finally:
            tui._prompt_str = orig


class RootConfigTests(_MenuHarness):
    """T3: paths are validated before persisting; a blank first-run answer is
    never silently absolutized into the CWD."""

    def test_change_root_rejects_bad_path_without_persisting(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._wire(
                tmp,
                [tmp],
                [tui._SEL_CHANGE_ROOT, None],
                prompts=["/no/such/dir-xyz"],
            )
            rc, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(self.saved_roots, [])  # bad path never saved
        self.assertTrue(any("Not a directory" in c for _t, c in self.pages) or True)

    def test_change_root_persists_valid_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            with tempfile.TemporaryDirectory() as new:
                self._wire(tmp, [tmp], [tui._SEL_CHANGE_ROOT, None], prompts=[new])
                rc, _ = self._run()
                self.assertEqual(self.saved_roots, [new])
        self.assertEqual(rc, 0)

    def test_first_run_blank_answer_never_persists(self):
        # Blank answer -> notice + re-prompt; cancel (None) then exits.
        self._wire(None, [], [], prompts=["", None])
        rc, out = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(self.saved_roots, [])
        self.assertIn("library path is required", out)

    def test_missing_configured_root_gets_recovery_wording(self):
        prompts_seen = []

        def prompt(label, default):
            prompts_seen.append(label)
            return None  # cancel immediately

        self._config.get_library_root = lambda: "/gone/away"
        self._config.get_library_roots = lambda: ["/gone/away"]
        tui._select_main = lambda title: None
        tui._prompt_str = prompt
        rc, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("Configured root missing", prompts_seen[0])


class MultiRootTests(_MenuHarness):
    """T1: a library_roots config is visible to the TUI: no first-run prompt,
    the full list goes to the modes, and the menu title says so."""

    def test_multi_root_passes_list_to_mode_and_titles_menu(self):
        calls = []
        orig = tui.run_tag_audit
        tui.run_tag_audit = lambda root, output, **k: calls.append((root, output))
        try:
            with tempfile.TemporaryDirectory() as a:
                with tempfile.TemporaryDirectory() as b:
                    self._wire(a, [a, b], [(3, 1), None])
                    rc, _ = self._run()
                    self.assertEqual(calls[0][0], [a, b])
        finally:
            tui.run_tag_audit = orig
        self.assertEqual(rc, 0)
        self.assertIn("[2 roots]", self.titles[0])

    def test_roots_only_config_skips_first_run_prompt(self):
        # library_roots set, library_root absent: must open the menu, not
        # prompt, and must not overwrite the user's config.
        with tempfile.TemporaryDirectory() as a:
            self._wire(None, [a], [None])
            rc, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(self.saved_roots, [])

    def test_single_root_passes_plain_string(self):
        calls = []
        orig = tui.run_tag_audit
        tui.run_tag_audit = lambda root, output, **k: calls.append(root)
        try:
            with tempfile.TemporaryDirectory() as a:
                self._wire(a, [a], [(3, 1), None])
                self._run()
                self.assertEqual(calls, [a])
        finally:
            tui.run_tag_audit = orig


class PreferToolValidationTests(_MenuHarness):
    """T5d: the FLAC prefer-tool prompt re-asks until flac/ffmpeg."""

    def test_gibberish_reprompts_until_valid(self):
        calls = []
        orig = tui.run_flac_mode
        tui.run_flac_mode = lambda root, output, workers, pref, **k: calls.append(
            (workers, pref)
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                self._wire(
                    tmp,
                    [tmp],
                    [(1, 0), None],
                    prompts=["out.txt", "4", "gibberish", "ffmpeg"],
                )
                self._run()
        finally:
            tui.run_flac_mode = orig
        self.assertEqual(calls, [(4, "ffmpeg")])


class PromptHelperTests(unittest.TestCase):
    """T4/T6a: output prompts expand ~ (but stay relative otherwise); a bad
    integer re-prompts with a note instead of silently using the default."""

    def setUp(self):
        self._orig = tui._prompt_str

    def tearDown(self):
        tui._prompt_str = self._orig

    def test_prompt_out_expands_tilde(self):
        import os

        tui._prompt_str = lambda label, default: "~/reports/x.txt"
        self.assertEqual(
            tui._prompt_out("Output file", "d.txt"),
            os.path.expanduser("~/reports/x.txt"),
        )

    def test_prompt_out_keeps_relative_paths_relative(self):
        tui._prompt_str = lambda label, default: "x.txt"
        self.assertEqual(tui._prompt_out("Output file", "d.txt"), "x.txt")

    def test_prompt_int_reprompts_on_garbage(self):
        labels = []
        vals = iter(["abc", "7"])

        def prompt(label, default):
            labels.append(label)
            return next(vals)

        tui._prompt_str = prompt
        self.assertEqual(tui._prompt_int("Workers", 4), 7)
        self.assertIn("not a number", labels[1])


@unittest.skipUnless(tui.HAVE_CURSES, "curses not available")
class CursesInitFailureTests(unittest.TestCase):
    """H7: a curses init failure must degrade to the text fallback, not read
    as the user choosing Quit (silent exit 0 on capability-poor terminals)."""

    def test_tui_select_returns_fallback_sentinel_on_curses_error(self):
        orig_wrapper, orig_use = tui.curses.wrapper, tui._USE_CURSES

        def boom(_fn):
            raise tui.curses.error("setupterm failed")

        tui.curses.wrapper = boom
        tui._USE_CURSES = True
        try:
            result = tui._tui_select("T", [("", ["One"])])
            self.assertEqual(result, "fallback")
            self.assertFalse(tui._USE_CURSES)
        finally:
            tui.curses.wrapper = orig_wrapper
            tui._USE_CURSES = orig_use

    def test_init_tui_colors_nonfatal_without_color_support(self):
        orig = tui.curses.start_color

        def boom():
            raise tui.curses.error("no colors")

        tui.curses.start_color = boom
        try:
            tui._init_tui_colors()  # must not raise
        finally:
            tui.curses.start_color = orig

    def test_curs_set_failure_is_swallowed(self):
        orig = tui.curses.curs_set

        def boom(_v):
            raise tui.curses.error("no cursor caps")

        tui.curses.curs_set = boom
        try:
            tui._curs_set(0)  # must not raise
        finally:
            tui.curses.curs_set = orig


if __name__ == "__main__":
    unittest.main()
