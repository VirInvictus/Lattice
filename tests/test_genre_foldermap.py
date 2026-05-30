import os
import sys
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path

# genre_foldermap.py lives in scripts/ (outside the lattice package); add it to
# the path so its pure helpers can be imported and unit-tested. The lattice scan
# (scan_album_dirs) is not exercised here — its records are faked — mirroring
# test_genre_tidy.py, which tests decision logic without the shell-out/scan.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import genre_foldermap as gf  # noqa: E402

# The scanner yields rows with .path and .genre; only those two fields are read.
FakeAD = namedtuple("FakeAD", "path genre")


class SanitizeTests(unittest.TestCase):
    def test_keeps_ntfs_safe_genres_verbatim(self):
        for g in ("R&B", "Drum & Bass", "G-Funk", "Hip Hop", "Post-Hardcore"):
            self.assertEqual(gf.sanitize_component(g), g)

    def test_folds_forbidden_chars(self):
        self.assertEqual(gf.sanitize_component("AC/DC"), "AC DC")
        self.assertEqual(gf.sanitize_component('a:b*c?"d'), "a b c d")

    def test_strips_trailing_dot_and_space(self):
        self.assertEqual(gf.sanitize_component("Genre. "), "Genre")

    def test_empty_becomes_unknown(self):
        self.assertEqual(gf.sanitize_component("///"), "Unknown")


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("/m")

    def test_album_uses_last_two_components(self):
        self.assertEqual(
            gf.classify(Path("/m/Aesop Rock/Skelethon"), self.root),
            ("album", "Aesop Rock", "Skelethon"),
        )

    def test_loose_artist_dir(self):
        self.assertEqual(
            gf.classify(Path("/m/J. Cole"), self.root), ("loose", "J. Cole")
        )

    def test_root_itself_is_skipped(self):
        kind, _reason = gf.classify(self.root, self.root)
        self.assertEqual(kind, "skip")


def _make_tree(root: Path, layout: dict) -> None:
    """layout maps a relative path -> file contents (str). Parent dirs are made."""
    for rel, content in layout.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


class BuildPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_album_maps_to_genre_artist_album(self):
        _make_tree(self.root, {"Aesop Rock/Skelethon/01.opus": "x"})
        rec = FakeAD(str(self.root / "Aesop Rock/Skelethon"), "Abstract Hip Hop")
        moves, issues, sources = gf.build_plan([rec], self.root)
        self.assertEqual(issues, [])
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0].kind, "dir")
        self.assertEqual(
            moves[0].dst, self.root / "Abstract Hip Hop/Aesop Rock/Skelethon"
        )
        self.assertIn(self.root / "Aesop Rock", sources)

    def test_loose_tracks_go_to_singles_as_file_moves(self):
        _make_tree(
            self.root,
            {
                "J. Cole/track1.mp3": "a",
                "J. Cole/track2.mp3": "b",
                "J. Cole/cover.jpg": "c",
            },
        )
        rec = FakeAD(str(self.root / "J. Cole"), "Conscious Hip Hop")
        moves, issues, sources = gf.build_plan([rec], self.root)
        self.assertEqual(issues, [])
        self.assertTrue(all(m.kind == "file" for m in moves))
        dsts = {m.dst for m in moves}
        self.assertEqual(
            dsts,
            {
                self.root / "Conscious Hip Hop/J. Cole/Singles/track1.mp3",
                self.root / "Conscious Hip Hop/J. Cole/Singles/track2.mp3",
                self.root / "Conscious Hip Hop/J. Cole/Singles/cover.jpg",
            },
        )

    def test_loose_dir_with_subfolder_album_only_moves_loose_files(self):
        # An artist with both a loose single AND an album subfolder: the loose
        # file is its own record; the subfolder is a separate record/genre.
        _make_tree(
            self.root,
            {
                "Denzel Curry/single.mp3": "a",
                "Denzel Curry/TA13OO/01.mp3": "b",
            },
        )
        loose = FakeAD(str(self.root / "Denzel Curry"), "Trap Metal")
        album = FakeAD(str(self.root / "Denzel Curry/TA13OO"), "Experimental Hip Hop")
        moves, issues, _ = gf.build_plan([loose, album], self.root)
        self.assertEqual(issues, [])
        by_dst = {m.dst: m for m in moves}
        # Loose single -> its genre's Singles; subfolder album never dragged along.
        self.assertIn(self.root / "Trap Metal/Denzel Curry/Singles/single.mp3", by_dst)
        self.assertIn(self.root / "Experimental Hip Hop/Denzel Curry/TA13OO", by_dst)
        self.assertNotIn(self.root / "Trap Metal/Denzel Curry/Singles/TA13OO", by_dst)

    def test_artist_level_cover_follows_artist_to_genre(self):
        # The orphan bug: an Artist/cover.jpg beside album subfolders must not be
        # left behind. It follows the artist to its (single) genre, sibling to
        # the album folders — not into a Singles folder.
        _make_tree(
            self.root,
            {
                "AFI/Sing the Sorrow/01.mp3": "a",
                "AFI/Decemberunderground/01.mp3": "b",
                "AFI/cover.jpg": "art",
            },
        )
        recs = [
            FakeAD(str(self.root / "AFI/Sing the Sorrow"), "Post-Hardcore"),
            FakeAD(str(self.root / "AFI/Decemberunderground"), "Post-Hardcore"),
        ]
        moves, issues, _ = gf.build_plan(recs, self.root)
        self.assertEqual(issues, [])  # single genre -> no ambiguity note
        cover = next(m for m in moves if m.src.name == "cover.jpg")
        self.assertEqual(cover.kind, "file")
        self.assertEqual(cover.dst, self.root / "Post-Hardcore/AFI/cover.jpg")

    def test_artist_level_sidecar_uses_dominant_genre_when_split(self):
        # An artist split across genres: the artist-level file goes to the
        # dominant (most-album) genre, and the split is flagged as a NOTE.
        _make_tree(
            self.root,
            {
                "Deftones/White Pony/01.mp3": "a",
                "Deftones/Around the Fur/01.mp3": "b",
                "Deftones/Saturday Night Wrist/01.mp3": "c",
                "Deftones/band.jpg": "art",
            },
        )
        recs = [
            FakeAD(str(self.root / "Deftones/White Pony"), "Alternative Metal"),
            FakeAD(str(self.root / "Deftones/Around the Fur"), "Alternative Metal"),
            FakeAD(str(self.root / "Deftones/Saturday Night Wrist"), "Nu Metal"),
        ]
        moves, issues, _ = gf.build_plan(recs, self.root)
        sidecar = next(m for m in moves if m.src.name == "band.jpg")
        self.assertEqual(sidecar.dst, self.root / "Alternative Metal/Deftones/band.jpg")
        self.assertTrue(any("artist spans 2 genres" in m for m in issues))

    def test_loose_artist_sidecars_stay_in_singles(self):
        # A loose-track artist's direct files (incl. cover) are swept to Singles
        # by the loose pass; the sidecar pass must not also touch them.
        _make_tree(
            self.root, {"Desiigner/01 - Panda.mp3": "a", "Desiigner/cover.jpg": "art"}
        )
        rec = FakeAD(str(self.root / "Desiigner"), "Trap")
        moves, issues, _ = gf.build_plan([rec], self.root)
        self.assertEqual(issues, [])
        dsts = {m.dst for m in moves}
        self.assertIn(self.root / "Trap/Desiigner/Singles/cover.jpg", dsts)
        self.assertNotIn(self.root / "Trap/Desiigner/cover.jpg", dsts)

    def test_artist_with_cover_is_fully_pruned_after_apply(self):
        # End-to-end: the artist folder must be empty (and removed) after the
        # albums and the artist-level cover have all moved out.
        _make_tree(
            self.root,
            {"AFI/Sing the Sorrow/01.mp3": "a", "AFI/cover.jpg": "art"},
        )
        rec = FakeAD(str(self.root / "AFI/Sing the Sorrow"), "Post-Hardcore")
        moves, issues, sources = gf.build_plan([rec], self.root)
        self.assertEqual(issues, [])
        with gf.Runner(self.root / "m.tsv", dry_run=False, quiet=True) as runner:
            gf.execute(moves, sources, runner)
        self.assertFalse((self.root / "AFI").exists())
        self.assertEqual((self.root / "Post-Hardcore/AFI/cover.jpg").read_text(), "art")
        self.assertEqual(
            (self.root / "Post-Hardcore/AFI/Sing the Sorrow/01.mp3").read_text(), "a"
        )

    def test_missing_genre_is_flagged_not_moved(self):
        _make_tree(self.root, {"Mystery/Album/01.opus": "x"})
        rec = FakeAD(str(self.root / "Mystery/Album"), "")
        moves, issues, _ = gf.build_plan([rec], self.root)
        self.assertEqual(moves, [])
        self.assertTrue(any("NO GENRE" in m for m in issues))

    def test_only_genre_filter(self):
        _make_tree(
            self.root,
            {"A/Alb1/01.opus": "x", "B/Alb2/01.opus": "y"},
        )
        recs = [
            FakeAD(str(self.root / "A/Alb1"), "Trap"),
            FakeAD(str(self.root / "B/Alb2"), "Drill"),
        ]
        moves, _, _ = gf.build_plan(recs, self.root, only_genres={"Trap"})
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0].dst, self.root / "Trap/A/Alb1")

    def test_dest_collision_is_flagged(self):
        # Two albums that would land on the same destination path.
        _make_tree(
            self.root,
            {"A/Live/01.opus": "x", "A/Live2/01.opus": "y"},
        )
        # Force a collision by faking identical album folder names via two recs
        # pointing at the same dest is unnatural; instead simulate a pre-existing
        # destination on disk.
        (self.root / "Trap/A/Live").mkdir(parents=True)
        rec = FakeAD(str(self.root / "A/Live"), "Trap")
        moves, issues, _ = gf.build_plan([rec], self.root)
        self.assertEqual(moves, [])
        self.assertTrue(any("DEST EXISTS" in m for m in issues))


