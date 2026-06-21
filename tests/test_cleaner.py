import os
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

# cleaner.py lives in scripts/ (outside the lattice package); add it to the path
# so its helpers can be imported. Filesystem ops are exercised against tempfile
# trees; normalize_name (a pure helper) is tested directly.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import cleaner  # noqa: E402


class NormalizeNameTests(unittest.TestCase):
    def test_case_and_whitespace_collapse(self):
        self.assertEqual(cleaner.normalize_name("  The   Album "), "the album")

    def test_dash_variants_fold(self):
        self.assertEqual(
            cleaner.normalize_name("Jay‐Z"), cleaner.normalize_name("Jay-Z")
        )
        self.assertEqual(cleaner.normalize_name("A–B"), cleaner.normalize_name("A-B"))

    def test_curly_quote_folds(self):
        self.assertEqual(
            cleaner.normalize_name("You’re Gonna Miss It"),
            cleaner.normalize_name("You're Gonna Miss It"),
        )

    def test_apostrophe_present_vs_absent(self):
        # The 2026-05-25 found-bug fix: apostrophes are stripped so these merge.
        self.assertEqual(
            cleaner.normalize_name("Director's Cut"),
            cleaner.normalize_name("Directors Cut"),
        )


def _make_dir(root: Path, name: str, files: dict[str, bytes]) -> Path:
    d = root / name
    d.mkdir(parents=True)
    for fname, content in files.items():
        (d / fname).write_bytes(content)
    return d


def _png(w: int, h: int, pad: int = 0) -> bytes:
    """A minimal byte blob with a valid PNG signature + IHDR so _get_image_size
    reads (w, h); `pad` varies the byte length to force a size mismatch."""
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + struct.pack(">LL", w, h)
        + b"\x00" * pad
    )


class ConsolidateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.log = self.root / "cleanup.log"

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, dry_run=False):
        return cleaner.Run(self.root, self.log, dry_run=dry_run)

    def test_find_groups_matches_normalized_siblings(self):
        _make_dir(self.root, "Album", {"a.flac": b"a"})
        _make_dir(self.root, "album", {"b.flac": b"b"})
        _make_dir(self.root, "Different", {"c.flac": b"c"})
        run = self._run()
        groups = cleaner.find_groups(self.root, run)
        run.close()
        self.assertEqual(len(groups), 1)
        self.assertEqual({p.name for p in groups[0]}, {"Album", "album"})

    def test_hidden_dirs_ignored(self):
        _make_dir(self.root, "Album", {"a.flac": b"a"})
        _make_dir(self.root, ".album", {"b.flac": b"b"})
        run = self._run()
        groups = cleaner.find_groups(self.root, run)
        run.close()
        self.assertEqual(groups, [])

    def test_non_colliding_merge_into_larger(self):
        canon = _make_dir(self.root, "Album", {"01.flac": b"x", "02.flac": b"y"})
        src = _make_dir(self.root, "album", {"03.flac": b"z"})
        run = self._run()
        cleaner.consolidate_group([canon, src], "test", run)
        run.close()
        self.assertTrue((canon / "03.flac").exists())
        self.assertFalse(src.exists())

    def test_audio_collision_different_size_kept_as_fragment(self):
        canon = _make_dir(self.root, "Album", {"01.flac": b"x" * 100, "02.flac": b"y"})
        src = _make_dir(self.root, "album", {"01.flac": b"x" * 200})
        run = self._run()
        cleaner.consolidate_group([canon, src], "test", run)
        run.close()
        self.assertEqual((canon / "01.flac").read_bytes(), b"x" * 100)
        self.assertTrue((canon / "01.from-fragment.flac").exists())
        self.assertEqual((canon / "01.from-fragment.flac").read_bytes(), b"x" * 200)
        self.assertFalse(src.exists())

    def test_audio_collision_identical_size_dropped(self):
        canon = _make_dir(self.root, "Album", {"01.flac": b"x" * 100, "02.flac": b"y"})
        src = _make_dir(self.root, "album", {"01.flac": b"x" * 100})
        run = self._run()
        cleaner.consolidate_group([canon, src], "test", run)
        run.close()
        self.assertFalse((canon / "01.from-fragment.flac").exists())
        self.assertFalse(src.exists())

    def test_non_audio_non_image_collision_dropped(self):
        # Non-image non-audio (.nfo) keeps canonical's copy, drops the source.
        canon = _make_dir(self.root, "Album", {"info.nfo": b"A" * 50, "01.flac": b"z"})
        src = _make_dir(self.root, "album", {"info.nfo": b"B" * 99})
        run = self._run()
        cleaner.consolidate_group([canon, src], "test", run)
        run.close()
        self.assertEqual((canon / "info.nfo").read_bytes(), b"A" * 50)
        self.assertFalse(any(canon.glob("*from-fragment*")))
        self.assertFalse(src.exists())

    def test_dry_run_changes_nothing(self):
        canon = _make_dir(self.root, "Album", {"01.flac": b"x", "02.flac": b"y"})
        src = _make_dir(self.root, "album", {"03.flac": b"z"})
        run = self._run(dry_run=True)
        cleaner.consolidate_group([canon, src], "test", run)
        run.close()
        self.assertTrue((src / "03.flac").exists())
        self.assertFalse((canon / "03.flac").exists())


