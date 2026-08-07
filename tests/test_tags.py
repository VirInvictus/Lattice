import os
import shutil
import tempfile
import unittest
from pathlib import Path

from lattice.tags import (
    _first_text,
    _parse_track_number,
    _popm_stars,
    _rg_flags,
    _tag_rating,
    get_all_tags,
)

FIXTURE_MP3 = str(
    Path(__file__).parent
    / "fixtures"
    / "library"
    / "Cursive"
    / "Domestica"
    / "01 - The Casualty.mp3"
)


class FirstTextTests(unittest.TestCase):
    def test_none(self):
        self.assertIsNone(_first_text(None))

    def test_plain_string(self):
        self.assertEqual(_first_text("hello"), "hello")

    def test_takes_first_of_list(self):
        self.assertEqual(_first_text(["a", "b"]), "a")

    def test_empty_list(self):
        self.assertIsNone(_first_text([]))

    def test_null_byte_becomes_slash(self):
        self.assertEqual(_first_text("a\x00b"), "a/b")

    def test_frame_joins_multi_values(self):
        # An ID3 frame object (has .text) joins all values with "/"; the tag
        # readers pass the frame itself so multi-valued TPE1/TIT2/TALB keep
        # every value instead of silently dropping all but the first.
        class FakeFrame:
            text = ["Artist One", "Artist Two"]

        self.assertEqual(_first_text(FakeFrame()), "Artist One/Artist Two")


class PopmStarsTests(unittest.TestCase):
    def test_canonical_bytes_map_to_whole_stars(self):
        # The WMP/foobar2000/Winamp convention bytes, valid for any email.
        for byte, stars in ((1, 1.0), (64, 2.0), (128, 3.0), (196, 4.0), (255, 5.0)):
            self.assertEqual(_popm_stars(byte), stars)

    def test_non_canonical_byte_falls_back_to_normalize(self):
        # 230 is no player's convention; the magnitude heuristic still applies.
        self.assertAlmostEqual(_popm_stars(230), 230 / 255 * 5, places=3)


class TagRatingTests(unittest.TestCase):
    def test_fmps_rating_scales_zero_to_one(self):
        self.assertEqual(_tag_rating("fmps_rating", "0.8"), 4.0)
        self.assertEqual(_tag_rating("txxx:fmps_rating", "1.0"), 5.0)

    def test_fmps_garbage_is_none(self):
        self.assertIsNone(_tag_rating("fmps_rating", "junk"))

    def test_plain_rating_key_uses_normalize(self):
        self.assertEqual(_tag_rating("rating", "4"), 4.0)
        self.assertEqual(_tag_rating("rating", "80"), 4.0)


class GetAllTagsMp3Tests(unittest.TestCase):
    """End-to-end reads against a temp copy of a fixture MP3."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.path = os.path.join(self.td, "track.mp3")
        shutil.copy2(FIXTURE_MP3, self.path)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_popm_canonical_byte_any_email_reads_whole_stars(self):
        from mutagen.id3 import ID3, POPM

        id3 = ID3(self.path)
        id3.delall("POPM")
        id3.add(POPM(email="no@email", rating=196))
        id3.save(self.path)
        self.assertEqual(get_all_tags(self.path).rating, 4.0)

    def test_multi_valued_tpe1_joins(self):
        from mutagen.id3 import ID3, TPE1

        id3 = ID3(self.path)
        id3.delall("TPE2")
        id3.add(TPE1(encoding=3, text=["Artist One", "Artist Two"]))
        id3.save(self.path)
        self.assertEqual(get_all_tags(self.path).artist, "Artist One/Artist Two")


class ParseTrackNumberTests(unittest.TestCase):
    def test_mp4_tuple_form(self):
        self.assertEqual(_parse_track_number([(3, 10)]), 3)

    def test_slash_form(self):
        self.assertEqual(_parse_track_number("5/12"), 5)

    def test_plain_number(self):
        self.assertEqual(_parse_track_number("7"), 7)

    def test_zero_is_none(self):
        self.assertIsNone(_parse_track_number("0"))

    def test_garbage_is_none(self):
        self.assertIsNone(_parse_track_number("abc"))

    def test_none(self):
        self.assertIsNone(_parse_track_number(None))

    def test_tuple_with_none_is_none(self):
        # A malformed trkn atom like (None, 0) must not raise TypeError.
        self.assertIsNone(_parse_track_number([(None, 0)]))


class ReplayGainFlagsTests(unittest.TestCase):
    def test_mp3_txxx_both(self):
        keys = ["TIT2", "TXXX:replaygain_track_gain", "TXXX:replaygain_album_gain"]
        self.assertEqual(_rg_flags(keys), (True, True))

    def test_opus_r128_both(self):
        # Opus stores gain as R128_*_GAIN, not replaygain_*_gain.
        keys = ["R128_TRACK_GAIN", "R128_ALBUM_GAIN", "replaygain_album_peak"]
        self.assertEqual(_rg_flags(keys), (True, True))

    def test_opus_r128_track_only(self):
        keys = ["R128_TRACK_GAIN", "replaygain_track_peak"]
        self.assertEqual(_rg_flags(keys), (True, False))

    def test_mp4_itunes_freeform(self):
        keys = ["----:com.apple.iTunes:replaygain_track_gain"]
        self.assertEqual(_rg_flags(keys), (True, False))

    def test_peak_only_is_not_gain(self):
        # A peak tag without a gain tag must not count as gain present.
        keys = ["replaygain_track_peak", "replaygain_album_peak"]
        self.assertEqual(_rg_flags(keys), (False, False))

    def test_no_replaygain(self):
        self.assertEqual(_rg_flags(["TIT2", "TALB"]), (False, False))


if __name__ == "__main__":
    unittest.main()
