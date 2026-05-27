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
        changes = rerate.rerate_file(self.path)
        self.assertEqual(changes, [("Windows Media Player 9 Series", 127, 64)])
        self.assertEqual(self._popm()["Windows Media Player 9 Series"], 64)

    def test_remaps_deadbeef_4star(self):
        self._set_popm("Windows Media Player 9 Series", 254)
        rerate.rerate_file(self.path)
        self.assertEqual(self._popm()["Windows Media Player 9 Series"], 196)

    def test_canonical_byte_left_alone(self):
        self._set_popm("Windows Media Player 9 Series", 196)
        self.assertEqual(rerate.rerate_file(self.path), [])
        self.assertEqual(self._popm()["Windows Media Player 9 Series"], 196)

    def test_idempotent(self):
        self._set_popm("Windows Media Player 9 Series", 254)
        rerate.rerate_file(self.path)
        self.assertEqual(rerate.rerate_file(self.path), [])
        self.assertEqual(self._popm()["Windows Media Player 9 Series"], 196)

    def test_only_remappable_frame_touched(self):
        # A MusicBee 4-star (186, already reads right) alongside a DeaDBeeF 254.
        self._set_popm("Windows Media Player 9 Series", 254)
        self._set_popm("MusicBee", 186)
        rerate.rerate_file(self.path)
        popm = self._popm()
        self.assertEqual(popm["Windows Media Player 9 Series"], 196)
        self.assertEqual(popm["MusicBee"], 186)


if __name__ == "__main__":
    unittest.main()
