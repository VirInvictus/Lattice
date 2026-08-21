#!/usr/bin/env python3
"""slipcover.py — embed folder cover art into audio files missing it.

Walks a directory recursively. In each folder containing audio files, it checks
if a cover image (cover.jpg/png, folder.jpg/png, etc.) exists in the folder.
If a folder image exists, it scans the audio files. For any audio file missing
embedded art, it embeds the folder image into the file. Files that already have
embedded art are left alone.

Destructive: writes tags in place. Preview with --dry-run.

Usage:
    ./slipcover.py /path/to/library
    ./slipcover.py /path/to/album --dry-run
    ./slipcover.py /path/to/library --log ~/slipcover.log
"""

import argparse
import base64
import mimetypes
import os
import sys
from datetime import datetime

import ui

__version__ = "1.0.0"


def _import_lattice():
    """Import lattice dependencies lazily."""
    try:
        sys.path.insert(
            0,
            str(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))),
        )
        from mutagen.id3 import APIC, ID3, ID3NoHeaderError
        from mutagen.mp4 import MP4Cover

        from lattice.config import AUDIO_EXTENSIONS
        from lattice.modes.artwork import _ART_EXTRACTORS
        from lattice.tags import (
            FLAC,
            HAVE_MUTAGEN_MP3,
            MP4,
            MUTAGEN_MP3,
            MutagenFile,
            Picture,
        )
        from lattice.utils import _find_cover_file, is_audio, iter_audio_dirs
    except ImportError as e:
        print(
            f"error: could not import lattice ({e}).\n"
            "Install it (pip install -e . / pipx install .) or run with "
            "PYTHONPATH=src.",
            file=sys.stderr,
        )
        sys.exit(2)

    return {
        "iter_audio_dirs": iter_audio_dirs,
        "find_cover": _find_cover_file,
        "is_audio": is_audio,
        "AUDIO_EXTENSIONS": AUDIO_EXTENSIONS,
        "ART_EXTRACTORS": _ART_EXTRACTORS,
        "FLAC": FLAC,
        "MutagenFile": MutagenFile,
        "Picture": Picture,
        "MP4": MP4,
        "HAVE_MUTAGEN_MP3": HAVE_MUTAGEN_MP3,
        "MUTAGEN_MP3": MUTAGEN_MP3,
        "ID3": ID3,
        "APIC": APIC,
        "ID3NoHeaderError": ID3NoHeaderError,
        "MP4Cover": MP4Cover,
    }


def has_embedded_art(filepath: str, ext: str, deps: dict) -> bool:
    extractor = deps["ART_EXTRACTORS"].get(ext)
    if not extractor:
        return False
    try:
        return extractor(filepath) is not None
    except Exception:  # noqa: BLE001
        return False


