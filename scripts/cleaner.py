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
are never overwritten or deleted: a colliding file is dropped as a
duplicate only when size AND sampled bytes (first/last 64 KiB) match;
any other audio collision keeps both copies (source renamed with a
`.from-fragment` suffix). On a cover-image collision the higher-resolution
file is kept; other non-audio collisions (.nfo, .cue) drop the source. The
surviving merged folder is renamed to its normalized form (ASCII hyphen,
straight quotes).

Passes:
  1. Artist-folder level (e.g. 'Jay-Z & Kanye West' vs 'JAY‐Z & Kanye West')
  2. Album-folder level within each artist directory
  3. (--normalize-names / --normalize-filenames) rename every remaining folder
     (any depth) and/or audio file whose name uses non-standard characters to
     its normalized form
  4. (--normalize-tags) library-wide tag normalization, all formats
     (MP3/FLAC/Ogg/Opus/m4a/WMA; other audio is reported and skipped).
     Title/album get a pure typographic fold everywhere (the words never
     change, so the folder is never an authority for them). Artist/albumartist
     fold the same way, except under a merged or renamed artist folder, where
     they are restamped to the surviving folder name: the folder depth of the
     artist component is read from --layout (default {artist}/{album}), and
     the survivor is the naming authority, so a merged-in variant like
     'Bonnie Prince Billy' (no quotes) and a CP1252-mojibake
     'Bonnie \x93Prince\x94 Billy' both become the survivor's
     "Bonnie 'Prince' Billy" in the tags. Guest credits are kept
     ("... feat. X"); the punctuation is folded to straight ASCII. Two players
     read APEv2 over ID3, so run apestrip.py first if a stray APE tag is in
     play.

Conservative by design — folders whose normalized names don't match are
never touched, even if they're "obviously" the same album. Cases like
'Domestica' vs 'Cursive's Domestica (Deluxe Edition)' require manual
intervention.

Dry-run fidelity: existence/size checks go through virtual-aware views of the
filesystem (removals AND creations this run would have made), so the preview's
decisions and stats match the apply run. Residual limitation: the *contents*
of a folder that only virtually moved are not modeled recursively, so a
pathological chain of merges-into-merged-folders may still preview
imperfectly; every normal fragment/rename shape is exact.

Usage:
    ./cleaner.py /mnt/SharedData/Music
    ./cleaner.py /mnt/SharedData/Music --dry-run
    ./cleaner.py ~/Music --log /tmp/music-cleanup.log
"""

import argparse
import os
import re
import shutil
import struct
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

try:
    from mutagen.asf import ASF
    from mutagen.flac import FLAC
    from mutagen.id3 import ID3, ID3NoHeaderError, TALB, TIT2, TPE1, TPE2
    from mutagen.mp4 import MP4
    from mutagen.oggopus import OggOpus
    from mutagen.oggvorbis import OggVorbis

    MUTAGEN_OK = True
except ImportError:  # tag normalization (--normalize-tags) needs mutagen
    MUTAGEN_OK = False

__version__ = "1.3.3"

# Containers whose title/album/artist/albumartist the tag pass can rewrite. Other
# AUDIO_EXT members (.wav/.aac/.alac/.ape/.wv/.aiff) carry no handled tag layout
# and are reported + skipped rather than silently no-oped.
TAGGABLE_EXT = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".mp4", ".wma"}

# Superset of the package's config.AUDIO_EXTENSIONS plus rarer lossless
# containers (.alac/.ape/.wv/.aiff) — a collision on any of these must keep both
# copies. ".mp4" is deliberately absent (ambiguous with video, so a colliding
# .mp4 is treated as non-audio) while staying taggable above.
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
    ".wma",
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


# CP1252 bytes read back as Latin-1 land on these C1 code points; together with
# the genuine Unicode curly punctuation they fold so a tag like
# "Bonnie \x93Prince\x94 Billy" or "Tim O\x92Brien" comes out clean.
#
# Deliberately narrow, matching canonical_render's philosophy: only the genuinely
# wrong glyphs go to ASCII. En/em dashes and the ellipsis are CORRECT typography
# (e.g. the range "85–92") and are preserved; a CP1252-mojibake dash/ellipsis is
# repaired to the real glyph, not flattened to a hyphen. Tags are not path
# components, so curly double quotes do fold to straight " (legal in a tag).
_TAG_FOLD = {
    # CP1252 C1 bytes (read back as Latin-1) -> intended glyph.
    0x91: "'",
    0x92: "'",
    0x93: '"',
    0x94: '"',
    0x96: "–",  # en dash (kept as a real en dash, not a hyphen)
    0x97: "—",  # em dash
    0x85: "…",  # ellipsis
    # Curly quotes/apostrophes -> straight ASCII.
    0x2018: "'",
    0x2019: "'",
    0x02BC: "'",  # modifier letter apostrophe
    0x201C: '"',
    0x201D: '"',
    # Genuinely broken hyphens -> ASCII hyphen. En/em dashes + ellipsis preserved.
    0x2010: "-",
    0x2011: "-",
    0x2012: "-",
    0x2015: "-",
}
# The marker needs a real token boundary on its left: a bare \s* is zero-width,
# which let the "ft" ending "Left"/"Swift"/"Croft" match and corrupt clean tags.
_FEAT_RE = re.compile(r"(?<!\w)(?:feat\.?|ft\.?|featuring)\s+(.*)$", re.I)


def tag_fold(s: str) -> str:
    """Fold CP1252 mojibake, curly quotes, and broken hyphens in a tag value to
    their clean form, collapsing whitespace. En/em dashes, ellipsis, and primes
    are preserved (correct typography), matching canonical_render's narrow fold;
    unlike it, curly double quotes go to straight " since tags are not paths."""
    return " ".join(s.translate(_TAG_FOLD).split())


