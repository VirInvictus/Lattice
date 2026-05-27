#!/usr/bin/env python3
"""genre_tidy.py — build an artist→genre authority and reconcile a library to it.

A two-phase companion that pairs Lattice (the read-only scanner) with retag.py
(the per-album genre rewriter):

    genre_tidy.py build <library>   # read-only: scan, write an editable TSV map
    genre_tidy.py apply <library>   # destructive: retag albums that disagree

The map (`<library>/genre_map.tsv` by default) is one editable tab-separated
line per artist, listing every genre that artist is allowed to carry:

    Artist<TAB>Genre<TAB>Second Genre<TAB>...

`build` seeds each line with every genre the artist currently uses (most-common
first), so `apply` is a no-op until you edit. To tidy, REMOVE a stray genre from
a line: `apply` re-scans, and for every album whose genre is no longer on its
artist's line, calls retag.py to overwrite it to the first (canonical) genre.
Reorder the line to change the fix target; leave only the artist (no genres) to
skip that artist entirely. Multi-genre artists also get a `#` comment with the
per-genre counts, so low-count strays worth trimming stand out.

Lives in scripts/ (outside the lattice package) because it mutates tags, which
the package's read-only contract (spec.md §5) forbids. It reads through lattice
and writes through retag.py; the package itself stays read-only.

Usage:
    ./genre_tidy.py build /mnt/SharedData/Music
    ./genre_tidy.py apply /mnt/SharedData/Music --dry-run
    ./genre_tidy.py apply /mnt/SharedData/Music --map ~/genres.tsv --log ~/tidy.log
"""

import argparse
import os
import subprocess
import sys
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

__version__ = "1.2.0"

# Folds curly quotes, dash variants, and case so artist/genre strings compare
# the way a human reads them. Mirrors audit._norm_key / cleaner.normalize_name;
# kept local because companion scripts stay self-contained for normalization.
_QUOTE_DASH_FOLD = {
    "‘": "'",
    "’": "'",
    "ʼ": "'",
    "“": '"',
    "”": '"',
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "–": "-",
    "—": "-",
    "―": "-",
}

TSV_HEADER = [
    "# Lattice genre authority map (genre_tidy.py)",
    "# Format:  Artist<TAB>Genre<TAB>Second Genre<TAB>...   (tab-separated)",
    "#   - Every genre on the line is ALLOWED for that artist; albums tagged",
    "#     with any of them are left untouched.",
    "#   - The FIRST genre is the fix target: `apply` retags any album whose",
    "#     genre is NOT on the line to that first genre.",
    "#   - `build` lists every genre an artist currently uses, so `apply`",
    "#     changes nothing until you edit. To tidy, REMOVE a stray genre from a",
    "#     line and its albums collapse to the first genre; reorder to retarget.",
    "#   - Leave only the artist (no genres) to skip that artist entirely.",
    "#   - Compilation album-artists (Various Artists) are flagged EXCLUDED and",
    "#     never enforced: a comp has no single canonical genre.",
    "#   - Lines starting with # are comments.",
    "",
]


