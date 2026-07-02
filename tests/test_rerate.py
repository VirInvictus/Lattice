import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# rerate.py lives in scripts/ (outside the lattice package); it rewrites POPM
# rating bytes, so it is exercised against a copy of the committed fixture MP3.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import rerate  # noqa: E402
from mutagen.id3 import ID3, POPM, ID3NoHeaderError  # noqa: E402

MP3_SRC = (
    Path(__file__).parent
    / "fixtures"
    / "library"
    / "Cursive"
    / "Domestica"
    / "01 - The Casualty.mp3"
)


class RemapPopmTests(unittest.TestCase):
    def test_deadbeef_bytes_remap(self):
        self.assertEqual(rerate.remap_popm(127), 64)
        self.assertEqual(rerate.remap_popm(254), 196)

    def test_canonical_and_other_bytes_untouched(self):
        for b in (0, 64, 128, 196, 255, 100, 186, 242):
            self.assertIsNone(rerate.remap_popm(b))


class RerateFileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "track.mp3")
        shutil.copy(MP3_SRC, self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def _set_popm(self, email, rating):
        try:
            tags = ID3(self.path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.add(POPM(email=email, rating=rating, count=0))
        tags.save(self.path, v2_version=3)

    def _popm(self):
        return {
            getattr(p, "email", ""): p.rating for p in ID3(self.path).getall("POPM")
        }

    def test_remaps_deadbeef_2star(self):
        self._set_popm("Windows Media Player 9 Series", 127)
        changes, error = rerate.rerate_file(self.path)
        self.assertIsNone(error)
        self.assertEqual(changes, [("Windows Media Player 9 Series", 127, 64)])
        self.assertEqual(self._popm()["Windows Media Player 9 Series"], 64)

    def test_remaps_deadbeef_4star(self):
        self._set_popm("Windows Media Player 9 Series", 254)
        rerate.rerate_file(self.path)
        self.assertEqual(self._popm()["Windows Media Player 9 Series"], 196)

    def test_canonical_byte_left_alone(self):
        self._set_popm("Windows Media Player 9 Series", 196)
        self.assertEqual(rerate.rerate_file(self.path), ([], None))
        self.assertEqual(self._popm()["Windows Media Player 9 Series"], 196)

    def test_idempotent(self):
        self._set_popm("Windows Media Player 9 Series", 254)
        rerate.rerate_file(self.path)
        self.assertEqual(rerate.rerate_file(self.path), ([], None))
        self.assertEqual(self._popm()["Windows Media Player 9 Series"], 196)

    def test_readonly_file_reports_error_and_leaves_byte(self):
        # M10: a failed save must come back as a per-file error result, not an
        # exception out of a function documented "never raises".
        self._set_popm("Windows Media Player 9 Series", 127)
        os.chmod(self.path, 0o444)
        try:
            changes, error = rerate.rerate_file(self.path)
        finally:
            os.chmod(self.path, 0o644)
        self.assertEqual(changes, [])
        self.assertIn("save failed", error)
        self.assertEqual(self._popm()["Windows Media Player 9 Series"], 127)

    def test_only_remappable_frame_touched(self):
        # A MusicBee 4-star (186, already reads right) alongside a DeaDBeeF 254.
        self._set_popm("Windows Media Player 9 Series", 254)
        self._set_popm("MusicBee", 186)
        rerate.rerate_file(self.path)
        popm = self._popm()
        self.assertEqual(popm["Windows Media Player 9 Series"], 196)
        self.assertEqual(popm["MusicBee"], 186)


class MainTests(unittest.TestCase):
    """M10: one bad file must not kill the run; the summary reports the error
    count and the exit code is nonzero."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        for dirpath, _dirs, files in os.walk(self.root):
            for f in files:
                os.chmod(os.path.join(dirpath, f), 0o644)
        self._tmp.cleanup()

    def _mp3(self, name, rating):
        p = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        shutil.copy(MP3_SRC, p)
        tags = ID3(p)
        tags.add(POPM(email="Windows Media Player 9 Series", rating=rating, count=0))
        tags.save(p, v2_version=3)
        return p

    def _run_main(self, argv):
        import contextlib
        import io

        old = sys.argv
        sys.argv = ["rerate.py", *argv]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                rc = rerate.main()
        finally:
            sys.argv = old
        return rc, buf.getvalue()

    def test_error_is_counted_and_run_completes(self):
        good = self._mp3("a.mp3", 127)
        bad = self._mp3("b.mp3", 127)
        os.chmod(bad, 0o444)
        rc, out = self._run_main([self.root])
        self.assertEqual(rc, 1)
        self.assertIn("1 error(s)", out)
        self.assertEqual(ID3(good).getall("POPM")[0].rating, 64)  # still rerated
        log_text = Path(self.root, "rerate.log").read_text(encoding="utf-8")
        self.assertIn("ERR", log_text)
        self.assertIn("errors: 1", log_text)

    def test_clean_run_exits_zero(self):
        self._mp3("a.mp3", 254)
        rc, _out = self._run_main([self.root])
        self.assertEqual(rc, 0)

    def test_hidden_dirs_are_pruned(self):
        # RR3: .testing/ album copies (and any dot-dir) must not be touched.
        hidden = self._mp3(".testing/a.mp3", 127)
        self._mp3("Album/b.mp3", 127)
        rc, out = self._run_main([self.root])
        self.assertEqual(rc, 0)
        self.assertIn("Rerated 1 of 1 MP3 file(s).", out)
        self.assertEqual(ID3(hidden).getall("POPM")[0].rating, 127)  # untouched

    def test_unopenable_log_is_a_clean_error(self):
        self._mp3("a.mp3", 127)
        bad = os.path.join(self.root, "no-such-dir", "rerate.log")
        rc, out = self._run_main([self.root, "--log", bad])
        self.assertEqual(rc, 1)
        self.assertIn("cannot open log file", out)


if __name__ == "__main__":
    unittest.main()