def canon_track_artist(raw: str, canonical: str) -> str:
    """Track-level artist for a file under a folder whose artist is `canonical`.
    Collapses the band name to `canonical` but keeps a trailing guest credit
    ("... feat. X"), folding the guest's punctuation."""
    folded = tag_fold(raw)
    m = _FEAT_RE.search(folded)
    if m is None:
        return canonical
    guest = m.group(1).strip()
    # A "(feat. X)" credit leaves its closing paren on the guest; drop it.
    if guest.endswith(")") and guest.count(")") > guest.count("("):
        guest = guest[:-1].rstrip()
    return f"{canonical} feat. {guest}"


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
    def __init__(
        self,
        root: Path,
        log_path: Path,
        dry_run: bool,
        normalize_tags: bool = False,
        artist_depth: int = 1,
    ):
        self.root = root
        self.dry_run = dry_run
        self.normalize_tags = normalize_tags
        # Path depth (components below root) at which a folder names an artist,
        # derived from --layout. Only folders at this depth seed the tag pass.
        self.artist_depth = artist_depth
        self.log_file = log_path.open("a", encoding="utf-8")
        # Paths (virtually) removed/created this run; lets dry-run existence
        # and emptiness checks predict the real outcome instead of seeing the
        # unchanged filesystem. `created` maps each virtual destination to the
        # real on-disk path currently holding its bytes, so size/kind checks
        # against a not-yet-moved file still read real data.
        self.removed: set[Path] = set()
        self.created: dict[Path, Path] = {}
        # (canonical_artist_name, [folders to walk]) seeded by merges/renames;
        # consumed by the Pass-4 tag pass. Folders are filtered for existence at
        # walk time, so dry-run (sources still present) and apply (sources gone,
        # everything under the survivor) both resolve to the same file set.
        self.tag_targets: list[tuple[str, list[Path]]] = []
        self.stats = {
            "groups": 0,
            "moves": 0,
            "collisions_kept": 0,
            "covers_replaced": 0,
            "non_audio_dropped": 0,
            "exact_dupes_dropped": 0,
            "renamed": 0,
            "rmdirs": 0,
            "files_renamed": 0,
            "tags_rewritten": 0,
            "tag_files_scanned": 0,
            "tag_unsupported_skipped": 0,
            "tag_no_id3_skipped": 0,
        }

    def _is_artist_level(self, p: Path) -> bool:
        try:
            return len(p.relative_to(self.root).parts) == self.artist_depth
        except ValueError:
            return False

    def _record_tag_target(self, name: str, folders: list[Path]) -> None:
        if self.normalize_tags:
            self.tag_targets.append((tag_fold(name), folders))

    def log(self, msg: str = "") -> None:
        ts = datetime.now().isoformat(timespec="seconds")
        prefix = "[DRY] " if self.dry_run else ""
        # A leading newline (the pass headers) becomes its own blank line, so
        # the timestamp prefix is never orphaned onto an empty line.
        if msg.startswith("\n"):
            self.log_file.write("\n")
            msg = msg.lstrip("\n")
        line = f"[{ts}] {prefix}{msg}" if msg else ""
        self.log_file.write(line + "\n")
        self.log_file.flush()

    def close(self) -> None:
        self.log_file.close()

    # ------- filesystem ops with dry-run guards -------

    def _effective_children(self, p: Path) -> list[Path]:
        """Children of p adjusted for this run's virtual removals/creations,
        so a dry-run predicts whether p would really be empty."""
        try:
            kids = [c for c in p.iterdir() if c not in self.removed]
        except OSError:
            return []
        kids += [c for c in self.created if c.parent == p and c not in kids]
        return kids

    # Virtual-aware filesystem views: identical to the plain calls during an
    # apply run (removed/created stay empty), but a dry-run sees the state the
    # apply run would have produced so far, which keeps collision and rename
    # decisions (and therefore the stats) identical between the two.

    def _real(self, p: Path) -> Path:
        """The on-disk path currently holding p's bytes (p itself unless p is
        a virtual destination of this dry-run)."""
        return self.created.get(p, p)

    def _exists(self, p: Path) -> bool:
        return p in self.created or (p.exists() and p not in self.removed)

    def _is_file(self, p: Path) -> bool:
        real = self.created.get(p)
        if real is not None:
            return real.is_file()
        return p.is_file() and p not in self.removed

    def _is_dir(self, p: Path) -> bool:
        real = self.created.get(p)
        if real is not None:
            return real.is_dir()
        return p.is_dir() and p not in self.removed

    def _size(self, p: Path) -> int:
        return self._real(p).stat().st_size

    def _move(self, src: Path, dst: Path) -> None:
        if self.dry_run:
            origin = self.created.pop(src, src)
            self.removed.add(src)
            self.created[dst] = origin
            return
        shutil.move(str(src), str(dst))

    def _unlink(self, p: Path) -> None:
        if self.dry_run:
            self.removed.add(p)
            self.created.pop(p, None)
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
            origin = self.created.pop(src, src)
            self.removed.add(src)
            self.created[dst] = origin
            return
        src.rename(dst)


