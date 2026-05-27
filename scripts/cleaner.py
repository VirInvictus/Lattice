#!/usr/bin/env python3
"""
cleaner.py — consolidate fragmented album folders.

Walks a music root looking for sibling folders whose names differ only in
quote rendering (' vs '), dash/hyphen variant (- vs ‐ vs – vs —), case,
whitespace, or apostrophe presence ("Director's Cut" vs "Directors Cut").
Such pairs typically result from inconsistent metadata
across import sources (e.g. some tracks tagged with curly apostrophes,
others straight) and produce album fragments scattered across two folders.

For each detected group, picks the folder with the most files as the
canonical target and merges siblings into it. mp3, opus, flac, etc.
are never overwritten or deleted: audio collisions where sizes differ
keep both copies (source renamed with a `.from-fragment` suffix). On a
cover-image collision the higher-resolution file is kept; other non-audio
collisions (.nfo, .cue) drop the source. The surviving merged folder is
renamed to its normalized form (ASCII hyphen, straight quotes).

Passes:
  1. Artist-folder level (e.g. 'Jay-Z & Kanye West' vs 'JAY‐Z & Kanye West')
  2. Album-folder level within each artist directory
  3. (only with --normalize-names) rename every remaining folder whose name
     uses non-standard characters to its normalized form

Conservative by design — folders whose normalized names don't match are
never touched, even if they're "obviously" the same album. Cases like
'Domestica' vs 'Cursive's Domestica (Deluxe Edition)' require manual
intervention.

Usage:
    ./cleaner.py /mnt/SharedData/Music
    ./cleaner.py /mnt/SharedData/Music --dry-run
    ./cleaner.py ~/Music --log /tmp/music-cleanup.log
"""

import argparse
import shutil
import struct
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

__version__ = "1.1.1"

AUDIO_EXT = {
    ".mp3",
    ".opus",
    ".flac",
    ".wav",
    ".m4a",
    ".ogg",
    ".aac",
    ".alac",
    ".ape",
    ".wv",
    ".aiff",
}

IMAGE_EXT = {".jpg", ".jpeg", ".png"}

QUOTE_DASH_FOLD = {
    "‘": "'",  # left single quote
    "’": "'",  # right single quote (curly apostrophe)
    "ʼ": "'",  # modifier letter apostrophe
    "“": '"',  # left double quote
    "”": '"',  # right double quote
    "‐": "-",  # hyphen
    "‑": "-",  # non-breaking hyphen
    "‒": "-",  # figure dash
    "–": "-",  # en dash
    "—": "-",  # em dash
    "―": "-",  # horizontal bar
}

# Narrower fold for *display* renaming (canonical_render): only characters that
# are genuinely wrong AND whose ASCII form is legal on every filesystem the
# library may live on. The target is frequently NTFS/exFAT (shared with
# Windows), which forbids a trailing "." and the literal " character. So this
# deliberately leaves alone:
#   - en/em dashes (correct punctuation, e.g. ranges like "85–92")
#   - the ellipsis glyph … ("..." would end a name in dots -> NTFS rejects it)
#   - curly double quotes (straight " is forbidden on Windows)
# Those glyphs are all valid in a path component, so keeping them is safe.
RENDER_FOLD = {
    "‐": "-",  # U+2010 hyphen
    "‑": "-",  # U+2011 non-breaking hyphen
    "‒": "-",  # U+2012 figure dash
    "―": "-",  # U+2015 horizontal bar
    "‘": "'",  # left single quote
    "’": "'",  # right single quote (curly apostrophe)
    "ʼ": "'",  # modifier letter apostrophe
}

# Path-component characters forbidden on Windows/NTFS/exFAT, plus the trailing
# "." / " " rule. A normalized name that would be illegal there is skipped.
_ILLEGAL_NAME_CHARS = set('<>:"/\\|?*')


def is_legal_name(name: str) -> bool:
    return bool(name) and not (_ILLEGAL_NAME_CHARS & set(name)) and name[-1] not in ". "


def normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    for k, v in QUOTE_DASH_FOLD.items():
        s = s.replace(k, v)
    # Fold apostrophe-present vs absent ("Director's Cut" == "Directors Cut") and
    # collapse whitespace so removing a quote can't leave a stray double space.
    s = s.replace("'", "").replace('"', "")
    return " ".join(s.split()).lower()


