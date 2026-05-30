#!/usr/bin/env python3
"""genre_foldermap.py — restructure a library into Genre/Artist/Album/Song.

Reorganizes a flat Artist/Album/Song tree into a Genre/Artist/Album/Song tree by
moving each album folder under a top-level genre directory. The genre is the
album's dominant embedded genre tag, read through Lattice's scanner (the same
aggregation every library/wing mode uses), so placement matches what Lattice
reports. Folder names are preserved verbatim; nothing is retagged.

    genre_foldermap.py <library>            # dry-run: print the move plan only
    genre_foldermap.py <library> --apply    # perform the moves, write a manifest
    genre_foldermap.py --revert <manifest>  # undo a prior run from its manifest

Recommended: tidy your genre tags before running this. Placement uses each
album's *dominant* genre, so an album whose tracks disagree on genre lands under
whichever value wins the count and the rest are not reflected in the tree. A
single, consistent genre per album is ideal; genre_tidy.py is built to enforce
that, so run it first.

Two directory shapes are handled:
  - Artist/Album  -> Genre/Artist/Album        (the whole album dir is moved)
  - Artist (loose tracks directly inside, no album subfolder)
                  -> Genre/Artist/Singles/     (only the loose files move; any
                     album subfolders are separate albums with their own genre)

Safety:
  - Dry-run is the DEFAULT. --apply is required to touch the filesystem.
  - Every performed move is appended to a manifest TSV (src<TAB>dst<TAB>time),
    which --revert replays in reverse. A run is therefore reversible.
  - A destination that already exists is never overwritten; that move is
    reported and skipped. Empty source artist folders are pruned afterward.

Lives in scripts/ (outside the lattice package) because it moves files, which
the package's read-only contract (spec.md) forbids. It reads through lattice;
the package itself stays read-only.

Usage:
    ./genre_foldermap.py /mnt/SharedData/Music
    ./genre_foldermap.py /mnt/SharedData/Music --only-genre "Comedy Rock" --apply
    ./genre_foldermap.py /mnt/SharedData/Music --apply --log ~/foldermap.tsv
    ./genre_foldermap.py --revert ~/foldermap.tsv
"""

import argparse
import shutil
import sys
from collections import Counter, namedtuple
from datetime import datetime
from pathlib import Path

__version__ = "1.1.0"

# Path-component characters forbidden on Windows/NTFS/exFAT (the library often
# lives on a shared NTFS volume), plus the trailing "." / " " rule. Genre names
# are folded to a safe form so a stray ":" or "/" in a tag can't break the tree.
_ILLEGAL_NAME_CHARS = '<>:"/\\|?*'

SINGLES_DIR = "Singles"

# A single planned filesystem move. `kind` is "dir" (a whole album folder) or
# "file" (one loose track/sidecar destined for a Singles folder); it only
# affects how empty-source pruning and logging treat the entry.
Move = namedtuple("Move", "src dst kind")


def sanitize_component(name: str) -> str:
    """Fold a tag value into a filesystem-legal single path component. Forbidden
    characters become spaces, runs of whitespace collapse, and trailing dots or
    spaces (which NTFS rejects) are stripped."""
    cleaned = "".join(" " if c in _ILLEGAL_NAME_CHARS else c for c in name)
    cleaned = " ".join(cleaned.split())
    return cleaned.rstrip(". ") or "Unknown"


