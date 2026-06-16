#!/usr/bin/env python3
"""apestrip.py — remove stray APEv2 tags from MP3 files, losslessly.

Some MP3s (notably torrent rips) carry an APEv2 tag *in addition to* their ID3
tags. Players that read APEv2 on MP3 (foobar2000, DeaDBeeF) merge the APE values
over the ID3 ones, so a stray APE genre like "Trash Metal" keeps reappearing no
matter how many times you fix the ID3 genre — standard tag editors never touch
the APEv2 block. retag.py removes APEv2 only as a side effect of rewriting the
genre; this is the general stripper.

By default apestrip simply **deletes** the APEv2 block and leaves ID3 untouched.
The whole point is to drop the stray APE values (the genre being the usual
culprit), so absorbing them back into ID3 would defeat the tool — it is exactly
how a bad APE genre ends up baked into ID3.

Pass --keep-metadata to opt in to migration: before deleting the APEv2 tag,
every APE field whose data is **not already in ID3** is copied into the right
ID3 frame, so nothing is lost:

    Year/Date          -> TDRC        Title/Artist/Album    -> TIT2/TPE1/TALB
    Album Artist/Band  -> TPE2        Track/Disc            -> TRCK/TPOS
    Composer/Publisher -> TCOM/TPUB   Comment               -> COMM
    Cover Art (Front)  -> APIC        Unsynced lyrics       -> USLT
    sort orders        -> TSOP/TSO2/TSOT/TSOA
    anything else (MusicBrainz IDs, ISRC, barcode, ReplayGain, ...) -> TXXX:<key>

Two deliberate exceptions hold even under --keep-metadata:
  * Genre is NEVER migrated. ID3 stays authoritative; the APE genre is exactly the
    value we distrust. (If a file has no ID3 genre at all, it is reported, not
    invented.)
  * Rating is NEVER written. APE and POPM use different rating scales, so an
    auto-conversion would corrupt star counts (see rerate.py). APE ratings are
    reported so you can apply them deliberately.

APE genre and ratings are always reported (even without --keep-metadata) so you
can see what is being dropped.

Destructive: it rewrites tags in place. Preview with --dry-run first. MP3-only;
other formats carry their own authoritative tags and are skipped. Recursive over
the given directory, so it handles one album or a whole library. Idempotent: a
file with no APEv2 tag is left untouched.

Usage:
    ./apestrip.py /path/to/album --dry-run
    ./apestrip.py "/mnt/SharedData/Music"
    ./apestrip.py /path/to/album --keep-metadata --yes --log ~/apestrip.log
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import cast

from mutagen.apev2 import APENoHeaderError, APEv2
from mutagen.mp3 import MP3
from mutagen.id3 import (
    APIC,
    COMM,
    ID3,
    Frame,
    TALB,
    TBPM,
    TCOM,
    TCOP,
    TENC,
    TEXT,
    TIT1,
    TIT2,
    TKEY,
    TLAN,
    TPE1,
    TPE2,
    TPOS,
    TPUB,
    TRCK,
    TSOA,
    TSO2,
    TSOP,
    TSOT,
    TSRC,
    TXXX,
    USLT,
    ID3NoHeaderError,
    TDRC,
)

__version__ = "1.1.0"

# APE key (lowercased) -> simple ID3 text frame class. Genre/Rating/Comment/cover/
# lyrics are handled out of band; everything not listed here is preserved via a
# TXXX:<key> passthrough so no datum is ever dropped.
SIMPLE_FRAMES: dict[str, type[Frame]] = {
    "title": TIT2,
    "artist": TPE1,
    "album": TALB,
    "band": TPE2,
    "album artist": TPE2,
    "albumartist": TPE2,
    "track": TRCK,
    "tracknumber": TRCK,
    "disc": TPOS,
    "discnumber": TPOS,
    "year": TDRC,
    "date": TDRC,
    "composer": TCOM,
    "publisher": TPUB,
    "label": TPUB,
    "copyright": TCOP,
    "grouping": TIT1,
    "bpm": TBPM,
    "language": TLAN,
    "isrc": TSRC,
    "initial key": TKEY,
    "encoded by": TENC,
    "lyricist": TEXT,
    "artistsortorder": TSOP,
    "albumartistsortorder": TSO2,
    "titlesortorder": TSOT,
    "albumsortorder": TSOA,
}

_COVER_KEYS = {"cover art (front)", "cover art(front)", "coverart", "cover"}
_LYRICS_KEYS = {"unsynced lyrics", "unsynchronised lyrics", "unsyncedlyrics", "lyrics"}


def classify_ape_field(key: str) -> tuple[str, type[Frame] | str | None]:
    """Map an APE key to an action + payload. The pure brain of the migration.

    Returns one of:
      ("genre", None)            -> never migrated (ID3 authoritative)
      ("rating", None)           -> never written, only reported
      ("comment", None)          -> COMM frame
      ("cover", None)            -> APIC front frame
      ("lyrics", None)           -> USLT frame
      ("frame", <ID3 class>)     -> a simple text frame
      ("txxx", "<desc>")         -> TXXX passthrough (lossless fallback)
    """
    k = key.strip().lower()
    if k == "genre":
        return ("genre", None)
    if k == "rating":
        return ("rating", None)
    if k == "comment":
        return ("comment", None)
    if k in _COVER_KEYS:
        return ("cover", None)
    if k in _LYRICS_KEYS:
        return ("lyrics", None)
    if k in SIMPLE_FRAMES:
        return ("frame", SIMPLE_FRAMES[k])
    return ("txxx", key.strip())


def _ape_text(value) -> str:
    """Text of an APE value; empty string for binary values."""
    try:
        if getattr(value, "kind", 0) == 1:  # BINARY
            return ""
        return str(value)
    except Exception:
        return ""


def _img_mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return "image/jpeg"


def _frame_nonempty(frame) -> bool:
    text = getattr(frame, "text", None)
    if not text:
        return False
    return str(text[0]).strip() != ""


def id3_has_equivalent(
    id3: ID3, action: str, payload: type[Frame] | str | None
) -> bool:
    """True if ID3 already carries the datum, so the APE field is redundant."""
    if action in ("genre", "rating"):
        return True  # handled out of band; never migrated
    if action == "frame":
        fr = id3.get(cast("type[Frame]", payload).__name__)
        return fr is not None and _frame_nonempty(fr)
    if action == "comment":
        return any(k.startswith("COMM") and _frame_nonempty(id3[k]) for k in id3)
    if action == "cover":
        return any(k.startswith("APIC") for k in id3)
    if action == "lyrics":
        return any(k.startswith("USLT") for k in id3)
    if action == "txxx":
        desc = str(payload).lower()
        for k in id3:
            if k.startswith("TXXX:") and k.split(":", 1)[1].lower() == desc:
                if _frame_nonempty(id3[k]):
                    return True
        return False
    return False


def migration_label(action: str, payload: type[Frame] | str | None, value) -> str:
    """Human-readable description of a migration, for preview and logging."""
    if action == "frame":
        return f"{cast('type[Frame]', payload).__name__} = {_ape_text(value)!r}"
    if action == "comment":
        return f"COMM = {_ape_text(value)!r}"
    if action == "lyrics":
        return f"USLT ({len(_ape_text(value))} chars)"
    if action == "cover":
        img = _cover_bytes(value)
        return f"APIC front ({_img_mime(img)}, {len(img)} bytes)"
    if action == "txxx":
        return f"TXXX:{payload} = {_ape_text(value)!r}"
    return "?"


def _cover_bytes(value) -> bytes:
    raw = value.value if getattr(value, "kind", 0) == 1 else b""
    _, sep, img = raw.partition(b"\x00")
    return img if sep else raw


def apply_migration(
    id3: ID3, action: str, payload: type[Frame] | str | None, value
) -> str:
    """Write one APE field into the ID3 object. Returns its migration label."""
    if action == "frame":
        cls = cast("type[Frame]", payload)
        id3.setall(cls.__name__, [cls(encoding=3, text=[_ape_text(value)])])
    elif action == "comment":
        id3.add(COMM(encoding=3, lang="eng", desc="", text=[_ape_text(value)]))
    elif action == "lyrics":
        id3.add(USLT(encoding=3, lang="eng", desc="", text=_ape_text(value)))
    elif action == "cover":
        img = _cover_bytes(value)
        id3.add(APIC(encoding=3, mime=_img_mime(img), type=3, desc="", data=img))
    elif action == "txxx":
        id3.add(TXXX(encoding=3, desc=str(payload), text=[_ape_text(value)]))
    return migration_label(action, payload, value)


@dataclass
class FileResult:
    path: str
    had_ape: bool = False
    migrated: list[tuple[str, str]] = field(default_factory=list)
    redundant: list[str] = field(default_factory=list)
    ratings: list[str] = field(default_factory=list)
    genre_warning: bool = False
    stripped: bool = False
    repaired: bool = False
    malformed: bool = False
    error: str | None = None


def _id3_has_genre(id3: ID3) -> bool:
    fr = id3.get("TCON")
    return fr is not None and _frame_nonempty(fr)


def _has_raw_ape_signature(path: str, window: int = 262144) -> bool:
    """True if an APETAGEX signature is present near the end of the file.

    A cheap second opinion for when mutagen's APEv2 reader raises: a malformed
    tag still leaves the signature on disk, and we would rather flag it than
    pretend the file is clean.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(-min(size, window), os.SEEK_END)
            return b"APETAGEX" in fh.read()
    except OSError:
        return False


