#!/usr/bin/env python3
"""genre_foldermap.py — restructure a library into Genre/Artist/Album/Song.

Reorganizes a flat Artist/Album/Song tree into a Genre/Artist/Album/Song tree by
moving each album folder under a top-level genre directory. The genre is the
album's dominant embedded genre tag, read through Lattice's scanner (the same
aggregation every library/wing mode uses), so placement matches what Lattice
reports. Folder names are preserved verbatim; nothing is retagged.

    genre_foldermap.py <library>                    # dry-run: print the plan only
    genre_foldermap.py <library> --apply            # perform the moves + manifest
    genre_foldermap.py --revert <manifest> --apply  # undo a prior run
    genre_foldermap.py --revert <manifest>          # preview that undo (dry-run)

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
Disc subfolders (Artist/Album/CD1, Disc 2, ...) are recognized and collapse to
their parent album, which moves as one unit with the discs inside; they never
read as an organized album under a genre named after the artist.

Albums already at Genre/Artist/Album (including their disc subfolders) are left
in place; if their folder genre disagrees with their tags, that is reported as
a NOTE, never silently re-filed.

A staging folder (default "Unfiltered") is transparent: its leading component is
stripped before classification, so Unfiltered/Artist/Album is filed into the real
taxonomy at the library root exactly like a flat Artist/Album stray, rather than
mistaking "Unfiltered" for a genre. This is for an inbox where a tagger (e.g.
Picard) dumps Artist folders so they stay out of the organized root. The staging
folder itself is left in place after its contents move out. Pass --staging "" to
disable, or --staging NAME to use a different inbox name.

Placement is gated by the library's own genre vocabulary: the set of genres the
scan found already organized at Genre/Artist/Album depth. (It is derived from
the scan records, not from the folder listing, so a genre folder holding no
readable audio at scan time drops out of the gate for that run.) A stray whose
tag genre isn't already one of those, compared case-insensitively, is flagged,
not filed into a brand-new top-level folder (pass --allow-new-genre to permit
creating one); a case-variant match reuses the existing folder's spelling. On a
flat library with no genre folders yet, the set is empty and gating is off, so
tags are trusted; this is the original flat -> genre conversion. The same gate
doubles as a wrong-root guard: aim the tool one level too high and almost
nothing matches the (absent) vocabulary, so it flags everything instead of
relocating the tree.

Safety:
  - Dry-run is the DEFAULT. --apply is required to touch the filesystem, for
    --revert exactly as for a forward run.
  - Every performed move is appended to a manifest TSV (src<TAB>dst<TAB>time),
    which --revert (plus --apply) replays in reverse. Files and moved folders
    are restored; source folders that were pruned empty are not recreated.
  - A destination that already exists is never overwritten; that move is
    reported and skipped. Empty source artist folders are pruned afterward.
  - Moves are same-filesystem only (`mv` semantics): a cross-device move would
    silently degrade to copy+delete and rewrite audio bytes, so it is refused.
  - A directory deeper than Genre/Artist/Album is flagged TOO DEEP and skipped
    rather than guessed at, so a wrong root can't silently move everything.

Lives in scripts/ (outside the lattice package) because it moves files, which
the package's read-only contract (spec.md) forbids. It reads through lattice;
the package itself stays read-only.

Usage:
    ./genre_foldermap.py /mnt/SharedData/Music
    ./genre_foldermap.py /mnt/SharedData/Music --only-genre "Comedy Rock" --apply
    ./genre_foldermap.py /mnt/SharedData/Music --apply --log ~/foldermap.tsv
    ./genre_foldermap.py --revert ~/foldermap.tsv --apply
"""

import argparse
import re
import shutil
import sys
from collections import Counter, namedtuple
from datetime import datetime
from pathlib import Path

__version__ = "1.3.3"

# Path-component characters forbidden on Windows/NTFS/exFAT (the library often
# lives on a shared NTFS volume), plus the trailing "." / " " rule. Genre names
# are folded to a safe form so a stray ":" or "/" in a tag can't break the tree.
_ILLEGAL_NAME_CHARS = '<>:"/\\|?*'

SINGLES_DIR = "Singles"

# Default name of the staging inbox at the library root. Artist folders dumped
# here (e.g. by Picard) are filed into the real taxonomy instead of being read
# as a genre folder. Overridable via --staging; pass "" to disable.
STAGING_DIR = "Unfiltered"

# Disc subfolders of a multi-disc album (Album/CD1, Album/Disc 2, ...). The
# scanner emits one record per audio-bearing dir, so these arrive as their own
# records one level deeper than the album; classify collapses them to the
# parent album so the album moves as a unit and the artist name never reads as
# a genre folder.
DISC_DIR_RE = re.compile(r"(?i)^(?:cd|disc|disk|dvd|side|vinyl)[\s._-]*\d+$")