def canonical_render(s: str) -> str:
    """Normalized *display* rendering of a folder name via RENDER_FOLD: broken
    hyphens and curly single-quotes/apostrophes go to ASCII, and whitespace is
    collapsed. Case, en/em dashes, curly double quotes, the ellipsis glyph, and
    prime marks are preserved (all valid path characters), and no NFKC is
    applied. Deliberately narrower than normalize_name, which folds aggressively
    (NFKC, en/em dashes, apostrophe stripping, lowercasing) for *duplicate
    matching*."""
    for k, v in RENDER_FOLD.items():
        s = s.replace(k, v)
    return " ".join(s.split())


def _get_image_size(data: bytes) -> tuple[int, int] | None:
    """Parse JPEG or PNG dimensions from header bytes without external libraries.
    Ported from lattice.modes.artwork._get_image_size to keep cleaner.py
    self-contained."""
    size = len(data)
    if size >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR":
        w, h = struct.unpack(">LL", data[16:24])
        return w, h
    if size >= 2 and data.startswith(b"\xff\xd8"):
        try:
            i = 2
            while i < size:
                while i < size and data[i] != 0xFF:
                    i += 1
                while i < size and data[i] == 0xFF:
                    i += 1
                if i >= size:
                    break
                marker = data[i]
                i += 1
                if marker == 0x01 or 0xD0 <= marker <= 0xD9:
                    continue
                if i + 2 > size:
                    break
                (length,) = struct.unpack(">H", data[i : i + 2])
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    if i + 7 <= size:
                        h, w = struct.unpack(">HH", data[i + 3 : i + 7])
                        return w, h
                i += length
        except Exception:
            pass
    return None


def image_pixels(path: Path) -> int | None:
    """Pixel count (w*h) of an image file, or None if it is not a parseable
    JPEG/PNG. Used to keep the higher-resolution cover on a collision."""
    try:
        with path.open("rb") as f:
            dims = _get_image_size(f.read())
    except OSError:
        return None
    return dims[0] * dims[1] if dims else None


class Run:
    def __init__(self, root: Path, log_path: Path, dry_run: bool):
        self.root = root
        self.dry_run = dry_run
        self.log_file = log_path.open("a", encoding="utf-8")
        # Paths (virtually) removed this run; lets dry-run emptiness checks
        # predict the real outcome instead of seeing the unchanged filesystem.
        self.removed: set[Path] = set()
        self.stats = {
            "groups": 0,
            "moves": 0,
            "collisions_kept": 0,
            "covers_replaced": 0,
            "non_audio_dropped": 0,
            "exact_dupes_dropped": 0,
            "renamed": 0,
            "rmdirs": 0,
        }

    def log(self, msg: str = "") -> None:
        ts = datetime.now().isoformat(timespec="seconds")
        prefix = "[DRY] " if self.dry_run else ""
        line = f"[{ts}] {prefix}{msg}" if msg else ""
        self.log_file.write(line + "\n")
        self.log_file.flush()

    def close(self) -> None:
        self.log_file.close()

    # ------- filesystem ops with dry-run guards -------

    def _effective_children(self, p: Path) -> list[Path]:
        """Children of p minus anything (virtually) removed this run, so a
        dry-run predicts whether p would really be empty."""
        try:
            return [c for c in p.iterdir() if c not in self.removed]
        except OSError:
            return []

    def _move(self, src: Path, dst: Path) -> None:
        if self.dry_run:
            self.removed.add(src)
            return
        shutil.move(str(src), str(dst))

    def _unlink(self, p: Path) -> None:
        if self.dry_run:
            self.removed.add(p)
            return
        p.unlink()

    def _rmdir(self, p: Path) -> bool:
        if self.dry_run:
            if self._effective_children(p):
                return False
            self.removed.add(p)
            return True
        try:
            p.rmdir()
            return True
        except OSError:
            return False

    def _rename(self, src: Path, dst: Path) -> None:
        if self.dry_run:
            self.removed.add(src)
            return
        src.rename(dst)


def find_groups(directory: Path, run: Run) -> list[list[Path]]:
    """Find groups of subdirs whose names normalize to the same key."""
    if not directory.is_dir():
        return []
    groups: dict[str, list[Path]] = {}
    try:
        for child in directory.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                key = normalize_name(child.name)
                groups.setdefault(key, []).append(child)
    except (PermissionError, OSError) as e:
        run.log(f"  WARN scan {directory}: {e}")
        return []
    return [g for g in groups.values() if len(g) > 1]


