import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import lattice.modes.integrity as integrity_mod
from lattice.modes.integrity import (
    classify_decode,
    run_mp3_mode,
    _find_files_by_ext_path,
    TIER_OK,
    TIER_METADATA,
    TIER_SUSPECT,
    TIER_CORRUPT,
)

# Real stderr signatures captured from ffmpeg / libFLAC during the audit.
_CLAVISH = (
    "Error submitting packet to decoder: Invalid data found when processing input\n"
    "[mp3float] Header missing\n"
    "Error submitting packet to decoder: Invalid data found when processing input"
)
_BOM_NOISE = (
    "Incorrect BOM value: 0x3500\n"
    "Error reading frame artists, skipped\n"
    "Incorrect BOM value: 0x3500\n"
    "Error reading frame PERFORMER_SORT_ORDER, skipped"
)
_FLAC_TRAILING = (
    "*** Got error code 0:FLAC__STREAM_DECODER_ERROR_STATUS_LOST_SYNC "
    "after processing 2527850 samples"
)
_FLAC_TRUNCATED = (
    "*** Got error code 0:FLAC__STREAM_DECODER_ERROR_STATUS_LOST_SYNC "
    "after processing 1589248 samples"
)
_FLAC_TOTAL = 2527850


def _tier(*args, **kwargs):
    return classify_decode(*args, **kwargs)[0]


class ClassifyDecodeTests(unittest.TestCase):
    def test_clean_is_ok(self):
        self.assertEqual(_tier(0, ""), TIER_OK)

    def test_pure_tag_noise_is_metadata(self):
        self.assertEqual(_tier(0, _BOM_NOISE), TIER_METADATA)

    def test_lone_header_missing_is_metadata(self):
        self.assertEqual(_tier(0, "[mp3float] Header missing"), TIER_METADATA)

    def test_lone_backstep_is_metadata(self):
        self.assertEqual(_tier(0, "[mp3float] invalid new backstep -1"), TIER_METADATA)

    def test_completed_decode_with_faults_is_suspect(self):
        # rc == 0: decoded to the end despite 'Invalid data' lines (it plays).
        self.assertEqual(_tier(0, _CLAVISH), TIER_SUSPECT)

    def test_single_decode_fault_is_suspect(self):
        self.assertEqual(
            _tier(0, "Error submitting packet to decoder: Invalid data found"),
            TIER_SUSPECT,
        )

    def test_unknown_line_is_suspect_not_hidden(self):
        self.assertEqual(_tier(0, "some unrecognized decoder whining"), TIER_SUSPECT)

    def test_mixed_metadata_and_decode_is_suspect(self):
        self.assertEqual(
            _tier(
                0, "Incorrect BOM value: 0x10\nError submitting packet: Invalid data"
            ),
            TIER_SUSPECT,
        )

    def test_cannot_open_is_corrupt(self):
        self.assertEqual(
            _tier(1, "Error opening input: Invalid data found when processing input"),
            TIER_CORRUPT,
        )

    def test_nonzero_exit_no_stderr_is_corrupt(self):
        self.assertEqual(_tier(1, ""), TIER_CORRUPT)

    def test_flac_trailing_is_suspect(self):
        self.assertEqual(_tier(1, _FLAC_TRAILING, _FLAC_TOTAL), TIER_SUSPECT)

    def test_flac_truncated_is_corrupt(self):
        self.assertEqual(_tier(1, _FLAC_TRUNCATED, _FLAC_TOTAL), TIER_CORRUPT)

    def test_flac_lostsync_without_declared_count_is_suspect(self):
        # Can't prove truncation without the declared total, so do not escalate.
        self.assertEqual(_tier(1, _FLAC_TRAILING), TIER_SUSPECT)

    def test_reason_is_returned(self):
        tier, reason = classify_decode(1, _FLAC_TRUNCATED, _FLAC_TOTAL)
        self.assertEqual(tier, TIER_CORRUPT)
        self.assertIn("1589248", reason)