class _TreeCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.log = self.root / "cleanup.log"

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, dry_run=False):
        return cleaner.Run(self.root, self.log, dry_run=dry_run)


class CanonicalRenderTests(unittest.TestCase):
    def test_unicode_dash_to_ascii(self):
        self.assertEqual(
            cleaner.canonical_render("Drive‐By Truckers"), "Drive-By Truckers"
        )

    def test_curly_apostrophe_to_straight(self):
        self.assertEqual(cleaner.canonical_render("You’re"), "You're")

    def test_case_preserved_whitespace_collapsed(self):
        self.assertEqual(cleaner.canonical_render("  The   XX "), "The XX")

    def test_curly_double_quotes_preserved(self):
        # Straight " is forbidden on Windows/NTFS, so curly double quotes stay.
        self.assertEqual(
            cleaner.canonical_render("Damian “Jr. Gong” Marley"),
            "Damian “Jr. Gong” Marley",
        )

    def test_ellipsis_preserved(self):
        # "..." would end a name in dots, which NTFS rejects; keep the glyph.
        self.assertEqual(cleaner.canonical_render("Rooms…"), "Rooms…")

    def test_en_dash_preserved(self):
        # En-dashes in ranges are correct; canonical_render must not fold them.
        self.assertEqual(cleaner.canonical_render("Works 85–92"), "Works 85–92")

    def test_em_dash_preserved(self):
        self.assertEqual(cleaner.canonical_render("peace — reworks"), "peace — reworks")

    def test_already_normal_unchanged(self):
        self.assertEqual(
            cleaner.canonical_render("Damian Jr. Gong Marley"),
            "Damian Jr. Gong Marley",
        )


class IsLegalNameTests(unittest.TestCase):
    def test_rejects_trailing_dot_or_space(self):
        self.assertFalse(cleaner.is_legal_name("Rooms..."))
        self.assertFalse(cleaner.is_legal_name("Album "))

    def test_rejects_windows_forbidden_chars(self):
        self.assertFalse(cleaner.is_legal_name('a"b'))
        self.assertFalse(cleaner.is_legal_name("a:b"))
        self.assertFalse(cleaner.is_legal_name("a/b"))

    def test_accepts_normal_names(self):
        self.assertTrue(cleaner.is_legal_name("Drive-By Truckers"))
        self.assertTrue(cleaner.is_legal_name("Get Rich or Die Tryin'"))
        self.assertTrue(cleaner.is_legal_name("85–92"))


class GetImageSizeTests(unittest.TestCase):
    def test_png(self):
        self.assertEqual(cleaner._get_image_size(_png(640, 480)), (640, 480))

    def test_jpeg(self):
        data = (
            b"\xff\xd8\xff\xc0\x00\x11\x08" + struct.pack(">HH", 300, 200) + b"\x00" * 8
        )
        self.assertEqual(cleaner._get_image_size(data), (200, 300))

    def test_garbage_none(self):
        self.assertIsNone(cleaner._get_image_size(b"not an image"))


class DryRunFidelityTests(unittest.TestCase):
    """A dry-run must predict the real run's rmdir decisions and counts (#1)."""

    def _scenario(self, dry):
        tmp = tempfile.mkdtemp()
        try:
            root = Path(tmp)
            canon = _make_dir(root, "Album", {"01.flac": b"x", "02.flac": b"y"})
            src = _make_dir(root, "album", {"03.flac": b"z"})
            run = cleaner.Run(root, root / "log", dry_run=dry)
            cleaner.consolidate_group([canon, src], "t", run)
            run.close()
            return dict(run.stats)
        finally:
            shutil.rmtree(tmp)

    def test_dry_stats_match_real(self):
        self.assertEqual(self._scenario(dry=True), self._scenario(dry=False))