def embed_art(filepath: str, img_data: bytes, mime: str, ext: str, deps: dict) -> bool:
    try:
        if ext == ".mp3":
            if not deps["HAVE_MUTAGEN_MP3"]:
                return False
            try:
                tags = deps["ID3"](filepath)
            except deps["ID3NoHeaderError"]:
                tags = deps["ID3"]()
            tags.delall("APIC")
            tags.add(
                deps["APIC"](encoding=3, mime=mime, type=3, desc="", data=img_data)
            )
            tags.save(filepath, v2_version=3, v1=2)

        elif ext == ".flac":
            audio = deps["FLAC"](filepath)
            audio.clear_pictures()
            pic = deps["Picture"]()
            pic.type = 3
            pic.mime = mime
            pic.data = img_data
            audio.add_picture(pic)
            audio.save()

        elif ext in (".opus", ".ogg"):
            audio = deps["MutagenFile"](filepath)
            if audio is None:
                return False
            pic = deps["Picture"]()
            pic.type = 3
            pic.mime = mime
            pic.data = img_data
            b64 = base64.b64encode(pic.write()).decode("ascii")
            audio.pop("metadata_block_picture", None)
            audio["metadata_block_picture"] = [b64]
            audio.save()

        elif ext in (".m4a", ".mp4"):
            audio = deps["MP4"](filepath)
            audio.pop("covr", None)
            fmt = (
                deps["MP4Cover"].FORMAT_PNG
                if mime == "image/png"
                else deps["MP4Cover"].FORMAT_JPEG
            )
            covr = deps["MP4Cover"](img_data, imageformat=fmt)
            audio["covr"] = [covr]
            audio.save()

        else:
            return False

        return True
    except Exception as e:  # noqa: BLE001
        print(
            ui.error(f"  [!] Failed to embed art in {os.path.basename(filepath)}: {e}"),
            file=sys.stderr,
        )
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Embed folder cover art into audio files missing it."
    )
    parser.add_argument("directory", help="Path to the library or album directory")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the changes per file; write nothing",
    )
    parser.add_argument(
        "--log",
        dest="log_path",
        default=None,
        help="Append a timestamped record of each change to this file",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the confirmation prompt on a real run",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    args = parser.parse_args()

    ui.print_header(
        "slipcover.py - Cover Art Embedder" + (" [DRY RUN]" if args.dry_run else "")
    )

    target_dir = os.path.abspath(args.directory)
    if not os.path.isdir(target_dir):
        print(ui.error(f"[!] Directory not found: {target_dir}"), file=sys.stderr)
        return 1

    deps = _import_lattice()

    # Identify default log path
    log_path = args.log_path or os.path.join(target_dir, "slipcover.log")

    # 1. Scan phase
    # Find all audio files lacking art in directories that possess a folder image.
    worklist: list[tuple[str, str, bytes, str]] = []  # (filepath, ext, img_data, mime)

    print(ui.info("Scanning for audio files missing embedded art..."))

    for _root, dirpath, _dirs, files in deps["iter_audio_dirs"](target_dir):
        audio_files = [f for f in files if deps["is_audio"](f)]
        if not audio_files:
            continue

        cover_file = deps["find_cover"](dirpath)
        if not cover_file:
            continue

        # Read the folder image data once per folder
        img_data = None
        mime = None

        for f in audio_files:
            ext = os.path.splitext(f)[1].lower()
            if ext not in deps["ART_EXTRACTORS"]:
                continue

            filepath = os.path.join(dirpath, f)
            if not has_embedded_art(filepath, ext, deps):
                if img_data is None:
                    try:
                        with open(cover_file, "rb") as cf:
                            img_data = cf.read()
                        mime = mimetypes.guess_type(cover_file)[0] or "image/jpeg"
                    except OSError as e:
                        print(
                            ui.error(
                                f"  [!] Failed to read cover image {cover_file}: {e}"
                            ),
                            file=sys.stderr,
                        )
                        break

                worklist.append((filepath, ext, img_data, mime))

    if not worklist:
        print(ui.success("No files need cover art embedded."))
        return 0

    print(
        ui.info(
            f"Found {len(worklist)} file(s) missing embedded art in directories with folder images."
        )
    )

    # 2. Confirmation phase
    if not args.dry_run and not args.yes and sys.stdin.isatty():
        print("Files to modify (showing up to 20):")
        for filepath, _, _, _ in worklist[:20]:
            print(f"  {filepath}")
        if len(worklist) > 20:
            print(f"  ... and {len(worklist) - 20} more")

        if not input("Proceed? [y/N] ").strip().lower().startswith("y"):
            print("Aborted.")
            return 0

    # 3. Apply phase
    log_fh = None
    if not args.dry_run:
        try:
            log_fh = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
        except OSError as e:
            print(
                ui.error(f"error: cannot open log file {log_path}: {e}"),
                file=sys.stderr,
            )
            return 1

    def log(msg: str) -> None:
        ui.tqdm.write(msg)
        if log_fh is not None:
            ts = datetime.now().isoformat(timespec="seconds")  # noqa: DTZ005
            log_fh.write(f"[{ts}] {msg}\n")

    if not args.dry_run:
        log("=" * 70)
        log(f"SLIPCOVER RUN START: {target_dir}")

    updated = 0
    failed = 0

    try:
        pbar = (
            ui.tqdm(worklist, desc=ui.info("Embedding art"))
            if not args.dry_run
            else worklist
        )
        for filepath, ext, img_data, mime in pbar:
            if args.dry_run:
                ui.tqdm.write(ui.dry_run(f"would embed {mime} into {filepath}"))
                updated += 1
            else:
                if embed_art(filepath, img_data, mime, ext, deps):
                    log(f"  embedded {mime} into {filepath}")
                    updated += 1
                else:
                    log(f"  FAILED to embed {mime} into {filepath}")
                    failed += 1
    finally:
        if log_fh is not None:
            log("SLIPCOVER RUN END")
            log("=" * 70)
            log_fh.close()

    verb = "Would update" if args.dry_run else "Updated"
    tail = f"  {failed} file(s) failed." if failed else ""
    print(ui.success(f"{verb} {updated} file(s).{tail}"))

    if not args.dry_run:
        print(ui.info(f"Log: {log_path}"))

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
