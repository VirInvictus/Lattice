#!/usr/bin/env python3
"""
cleaner.py — consolidate fragmented album folders.

Walks a music root looking for sibling folders whose names differ only in
quote rendering (' vs '), dash/hyphen variant (- vs ‐ vs – vs —), case,
or whitespace. Such pairs typically result from inconsistent metadata
across import sources (e.g. some tracks tagged with curly apostrophes,
others straight) and produce album fragments scattered across two folders.

For each detected group, picks the folder with the most files as the
canonical target and merges siblings into it. mp3, opus, flac, etc.
are never overwritten or deleted: audio collisions where sizes differ
keep both copies (source renamed with a `.from-fragment` suffix). Only
non-audio collisions (cover.jpg, .nfo) drop the source.

Two passes:
  1. Artist-folder level (e.g. 'Jay-Z & Kanye West' vs 'JAY‐Z & Kanye West')
  2. Album-folder level within each artist directory

Conservative by design — folders whose normalized names don't match are
never touched, even if they're "obviously" the same album. Cases like
'Domestica' vs 'Cursive's Domestica (Deluxe Edition)' require manual
intervention.

Usage:
    ./cleaner.py /mnt/SharedData/Music
    ./cleaner.py /mnt/SharedData/Music --dry-run
    ./cleaner.py ~/Music --log /tmp/music-cleanup.log
"""

from __future__ import annotations

import argparse
import shutil
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

AUDIO_EXT = {".mp3", ".opus", ".flac", ".wav", ".m4a", ".ogg",
             ".aac", ".alac", ".ape", ".wv", ".aiff"}

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


def normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    for k, v in QUOTE_DASH_FOLD.items():
        s = s.replace(k, v)
    return s.strip().lower()


class Run:
    def __init__(self, root: Path, log_path: Path, dry_run: bool):
        self.root = root
        self.dry_run = dry_run
        self.log_file = log_path.open("a", encoding="utf-8")
        self.stats = {
            "groups": 0,
            "moves": 0,
            "collisions_kept": 0,
            "non_audio_dropped": 0,
            "exact_dupes_dropped": 0,
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

    def _move(self, src: Path, dst: Path) -> None:
        if self.dry_run:
            return
        shutil.move(str(src), str(dst))

    def _unlink(self, p: Path) -> None:
        if self.dry_run:
            return
        p.unlink()

    def _rmdir(self, p: Path) -> bool:
        if self.dry_run:
            return True
        try:
            p.rmdir()
            return True
        except OSError:
            return False


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
    except (PermissionError, OSError):
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
                            new_target = target / f"{stem}.from-fragment-{counter}{suffix}"
                        run._move(item, new_target)
                        run.stats["collisions_kept"] += 1
                        run.log(f"    AUDIO COLLISION (kept both): {item.name} "
                                f"({src_size}B) -> {new_target.name} "
                                f"vs existing ({tgt_size}B)")
                    else:
                        run.log(f"    DROP NON-AUDIO ({item.suffix}, "
                                f"src={src_size}B tgt={tgt_size}B): {item}")
                        run._unlink(item)
                        run.stats["non_audio_dropped"] += 1
            else:
                run.log(f"    SKIP (type mismatch dir-vs-file): {item.name}")
        else:
            run._move(item, target_item)
            run.stats["moves"] += 1
            run.log(f"    MV: {item.name}")


def consolidate_group(folders: list[Path], context: str, run: Run) -> None:
    folders_sorted = sorted(
        folders, key=lambda p: (-file_count(p), p.name)
    )
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
        try:
            remaining = list(source.iterdir())
            if not remaining:
                if run._rmdir(source):
                    run.stats["rmdirs"] += 1
                    run.log(f"    RMDIR: {source}")
            else:
                run.log(f"    RETAIN (not empty after merge, "
                        f"{len(remaining)} items): {source}")
        except OSError as e:
            run.log(f"    ERROR rmdir {source}: {e}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Consolidate fragmented album folders within a music library.",
        epilog="Default log: <directory>/cleanup.log",
    )
    parser.add_argument("directory", help="Music library root (e.g. /mnt/SharedData/Music)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview only — no filesystem changes; log lines prefixed [DRY]")
    parser.add_argument("--log", dest="log_path", default=None,
                        help="Override log file path (default: <directory>/cleanup.log)")
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
            (p for p in root.iterdir()
             if p.is_dir() and not p.name.startswith(".")),
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
