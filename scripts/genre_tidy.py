#!/usr/bin/env python3
"""genre_tidy.py — build an artist→genre authority and reconcile a library to it.

A two-phase companion that pairs Lattice (the read-only scanner) with retag.py
(the per-album genre rewriter):

    genre_tidy.py build <library>   # read-only: scan, write an editable TSV map
    genre_tidy.py apply <library>   # destructive: retag albums that disagree

The map (`<library>/genre_map.tsv` by default) is one editable line per artist:

    Artist<TAB>Canonical Genre; Other Allowed Genre; ...

`build` seeds each artist with the single most-common genre across their albums
and leaves a `#` comment for any artist whose albums disagree, so the rows worth
reviewing are obvious. `apply` reads the map, re-scans the library, and for every
album whose genre is not in its artist's allowed set, calls retag.py to overwrite
it to the first (canonical) genre. The first genre is the fix target; the rest
are extra genres that are left untouched (e.g. an artist who legitimately spans
two genres). Blank the genre column to skip an artist entirely.

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

__version__ = "1.0.0"

ALLOWED_SEP = ";"  # separates allowed genres for one artist in the TSV

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
    "# Format:  Artist<TAB>Canonical Genre; Other Allowed Genre; ...",
    "#   - The first genre is the fix target: `apply` overwrites any album whose",
    "#     genre is not in this list to the first genre.",
    f"#   - Add more allowed genres after a '{ALLOWED_SEP}' to leave those albums alone.",
    "#   - Blank the genre column to skip an artist entirely.",
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


# ============================ pure map helpers ============================


class MapEntry(NamedTuple):
    display: str
    canonical: str  # first allowed genre, original case; "" means skip artist
    allowed_norm: frozenset[str]


def canonical_genre(genres: Counter) -> str:
    """Most-common genre string; ties broken by first insertion (Counter order)."""
    return genres.most_common(1)[0][0] if genres else ""


def parse_allowed(field: str) -> list[str]:
    return [g.strip() for g in field.split(ALLOWED_SEP) if g.strip()]


def parse_map(lines) -> dict[str, MapEntry]:
    """Parse TSV lines into {norm_artist: MapEntry}. Skips blank and # lines."""
    entries: dict[str, MapEntry] = {}
    for raw in lines:
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cols = line.split("\t")
        artist = cols[0].strip()
        if not artist:
            continue
        allowed = parse_allowed(cols[1]) if len(cols) > 1 else []
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
    """Format reduced artists into TSV rows, sorted by artist. Artists whose
    albums disagree (or have no genre) get a leading # comment to flag review."""
    rows: list[str] = []
    for key in sorted(reduced, key=lambda k: reduced[k][0].lower()):
        display, genres = reduced[key]
        canonical = canonical_genre(genres)
        if len(genres) > 1:
            breakdown = ", ".join(f"{g}×{n}" for g, n in genres.most_common())
            rows.append(
                f'# {display}: {breakdown}  (append "{ALLOWED_SEP} <genre>" to allow extras)'
            )
        elif not genres:
            rows.append(f"# {display}: no genre tags found")
        rows.append(f"{display}\t{canonical}")
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
    print(f"  {flagged} flagged for review (albums disagree / no genre).")
    print("Review the map, then: genre_tidy.py apply <library> --dry-run")
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
