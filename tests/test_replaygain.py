import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# replaygain.py lives in scripts/ (outside the lattice package); it shells out to
# rsgain to write ReplayGain tags. The pure helpers are tested directly and the
# rsgain call is mocked, so these tests never invoke rsgain or mutate a library.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import replaygain  # noqa: E402
from mutagen.id3 import ID3, TXXX  # noqa: E402
from mutagen.flac import FLAC  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "library"
MP3_SRC = FIXTURE / "Cursive" / "Domestica" / "01 - The Casualty.mp3"
FLAC_SRC = FIXTURE / "Aphex Twin" / "Selected Ambient Works" / "01 - Xtal.flac"

AUDIO_EXTS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".wav", ".wma", ".aac"}


class CoverageLabelTests(unittest.TestCase):
    def test_none(self):
        self.assertEqual(replaygain.coverage_label(0, 0, 5), "none")

    def test_partial(self):
        self.assertEqual(replaygain.coverage_label(3, 0, 5), "partial")

    def test_no_album_gain(self):
        self.assertEqual(replaygain.coverage_label(5, 0, 5), "no-album-gain")

    def test_no_album_gain_when_album_incomplete(self):
        self.assertEqual(replaygain.coverage_label(5, 3, 5), "no-album-gain")

    def test_ok(self):
        self.assertEqual(replaygain.coverage_label(5, 5, 5), "ok")


class FindAlbumDirsTests(unittest.TestCase):
    def test_finds_audio_dirs_and_prunes_hidden(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Album A").mkdir()
            (root / "Album A" / "01.mp3").write_bytes(b"x")
            (root / "Album A" / "cover.jpg").write_bytes(b"x")  # not audio
            (root / "Empty").mkdir()  # no audio -> excluded
            (root / ".hidden").mkdir()
            (root / ".hidden" / "01.flac").write_bytes(b"x")  # pruned

            found = replaygain.find_album_dirs(str(root), AUDIO_EXTS)

            names = {os.path.basename(d) for d, _ in found}
            self.assertEqual(names, {"Album A"})
            (_dir, files) = found[0]
            self.assertEqual(files, ["01.mp3"])


class AlbumCoverageTests(unittest.TestCase):
    def test_aggregates_from_reader(self):
        # Fake reader: first two files fully tagged, third bare.
        def fake_read(path):
            n = os.path.basename(path)
            full = n in ("a", "b")
            return mock.Mock(has_track_gain=full, has_album_gain=full)

        n_total, n_track, n_album, label = replaygain.album_coverage(
            fake_read, "/x", ["a", "b", "c"]
        )
        self.assertEqual((n_total, n_track, n_album), (3, 2, 2))
        self.assertEqual(label, "partial")


class ReadGainStringsTests(unittest.TestCase):
    def test_mp3_txxx(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "t.mp3")
            shutil.copy(MP3_SRC, p)
            tags = ID3(p)
            tags.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text=["-6.66 dB"]))
            tags.add(TXXX(encoding=3, desc="REPLAYGAIN_ALBUM_GAIN", text=["-7.00 dB"]))
            tags.save(p)
            self.assertEqual(replaygain.read_gain_strings(p), ("-6.66 dB", "-7.00 dB"))

    def test_flac_vorbis(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "t.flac")
            shutil.copy(FLAC_SRC, p)
            f = FLAC(p)
            f["replaygain_track_gain"] = "-5.00 dB"
            f["replaygain_album_gain"] = "-4.00 dB"
            f.save()
            self.assertEqual(replaygain.read_gain_strings(p), ("-5.00 dB", "-4.00 dB"))

    def test_untagged_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "t.flac")
            shutil.copy(FLAC_SRC, p)
            self.assertEqual(replaygain.read_gain_strings(p), (None, None))


class ScanAlbumTests(unittest.TestCase):
    def test_builds_rsgain_easy_command_single_thread(self):
        with mock.patch.object(replaygain.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            rc, _out, _err = replaygain.scan_album("/music/Album", ["01.mp3"], 1, None)
        self.assertEqual(rc, 0)
        argv = run.call_args[0][0]
        self.assertEqual(argv, ["rsgain", "easy", "-q", "/music/Album"])

    def test_threads_add_multithread_flag_in_easy_mode(self):
        with mock.patch.object(replaygain.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            replaygain.scan_album("/music/Album", ["01.mp3"], 4, None)
        argv = run.call_args[0][0]
        self.assertEqual(argv, ["rsgain", "easy", "-q", "-m", "4", "/music/Album"])

    def test_target_lufs_uses_custom_mode_with_explicit_files(self):
        with mock.patch.object(replaygain.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            # threads is ignored in custom mode (-m there means max-peak, not threads).
            replaygain.scan_album("/music/Album", ["01.mp3", "02.flac"], 4, -14.0)
        argv = run.call_args[0][0]
        self.assertEqual(argv[:3], ["rsgain", "custom", "-q"])
        # writes RG2 tags, album gain, positive-gain clip protection, the target.
        for flag in ("-a", "-s", "i", "-c", "p"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("-l") + 1], "-14")
        # explicit file list, no -m thread flag, no directory.
        self.assertIn("/music/Album/01.mp3", argv)
        self.assertIn("/music/Album/02.flac", argv)
        self.assertNotIn("-m", argv)
        self.assertNotIn("/music/Album", argv)

    def test_target_lufs_formats_without_trailing_zero(self):
        with mock.patch.object(replaygain.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            replaygain.scan_album("/a", ["x.mp3"], 1, -16.5)
        argv = run.call_args[0][0]
        self.assertEqual(argv[argv.index("-l") + 1], "-16.5")


if __name__ == "__main__":
    unittest.main()