def find_groups(directory: Path, run: Run) -> list[list[Path]]:
    """Find groups of subdirs whose names normalize to the same key."""
    if not directory.is_dir():
        return []
    groups: dict[str, list[Path]] = {}
    try:
        for child in directory.iterdir():
            if child in run.removed:
                continue  # dry-run: merged away by an earlier pass
            if child.is_dir() and not child.name.startswith("."):
                key = normalize_name(child.name)
                groups.setdefault(key, []).append(child)
    except (PermissionError, OSError) as e:
        run.log(f"  WARN scan {directory}: {e}")
        return []
    return [g for g in groups.values() if len(g) > 1]


def file_count(p: Path) -> int:
    try:
        return sum(1 for f in p.rglob("*") if f.is_file())
    except OSError:  # PermissionError is an OSError subclass
        return 0


def head_tail_equal(a: Path, b: Path, chunk: int = 65536) -> bool:
    """Cheap content check for same-size files: equal first and last `chunk`
    bytes. Not a full compare — a difference confined to the middle of two
    same-size files slips through — but it catches re-encodes, retags, and
    truncation-with-padding that a size-only check calls "identical"."""
    try:
        size = a.stat().st_size
        with a.open("rb") as fa, b.open("rb") as fb:
            if fa.read(chunk) != fb.read(chunk):
                return False
            if size > chunk:
                fa.seek(max(0, size - chunk))
                fb.seek(max(0, size - chunk))
                return fa.read(chunk) == fb.read(chunk)
        return True
    except OSError:
        return False