def classify(path: Path, root: Path) -> tuple:
    """Decide how a scanned audio directory maps into the new tree, from its
    position under root alone. Returns one of:
        ("album", artist, album)  - an Artist/Album folder (move the whole dir)
        ("loose", artist)         - an Artist folder with loose tracks
        ("skip", reason)          - anything we won't place (e.g. the root)
    The on-disk library is strictly one or two levels deep; a deeper directory
    is mapped by its last two components and flagged so it surfaces in review."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return ("skip", "outside root")
    parts = rel.parts
    if len(parts) == 0:
        return ("skip", "loose audio at library root")
    if len(parts) == 1:
        return ("loose", parts[0])
    return ("album", parts[-2], parts[-1])


def build_plan(records, root: Path, only_genres=None):
    """Turn scanner records (each with `.path` and `.genre`) into a list of
    Moves, plus a list of human-readable issues and the set of source artist
    directories that may be left empty. `only_genres` (a set of genre strings)
    restricts the plan to those genres, supporting a staged rollout.

    Loose-track directories are read from disk here to enumerate the individual
    files to move; album directories move as a single unit. Artist-level
    sidecar files (e.g. an Artist/cover.jpg beside album subfolders) follow the
    artist to its dominant genre so they are not orphaned."""
    moves: list[Move] = []
    issues: list[str] = []
    source_artist_dirs: set[Path] = set()
    seen_dst: dict[Path, Path] = {}
    # Album-record artist dirs and the genres their albums carry, plus the dirs
    # that are themselves loose records — both feed the artist-level sidecar
    # pass below.
    album_artist_genres: dict[Path, Counter] = {}
    loose_dirs: set[Path] = set()

    def add(src: Path, dst: Path, kind: str) -> None:
        prior = seen_dst.get(dst)
        if prior is not None:
            issues.append(
                f"COLLISION (two sources -> one dest): {dst}\n    {prior}\n    {src}"
            )
            return
        if dst.exists():
            issues.append(f"DEST EXISTS (skipped): {src} -> {dst}")
            return
        seen_dst[dst] = src
        moves.append(Move(src, dst, kind))

    for rec in sorted(records, key=lambda r: r.path):
        path = Path(rec.path)
        genre = (rec.genre or "").strip()
        if not genre:
            issues.append(f"NO GENRE (skipped): {path}")
            continue
        if only_genres is not None and genre not in only_genres:
            continue
        safe_genre = sanitize_component(genre)

        kind, *rest = classify(path, root)
        if kind == "skip":
            issues.append(f"SKIP ({rest[0]}): {path}")
            continue
        if kind == "album":
            artist, album = rest
            dst = root / safe_genre / artist / album
            if dst == path:
                continue  # already in place
            add(path, dst, "dir")
            source_artist_dirs.add(path.parent)
            album_artist_genres.setdefault(path.parent, Counter())[genre] += 1
        else:  # loose
            artist = rest[0]
            dst_dir = root / safe_genre / artist / SINGLES_DIR
            loose_files = sorted(p for p in _safe_iterdir(path) if p.is_file())
            for f in loose_files:
                add(f, dst_dir / f.name, "file")
            source_artist_dirs.add(path)
            loose_dirs.add(path)

    # Artist-level sidecars: files (cover art, .nfo, ...) sitting in an Artist/
    # folder beside its album subfolders. They belong to no single album, so
    # moving the albums out would orphan them in an otherwise-empty folder.
    # Relocate them to the artist's folder under its dominant genre. Skipped for
    # dirs that are themselves loose records — their direct files already went
    # to Singles above.
    for artist_dir, genres in album_artist_genres.items():
        if artist_dir in loose_dirs:
            continue
        sidecars = sorted(p for p in _safe_iterdir(artist_dir) if p.is_file())
        if not sidecars:
            continue
        dominant = genres.most_common(1)[0][0]
        if only_genres is not None and dominant not in only_genres:
            continue
        if len(genres) > 1:
            issues.append(
                f"NOTE: {len(sidecars)} artist-level file(s) in {artist_dir.name} "
                f"-> dominant genre {dominant!r} (artist spans {len(genres)} genres)"
            )
        dst_dir = root / sanitize_component(dominant) / artist_dir.name
        for f in sidecars:
            add(f, dst_dir / f.name, "file")

    return moves, issues, source_artist_dirs


def _safe_iterdir(p: Path) -> list[Path]:
    try:
        return list(p.iterdir())
    except OSError:
        return []


# ============================ lattice (read half) ============================


def _import_lattice():
    try:
        from lattice.modes.library import _scan_album_dirs
        from lattice.utils import as_roots, count_audio_files, _make_pbar
    except ImportError as e:
        print(
            f"error: could not import lattice ({e}).\n"
            "Install it (pip install -e . / pipx install .) or run with "
            "PYTHONPATH=src.",
            file=sys.stderr,
        )
        sys.exit(2)
    return _scan_album_dirs, as_roots, count_audio_files, _make_pbar


def scan_album_dirs(directory: Path, quiet: bool):
    scan, as_roots, count_audio_files, make_pbar = _import_lattice()
    roots = as_roots(str(directory))
    pbar = make_pbar(count_audio_files(roots), "Scanning", quiet)
    # Scan against the *current* on-disk layout so the path-fallback fills in
    # any untagged file's artist/album; genre always comes from the tag.
    dirs = scan(roots, "{artist}/{album}", pbar)
    pbar.close()
    return dirs


# ============================ execution ============================


class Runner:
    """Performs (or, in dry-run, narrates) the planned moves and appends each
    real move to an append-only manifest used by --revert."""

    def __init__(self, manifest_path: Path, dry_run: bool, quiet: bool):
        self.manifest_path = manifest_path
        self.dry_run = dry_run
        self.quiet = quiet
        self.mf = None
        self.stats: Counter = Counter()
        # Paths (virtually) removed this run. In a dry-run nothing actually
        # moves, so the prune steps consult this to predict which folders would
        # be emptied — matching the real run's output instead of seeing the
        # unchanged disk.
        self.removed: set[Path] = set()

    def _effective_children(self, directory: Path) -> list[Path]:
        return [c for c in _safe_iterdir(directory) if c not in self.removed]

    def __enter__(self):
        if not self.dry_run:
            fresh = not self.manifest_path.exists()
            self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
            self.mf = self.manifest_path.open("a", encoding="utf-8")
            if fresh:
                self.mf.write("# genre_foldermap manifest — src<TAB>dst<TAB>time\n")
                self.mf.write(
                    "# revert with: genre_foldermap.py --revert <this file>\n"
                )
        return self

    def __exit__(self, *_exc):
        if self.mf:
            self.mf.close()

    def _emit(self, msg: str) -> None:
        if not self.quiet:
            prefix = "[DRY] " if self.dry_run else ""
            print(f"{prefix}{msg}")

    def do_move(self, src: Path, dst: Path, kind: str) -> None:
        self._emit(f"MV {kind}: {src}  ->  {dst}")
        self.stats[f"moved_{kind}"] += 1
        if self.dry_run:
            self.removed.add(src)
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        if self.mf:
            ts = datetime.now().isoformat(timespec="seconds")
            self.mf.write(f"{src}\t{dst}\t{ts}\n")
            self.mf.flush()

    def prune_empty(self, directory: Path) -> None:
        """Remove `directory` and any now-empty subdirectories beneath it.
        Used to clear out source artist folders vacated by the moves; a folder
        that still holds files (or album subfolders not yet moved) is kept."""
        if not directory.is_dir() or directory in self.removed:
            return
        for child in self._effective_children(directory):
            if child.is_dir():
                self.prune_empty(child)
        if not self._effective_children(directory):
            self._emit(f"RMDIR (emptied): {directory}")
            self.stats["pruned"] += 1
            if self.dry_run:
                self.removed.add(directory)
            else:
                try:
                    directory.rmdir()
                except OSError:
                    pass

    def prune_up(self, directory: Path) -> None:
        """Remove `directory` and walk upward, removing each emptied ancestor
        until one still holds entries (the populated library root stops it).
        Used after a revert to clear the now-empty genre/artist/album dirs."""
        cur = directory
        while cur != cur.parent and cur.is_dir() and not self._effective_children(cur):
            self._emit(f"RMDIR (emptied): {cur}")
            self.stats["pruned"] += 1
            parent = cur.parent
            if self.dry_run:
                self.removed.add(cur)
            else:
                try:
                    cur.rmdir()
                except OSError:
                    break
            cur = parent


def execute(moves, source_artist_dirs, runner: Runner) -> None:
    for mv in moves:
        runner.do_move(mv.src, mv.dst, mv.kind)
    # Prune after every move so a loose-track artist folder that also held album
    # subfolders is only removed once those albums have moved out too.
    for d in sorted(source_artist_dirs, key=lambda p: len(p.parts), reverse=True):
        runner.prune_empty(d)


def parse_manifest(lines) -> list[tuple[str, str]]:
    """Parse manifest lines into (src, dst) pairs, skipping comments/blanks."""
    pairs = []
    for raw in lines:
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) >= 2:
            pairs.append((cols[0], cols[1]))
    return pairs


def revert(manifest_path: Path, dry_run: bool, quiet: bool) -> int:
    if not manifest_path.is_file():
        print(f"error: no manifest at {manifest_path}", file=sys.stderr)
        return 1
    pairs = parse_manifest(manifest_path.read_text(encoding="utf-8").splitlines())
    runner = Runner(manifest_path.with_suffix(".revert.tsv"), dry_run, quiet)
    prune_parents: set[Path] = set()
    # Reverse order so nested moves undo cleanly (deepest dst first).
    for src, dst in reversed(pairs):
        srcp, dstp = Path(src), Path(dst)
        if not dstp.exists():
            runner._emit(f"MISSING (already reverted?): {dstp}")
            runner.stats["missing"] += 1
            continue
        if srcp.exists():
            runner._emit(f"SRC EXISTS (skipped): {srcp}")
            runner.stats["src_exists"] += 1
            continue
        runner._emit(f"REVERT: {dstp}  ->  {srcp}")
        runner.stats["reverted"] += 1
        if not dry_run:
            srcp.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dstp), str(srcp))
        prune_parents.add(dstp.parent)
    # Clear out the genre/artist/album dirs vacated by the revert, walking up
    # from each and stopping at the first still-populated ancestor.
    for d in sorted(prune_parents, key=lambda p: len(p.parts), reverse=True):
        runner.prune_up(d)
    _print_summary(runner.stats, dry_run, label="revert", quiet=quiet)
    return 0


# ============================ cli ============================


def _print_summary(
    stats: Counter, dry_run: bool, *, label: str, quiet: bool = False
) -> None:
    if quiet:
        return
    verb = "Would" if dry_run else "Done:"
    print()
    print(
        f"{verb} {label} —",
        ", ".join(f"{k}={v}" for k, v in stats.items()) or "nothing",
    )


def looks_like_library(directory: Path) -> bool:
    """Cheap guard: the root has at least one subdirectory. Prevents pointing
    the tool at an empty or wrong path by mistake."""
    return any(p.is_dir() for p in _safe_iterdir(directory))


def cmd_map(args) -> int:
    directory = Path(args.directory).resolve()
    if not directory.is_dir():
        print(f"error: {directory} is not a directory", file=sys.stderr)
        return 1
    if not looks_like_library(directory):
        print(f"error: {directory} has no subfolders; refusing to run", file=sys.stderr)
        return 1

    only = set(args.only_genre) if args.only_genre else None
    records = scan_album_dirs(directory, args.quiet)
    moves, issues, source_dirs = build_plan(records, directory, only)

    if issues:
        print(f"--- {len(issues)} issue(s) flagged for review ---")
        for msg in issues:
            print(f"  {msg}")
        print()

    if not moves:
        print("No moves to make (everything already in place, or filtered out).")
        return 0

    manifest = (
        Path(args.log_path)
        if args.log_path
        else directory / "genre_foldermap.manifest.tsv"
    )
    with Runner(manifest, dry_run=not args.apply, quiet=args.quiet) as runner:
        execute(moves, source_dirs, runner)
        _print_summary(
            runner.stats, not args.apply, label="reorganize", quiet=args.quiet
        )
        if args.apply:
            print(f"Manifest: {manifest}")
        else:
            print(
                "\nDry run — nothing moved. Re-run with --apply to perform these moves."
            )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Restructure a music library into Genre/Artist/Album/Song.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "directory",
        nargs="?",
        help="Music library root (e.g. /mnt/SharedData/Music)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the moves. Without this flag the tool only prints the plan.",
    )
    parser.add_argument(
        "--only-genre",
        action="append",
        metavar="GENRE",
        help="Restrict to this genre (repeatable). Useful for a staged rollout. "
        "Note: an artist-level sidecar (e.g. cover.jpg) follows its artist's "
        "dominant genre, which may not be one you selected.",
    )
    parser.add_argument(
        "--log",
        dest="log_path",
        default=None,
        help="Manifest path (default: <directory>/genre_foldermap.manifest.tsv)",
    )
    parser.add_argument(
        "--revert",
        metavar="MANIFEST",
        default=None,
        help="Undo a prior run by replaying its manifest in reverse, then exit.",
    )
    parser.add_argument("--quiet", action="store_true", help="Minimize output")
    args = parser.parse_args()

    if args.revert:
        return revert(
            Path(args.revert).resolve(), dry_run=not args.apply, quiet=args.quiet
        )
    if not args.directory:
        parser.error("directory is required (or use --revert MANIFEST)")
    return cmd_map(args)


if __name__ == "__main__":
    sys.exit(main())