class Mp3HeaderInfoTests(unittest.TestCase):
    def test_vbr_mode_is_the_member_name(self):
        # __class__.__name__ rendered every bitrate mode as "BitrateMode";
        # the row must carry the actual member ("CBR"/"VBR"/"ABR").
        from pathlib import Path

        from lattice.modes.integrity import _mutagen_header_info

        fixture = (
            Path(__file__).parent
            / "fixtures"
            / "library"
            / "Cursive"
            / "Domestica"
            / "01 - The Casualty.mp3"
        )
        meta = _mutagen_header_info(fixture)
        self.assertEqual(meta.get("vbr_mode"), "CBR")


class FindFilesByExtTests(unittest.TestCase):
    """The integrity file walk must agree with utils.iter_audio_dirs, which
    every other mode uses: hidden directories are pruned, and the order is
    stable so two scans of one library produce comparable reports."""

    def _tree(self, td: str) -> None:
        for rel in (
            "Zed/Album/02.mp3",
            "Zed/Album/01.mp3",
            "Abe/Album/01.mp3",
            ".testing/Copy/01.mp3",
            "Abe/.stash/01.mp3",
        ):
            p = Path(td) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"")

    def test_hidden_dirs_pruned(self):
        with tempfile.TemporaryDirectory() as td:
            self._tree(td)
            found = _find_files_by_ext_path([td], ".mp3")
            rels = {str(p.relative_to(td)) for p in found}
            self.assertEqual(
                rels,
                {
                    os.path.join("Zed", "Album", "02.mp3"),
                    os.path.join("Zed", "Album", "01.mp3"),
                    os.path.join("Abe", "Album", "01.mp3"),
                },
            )

    def test_results_are_sorted(self):
        with tempfile.TemporaryDirectory() as td:
            self._tree(td)
            found = [str(p) for p in _find_files_by_ext_path([td], ".mp3")]
            self.assertEqual(found, sorted(found))

    def test_explicit_file_root_still_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "track.mp3"
            f.write_bytes(b"")
            self.assertEqual(_find_files_by_ext_path([str(f)], ".mp3"), [f])


class DecodeReportOrderTests(unittest.TestCase):
    """Rows come back in as_completed order, so the report sections sort by
    path; without that, two runs over an unchanged library produced
    differently-ordered reports that could not be diffed."""

    def test_ok_section_is_path_sorted(self):
        names = ["Abe", "Mid", "Zed"]
        # Sleep longest for the alphabetically-first directory, so with a pool
        # the futures complete in exactly reverse-sorted order. Without the
        # sort in _section the report comes out Zed/Mid/Abe and this fails;
        # relying on scheduling luck would have made the guard meaningless.
        delay = {n: (len(names) - i) * 0.02 for i, n in enumerate(names)}
        real = integrity_mod._scan_one_file

        def slow(path, ffmpeg_path, *, enrich=False):
            time.sleep(delay.get(Path(path).parent.name, 0.0))
            return real(path, ffmpeg_path, enrich=enrich)

        with tempfile.TemporaryDirectory() as td:
            for name in names:
                p = Path(td) / name / "01.mp3"
                p.parent.mkdir(parents=True)
                p.write_bytes(b"")
            out = Path(td) / "report.txt"
            # A nonexistent --ffmpeg makes every file skip its decode and land
            # in OK, which --no-only-errors then lists; no decoder needed.
            with mock.patch.object(integrity_mod, "_scan_one_file", slow):
                rc = run_mp3_mode(
                    [td],
                    str(out),
                    len(names),
                    os.path.join(td, "no-such-ffmpeg"),
                    only_errors=False,
                    verbose=False,
                    quiet=True,
                )
            self.assertEqual(rc, 0)
            listed = [
                ln.strip()
                for ln in out.read_text(encoding="utf-8").splitlines()
                if ln.startswith("  ") and ln.strip().endswith("01.mp3")
            ]
            self.assertEqual(listed, sorted(listed))
            self.assertEqual(len(listed), 3)


if __name__ == "__main__":
    unittest.main()