def merge_dir(source: Path, target: Path, run: Run) -> None:
    """Merge source contents into target, recursing into subdirs. Existence
    and size checks go through the Run's virtual-aware views so a dry-run
    makes the same decisions the apply run will."""
    for item in list(source.iterdir()):
        target_item = target / item.name
        if run._exists(target_item):
            if item.is_dir() and run._is_dir(target_item):
                merge_dir(item, target_item, run)
                if run._rmdir(item):
                    run.stats["rmdirs"] += 1
                    run.log(f"    RMDIR (after recursive merge): {item}")
                else:
                    run.log(f"    RETAIN (subdir not empty): {item}")
            elif item.is_file() and run._is_file(target_item):
                src_size = item.stat().st_size
                tgt_size = run._size(target_item)
                identical = src_size == tgt_size and head_tail_equal(
                    item, run._real(target_item)
                )
                if identical:
                    run.log(
                        f"    DROP DUPE (identical size + sampled bytes, "
                        f"{src_size}B): {item}"
                    )
                    run._unlink(item)
                    run.stats["exact_dupes_dropped"] += 1
                else:
                    if item.suffix.lower() in AUDIO_EXT:
                        stem = item.stem
                        suffix = item.suffix
                        new_target = target / f"{stem}.from-fragment{suffix}"
                        counter = 1
                        while run._exists(new_target):
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
                        tgt_px = image_pixels(run._real(target_item))
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


def _rename_to(path: Path, target_name: str, run: Run, *, kind: str) -> Path:
    """Shared rename-with-guards for folder and file normalization: legality
    check, virtual-aware collision guard (in a dry-run a merged-away source
    still exists on disk, and an earlier virtual rename may already occupy the
    target), error containment (a filesystem-rejected name logs and is skipped,
    never aborting the run), stats and logging. Returns the (possibly
    unchanged) path."""
    folder_kind = kind == "folder"
    if target_name == path.name:
        return path
    if not is_legal_name(target_name):
        label = "RENAME" if folder_kind else "RENAME FILE"
        run.log(f"    SKIP {label} (illegal target name): {path.name}")
        return path
    dst = path.parent / target_name
    if run._exists(dst) and dst != path:
        retain = "RETAIN NAME" if folder_kind else "RETAIN FILE NAME"
        run.log(f"    {retain} (normalized target exists): {path.name}")
        return path
    try:
        run._rename(path, dst)
    except OSError as e:
        noun = "" if folder_kind else "file "
        run.log(f"    ERROR rename {noun}{path.name} -> {target_name}: {e}")
        return path
    if folder_kind:
        run.stats["renamed"] += 1
        run.log(f"    RENAME: {path.name}  ->  {target_name}")
    else:
        run.stats["files_renamed"] += 1
        run.log(f"    RENAME FILE: {path.name}  ->  {target_name}")
    return dst


def _normalize_folder_name(folder: Path, run: Run, record_tags: bool = True) -> Path:
    """Rename `folder` to its canonical_render when they differ. Used for both
    merge survivors and the --normalize-names sweep. A renamed artist folder is
    also a tag target for Pass 3 sweeps; Pass 1/2 survivor renames pass
    record_tags=False because consolidate_group records its own entry (which
    also includes the merged sources), so the survivor isn't recorded twice."""
    dst = _rename_to(folder, canonical_render(folder.name), run, kind="folder")
    if (
        record_tags
        and dst != folder
        and run.normalize_tags
        and run._is_artist_level(dst)
    ):
        # dst exists after an apply rename, the original after a dry-run one;
        # both are offered to walk.
        run._record_tag_target(dst.name, [dst, folder])
    return dst


def consolidate_group(folders: list[Path], context: str, run: Run) -> None:
    counts = {p: file_count(p) for p in folders}  # one rglob walk per folder
    folders_sorted = sorted(folders, key=lambda p: (-counts[p], p.name))
    canonical = folders_sorted[0]
    sources = folders_sorted[1:]
    run.log(f"  GROUP @ {context}")
    run.log(f"    canonical: {canonical.name}  ({counts[canonical]} files)")
    for s in sources:
        run.log(f"    source:    {s.name}  ({counts[s]} files)")
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
    # less-standard variant (unicode hyphen, curly quote); normalize the
    # survivor. record_tags=False: the recording below covers the rename case
    # too and also carries the sources.
    survivor = _normalize_folder_name(canonical, run, record_tags=False)

    # Seed the tag pass when the survivor is an artist folder: its name is the
    # naming authority for every track merged under it. Include the survivor's
    # pre-rename path and the sources so a dry-run (nothing has moved yet) still
    # walks the same files an apply run would find consolidated under survivor.
    if run.normalize_tags and run._is_artist_level(survivor):
        run._record_tag_target(survivor.name, [survivor, canonical, *sources])