# A single planned filesystem move. `kind` is "dir" (a whole album folder) or
# "file" (one loose track/sidecar destined for a Singles folder); it only
# affects the emitted MV label and which stats counter the move lands in.
Move = namedtuple("Move", "src dst kind")


def sanitize_component(name: str) -> str:
    """Fold a tag value into a filesystem-legal single path component. Forbidden
    characters become spaces, runs of whitespace collapse, and trailing dots or
    spaces (which NTFS rejects) are stripped."""
    cleaned = "".join(" " if c in _ILLEGAL_NAME_CHARS else c for c in name)
    cleaned = " ".join(cleaned.split())
    return cleaned.rstrip(". ") or "Unknown"


def classify(path: Path, root: Path, staging: str | None = None) -> tuple:
    """Decide how a scanned audio directory maps into the new tree, from its
    depth under root. A `staging` folder name (e.g. "Unfiltered") is stripped
    from the front of the path first, so an album dumped at staging/Artist/Album
    classifies as the flat-stray ("album", Artist, Album) and is filed into the
    real taxonomy, not read as a genre folder. Returns one of:
        ("album", artist, album)             - Artist/Album (depth 2): a stray
                                               flat album to file under a genre.
                                               A disc subfolder (Artist/Album/CD1)
                                               collapses to this too; the parent
                                               album dir is the unit that moves
        ("organized", genre, artist, album)  - Genre/Artist/Album (depth 3), or
                                               a disc subfolder of one (depth 4):
                                               already in the genre tree
        ("loose", artist)                    - Artist/ with loose tracks (depth 1)
        ("toodeep", depth)                   - deeper than Genre/Artist/Album:
                                               not placed (usually the wrong root)
        ("staged-toodeep", depth)            - inside the staging inbox but deeper
                                               than Artist/Album (+discs): nothing
                                               in the inbox is ever "organized",
                                               so it is flagged, left in place
        ("skip", reason)                     - anything we won't place (e.g. root)
    The intended library is Genre/Artist/Album; a flat Artist/Album library is
    the input this tool converts. Anything deeper is flagged rather than guessed
    at, so pointing the tool one level too high can't silently relocate the
    whole tree."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return ("skip", "outside root")
    parts = rel.parts
    staged = False
    if staging and parts and parts[0] == staging:
        parts = parts[1:]
        staged = True
    if len(parts) == 0:
        return ("skip", "loose audio at library root")
    if len(parts) == 1:
        return ("loose", parts[0])
    if len(parts) == 2:
        return ("album", parts[0], parts[1])
    if len(parts) == 3 and DISC_DIR_RE.match(parts[2]):
        # A disc subfolder of a flat album: the parent Artist/Album dir is the
        # unit that moves, discs riding along inside it.
        return ("album", parts[0], parts[1])
    if staged:
        return ("staged-toodeep", len(parts))
    if len(parts) == 3:
        return ("organized", parts[0], parts[1], parts[2])
    if len(parts) == 4 and DISC_DIR_RE.match(parts[3]):
        # A disc subfolder of an organized album IS that album, not TOO DEEP.
        return ("organized", parts[0], parts[1], parts[2])
    return ("toodeep", len(parts))


def build_plan(
    records, root: Path, only_genres=None, allow_new_genre=False, staging=None
):
    """Turn scanner records (each with `.path` and `.genre`) into a list of
    Moves, plus a list of human-readable issues and the set of source artist
    directories that may be left empty. `only_genres` (a set of genre strings)
    restricts the plan to those genres, supporting a staged rollout.

    Placement is gated by the library's existing genre vocabulary: the genres
    of albums this scan found already organized at Genre/Artist/Album depth
    (record-derived, so a genre folder with no readable audio this run drops
    out of the gate). Matching is case-insensitive and a match reuses the
    existing folder's spelling; a stray whose tag genre isn't in the vocabulary
    is flagged, not filed into a new top-level folder, unless `allow_new_genre`
    is set. When no genre folders exist yet (a flat library) the set is empty
    and gating is off.

    `staging` (a folder name like "Unfiltered") is passed through to classify so
    albums dumped in that inbox are filed into the real taxonomy at the root, not
    read as a genre. The inbox's per-artist source dirs are pruned when emptied,
    but the inbox folder itself is never a source dir, so it is left in place.

    Loose-track directories are read from disk here to enumerate the individual
    files to move; album directories move as a single unit. Artist-level
    sidecar files (e.g. an Artist/cover.jpg beside album subfolders) follow the
    artist to its dominant genre so they are not orphaned."""
    moves: list[Move] = []
    issues: list[str] = []
    source_artist_dirs: set[Path] = set()
    seen_dst: dict[Path, Path] = {}
    seen_src: dict[Path, Path] = {}
    # Album-record artist dirs and the genres their albums carry, plus the dirs
    # that are themselves loose records — both feed the artist-level sidecar
    # pass below.
    album_artist_genres: dict[Path, Counter] = {}
    loose_dirs: set[Path] = set()

    def add(src: Path, dst: Path, kind: str) -> bool:
        """Plan one move. Returns True only when a Move was actually appended,
        so callers can gate their bookkeeping (sidecar/prune registration) on
        it instead of acting on a rejected move."""
        if any("\t" in s or "\n" in s for s in (str(src), str(dst))):
            # A tab/newline in a path would corrupt the manifest TSV, making
            # the move unrevertable; refuse it (rename the folder first).
            issues.append(f"UNSAFE NAME (tab/newline in path; skipped): {src}")
            return False
        prior_dst = seen_src.get(src)
        if prior_dst is not None:
            # Disc records of one album resolve to the same source unit: the
            # same (src, dst) pair is one move, a differing dst means the
            # discs' genre tags disagree (first record wins, conflict flagged).
            if prior_dst != dst:
                issues.append(
                    f"DISC GENRE MISMATCH (kept first): {src}\n"
                    f"    {prior_dst}\n    {dst}"
                )
            return False
        prior = seen_dst.get(dst)
        if prior is not None:
            issues.append(
                f"COLLISION (two sources -> one dest): {dst}\n    {prior}\n    {src}"
            )
            return False
        if dst.exists():
            seen_src[src] = dst  # remember the rejection so discs don't re-flag
            issues.append(f"DEST EXISTS (skipped): {src} -> {dst}")
            return False
        seen_src[src] = dst
        seen_dst[dst] = src
        moves.append(Move(src, dst, kind))
        return True

    # First pass: classify every record once and learn the library's existing
    # genre vocabulary from the albums already at Genre/Artist/Album depth. That
    # derived set gates placement below; an empty set (a flat library) disables
    # gating so the original flat -> genre conversion still works.
    classified = [
        (
            Path(rec.path),
            (rec.genre or "").strip(),
            classify(Path(rec.path), root, staging),
        )
        for rec in sorted(records, key=lambda r: r.path)
    ]
    allowed_genres = {info[1] for _p, _g, info in classified if info[0] == "organized"}
    gating = bool(allowed_genres) and not allow_new_genre
    # Case-insensitive view of the vocabulary: a stray tagged "hip hop" files
    # into an existing "Hip Hop" folder (reusing its spelling) instead of being
    # flagged UNKNOWN or, worse, minting a case-variant duplicate top level.
    allowed_by_fold = {g.casefold(): g for g in sorted(allowed_genres)}

    def vocab_spelling(safe_genre: str) -> str | None:
        return allowed_by_fold.get(safe_genre.casefold())

    for path, genre, info in classified:
        kind = info[0]
        if kind == "skip":
            issues.append(f"SKIP ({info[1]}): {path}")
            continue
        if kind == "toodeep":
            issues.append(
                f"TOO DEEP (skipped): {path}\n"
                f"    {info[1]} levels under root; expected Genre/Artist/Album. "
                "Wrong root?"
            )
            continue
        if kind == "staged-toodeep":
            issues.append(
                f"STAGED TOO DEEP (left in inbox): {path}\n"
                f"    {info[1]} levels under the inbox; expected Artist/Album "
                "(discs inside the album folder)."
            )
            continue
        if not genre:
            issues.append(f"NO GENRE (skipped): {path}")
            continue
        if only_genres is not None and genre not in only_genres:
            continue
        safe_genre = sanitize_component(genre)

        if kind == "organized":
            current_genre = info[1]
            if safe_genre.casefold() == current_genre.casefold():
                continue  # already filed under its genre (case-insensitively)
            issues.append(
                f"NOTE: filed under {current_genre!r} but tags say {genre!r} "
                f"(left in place): {path}"
            )
            continue

        # A genre the library already uses (any casing) reuses the existing
        # folder's spelling; one it doesn't is refused a new top-level folder
        # unless explicitly allowed.
        existing = vocab_spelling(safe_genre)
        if existing is not None:
            safe_genre = existing
        elif gating:
            issues.append(
                f"UNKNOWN GENRE {genre!r} (skipped): {path}\n"
                "    not an existing library genre; pass --allow-new-genre to create it."
            )
            continue

        if kind == "album":
            artist, album = info[1], info[2]
            rel = path.relative_to(root).parts
            if staging and rel and rel[0] == staging:
                rel = rel[1:]
            # A depth-3 record here is a disc subfolder (classify collapsed it
            # to its parent album); the parent dir is the unit that moves.
            src = path.parent if len(rel) == 3 else path
            dst = root / safe_genre / artist / album
            if dst == src:
                # Only reachable when the genre folder name equals the staging
                # inbox name (dst recomputes to the same path inside the inbox).
                continue
            # Bookkeeping only for a move that was actually planned: a
            # rejected album must not have its artist-level sidecars moved
            # out from under it, or its source dir registered for pruning.
            if add(src, dst, "dir"):
                source_artist_dirs.add(src.parent)
                album_artist_genres.setdefault(src.parent, Counter())[genre] += 1
        else:  # loose
            artist = info[1]
            dst_dir = root / safe_genre / artist / SINGLES_DIR
            loose_files = sorted(p for p in _safe_iterdir(path) if p.is_file())
            planned = sum(1 for f in loose_files if add(f, dst_dir / f.name, "file"))
            if planned:
                source_artist_dirs.add(path)
            # Marked regardless: the sidecar pass must not re-handle a loose
            # dir's direct files even when their moves were all rejected.
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
        safe_dom = sanitize_component(dominant)
        dst_dir = root / (vocab_spelling(safe_dom) or safe_dom) / artist_dir.name
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
        # Paths (virtually) removed/created this run. In a dry-run nothing
        # actually moves, so the prune steps consult these to predict which
        # folders would really be emptied — a revert's restored paths count as
        # children of their (still-empty-on-disk) original parent.
        self.removed: set[Path] = set()
        self.created: set[Path] = set()

    def _effective_children(self, directory: Path) -> list[Path]:
        kids = [c for c in _safe_iterdir(directory) if c not in self.removed]
        kids += [c for c in self.created if c.parent == directory and c not in kids]
        return kids

    def __enter__(self):
        if not self.dry_run:
            fresh = not self.manifest_path.exists()
            self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
            self.mf = self.manifest_path.open("a", encoding="utf-8")
            if fresh:
                self.mf.write("# genre_foldermap manifest — src<TAB>dst<TAB>time\n")
                self.mf.write(
                    "# revert with: genre_foldermap.py --revert <this file> --apply\n"
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
        if not self.dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            # shutil.move silently degrades to copy+delete across devices,
            # against the mv-only contract (audio bytes are never rewritten);
            # refuse instead of copying.
            if src.stat().st_dev != dst.parent.stat().st_dev:
                self._emit(f"CROSS-DEVICE (refused): {src}  ->  {dst}")
                self.stats["cross_device_refused"] += 1
                return
        self._emit(f"MV {kind}: {src}  ->  {dst}")
        self.stats[f"moved_{kind}"] += 1
        if self.dry_run:
            self.removed.add(src)
            self.created.add(dst)
            return
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
    # One prune pass after ALL moves (a source dir is only removable once every
    # album inside it has moved out), deepest-first so a nested source empties
    # before its parent is considered.
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
    # The Runner context gives a revert the same guarantees as an apply: each
    # restore goes through do_move, so it is logged, appended to the
    # .revert.tsv manifest (itself replayable), counted, and — in a dry-run —
    # recorded in `removed` so prune_up predicts the prunes apply performs.
    with Runner(manifest_path.with_suffix(".revert.tsv"), dry_run, quiet) as runner:
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
            runner.do_move(dstp, srcp, "revert")
            prune_parents.add(dstp.parent)
        # Clear out the genre/artist/album dirs vacated by the revert, walking
        # up from each and stopping at the first still-populated ancestor.
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
    staging = args.staging or None
    records = scan_album_dirs(directory, args.quiet)
    moves, issues, source_dirs = build_plan(
        records, directory, only, args.allow_new_genre, staging
    )

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
        "--staging",
        metavar="DIR",
        default=STAGING_DIR,
        help=f"Name of a top-level staging inbox (default: {STAGING_DIR!r}) whose "
        "Artist/Album contents are filed into the real taxonomy instead of being "
        "read as a genre. The inbox folder itself is left in place. Pass an empty "
        "string to disable.",
    )
    parser.add_argument(
        "--allow-new-genre",
        action="store_true",
        help="Permit creating a new top-level genre folder when an album's genre "
        "isn't one the library already uses. Without it, such albums are flagged "
        "and skipped; the library's existing genre folders gate placement.",
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
        help="Undo a prior run by replaying its manifest in reverse, then exit. "
        "Dry-run by default, like a forward run: add --apply to execute.",
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
