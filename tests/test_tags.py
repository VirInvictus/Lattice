import unittest

from lattice.tags import _first_text, _parse_track_number


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


if __name__ == "__main__":
    unittest.main()