def norm(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    for k, v in _QUOTE_DASH_FOLD.items():
        s = s.replace(k, v)
    return " ".join(s.split()).lower()


# Compilation album-artists: a "Various Artists" disc collects unrelated tracks
# with no single canonical genre, so there is nothing to enforce. build flags
# these for manual review (a commented line, no data row) and apply never
# retags them. (TagBundle.artist already prefers the album-artist tag, so this
# keys off album-artist, not the per-track performer.)
EXCLUDED_ARTISTS = {"various artists", "various", "va"}


# ============================ pure map helpers ============================


class MapEntry(NamedTuple):
    display: str
    canonical: str  # first allowed genre, original case; "" means skip artist
    allowed_norm: frozenset[str]


def parse_map(lines) -> dict[str, MapEntry]:
    """Parse TSV lines into {norm_artist: MapEntry}. Skips blank and # lines.
    Columns after the artist are the allowed genres; the first is canonical."""
    entries: dict[str, MapEntry] = {}
    for raw in lines:
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cols = line.split("\t")
        artist = cols[0].strip()
        if not artist:
            continue
        allowed = [c.strip() for c in cols[1:] if c.strip()]
        entries[norm(artist)] = MapEntry(
            display=artist,
            canonical=allowed[0] if allowed else "",
            allowed_norm=frozenset(norm(a) for a in allowed),
        )
    return entries


def is_compliant(album_genre: str | None, allowed_norm: frozenset[str]) -> bool:
    """True when the album's genre is one of the artist's allowed genres. An
    empty/missing genre is never compliant, so `apply` fills it with canonical."""
    return norm(album_genre) in allowed_norm


def retag_argv(
    retag_path: Path, album_path: str, canonical: str, *, dry_run: bool
) -> list[str]:
    """Build the retag.py invocation. A '/'-joined canonical is split into the
    separate genre values Lattice treats as a multi-genre album."""
    genres = [g.strip() for g in canonical.split("/") if g.strip()]
    argv = [sys.executable, str(retag_path), album_path, *genres]
    if dry_run:
        argv.append("--dry-run")
    return argv


def reduce_artists(album_dirs) -> dict[str, tuple[str, Counter]]:
    """Collapse scanned album dirs to {norm_artist: (display_name, genre Counter)}.
    Genres are weighted by album count; the display name is the most-common
    spelling seen for that artist."""
    display_counts: dict[str, Counter] = {}
    genre_counts: dict[str, Counter] = {}
    for ad in album_dirs:
        if not ad.artist:
            continue
        key = norm(ad.artist)
        if not key:
            continue
        display_counts.setdefault(key, Counter())[ad.artist] += 1
        gc = genre_counts.setdefault(key, Counter())
        if ad.genre:
            gc[ad.genre] += 1
    return {
        key: (display_counts[key].most_common(1)[0][0], genre_counts[key])
        for key in display_counts
    }


def build_rows(reduced: dict[str, tuple[str, Counter]]) -> list[str]:
    """Format reduced artists into TSV rows, sorted by artist. The line lists
    every genre the artist currently uses (most-frequent first = canonical).
    Multi-genre artists also get a leading # comment with the per-genre counts,
    so low-count strays worth trimming stand out."""
    rows: list[str] = []
    for key in sorted(reduced, key=lambda k: reduced[k][0].lower()):
        display, genres = reduced[key]
        ordered = [g for g, _ in genres.most_common()]
        if key in EXCLUDED_ARTISTS:
            spread = ", ".join(f"{g}×{n}" for g, n in genres.most_common())
            rows.append(
                f"# {display}: EXCLUDED (compilation): {spread or 'no genre tags'}. "
                f"Not enforced; investigate manually."
            )
            continue
        if len(ordered) > 1:
            breakdown = ", ".join(f"{g}×{n}" for g, n in genres.most_common())
            rows.append(f"# {display}: {len(ordered)} genres: {breakdown}")
        elif not ordered:
            rows.append(f"# {display}: no genre tags found")
        rows.append("\t".join([display, *ordered]))
    return rows


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
    dirs = scan(roots, "{artist}/{album}", pbar)
    pbar.close()
    return dirs


# ============================ logging (apply) ============================


class _Log:
    """Append-only timestamped log, file-only (matches cleaner.py's logger)."""

    def __init__(self, path: Path, dry_run: bool):
        self.path = path
        self.dry_run = dry_run
        self.fh = path.open("a", encoding="utf-8")

    def write(self, msg: str = "") -> None:
        if msg:
            ts = datetime.now().isoformat(timespec="seconds")
            prefix = "[DRY] " if self.dry_run else ""
            self.fh.write(f"[{ts}] {prefix}{msg}\n")
        else:
            self.fh.write("\n")
        self.fh.flush()

    def close(self) -> None:
        self.fh.close()


# ============================ subcommands ============================


def cmd_build(args) -> int:
    directory = Path(args.directory).resolve()
    if not directory.is_dir():
        print(f"error: {directory} is not a directory", file=sys.stderr)
        return 1

    map_path = Path(args.map_path) if args.map_path else directory / "genre_map.tsv"
    reduced = reduce_artists(scan_album_dirs(directory, args.quiet))

    if map_path.exists():
        existing = map_path.read_text(encoding="utf-8").splitlines()
        present = set(parse_map(existing).keys())
        new = {k: v for k, v in reduced.items() if k not in present}
        if not new:
            print(f"No new artists; {map_path} unchanged ({len(present)} artists).")
            return 0
        added = build_rows(new)
        stamp = datetime.now().date().isoformat()
        map_path.write_text(
            "\n".join(existing + ["", f"# --- added {stamp} ---"] + added) + "\n",
            encoding="utf-8",
        )
        print(f"Appended {len(new)} new artist(s) to {map_path}.")
        return 0

    rows = build_rows(reduced)
    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text("\n".join(TSV_HEADER + rows) + "\n", encoding="utf-8")
    flagged = sum(1 for r in rows if r.startswith("#"))
    print(f"Wrote {len(reduced)} artists to {map_path}.")
    print(f"  {flagged} artist(s) carry multiple genres (commented with counts).")
    print("Trim any stray genres, then: genre_tidy.py apply <library> --dry-run")
    return 0


def cmd_apply(args) -> int:
    directory = Path(args.directory).resolve()
    if not directory.is_dir():
        print(f"error: {directory} is not a directory", file=sys.stderr)
        return 1

    map_path = Path(args.map_path) if args.map_path else directory / "genre_map.tsv"
    if not map_path.exists():
        print(
            f"error: no map at {map_path}. Run `genre_tidy.py build` first.",
            file=sys.stderr,
        )
        return 1

    retag_path = Path(__file__).resolve().parent / "retag.py"
    if not retag_path.exists():
        print(f"error: retag.py not found at {retag_path}", file=sys.stderr)
        return 1

    entries = parse_map(map_path.read_text(encoding="utf-8").splitlines())
    album_dirs = scan_album_dirs(directory, args.quiet)
    log_path = Path(args.log_path) if args.log_path else directory / "genre_tidy.log"

    log = _Log(log_path, args.dry_run)
    stats: Counter = Counter()
    try:
        mode = "DRY RUN" if args.dry_run else "APPLY"
        log.write("=" * 70)
        log.write(f"GENRE TIDY [{mode}]: {directory}")
        log.write(f"map: {map_path}  ({len(entries)} artists)")
        log.write("=" * 70)

        for ad in sorted(album_dirs, key=lambda a: a.path):
            rel = os.path.relpath(ad.path, directory)
            if norm(ad.artist) in EXCLUDED_ARTISTS:
                stats["excluded"] += 1
                log.write(f"  SKIP (compilation/various-artists): {rel}  [{ad.artist}]")
                continue
            entry = entries.get(norm(ad.artist))
            if entry is None:
                stats["unmapped"] += 1
                log.write(f"  SKIP (artist not in map): {rel}  [{ad.artist}]")
                continue
            if not entry.canonical:
                stats["skipped_blank"] += 1
                log.write(f"  SKIP (artist genre blanked): {rel}")
                continue
            if is_compliant(ad.genre, entry.allowed_norm):
                stats["ok"] += 1
                continue

            stats["retagged"] += 1
            log.write(f"  RETAG {rel}: {ad.genre or '(none)'!r} -> {entry.canonical}")
            result = subprocess.run(
                retag_argv(retag_path, ad.path, entry.canonical, dry_run=args.dry_run),
                capture_output=True,
                text=True,
            )
            for line in result.stdout.splitlines():
                log.write(f"      {line}")
            if result.returncode != 0:
                stats["errors"] += 1
                for line in result.stderr.splitlines():
                    log.write(f"      ERR {line}")

        log.write("--- SUMMARY ---")
        for key, value in stats.items():
            log.write(f"  {key}: {value}")
        log.write(f"GENRE TIDY END [{mode}]")
        log.write("=" * 70)
    finally:
        log.close()

    verb = "would retag" if args.dry_run else "retagged"
    print(f"\n{verb} {stats['retagged']} album(s); {stats['ok']} already compliant.")
    if stats["unmapped"]:
        print(f"  {stats['unmapped']} album(s) skipped (artist not in map).")
    if stats["skipped_blank"]:
        print(f"  {stats['skipped_blank']} album(s) skipped (artist genre blanked).")
    if stats["excluded"]:
        print(
            f"  {stats['excluded']} album(s) skipped (compilation / various-artists)."
        )
    if stats["errors"]:
        print(f"  {stats['errors']} retag error(s) — see log.")
    print(f"Log: {log_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build and enforce an artist→genre authority map for a music library."
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser(
        "build", help="Scan (read-only) and write the artist→genre TSV map"
    )
    b.add_argument("directory", help="Music library root")
    b.add_argument(
        "--map",
        dest="map_path",
        default=None,
        help="Map path (default: <directory>/genre_map.tsv)",
    )
    b.add_argument("--quiet", action="store_true", help="Minimize progress output")
    b.set_defaults(func=cmd_build)

    a = sub.add_parser(
        "apply", help="Retag albums that disagree with the map (destructive)"
    )
    a.add_argument("directory", help="Music library root")
    a.add_argument(
        "--map",
        dest="map_path",
        default=None,
        help="Map path (default: <directory>/genre_map.tsv)",
    )
    a.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview retag calls; write nothing (log lines prefixed [DRY])",
    )
    a.add_argument(
        "--log",
        dest="log_path",
        default=None,
        help="Log path (default: <directory>/genre_tidy.log)",
    )
    a.add_argument("--quiet", action="store_true", help="Minimize progress output")
    a.set_defaults(func=cmd_apply)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
