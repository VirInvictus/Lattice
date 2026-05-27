import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# retag.py lives in scripts/ (outside the lattice package). It writes tags, so
# it is exercised against copies of the committed fixture files. The .flac case
# covers the shared Vorbis branch (.flac/.ogg/.opus); the .mp3 case locks in the
# v4.6.0 "deadbeef trap" fix. (.m4a/MP4 has no fixture and is not covered here.)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import retag  # noqa: E402
from mutagen.apev2 import APEv2, APENoHeaderError  # noqa: E402
from mutagen.id3 import ID3, TCON, TXXX, ID3NoHeaderError  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "library"
FLAC_SRC = FIXTURES / "Aphex Twin" / "Selected Ambient Works" / "01 - Xtal.flac"
MP3_SRC = FIXTURES / "Cursive" / "Domestica" / "01 - The Casualty.mp3"


class ApplyGenresFlacTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "track.flac")
        shutil.copy(FLAC_SRC, self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_round_trip_single(self):
        self.assertTrue(retag.apply_genres(self.path, ["Ambient"]))
        self.assertEqual(retag.read_genres(self.path), ["Ambient"])

    def test_round_trip_multi(self):
        # Vorbis comments natively carry repeated keys.
        self.assertTrue(retag.apply_genres(self.path, ["Ambient", "IDM"]))
        self.assertEqual(retag.read_genres(self.path), ["Ambient", "IDM"])

    def test_overwrites_existing(self):
        retag.apply_genres(self.path, ["Old"])
        retag.apply_genres(self.path, ["New"])
        self.assertEqual(retag.read_genres(self.path), ["New"])


class ApplyGenresMp3Tests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "track.mp3")
        shutil.copy(MP3_SRC, self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def _id3(self) -> ID3:
        try:
            return ID3(self.path)
        except ID3NoHeaderError:
            return ID3()

    def test_round_trip_sets_tcon(self):
        self.assertTrue(retag.apply_genres(self.path, ["Trip Hop"]))
        self.assertEqual(retag.read_genres(self.path), ["Trip Hop"])
        self.assertEqual(self._id3()["TCON"].text, ["Trip Hop"])

    def test_clears_every_stale_genre_spot(self):
        # Seed the three override spots that survived the old EasyID3 path and
        # kept players like deadbeef showing a stale genre, plus one qualified
        # TXXX frame that must be preserved.
        ape = APEv2()  # a non-empty APE tag, so the save actually persists
        ape["Genre"] = "StaleApe"
        ape.save(self.path)

        tags = self._id3()
        tags.setall("TCON", [TCON(encoding=3, text=["StaleTcon"])])
        tags.add(TXXX(encoding=3, desc="GENRE", text=["StaleTxxx"]))
        tags.add(TXXX(encoding=3, desc="AB:GENRE", text=["KeepMe"]))
        tags.save(self.path, v1=2)

        self.assertTrue(retag.apply_genres(self.path, ["Clean Genre"]))

        self.assertEqual(retag.read_genres(self.path), ["Clean Genre"])
        with self.assertRaises(APENoHeaderError):
            APEv2(self.path)  # APEv2 tag fully removed
        tags = ID3(self.path)
        self.assertEqual(tags["TCON"].text, ["Clean Genre"])
        self.assertNotIn("TXXX:GENRE", tags)  # bare custom genre frame gone
        self.assertIn("TXXX:AB:GENRE", tags)  # qualified frame preserved


class ApplyGenresGuardTests(unittest.TestCase):
    def test_unsupported_extension_returns_false(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "notes.txt")
            Path(p).write_text("x")
            self.assertFalse(retag.apply_genres(p, ["Whatever"]))

    def test_read_genres_never_raises_on_garbage(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "broken.flac")
            Path(p).write_bytes(b"not audio")
            self.assertEqual(retag.read_genres(p), [])


if __name__ == "__main__":
    unittest.main()
