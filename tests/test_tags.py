import unittest

from lattice.tags import _first_text, _parse_track_number, _rg_flags


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
