import os
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

    def test_non_audio_collision_dropped(self):
        canon = _make_dir(self.root, "Album", {"cover.jpg": b"A" * 50, "01.flac": b"z"})
        src = _make_dir(self.root, "album", {"cover.jpg": b"B" * 99})
        run = self._run()
        cleaner.consolidate_group([canon, src], "test", run)
        run.close()
        self.assertEqual((canon / "cover.jpg").read_bytes(), b"A" * 50)
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


if __name__ == "__main__":
    unittest.main()
