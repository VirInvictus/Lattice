import unittest

from lattice.modes.audit import (
    _norm_key,
    _loose_key,
    _fmt_size,
    _fmt_duration,
    _cluster_by_duration,
    _DirInfo,
)
from lattice.tags import TagBundle


class NormKeyTests(unittest.TestCase):
    def test_empty_inputs(self):
        self.assertEqual(_norm_key(None), "")
        self.assertEqual(_norm_key(""), "")

    def test_dash_variants_fold(self):
        # U+2010 hyphen and ASCII hyphen collapse to the same key.
        self.assertEqual(_norm_key("Jay‐Z"), _norm_key("Jay-Z"))

    def test_curly_apostrophe_folds(self):
        self.assertEqual(_norm_key("Director’s"), "director's")

    def test_whitespace_collapsed_and_lowered(self):
        self.assertEqual(_norm_key("  Hello   World "), "hello world")


class LooseKeyTests(unittest.TestCase):
    def test_strips_trailing_parenthetical(self):
        self.assertEqual(_loose_key("Domestica (Deluxe Edition)"), "domestica")

    def test_strips_feat_clause(self):
        self.assertEqual(_loose_key("Song feat. Someone"), "song")

    def test_strips_multiple_trailing_parens(self):
        self.assertEqual(_loose_key("Album (Remastered) (2009)"), "album")


class FmtSizeTests(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(_fmt_size(512), "512 B")

    def test_kb(self):
        self.assertEqual(_fmt_size(2048), "2.0 KB")

    def test_mb(self):
        self.assertEqual(_fmt_size(5 * 1024 * 1024), "5.0 MB")

    def test_gb(self):
        self.assertEqual(_fmt_size(3 * 1024**3), "3.0 GB")


class FmtDurationTests(unittest.TestCase):
    def test_none(self):
        self.assertEqual(_fmt_duration(None), "--:--")

    def test_minutes_seconds(self):
        self.assertEqual(_fmt_duration(125), "2:05")


_FNAME: str = "track.mp3"


def _entry(path, dur):
    info = _DirInfo(
        path=path,
        artist="",
        album="",
        norm_artist="",
        norm_album="",
        loose_album="",
        total_bytes=0,
        formats={},
        fmt_bitrate={},
        files=[],
    )
    return (info, _FNAME, TagBundle(duration_s=dur))


class ClusterByDurationTests(unittest.TestCase):
    def test_single_cluster_within_delta(self):
        entries = [_entry("/A", 100.0), _entry("/B", 101.0)]
        clusters = _cluster_by_duration(entries, delta=2.0)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0]), 2)

    def test_split_when_beyond_delta(self):
        # Two copies 100s apart, so neither cluster has 2 entries.
        entries = [_entry("/A", 100.0), _entry("/B", 200.0)]
        self.assertEqual(_cluster_by_duration(entries, delta=2.0), [])

    def test_studio_and_live_surface_separately(self):
        entries = [
            _entry("/A", 100.0),
            _entry("/B", 101.0),
            _entry("/C", 240.0),
            _entry("/D", 241.0),
        ]
        clusters = _cluster_by_duration(entries, delta=2.0)
        self.assertEqual(len(clusters), 2)

    def test_same_directory_not_a_cluster(self):
        # Two entries within delta but in one directory: not cross-library.
        entries = [_entry("/A", 100.0), _entry("/A", 101.0)]
        self.assertEqual(_cluster_by_duration(entries, delta=2.0), [])

    def test_durationless_entries_cluster_together(self):
        entries = [_entry("/A", None), _entry("/B", None)]
        clusters = _cluster_by_duration(entries, delta=2.0)
        self.assertEqual(len(clusters), 1)


if __name__ == "__main__":
    unittest.main()