def file_count(p: Path) -> int:
    try:
        return sum(1 for _ in p.rglob("*") if _.is_file())
    except PermissionError, OSError:
        return 0


def merge_dir(source: Path, target: Path, run: Run) -> None:
    """Merge source contents into target, recursing into subdirs."""
    for item in list(source.iterdir()):
        target_item = target / item.name
        if target_item.exists():
            if item.is_dir() and target_item.is_dir():
                merge_dir(item, target_item, run)
                if run._rmdir(item):
                    run.stats["rmdirs"] += 1
                    run.log(f"    RMDIR (after recursive merge): {item}")
                else:
                    run.log(f"    RETAIN (subdir not empty): {item}")
            elif item.is_file() and target_item.is_file():
                src_size = item.stat().st_size
                tgt_size = target_item.stat().st_size
                same_size = src_size == tgt_size
                if same_size:
                    run.log(f"    DROP DUPE (identical size, {src_size}B): {item}")
                    run._unlink(item)
                    run.stats["exact_dupes_dropped"] += 1
                else:
                    if item.suffix.lower() in AUDIO_EXT:
                        stem = item.stem
                        suffix = item.suffix
                        new_target = target / f"{stem}.from-fragment{suffix}"
                        counter = 1
                        while new_target.exists():
                            counter += 1
                            new_target = (
                                target / f"{stem}.from-fragment-{counter}{suffix}"
                            )
                        run._move(item, new_target)
                        run.stats["collisions_kept"] += 1
                        run.log(
                            f"    AUDIO COLLISION (kept both): {item.name} "
                            f"({src_size}B) -> {new_target.name} "
                            f"vs existing ({tgt_size}B)"
                        )
                    elif item.suffix.lower() in IMAGE_EXT:
                        # Keep the better cover instead of blindly keeping
                        # canonical's: more pixels wins, ties (or unparseable)
                        # fall back to larger bytes.
                        src_px = image_pixels(item)
                        tgt_px = image_pixels(target_item)
                        if src_px is not None and tgt_px is not None:
                            source_wins = (src_px, src_size) > (tgt_px, tgt_size)
                        else:
                            source_wins = src_size > tgt_size
                        if source_wins:
                            run._unlink(target_item)
                            run._move(item, target_item)
                            run.stats["covers_replaced"] += 1
                            run.log(
                                f"    REPLACE IMAGE (higher-res source kept): "
                                f"{item.name}  src={src_px}px/{src_size}B "
                                f"tgt={tgt_px}px/{tgt_size}B"
                            )
                        else:
                            run._unlink(item)
                            run.stats["non_audio_dropped"] += 1
                            run.log(
                                f"    DROP IMAGE (canonical higher-res): "
                                f"{item.name}  src={src_px}px/{src_size}B "
                                f"tgt={tgt_px}px/{tgt_size}B"
                            )
                    else:
                        run.log(
                            f"    DROP NON-AUDIO ({item.suffix}, "
                            f"src={src_size}B tgt={tgt_size}B): {item}"
                        )
                        run._unlink(item)
                        run.stats["non_audio_dropped"] += 1
            else:
                run.log(f"    SKIP (type mismatch dir-vs-file): {item.name}")
        else:
            run._move(item, target_item)
            run.stats["moves"] += 1
            run.log(f"    MV: {item.name}")


def _normalize_folder_name(folder: Path, run: Run) -> Path:
    """Rename `folder` to its canonical_render when they differ, guarding a
    collision with an existing different folder. Returns the (possibly new)
    path. Used for both merge survivors and the --normalize-names sweep."""
    target_name = canonical_render(folder.name)
    if target_name == folder.name:
        return folder
    if not is_legal_name(target_name):
        run.log(f"    SKIP RENAME (illegal target name): {folder.name}")
        return folder
    dst = folder.parent / target_name
    if dst.exists() and dst != folder:
        run.log(f"    RETAIN NAME (normalized target exists): {folder.name}")
        return folder
    try:
        run._rename(folder, dst)
    except OSError as e:
        # A bad rename (e.g. a filesystem-rejected name) logs and is skipped;
        # it must never abort the whole run mid-way.
        run.log(f"    ERROR rename {folder.name} -> {target_name}: {e}")
        return folder
    run.stats["renamed"] += 1
    run.log(f"    RENAME: {folder.name}  ->  {target_name}")
    return dst