class SurvivorRenameTests(_TreeCase):
    def test_unicode_canonical_renamed_to_ascii(self):
        canon = _make_dir(
            self.root, "Drive‐By Truckers", {"a.flac": b"1", "b.flac": b"2"}
        )
        src = _make_dir(self.root, "Drive-By Truckers", {"c.flac": b"3"})
        run = self._run()
        cleaner.consolidate_group([canon, src], "artists", run)
        run.close()
        self.assertTrue((self.root / "Drive-By Truckers").is_dir())
        self.assertFalse((self.root / "Drive‐By Truckers").exists())
        self.assertEqual(run.stats["renamed"], 1)

    def test_already_normalized_no_rename(self):
        canon = _make_dir(self.root, "Album", {"a.flac": b"1", "b.flac": b"2"})
        src = _make_dir(self.root, "album", {"c.flac": b"3"})
        run = self._run()
        cleaner.consolidate_group([canon, src], "t", run)
        run.close()
        self.assertEqual(run.stats["renamed"], 0)


class CoverResolutionTests(_TreeCase):
    def test_higher_res_replaces_even_if_smaller_bytes(self):
        canon = _make_dir(
            self.root,
            "Album",
            {"cover.png": _png(100, 100, pad=500), "01.flac": b"z"},
        )
        src = _make_dir(self.root, "album", {"cover.png": _png(400, 400)})
        run = self._run()
        cleaner.consolidate_group([canon, src], "t", run)
        run.close()
        self.assertEqual(
            cleaner._get_image_size((canon / "cover.png").read_bytes()), (400, 400)
        )
        self.assertEqual(run.stats["covers_replaced"], 1)
        self.assertFalse(src.exists())

    def test_equal_res_falls_back_to_larger_bytes(self):
        canon = _make_dir(
            self.root, "Album", {"cover.png": _png(200, 200), "01.flac": b"z"}
        )
        src = _make_dir(self.root, "album", {"cover.png": _png(200, 200, pad=300)})
        run = self._run()
        cleaner.consolidate_group([canon, src], "t", run)
        run.close()
        self.assertEqual(run.stats["covers_replaced"], 1)
        self.assertEqual(
            len((canon / "cover.png").read_bytes()), len(_png(200, 200, pad=300))
        )


class NormalizeTreeTests(_TreeCase):
    def test_renames_lone_unicode_hyphen_artist(self):
        _make_dir(self.root, "Jay‐Z", {"a.flac": b"1"})
        run = self._run()
        cleaner.normalize_tree(self.root, run)
        run.close()
        self.assertTrue((self.root / "Jay-Z").is_dir())
        self.assertFalse((self.root / "Jay‐Z").exists())

    def test_renames_album_within_artist(self):
        album = self.root / "Artist" / "4‐44"
        album.mkdir(parents=True)
        (album / "t.flac").write_bytes(b"1")
        run = self._run()
        cleaner.normalize_tree(self.root, run)
        run.close()
        self.assertTrue((self.root / "Artist" / "4-44").is_dir())

    def test_dry_run_counts_but_does_not_rename(self):
        _make_dir(self.root, "Jay‐Z", {"a.flac": b"1"})
        run = self._run(dry_run=True)
        cleaner.normalize_tree(self.root, run)
        run.close()
        self.assertTrue((self.root / "Jay‐Z").exists())
        self.assertEqual(run.stats["renamed"], 1)

    def test_ellipsis_folder_left_alone(self):
        # Regression: folding … -> "..." produced an NTFS-illegal trailing-dot
        # name and crashed the run. The glyph is valid, so it must be kept.
        album = self.root / "Artist" / "Rooms…"
        album.mkdir(parents=True)
        (album / "t.flac").write_bytes(b"1")
        run = self._run()
        cleaner.normalize_tree(self.root, run)
        run.close()
        self.assertTrue((self.root / "Artist" / "Rooms…").is_dir())
        self.assertEqual(run.stats["renamed"], 0)


from mutagen.id3 import ID3  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "library"
MP3_SRC = FIXTURES / "Cursive" / "Domestica" / "01 - The Casualty.mp3"


class TagFoldTests(unittest.TestCase):
    def test_cp1252_mojibake_to_ascii(self):
        # \x93/\x94/\x92 are CP1252 bytes read back as Latin-1 C1 controls.
        self.assertEqual(
            cleaner.tag_fold("Bonnie \x93Prince\x94 Billy"), 'Bonnie "Prince" Billy'
        )
        self.assertEqual(cleaner.tag_fold("Tim O\x92Brien"), "Tim O'Brien")

    def test_curly_quotes_to_ascii(self):
        self.assertEqual(cleaner.tag_fold("Singer’s Grave"), "Singer's Grave")

    def test_whitespace_collapsed(self):
        self.assertEqual(cleaner.tag_fold("  A   B "), "A B")


