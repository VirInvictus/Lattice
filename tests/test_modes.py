"""Integration tests: run the mode functions against the committed fixture
library (tests/fixtures/library/) and assert on their reports. These cover the
walk + aggregate + report paths that the pure-helper tests do not.

The read-only report modes need only mutagen (the fixtures are pre-tagged), so
these run anywhere. Integrity modes, which shell out to flac/ffmpeg, are not
exercised here; classify_decode is unit-tested in test_integrity.py.
"""

import os
import re
import tempfile
import unittest
from pathlib import Path

from lattice.modes.library import (
    write_music_library_tree,
    write_ai_library,
    write_all_wings,
)
from lattice.modes.stats import run_stats
from lattice.modes.audit import run_duplicates, run_tag_audit, run_bitrate_audit
from lattice.modes.artwork import run_missing_art
from lattice.modes.playlists import generate_playlist

FIXTURE = str(Path(__file__).parent / "fixtures" / "library")


def _write_to_temp(run) -> str:
    """Call run(out_path) and return the text it wrote."""
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "out.txt")
        run(out)
        return Path(out).read_text(encoding="utf-8")


class LibraryTreeTests(unittest.TestCase):
    def test_structure_genre_and_rating(self):
        text = _write_to_temp(
            lambda o: write_music_library_tree(FIXTURE, o, quiet=True, show_genre=True)
        )
        self.assertIn("ARTIST: Aphex Twin", text)
        self.assertIn("ALBUM: Selected Ambient Works (Electronic)", text)
        self.assertIn("Xtal", text)
        self.assertIn("[★★★★★ 5.0/5]", text)


class AiLibraryTests(unittest.TestCase):
    def test_pipe_delimited_rated_row(self):
        text = _write_to_temp(lambda o: write_ai_library(FIXTURE, o, quiet=True))
        self.assertIn("Artist | Album | Genre | Rating | Tracks", text)
        # Aphex album: ratings 5.0/5.0/4.0 -> avg 4.7, 3 tracks.
        self.assertRegex(
            text, r"Aphex Twin \| Selected Ambient Works \| Electronic \| 4\.\d \| 3"
        )


class StatsTests(unittest.TestCase):
    def test_overview_and_format_breakdown(self):
        report = run_stats(FIXTURE, None, quiet=True)
        self.assertRegex(report, r"Total files:\s+9")
        self.assertRegex(report, r"\.flac\s+6 files")
        self.assertRegex(report, r"\.mp3\s+3 files")

    def test_five_star_count(self):
        report = run_stats(FIXTURE, None, quiet=True)
        self.assertRegex(report, r"★★★★★ \(5\)\s+3")

    def test_stats_five_star_matches_playlist_rating_ge_5(self):
        """Audit loose end locked in: --stats 5-star and --playlist 'rating >= 5'
        must select the same files (int(rating) >= 5 vs rating >= 5 are identical
        once ratings are capped at 5.0)."""
        report = run_stats(FIXTURE, None, quiet=True)
        m = re.search(r"★★★★★ \(5\)\s+(\d+)", report)
        stats_five = int(m.group(1)) if m else 0
        playlist = _write_to_temp(
            lambda o: generate_playlist(FIXTURE, o, "rating >= 5", quiet=True)
        )
        tracks = sum(
            1 for ln in playlist.splitlines() if ln.strip() and not ln.startswith("#")
        )
        self.assertEqual(stats_five, 3)
        self.assertEqual(stats_five, tracks)


class DuplicateTests(unittest.TestCase):
    def test_all_four_sections(self):
        text = _write_to_temp(lambda o: run_duplicates(FIXTURE, o, quiet=True))
        self.assertIn("[EXACT ALBUM DUPLICATES]    (1 album(s), 2 directories)", text)
        self.assertIn("[WITHIN-DIRECTORY MULTI-FORMAT]    (1 directories)", text)
        self.assertRegex(text, r"\[TRACK-LEVEL DUPLICATES\].*2 track\(s\)")
        self.assertIn("Re-do", text)


class AuditTests(unittest.TestCase):
    def test_tag_audit_flags_missing_genre(self):
        text = _write_to_temp(lambda o: run_tag_audit(FIXTURE, o, quiet=True))
        self.assertRegex(text, r"Scanned:\s*9\s+Incomplete:\s*1")
        self.assertIn("genre: 1", text)
        self.assertIn("The Martyr", text)

    def test_bitrate_audit_flags_low_bitrate_file(self):
        text = _write_to_temp(lambda o: run_bitrate_audit(FIXTURE, o, 192, quiet=True))
        self.assertIn("The Martyr", text)


class MissingArtTests(unittest.TestCase):
    def test_reports_dirs_without_art(self):
        text = _write_to_temp(lambda o: run_missing_art(FIXTURE, o, quiet=True))
        self.assertIn("No art at all: 4", text)
        self.assertIn("Cursive/Domestica", text)


class WingsTests(unittest.TestCase):
    def test_one_file_per_genre(self):
        with tempfile.TemporaryDirectory() as td:
            outdir = os.path.join(td, "wings")
            write_all_wings(FIXTURE, outdir, quiet=True)
            names = set(os.listdir(outdir))
        self.assertIn("Electronic_Library.txt", names)
        self.assertIn("Rock_Library.txt", names)
        self.assertIn("Indie_Library.txt", names)


if __name__ == "__main__":
    unittest.main()