def consolidate_group(folders: list[Path], context: str, run: Run) -> None:
    folders_sorted = sorted(folders, key=lambda p: (-file_count(p), p.name))
    canonical = folders_sorted[0]
    sources = folders_sorted[1:]
    run.log(f"  GROUP @ {context}")
    run.log(f"    canonical: {canonical.name}  ({file_count(canonical)} files)")
    for s in sources:
        run.log(f"    source:    {s.name}  ({file_count(s)} files)")
    run.stats["groups"] += 1

    for source in sources:
        run.log(f"  MERGING: {source.name}  ->  {canonical.name}")
        merge_dir(source, canonical, run)
        remaining = run._effective_children(source)
        if not remaining:
            if run._rmdir(source):
                run.stats["rmdirs"] += 1
                run.log(f"    RMDIR: {source}")
        else:
            run.log(
                f"    RETAIN (not empty after merge, {len(remaining)} items): {source}"
            )

    # The folder with the most files won as canonical, but its name may be the
    # less-standard variant (unicode hyphen, curly quote); normalize the survivor.
    _normalize_folder_name(canonical, run)


def normalize_tree(root: Path, run: Run) -> None:
    """Rename every artist/album folder whose name is not its canonical_render.
    Albums first, then the artist folder, so child paths stay valid. Rename-only:
    merging duplicates is Passes 1-2' job."""
    artists = sorted(
        (p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")),
        key=lambda p: p.name.lower(),
    )
    for artist_dir in artists:
        try:
            albums = [
                a
                for a in artist_dir.iterdir()
                if a.is_dir() and not a.name.startswith(".")
            ]
        except OSError as e:
            run.log(f"  WARN scan {artist_dir}: {e}")
            albums = []
        for album_dir in sorted(albums, key=lambda p: p.name.lower()):
            _normalize_folder_name(album_dir, run)
        _normalize_folder_name(artist_dir, run)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Consolidate fragmented album folders within a music library.",
        epilog="Default log: <directory>/cleanup.log",
    )
    parser.add_argument(
        "directory", help="Music library root (e.g. /mnt/SharedData/Music)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only — no filesystem changes; log lines prefixed [DRY]",
    )
    parser.add_argument(
        "--log",
        dest="log_path",
        default=None,
        help="Override log file path (default: <directory>/cleanup.log)",
    )
    parser.add_argument(
        "--normalize-names",
        action="store_true",
        help="Also rename non-duplicate folders with non-standard characters "
        "(unicode dashes, curly quotes) to their normalized ASCII/straight form",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    args = parser.parse_args()

    root = Path(args.directory).resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 1

    log_path = Path(args.log_path) if args.log_path else root / "cleanup.log"
    run = Run(root, log_path, dry_run=args.dry_run)

    try:
        run.log("=" * 70)
        mode = "DRY RUN" if args.dry_run else "APPLY"
        run.log(f"CLEANUP RUN START [{mode}]: {root}")
        run.log("=" * 70)

        run.log("\n--- PASS 1: artist-level consolidation ---")
        artist_groups = find_groups(root, run)
        run.log(f"detected {len(artist_groups)} artist group(s)")
        for group in artist_groups:
            consolidate_group(group, context="artists", run=run)

        run.log("\n--- PASS 2: album-level consolidation per artist ---")
        artists = sorted(
            (p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")),
            key=lambda p: p.name.lower(),
        )
        scanned = 0
        for artist_dir in artists:
            album_groups = find_groups(artist_dir, run)
            if not album_groups:
                continue
            scanned += 1
            for group in album_groups:
                consolidate_group(group, context=artist_dir.name, run=run)
        run.log(f"album-level consolidation touched {scanned} artist(s)")

        if args.normalize_names:
            run.log("\n--- PASS 3: normalize folder names ---")
            normalize_tree(root, run)

        run.log("\n--- SUMMARY ---")
        for k, v in run.stats.items():
            run.log(f"  {k}: {v}")
        run.log(f"CLEANUP RUN END [{mode}]")
        run.log("=" * 70 + "\n")
    finally:
        run.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
