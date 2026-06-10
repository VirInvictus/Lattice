import unittest

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


if __name__ == "__main__":
    unittest.main()