class CanonTrackArtistTests(unittest.TestCase):
    def test_plain_collapses_to_canonical(self):
        self.assertEqual(
            cleaner.canon_track_artist("Bonnie Prince Billy", "Bonnie 'Prince' Billy"),
            "Bonnie 'Prince' Billy",
        )

    def test_doubled_junk_collapses(self):
        self.assertEqual(
            cleaner.canon_track_artist(
                "Bonnie Prince Billy / Bonnie 'Prince' Billy", "Bonnie 'Prince' Billy"
            ),
            "Bonnie 'Prince' Billy",
        )

    def test_feat_preserved_and_folded(self):
        self.assertEqual(
            cleaner.canon_track_artist(
                "Bonnie \x93Prince\x94 Billy feat. Tim O\x92Brien",
                "Bonnie 'Prince' Billy",
            ),
            "Bonnie 'Prince' Billy feat. Tim O'Brien",
        )


def _mp3(path: Path, artist: str, albumartist: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(MP3_SRC, path)
    tags = ID3(path)
    tags.add(cleaner.TPE1(encoding=3, text=[artist]))
    tags.add(cleaner.TPE2(encoding=3, text=[albumartist]))
    tags.save(path, v2_version=3)


class NormalizeTagsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.log = self.root / "cleanup.log"
        self.genre = self.root / "Alt Country"
        # Survivor wins by file count (2 > 1), so its quoted name is authority.
        self.surv = self.genre / "Bonnie 'Prince' Billy"
        _mp3(
            self.surv / "I See a Darkness" / "01.mp3",
            "Bonnie Prince Billy / Bonnie 'Prince' Billy",
            "Bonnie 'Prince' Billy",
        )
        _mp3(
            self.surv / "I See a Darkness" / "02.mp3",
            "Bonnie 'Prince' Billy",
            "Bonnie 'Prince' Billy",
        )
        # Variant folders merged in: no-quotes, and CP1252-mojibake with a guest.
        _mp3(
            self.genre / "Bonnie Prince Billy" / "Beware" / "01.mp3",
            "Bonnie Prince Billy",
            "Bonnie Prince Billy",
        )
        _mp3(
            self.genre / "Bonnie “Prince” Billy" / "Purple Bird" / "01.mp3",
            "Bonnie \x93Prince\x94 Billy feat. Tim O\x92Brien",
            "Bonnie \x93Prince\x94 Billy",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, dry_run=False):
        return cleaner.Run(
            self.root, self.log, dry_run=dry_run, normalize_tags=True, artist_depth=2
        )

    def _consolidate(self, run):
        cleaner.consolidate_group(
            cleaner.find_groups(self.genre, run)[0], "Alt Country", run
        )

    def test_merge_restamps_all_tags_to_survivor(self):
        run = self._run()
        self._consolidate(run)
        cleaner.normalize_tags(run)
        run.close()
        beware = self.surv / "Beware" / "01.mp3"
        purple = self.surv / "Purple Bird" / "01.mp3"
        self.assertEqual(ID3(beware)["TPE1"].text[0], "Bonnie 'Prince' Billy")
        self.assertEqual(ID3(beware)["TPE2"].text[0], "Bonnie 'Prince' Billy")
        # Guest credit preserved, band name + mojibake normalized.
        self.assertEqual(
            ID3(purple)["TPE1"].text[0], "Bonnie 'Prince' Billy feat. Tim O'Brien"
        )
        self.assertEqual(ID3(purple)["TPE2"].text[0], "Bonnie 'Prince' Billy")
        # The doubled-junk artist on the survivor's own track is fixed too.
        darkness = self.surv / "I See a Darkness" / "01.mp3"
        self.assertEqual(ID3(darkness)["TPE1"].text[0], "Bonnie 'Prince' Billy")

    def test_dry_run_reports_but_does_not_write(self):
        run = self._run(dry_run=True)
        self._consolidate(run)
        cleaner.normalize_tags(run)
        run.close()
        # Nothing moved or written in dry-run.
        self.assertEqual(
            ID3(self.genre / "Bonnie Prince Billy" / "Beware" / "01.mp3")["TPE1"].text[
                0
            ],
            "Bonnie Prince Billy",
        )
        self.assertGreater(run.stats["tags_rewritten"], 0)

    def test_clean_files_not_counted_as_rewrites(self):
        run = self._run()
        self._consolidate(run)
        cleaner.normalize_tags(run)
        run.close()
        # Survivor track 02 was already correct: 4 tracks total, 3 needed changes.
        self.assertEqual(run.stats["tags_rewritten"], 3)
        self.assertEqual(run.stats["tag_files_scanned"], 4)

    def test_non_artist_depth_not_retagged(self):
        # With artist_depth=1 the depth-2 survivor is not an artist folder, so no
        # tag target is seeded and nothing is rewritten.
        run = cleaner.Run(
            self.root, self.log, dry_run=False, normalize_tags=True, artist_depth=1
        )
        self._consolidate(run)
        cleaner.normalize_tags(run)
        run.close()
        self.assertEqual(run.stats["tags_rewritten"], 0)


if __name__ == "__main__":
    unittest.main()