_APE_IS_HEADER = 0x80000000


class _RawAPEValue:
    """A stand-in for a mutagen APE value, built from a manually parsed item.

    Only the surface `_ape_text`, `_cover_bytes`, and `apply_migration` rely on
    (`kind`, `value`, `str()`) is implemented, so the normal migration path can
    consume items from a malformed tag mutagen refuses to load.
    """

    def __init__(self, kind: int, data: bytes):
        self.kind = kind
        self.value = data

    def __str__(self) -> str:
        return self.value.decode("utf-8", "replace")


def _parse_raw_ape(data: bytes):
    """Parse a malformed APEv2 tag straight from bytes.

    Returns ``(header_start, excise_end, items)`` where excising
    ``data[header_start:excise_end]`` removes the whole APE block (and any junk
    between its footer and a trailing ID3v1), or ``None`` if no structurally
    consistent tag is found. The consistency check (footer exactly where the
    header's size field points) is what makes the byte surgery safe: it proves
    ``header_start`` is a real tag boundary, not a chance signature in the audio.
    """
    h = data.find(b"APETAGEX")
    if h == -1 or len(data) < h + 32:
        return None
    _ver, size, count, flags = struct.unpack("<IIII", data[h + 8 : h + 24])
    if not (flags & _APE_IS_HEADER):
        return None  # first signature must be the header
    footer = data.rfind(b"APETAGEX")
    if footer != h + size:
        return None  # footer not where the header claims; refuse to touch
    id3v1_start = len(data) - 128 if data[-128:][:3] == b"TAG" else len(data)
    if not (h < footer < id3v1_start):
        return None
    items: dict[str, _RawAPEValue] = {}
    p = h + 32  # items begin after the 32-byte header
    for _ in range(count):
        if p + 8 > footer:
            break
        vlen, vflags = struct.unpack("<II", data[p : p + 8])
        p += 8
        ks = data.find(b"\x00", p)
        if ks == -1 or ks >= footer:
            break
        key = data[p:ks].decode("latin1")
        p = ks + 1
        kind = (vflags >> 1) & 3
        items[key] = _RawAPEValue(1 if kind == 1 else 0, data[p : p + vlen])
        p += vlen
    return h, id3v1_start, items