def _normalize_file_name(path: Path, run: Run) -> None:
    """Rename a track file to canonical_render(stem) + suffix when they differ,
    with the same legality + collision guards as the folder rename. The extension
    is preserved verbatim."""
    _rename_to(path, canonical_render(path.stem) + path.suffix, run, kind="file")


def normalize_tree(root: Path, run: Run, do_folders: bool, do_files: bool) -> None:
    """Bottom-up rename of folder names (every depth) and/or audio file names to
    their canonical_render form. Files in a directory are renamed before the
    directory itself, and directories before their parents (os.walk topdown=False),
    so captured paths stay valid through an apply run. Rename-only: merging is
    Passes 1-2' job. `--normalize-names` drives folders, `--normalize-filenames`
    drives files; either may run alone."""
    for dirpath, _dirnames, filenames in list(os.walk(root, topdown=False)):
        d = Path(dirpath)
        # Dry-run: skip folders (and their contents) an earlier pass merged
        # away; the apply run would not find them here.
        if d in run.removed or any(parent in run.removed for parent in d.parents):
            continue
        if do_files:
            for fn in sorted(filenames):
                if fn.startswith("."):
                    continue
                fp = d / fn
                if fp.suffix.lower() in AUDIO_EXT:
                    _normalize_file_name(fp, run)
        if do_folders and d != root and not d.name.startswith("."):
            _normalize_folder_name(d, run)


def _planned_tag_values(
    cur: dict[str, list[str] | None], authority: str | None
) -> dict[str, list[str]]:
    """Given the current title/album/artist/albumartist value lists (None =
    field absent), return only the fields whose values should change. Title and
    album get a pure typographic fold of every value (multi-valued tags keep
    all their values) and are never synthesized when absent. Artist/albumartist
    follow the surviving folder name when `authority` is set (and are created
    if absent, as the artist restamp did before), else a typographic fold."""
    out: dict[str, list[str]] = {}
    for field in ("title", "album"):
        vals = cur.get(field)
        if vals is None:
            continue
        folded = [tag_fold(v) for v in vals]
        if folded != vals:
            out[field] = folded

    artist = cur.get("artist")
    albumartist = cur.get("albumartist")
    if authority:
        # Deliberate collapse: under a merged/renamed artist folder the
        # surviving folder name IS the artist, so a multi-valued artist tag
        # becomes the single canonical value (guest credit preserved).
        new_artist = [canon_track_artist(artist[0] if artist else "", authority)]
        if artist is None or new_artist != artist:
            out["artist"] = new_artist
        if albumartist is None or [authority] != albumartist:
            out["albumartist"] = [authority]
    else:
        for field, vals in (("artist", artist), ("albumartist", albumartist)):
            if vals is None:
                continue
            folded = [tag_fold(v) for v in vals]
            if folded != vals:
                out[field] = folded
    return out


def _values(values) -> list[str] | None:
    """Non-empty values of a mutagen list as plain strings, unwrapping ASF
    attributes; None when the field is absent or entirely empty."""
    if not values:
        return None
    out = [str(getattr(v, "value", v)) for v in values]  # ASFUnicodeAttribute
    out = [v for v in out if v]
    return out or None