class ApplyRevertRoundTripTests(unittest.TestCase):
    """End-to-end on a real temp tree: apply the plan, assert the new layout,
    then revert and assert the original tree is restored byte-for-byte."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.original = {
            "Aesop Rock/Skelethon/01 - ZZZ Top.opus": "song-a",
            "Aesop Rock/Skelethon/cover.jpg": "art",
            "J. Cole/loose single.mp3": "song-b",
            # "The Diplomats" is both a genre and an artist (the real name clash).
            "The Diplomats/Diplomatic Immunity/01.mp3": "song-c",
        }
        _make_tree(self.root, self.original)
        self.records = [
            FakeAD(str(self.root / "Aesop Rock/Skelethon"), "Abstract Hip Hop"),
            FakeAD(str(self.root / "J. Cole"), "Conscious Hip Hop"),
            FakeAD(
                str(self.root / "The Diplomats/Diplomatic Immunity"), "The Diplomats"
            ),
        ]
        self.manifest = self.root / "manifest.tsv"

    def tearDown(self):
        self.tmp.cleanup()

    def _snapshot(self) -> dict:
        return {
            str(p.relative_to(self.root)): p.read_text(encoding="utf-8")
            for p in self.root.rglob("*")
            if p.is_file() and p != self.manifest
        }

    def _apply(self):
        moves, issues, sources = gf.build_plan(self.records, self.root)
        self.assertEqual(issues, [])
        with gf.Runner(self.manifest, dry_run=False, quiet=True) as runner:
            gf.execute(moves, sources, runner)

    def test_applied_layout(self):
        self._apply()
        snap = self._snapshot()
        self.assertEqual(
            snap["Abstract Hip Hop/Aesop Rock/Skelethon/01 - ZZZ Top.opus"], "song-a"
        )
        self.assertEqual(snap["Abstract Hip Hop/Aesop Rock/Skelethon/cover.jpg"], "art")
        # Loose track wrapped in Singles/.
        self.assertEqual(
            snap["Conscious Hip Hop/J. Cole/Singles/loose single.mp3"], "song-b"
        )
        # The name-clash album placed under the genre that shares the artist name.
        self.assertEqual(
            snap["The Diplomats/The Diplomats/Diplomatic Immunity/01.mp3"], "song-c"
        )
        # Original artist folders pruned away.
        self.assertFalse((self.root / "Aesop Rock").exists())
        self.assertFalse((self.root / "J. Cole").exists())

    def test_dry_run_touches_nothing(self):
        before = self._snapshot()
        moves, _, sources = gf.build_plan(self.records, self.root)
        with gf.Runner(self.manifest, dry_run=True, quiet=True) as runner:
            gf.execute(moves, sources, runner)
        self.assertEqual(self._snapshot(), before)
        self.assertFalse(self.manifest.exists())

    def test_dry_run_predicts_pruning_without_removing(self):
        # The prune step must *predict* emptied source dirs in a dry-run (via the
        # virtual-removed set), not read the unchanged disk and report nothing.
        moves, _, sources = gf.build_plan(self.records, self.root)
        with gf.Runner(self.manifest, dry_run=True, quiet=True) as runner:
            gf.execute(moves, sources, runner)
        self.assertGreater(runner.stats["pruned"], 0)
        self.assertTrue((self.root / "Aesop Rock").exists())  # still there

    def test_round_trip_restores_original(self):
        before = self._snapshot()
        self._apply()
        self.assertNotEqual(self._snapshot(), before)
        rc = gf.revert(self.manifest, dry_run=False, quiet=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self._snapshot(), before)
        # Genre trees cleared out by the revert prune.
        self.assertFalse((self.root / "Abstract Hip Hop").exists())
        self.assertFalse((self.root / "Conscious Hip Hop").exists())


class ManifestTests(unittest.TestCase):
    def test_parse_skips_comments_and_blanks(self):
        lines = [
            "# header",
            "",
            "/m/A/Alb\t/m/Genre/A/Alb\t2026-05-30T12:00:00",
            "  ",
        ]
        self.assertEqual(gf.parse_manifest(lines), [("/m/A/Alb", "/m/Genre/A/Alb")])


if __name__ == "__main__":
    unittest.main()
