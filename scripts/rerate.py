#!/usr/bin/env python3
"""rerate.py — reconcile DeaDBeeF MP3 star ratings with foobar2000.

DeaDBeeF and foobar2000 both store MP3 ratings in an ID3 POPM frame (a 0-255
byte), but on different scales, so a rating set in one shows up shifted in the
other. Measured on a real library:

    DeaDBeeF 2 stars -> byte 127 -> foobar reads it as 3 stars
    DeaDBeeF 4 stars -> byte 254 -> foobar reads it as 5 stars

foobar's own values (64=2, 196=4, 255=5) are read the same by *both* players
(verified: byte 196 shows 4 stars in DeaDBeeF and foobar alike). So rewriting
DeaDBeeF's odd bytes to the equivalent foobar value makes the two agree without
changing what DeaDBeeF shows:

    127 -> 64   (both then show 2 stars)
    254 -> 196  (both then show 4 stars)

Only those exact bytes are touched. foobar's canonical bytes, MusicBee's values
(186/242, which already read correctly), unrated (0), and every non-MP3 file are
left alone. Vorbis/Opus ratings are clean 0-5 integers and are not affected.

Destructive: it rewrites the POPM byte in place. Preview with --dry-run first.

Usage:
    ./rerate.py /mnt/SharedData/Music --dry-run
    ./rerate.py /mnt/SharedData/Music
    ./rerate.py ~/Music --log ~/rerate.log
"""

import argparse
import os
import sys
from datetime import datetime

from mutagen.id3 import ID3, ID3NoHeaderError

__version__ = "1.0.0"

# DeaDBeeF byte -> foobar/WMP byte that both players read as the same stars.
# The full DeaDBeeF write scale is ~stars*63.75 (so 1->64 would also be 1*, 3->190
# -> 3*); only 127 (2*) and 254 (4*) occur in the library, but extra entries here
# are harmless since each maps to its both-agree foobar value.
REMAP = {127: 64, 254: 196}


def remap_popm(rating: int) -> int | None:
    """The reconciled byte for a POPM rating byte, or None to leave it as is."""
    return REMAP.get(rating)


def rerate_file(filepath: str) -> list[tuple[str, int, int]]:
    """Rewrite any remappable POPM byte in one MP3. Returns the changes made as
    (email, old_byte, new_byte); empty if nothing changed. Never raises."""
    try:
        tags = ID3(filepath)
    except ID3NoHeaderError, Exception:
        return []
    changes: list[tuple[str, int, int]] = []
    for popm in tags.getall("POPM"):
        new = remap_popm(popm.rating)
        if new is not None:
            changes.append((getattr(popm, "email", ""), popm.rating, new))
            popm.rating = new
    if changes:
        # v2.3 + refreshed ID3v1 for the same broad compatibility retag.py uses.
        tags.save(filepath, v2_version=3, v1=2)
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile DeaDBeeF MP3 POPM ratings with foobar2000.",
        epilog="Default log: <directory>/rerate.log",
    )
    parser.add_argument("directory", help="Music library root")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the byte changes per file; write nothing",
    )
    parser.add_argument(
        "--log",
        dest="log_path",
        default=None,
        help="Override log file path (default: <directory>/rerate.log)",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    args = parser.parse_args()

    root = args.directory
    if not os.path.isdir(root):
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 1

    log_path = args.log_path or os.path.join(root, "rerate.log")
    log_fh = open(log_path, "a", encoding="utf-8")

    def log(msg: str = "") -> None:
        if msg:
            ts = datetime.now().isoformat(timespec="seconds")
            prefix = "[DRY] " if args.dry_run else ""
            log_fh.write(f"[{ts}] {prefix}{msg}\n")
        else:
            log_fh.write("\n")
        log_fh.flush()

    scanned = 0
    changed = 0
    by_remap: dict[tuple[int, int], int] = {}
    try:
        mode = "DRY RUN" if args.dry_run else "APPLY"
        log("=" * 70)
        log(f"RERATE RUN START [{mode}]: {root}   map: {REMAP}")
        log("=" * 70)

        for dirpath, _subdirs, files in os.walk(root):
            for f in sorted(files):
                if not f.lower().endswith(".mp3"):
                    continue
                scanned += 1
                path = os.path.join(dirpath, f)
                if args.dry_run:
                    # Preview without writing: read the bytes and report.
                    try:
                        tags = ID3(path)
                    except ID3NoHeaderError, Exception:
                        continue
                    changes = []
                    for p in tags.getall("POPM"):
                        new = remap_popm(p.rating)
                        if new is not None:
                            changes.append((getattr(p, "email", ""), p.rating, new))
                else:
                    changes = rerate_file(path)
                if changes:
                    changed += 1
                    rel = os.path.relpath(path, root)
                    verb = "would rerate" if args.dry_run else "rerated"
                    for email, old, new in changes:
                        log(f"  {verb} {rel}: {old} -> {new}  [{email}]")
                        by_remap[(old, new)] = by_remap.get((old, new), 0) + 1

        log("--- SUMMARY ---")
        log(f"  MP3 files scanned: {scanned}")
        log(f"  files {'that would change' if args.dry_run else 'changed'}: {changed}")
        for (old, new), n in sorted(by_remap.items()):
            log(f"  byte {old} -> {new}: {n}")
        log(f"RERATE RUN END [{mode}]")
        log("=" * 70)
    finally:
        log_fh.close()

    verb = "Would rerate" if args.dry_run else "Rerated"
    print(f"{verb} {changed} of {scanned} MP3 file(s).")
    for (old, new), n in sorted(by_remap.items()):
        print(f"  byte {old} -> {new}: {n}")
    print(f"Log: {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