def _open_for_tags(path: Path, ext: str):
    """Return (current_fields, apply_fn) for a taggable file, or None if it has no
    readable tag block (e.g. an MP3 with no ID3 header). apply_fn(out) writes only
    the fields in `out`. Raw per-container mutagen, mirroring retag.py."""
    if ext == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            return None
        id3map = {
            "title": ("TIT2", TIT2),
            "album": ("TALB", TALB),
            "artist": ("TPE1", TPE1),
            "albumartist": ("TPE2", TPE2),
        }
        cur = {}
        for field, (fid, _cls) in id3map.items():
            frame = tags.get(fid)
            cur[field] = (
                _values([str(t) for t in frame.text]) if frame is not None else None
            )

        def apply(out):
            for field, vals in out.items():
                fid, cls = id3map[field]
                tags.setall(fid, [cls(encoding=3, text=list(vals))])
            tags.save(path, v2_version=3, v1=2)

        return cur, apply

    vorbis_cls = {".flac": FLAC, ".ogg": OggVorbis, ".opus": OggOpus}.get(ext)
    if vorbis_cls is not None:
        tags = vorbis_cls(path)
        keymap = {k.lower(): k for k in tags.keys()}

        def get(name):
            key = keymap.get(name)
            return _values(tags[key]) if key is not None else None

        cur = {f: get(f) for f in ("title", "album", "artist", "albumartist")}

        def apply(out):
            for field, vals in out.items():
                for existing in [k for k in list(tags.keys()) if k.lower() == field]:
                    del tags[existing]
                tags[field] = list(vals)
            tags.save()

        return cur, apply

    if ext in (".m4a", ".mp4"):
        tags = MP4(path)
        atom = {
            "title": "\xa9nam",
            "album": "\xa9alb",
            "artist": "\xa9ART",
            "albumartist": "aART",
        }
        cur = {f: _values(tags.get(a)) for f, a in atom.items()}

        def apply(out):
            for field, vals in out.items():
                tags[atom[field]] = list(vals)
            tags.save()

        return cur, apply

    if ext == ".wma":
        tags = ASF(path)
        keymap = {k.lower(): k for k in tags.keys()}
        asf = {
            "title": "Title",
            "album": "WM/AlbumTitle",
            "artist": "Author",
            "albumartist": "WM/AlbumArtist",
        }
        cur = {f: _values(tags.get(keymap.get(k.lower(), k))) for f, k in asf.items()}

        def apply(out):
            for field, vals in out.items():
                key = asf[field]
                # Delete case-variant originals first (like the Vorbis branch):
                # writing canonical case beside a variant would leave two keys.
                for existing in [
                    k
                    for k in list(tags.keys())
                    if k.lower() == key.lower() and k != key
                ]:
                    del tags[existing]
                tags[key] = list(vals)
            tags.save()

        return cur, apply

    return None


def normalize_file_tags(path: Path, authority: str | None, run: Run) -> None:
    """Typographically normalize title/album (and artist/albumartist, restamped to
    `authority` when set) on one file. No-op when nothing changes. Multi-format."""
    ext = path.suffix.lower()
    rel = path.relative_to(run.root)
    try:
        opened = _open_for_tags(path, ext)
    except Exception as e:  # a corrupt tag block must not abort the whole run
        run.log(f"    TAG ERROR (read {rel}): {e}")
        return
    if opened is None:
        # An MP3 whose tags live only in APEv2/ID3v1: reported and skipped,
        # like the unsupported-format path (the contract is never a silent skip).
        run.stats["tag_no_id3_skipped"] += 1
        run.log(f"    SKIP (no ID3 header; tags in APEv2/ID3v1 only?): {rel}")
        return
    cur, apply = opened
    out = _planned_tag_values(cur, authority)
    if not out:
        return

    def disp(v):
        # Single-valued fields (the norm) log as the bare string.
        return v[0] if isinstance(v, list) and len(v) == 1 else v

    run.stats["tags_rewritten"] += 1
    run.log(f"    TAG: {rel}")
    for field in ("title", "album", "artist", "albumartist"):
        if field in out:
            run.log(f"      {field}: {disp(cur.get(field))!r} -> {disp(out[field])!r}")
    if run.dry_run:
        return
    try:
        apply(out)
    except Exception as e:
        run.stats["tags_rewritten"] -= 1
        run.log(f"    TAG ERROR (save {rel}): {e}")


