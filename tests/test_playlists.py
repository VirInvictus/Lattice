import unittest
from collections import namedtuple

from lattice.modes.playlists import _evaluate_rule

# Only the fields _evaluate_rule reads.
FakeTag = namedtuple(
    "FakeTag", "rating genre artist album title duration_s bitrate_kbps"
)


def tag(**kw):
    base = dict(
        rating=None,
        genre=None,
        artist=None,
        album=None,
        title=None,
        duration_s=None,
        bitrate_kbps=None,
    )
    base.update(kw)
    return FakeTag(**base)


class RuleEvalTests(unittest.TestCase):
    def test_empty_rule_matches_everything(self):
        self.assertTrue(_evaluate_rule("", tag(), {}))
        self.assertTrue(_evaluate_rule("   ", tag(), {}))

    def test_numeric_comparison(self):
        self.assertTrue(_evaluate_rule("rating >= 4", tag(rating=5.0), {}))
        self.assertFalse(_evaluate_rule("rating >= 4", tag(rating=3.0), {}))

    def test_string_equality_and_membership(self):
        self.assertTrue(_evaluate_rule("genre == 'Jazz'", tag(genre="Jazz"), {}))
        self.assertTrue(_evaluate_rule("'azz' in genre", tag(genre="Jazz"), {}))
        self.assertFalse(_evaluate_rule("genre == 'Rock'", tag(genre="Jazz"), {}))

    def test_boolean_and_or(self):
        t = tag(rating=5.0, genre="Jazz")
        self.assertTrue(_evaluate_rule("rating >= 4 and genre == 'Jazz'", t, {}))
        self.assertFalse(_evaluate_rule("rating >= 4 and genre == 'Rock'", t, {}))
        self.assertTrue(_evaluate_rule("rating < 2 or genre == 'Jazz'", t, {}))

    def test_sql_style_and_or_convenience(self):
        t = tag(rating=5.0, genre="Jazz")
        self.assertTrue(_evaluate_rule("rating >= 4 AND genre == 'Jazz'", t, {}))
        self.assertTrue(_evaluate_rule("rating < 2 OR genre == 'Jazz'", t, {}))

    def test_chained_comparison(self):
        self.assertTrue(_evaluate_rule("2 <= rating <= 4", tag(rating=3.0), {}))
        self.assertFalse(_evaluate_rule("2 <= rating <= 4", tag(rating=5.0), {}))

    def test_layout_fallback_fields(self):
        self.assertTrue(
            _evaluate_rule("artist == 'Aphex Twin'", tag(), {"artist": "Aphex Twin"})
        )

    # --- security: the old eval() sandbox was escapable; these must NOT run ---

    def test_attribute_access_is_rejected(self):
        # The classic sandbox escape; must be refused, not executed.
        self.assertFalse(
            _evaluate_rule("genre.__class__.__mro__[-1]", tag(genre="x"), {})
        )

    def test_dunder_and_calls_are_rejected(self):
        self.assertFalse(_evaluate_rule("__import__('os')", tag(), {}))
        self.assertFalse(_evaluate_rule("open('/etc/passwd')", tag(), {}))

    def test_unknown_field_is_rejected(self):
        self.assertFalse(_evaluate_rule("bogus == 1", tag(), {}))

    def test_subscript_is_rejected(self):
        self.assertFalse(_evaluate_rule("genre[0] == 'J'", tag(genre="Jazz"), {}))


if __name__ == "__main__":
    unittest.main()
