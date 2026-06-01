"""Integration tests: run the mode functions against the committed fixture
library (tests/fixtures/library/) and assert on their reports. These cover the
walk + aggregate + report paths that the pure-helper tests do not.

The read-only report modes need only mutagen (the fixtures are pre-tagged), so
these run anywhere. Integrity modes, which shell out to flac/ffmpeg, are not
exercised here; classify_decode is unit-tested in test_integrity.py.
"""

import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import lattice.modes.library as library_mod
from lattice.modes.library import (
    write_music_library_tree,
    write_ai_library,
    write_all_wings,
)
from lattice.modes.stats import run_stats
from lattice.modes.audit import (
    run_duplicates,
    run_tag_audit,
    run_bitrate_audit,
    run_replaygain_audit,
)
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


class ReplayGainAuditTests(unittest.TestCase):
    """Build a tmp library of FLAC albums with hand-written ReplayGain tags, one
    per coverage bucket, and assert the audit sorts them correctly. Opus R128 is
    covered by the _rg_flags unit tests; here we exercise the walk + classify +
    report path on real files."""

    def _album(self, root: Path, name: str, *, track: bool, album: bool) -> None:
        from mutagen.flac import FLAC

        src = Path(FIXTURE) / "Aphex Twin" / "Selected Ambient Works"
        dst = root / name
        dst.mkdir(parents=True)
        for fname in ("01 - Xtal.flac", "02 - Tha.flac"):
            target = dst / fname
            shutil.copy(src / fname, target)
            f = FLAC(target)
            for k in ("replaygain_track_gain", "replaygain_album_gain"):
                f.pop(k, None)
            if track:
                f["replaygain_track_gain"] = "-6.66 dB"
            if album:
                f["replaygain_album_gain"] = "-7.00 dB"
            f.save()

    def test_buckets(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._album(root, "Fully Tagged", track=True, album=True)
            self._album(root, "No Album Gain", track=True, album=False)
            self._album(root, "Missing", track=False, album=False)
            # Partial: one track fully tagged, one bare.
            partial = root / "Partial"
            partial.mkdir()
            src = Path(FIXTURE) / "Aphex Twin" / "Selected Ambient Works"
            from mutagen.flac import FLAC

            for fname, tag in (("01 - Xtal.flac", True), ("02 - Tha.flac", False)):
                target = partial / fname
                shutil.copy(src / fname, target)
                f = FLAC(target)
                f.pop("replaygain_track_gain", None)
                f.pop("replaygain_album_gain", None)
                if tag:
                    f["replaygain_track_gain"] = "-6.66 dB"
                    f["replaygain_album_gain"] = "-7.00 dB"
                f.save()

            text = _write_to_temp(
                lambda o: run_replaygain_audit(str(root), o, verbose=True, quiet=True)
            )

        self.assertIn("OK: 1   No album gain: 1   Partial: 1   Missing: 1", text)
        missing = text[text.find("[MISSING") : text.find("[PARTIAL")]
        self.assertIn("Missing/", missing)
        partial_sec = text[text.find("[PARTIAL") : text.find("[NO ALBUM")]
        self.assertIn("Partial/    (1/2 tracks tagged)", partial_sec)
        noalbum = text[text.find("[NO ALBUM") : text.find("[FULLY")]
        self.assertIn("No Album Gain/    (2/2 track gain, 0/2 album gain)", noalbum)
        self.assertIn("Fully Tagged/", text[text.find("[FULLY") :])


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


class MultiRootTests(unittest.TestCase):
    """A single invocation can scan several roots at once; results aggregate
    across them. The second root is a temp copy of one fixture album, so the
    same content lives under two roots."""

    def _second_root(self, td: str) -> str:
        """Build a second library under `td` holding a copy of Cursive/Domestica
        (present exactly once in the fixture, so any duplicate it produces is
        unambiguously cross-root)."""
        dst = Path(td) / "Cursive" / "Domestica"
        shutil.copytree(Path(FIXTURE) / "Cursive" / "Domestica", dst)
        return td

    def test_combined_stats_sum_both_roots(self):
        with tempfile.TemporaryDirectory() as td:
            second = self._second_root(td)
            report = run_stats([FIXTURE, second], None, quiet=True)
        # 9 in the fixture + the 2 copied Domestica tracks.
        self.assertRegex(report, r"Total files:\s+11")
        # The header lists both roots.
        self.assertIn(",", report.split("Root:", 1)[1].splitlines()[0])

    def test_duplicates_detected_across_roots(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as od:
            second = self._second_root(td)
            out = os.path.join(od, "dupes.txt")
            run_duplicates([FIXTURE, second], out, quiet=True)
            text = Path(out).read_text(encoding="utf-8")
        # Domestica is unique within the fixture, so its appearance as an exact
        # album duplicate proves cross-root grouping.
        exact = text[text.find("[EXACT") : text.find("[WITHIN")]
        self.assertIn("Domestica", exact)
        # With two roots, entries are prefixed by their root's basename so the
        # two copies are distinguishable rather than identical relative paths.
        self.assertIn("library/Cursive/Domestica", exact)

    def test_single_root_as_list_matches_bare_string(self):
        # Back-compat: a one-element list and a bare string are equivalent.
        self.assertEqual(
            run_stats(FIXTURE, None, quiet=True),
            run_stats([FIXTURE], None, quiet=True),
        )


class ScanGenreFallbackTests(unittest.TestCase):
    """The scanner derives genre from the path layout when a file carries no
    genre tag — parity with the existing artist/album path-fallback."""

    class _Pbar:
        def update(self, n: int = 1) -> None:
            pass

    def test_genre_from_path_when_tag_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            f = root / "Jazz" / "Miles Davis" / "Kind of Blue" / "01.mp3"
            f.parent.mkdir(parents=True)
            f.write_text("x")  # contents irrelevant; get_all_tags is mocked
            tagless = SimpleNamespace(artist=None, album=None, genre=None)
            with mock.patch.object(library_mod, "get_all_tags", return_value=tagless):
                dirs = library_mod._scan_album_dirs(
                    [str(root)], "{genre}/{artist}/{album}", self._Pbar()
                )
            self.assertEqual(len(dirs), 1)
            self.assertEqual(dirs[0].genre, "Jazz")
            self.assertEqual(dirs[0].artist, "Miles Davis")
            self.assertEqual(dirs[0].album, "Kind of Blue")


if __name__ == "__main__":
    unittest.main()