def normalize_tags(run: Run) -> None:
    """Pass 4: library-wide tag normalization. Title and album get a typographic
    fold everywhere; artist/albumartist are restamped to the surviving folder name
    under merged/renamed artist folders (the authority map) and folded elsewhere.
    Every taggable file is visited once; unsupported audio is reported and skipped."""
    run.log("\n--- PASS 4: normalize tags (library-wide) ---")
    if not MUTAGEN_OK:
        run.log("  SKIP: mutagen not importable; cannot read/write tags")
        return

    # Artist-depth folders recorded by merges/renames map to their canonical name.
    # Sources are recorded alongside survivors, so a dry-run file under a not-yet-
    # moved source resolves to the same authority an apply run would give it.
    authority_map = {
        os.path.realpath(folder): canonical
        for canonical, folders in run.tag_targets
        for folder in folders
    }

    def resolve_authority(p: Path) -> str | None:
        for parent in p.parents:
            name = authority_map.get(os.path.realpath(parent))
            if name is not None:
                return name
        return None

    show_progress = sys.stderr.isatty()
    seen: set[str] = set()
    scanned = 0
    for f in sorted(run.root.rglob("*")):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext not in AUDIO_EXT and ext not in TAGGABLE_EXT:
            continue
        real = os.path.realpath(f)
        if real in seen:
            continue
        seen.add(real)
        if ext not in TAGGABLE_EXT:
            run.stats["tag_unsupported_skipped"] += 1
            run.log(f"    SKIP (no tag layout, {ext}): {f.relative_to(run.root)}")
            continue
        run.stats["tag_files_scanned"] += 1
        scanned += 1
        normalize_file_tags(f, resolve_authority(f), run)
        if show_progress and scanned % 250 == 0:
            print(
                f"\r  tags: {scanned} scanned, {run.stats['tags_rewritten']} changed",
                end="",
                file=sys.stderr,
            )
    if show_progress and scanned:
        print(
            f"\r  tags: {scanned} scanned, {run.stats['tags_rewritten']} changed",
            file=sys.stderr,
        )


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
        help="Also rename non-duplicate folders at every depth with non-standard "
        "characters (unicode dashes, curly quotes) to their normalized form",
    )
    parser.add_argument(
        "--normalize-filenames",
        action="store_true",
        help="Also rename audio track files the same way (separate from "
        "--normalize-names since renaming files is a distinct change)",
    )
    parser.add_argument(
        "--normalize-tags",
        action="store_true",
        help="Library-wide: typographically normalize title/album tags on every "
        "file and restamp artist/albumartist to the surviving folder name under "
        "merged/renamed artist folders (all formats; Pass 4)",
    )
    parser.add_argument(
        "--layout",
        default="{artist}/{album}",
        help="Folder layout, used by --normalize-tags to locate the artist level "
        "(default '{artist}/{album}'; for a genre-foldered library pass "
        "'{genre}/{artist}/{album}')",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    args = parser.parse_args()

    root = Path(args.directory).resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 1

    artist_depth = 1
    if args.normalize_tags:
        if not MUTAGEN_OK:
            print(
                "error: --normalize-tags needs mutagen (pip install mutagen)",
                file=sys.stderr,
            )
            return 1
        layout_parts = [p for p in args.layout.replace("\\", "/").split("/") if p]
        try:
            artist_depth = (
                next(
                    i
                    for i, p in enumerate(layout_parts)
                    if p.strip("{}").lower() == "artist"
                )
                + 1
            )
        except StopIteration:
            print(
                f"error: --layout {args.layout!r} has no {{artist}} component",
                file=sys.stderr,
            )
            return 1

    log_path = Path(args.log_path) if args.log_path else root / "cleanup.log"
    run = Run(
        root,
        log_path,
        dry_run=args.dry_run,
        normalize_tags=args.normalize_tags,
        artist_depth=artist_depth,
    )

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
            (
                p
                for p in root.iterdir()
                if p.is_dir() and not p.name.startswith(".") and p not in run.removed
            ),
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

        if args.normalize_names or args.normalize_filenames:
            what = " + ".join(
                w
                for w, on in (
                    ("folders", args.normalize_names),
                    ("filenames", args.normalize_filenames),
                )
                if on
            )
            run.log(f"\n--- PASS 3: normalize names ({what}) ---")
            normalize_tree(root, run, args.normalize_names, args.normalize_filenames)

        if args.normalize_tags:
            normalize_tags(run)

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