def repair_file(path: str, dry_run: bool, keep_metadata: bool = False) -> FileResult:
    """Repair a malformed APEv2 tag mutagen cannot parse by excising it.

    Same contract as the normal path: with keep_metadata, sole-source fields are
    migrated into ID3 first (genre is never migrated, ratings are reported);
    without it the block is simply excised. The APE region is then cut out of the
    file bytes directly; the result is written to a temp file, verified (decodes,
    no APE signature survives), and atomically swapped in.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    parsed = _parse_raw_ape(data)
    if parsed is None:
        return FileResult(
            path,
            had_ape=True,
            error="malformed APEv2 tag could not be safely parsed for repair",
        )
    header_start, excise_end, items = parsed

    try:
        id3 = ID3(path)
    except ID3NoHeaderError:
        id3 = ID3()
    except Exception as e:
        return FileResult(path, had_ape=True, error=str(e))

    migrations, redundant, ratings, genre_warning = plan_file(id3, items, keep_metadata)
    result = FileResult(
        path,
        had_ape=True,
        redundant=redundant,
        ratings=ratings,
        genre_warning=genre_warning,
        malformed=True,
    )

    if dry_run:
        result.migrated = [
            (key, migration_label(action, payload, value))
            for key, action, payload, value in migrations
        ]
        return result

    for key, action, payload, value in migrations:
        result.migrated.append((key, apply_migration(id3, action, payload, value)))

    # Everything before header_start (ID3v2 + audio) and the trailing ID3v1 are
    # preserved byte for byte; only the APE block and its trailing junk are cut.
    new = data[:header_start] + data[excise_end:]
    tmp_dir = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=tmp_dir, suffix=".apestrip.tmp")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(new)
            out.flush()
            os.fsync(out.fileno())
        if migrations:
            id3.save(tmp, v2_version=3, v1=2)
        with open(tmp, "rb") as vf:
            if b"APETAGEX" in vf.read():
                raise ValueError("APE signature survived repair")
        if getattr(MP3(tmp).info, "sample_rate", 0) <= 0:
            raise ValueError("repaired file does not decode")
        os.replace(tmp, path)
        result.repaired = True
        result.stripped = True
    except Exception as e:
        if os.path.exists(tmp):
            os.unlink(tmp)
        result.error = f"repair failed: {e}"
    return result


def _handle_malformed(
    path: str, dry_run: bool, repair: bool, keep_metadata: bool = False
) -> FileResult:
    if repair:
        return repair_file(path, dry_run, keep_metadata)
    return FileResult(
        path,
        had_ape=True,
        error="malformed APEv2 tag (mutagen cannot parse); "
        "not stripped (use --repair-malformed)",
    )


def plan_file(id3: ID3, ape: APEv2, keep_metadata: bool = False):
    """Decide, for one file's parsed tags, what to migrate / drop / report.

    Pure: inspects but does not mutate. Returns (migrations, redundant, ratings,
    genre_warning), where migrations is a list of (key, action, payload, value).

    Without keep_metadata the default is a pure strip: nothing is migrated, so
    migrations and redundant come back empty. Genre and rating are still scanned
    so they can be reported (the values being dropped).
    """
    migrations = []
    redundant: list[str] = []
    ratings: list[str] = []
    has_ape_genre = False
    for key in ape.keys():
        value = ape[key]
        action, payload = classify_ape_field(key)
        if action == "genre":
            has_ape_genre = True
            continue
        if action == "rating":
            ratings.append(_ape_text(value))
            continue
        if not keep_metadata:
            continue  # strip-only: drop every other field with the tag
        if id3_has_equivalent(id3, action, payload):
            redundant.append(key)
            continue
        migrations.append((key, action, payload, value))
    genre_warning = has_ape_genre and not _id3_has_genre(id3)
    return migrations, redundant, ratings, genre_warning


def process_file(
    path: str, dry_run: bool, repair: bool = False, keep_metadata: bool = False
) -> FileResult:
    """Plan and (unless dry_run) execute the strip for one MP3."""
    try:
        ape = APEv2(path)
    except APENoHeaderError:
        # mutagen sees no APE tag, but a raw APETAGEX signature can still be
        # present if the tag is malformed (e.g. a footer with the IS_HEADER bit
        # wrongly set). Repair it if asked, otherwise report it rather than
        # silently leaving it behind.
        if _has_raw_ape_signature(path):
            return _handle_malformed(path, dry_run, repair, keep_metadata)
        return FileResult(path, had_ape=False)
    except Exception:  # malformed tag mutagen chokes on
        if _has_raw_ape_signature(path):
            return _handle_malformed(path, dry_run, repair, keep_metadata)
        return FileResult(path, had_ape=True, error="unreadable APEv2 tag")

    try:
        id3 = ID3(path)
    except ID3NoHeaderError:
        id3 = ID3()
    except Exception as e:
        return FileResult(path, had_ape=True, error=str(e))

    migrations, redundant, ratings, genre_warning = plan_file(id3, ape, keep_metadata)
    result = FileResult(
        path,
        had_ape=True,
        redundant=redundant,
        ratings=ratings,
        genre_warning=genre_warning,
    )

    if dry_run:
        result.migrated = [
            (key, migration_label(action, payload, value))
            for key, action, payload, value in migrations
        ]
        return result

    try:
        for key, action, payload, value in migrations:
            label = apply_migration(id3, action, payload, value)
            result.migrated.append((key, label))
        # Delete the APE tag first (retag.py ordering). Re-save ID3 only when we
        # actually migrated something; a pure strip leaves ID3 byte-for-byte.
        APEv2(path).delete()
        if migrations:
            id3.save(path, v2_version=3, v1=2)
        # Verify the APE tag is gone.
        try:
            APEv2(path)
            result.error = "APEv2 tag still present after delete"
        except APENoHeaderError:
            result.stripped = True
    except Exception as e:
        result.error = str(e)
    return result


AUDIO_EXT = ".mp3"


def _iter_mp3s(root: str):
    for dirpath, _, filenames in os.walk(root):
        for fn in sorted(filenames):
            if fn.lower().endswith(AUDIO_EXT):
                yield os.path.join(dirpath, fn)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Losslessly strip stray APEv2 tags from MP3 files."
    )
    parser.add_argument("directory", help="Directory to walk (album or library root)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview migrations and strips; write nothing",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt (auto-skipped when stdin is not a TTY)",
    )
    parser.add_argument(
        "--keep-metadata",
        action="store_true",
        help="Before stripping, migrate APE fields not already in ID3 into the "
        "matching ID3 frame (genre is never migrated, ratings never written). "
        "Off by default: the default is a pure strip that leaves ID3 untouched.",
    )
    parser.add_argument(
        "--repair-malformed",
        action="store_true",
        help="Also repair malformed APE tags mutagen cannot parse, by excising "
        "the tag bytes directly (verified + atomic). Off by default; without it "
        "such files are only reported.",
    )
    parser.add_argument(
        "--log",
        dest="log_path",
        default=None,
        help="Append a timestamped record to this file "
        "(default: <directory>/apestrip.log)",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    args = parser.parse_args()

    root = args.directory
    if not os.path.isdir(root):
        print(f"[!] Directory not found: {root}", file=sys.stderr)
        return 1

    log_path = args.log_path or os.path.join(root, "apestrip.log")
    log_fh = None if args.dry_run else open(log_path, "a", encoding="utf-8")

    def log(msg: str) -> None:
        print(msg)
        if log_fh is not None:
            ts = datetime.now().isoformat(timespec="seconds")
            log_fh.write(f"[{ts}] {msg}\n")

    try:
        # Planning pass: build the worklist without writing.
        worklist: list[FileResult] = []
        for path in _iter_mp3s(root):
            r = process_file(
                path,
                dry_run=True,
                repair=args.repair_malformed,
                keep_metadata=args.keep_metadata,
            )
            if r.had_ape:
                worklist.append(r)

        if not worklist:
            print("No MP3s with an APEv2 tag found. Nothing to do.")
            return 0

        head = "[DRY RUN] " if args.dry_run else ""
        print(f"{head}APEv2 tags found in {len(worklist)} file(s) under {root}\n")
        rating_files = 0
        warn_files = 0
        for r in worklist:
            rel = os.path.relpath(r.path, root)
            if r.error:
                print(f"  [!] {rel}: {r.error}")
                continue
            print(f"  {rel}{'  [repair malformed]' if r.malformed else ''}")
            for key, label in r.migrated:
                print(f"      migrate {key!r} -> {label}")
            if not r.migrated:
                if args.keep_metadata:
                    print("      (APE fully redundant with ID3; strip only)")
                else:
                    print("      (strip only; --keep-metadata to migrate into ID3)")
            for val in r.ratings:
                rating_files += 1
                print(f"      [rating] APE Rating={val!r} (reported, not written)")
            if r.genre_warning:
                warn_files += 1
                print("      [warn] APE genre present but ID3 has no genre; left blank")

        total_migrations = sum(len(r.migrated) for r in worklist)
        print(
            f"\n{head}{len(worklist)} file(s), {total_migrations} field migration(s), "
            f"{rating_files} rating(s) reported, {warn_files} genre warning(s)."
        )

        if args.dry_run:
            print("\nDry run: no files modified.")
            return 0

        # Confirmation, replaygain.py style.
        if not args.yes and sys.stdin.isatty():
            try:
                ans = input("\nStrip these APEv2 tags? [y/N] ").strip().lower()
            except EOFError:
                ans = ""
            if ans not in ("y", "yes"):
                log("Aborted by user; no files modified.")
                return 0

        log(f"Stripping APEv2 from {len(worklist)} file(s) under {root}")
        stripped = 0
        repaired = 0
        errors = 0
        for r0 in worklist:
            r = process_file(
                r0.path,
                dry_run=False,
                repair=args.repair_malformed,
                keep_metadata=args.keep_metadata,
            )
            rel = os.path.relpath(r.path, root)
            if r.error:
                errors += 1
                log(f"  [!] {rel}: {r.error}")
                continue
            if r.stripped:
                stripped += 1
            if r.repaired:
                repaired += 1
            for key, label in r.migrated:
                log(f"  {rel}: migrated {key!r} -> {label}")
            for val in r.ratings:
                log(f"  {rel}: [rating] APE Rating={val!r} (not written)")
            if r.genre_warning:
                log(f"  {rel}: [warn] no ID3 genre after strip")
            if r.repaired:
                log(f"  {rel}: repaired (excised malformed APEv2 tag)")
            else:
                log(f"  {rel}: stripped APEv2 tag")
        log(
            f"-> stripped {stripped} file(s) "
            f"({repaired} via malformed-tag repair); {errors} error(s)."
        )
    finally:
        if log_fh is not None:
            log_fh.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
