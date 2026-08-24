#!/usr/bin/env python3
"""slipcover.py — embed folder cover art into audio files missing it.

Walks a directory recursively. In each folder containing audio files, it checks
if a cover image (cover.jpg/png, folder.jpg/png, etc.) exists in the folder.
If a folder image exists, it scans the audio files. For any audio file missing
embedded art, it embeds the folder image into the file. Files that already have
embedded art are left alone.

If an album lacks cover art entirely (no folder image and no embedded art):
  - `--report` will print a list of these completely bare directories.
  - `--fetch` will query the iTunes Search API using the album's artist/album tags,
    download the highest-resolution cover available as `cover.jpg`, and then embed it.

Destructive: writes tags in place. Preview with --dry-run.

Usage:
    ./slipcover.py /path/to/library
    ./slipcover.py /path/to/album --dry-run
    ./slipcover.py /path/to/library --report
    ./slipcover.py /path/to/library --fetch
"""

import argparse
import base64
import json
import mimetypes
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime

from vir_tui import core as ui

__version__ = "1.1.0"


def _import_lattice():
    """Import lattice dependencies lazily."""
    try:
        sys.path.insert(
            0,
            str(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))),
        )
        from lattice.utils import iter_audio_dirs, _find_cover_file, is_audio
        from lattice.config import AUDIO_EXTENSIONS
        from lattice.tags import (
            FLAC,
            MutagenFile,
            Picture,
            MP4,
            HAVE_MUTAGEN_MP3,
            MUTAGEN_MP3,
            get_all_tags,
        )
        from lattice.modes.artwork import _ART_EXTRACTORS
        from mutagen.id3 import ID3, APIC, ID3NoHeaderError
        from mutagen.mp4 import MP4Cover
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
        "get_all_tags": get_all_tags,
    }


def has_embedded_art(filepath: str, ext: str, deps: dict) -> bool:
    extractor = deps["ART_EXTRACTORS"].get(ext)
    if not extractor:
        return False
    try:
        return extractor(filepath) is not None
    except Exception:  # noqa: BLE001
        return False


def fetch_art_for_dir(
    dirpath: str, audio_files: list[str], deps: dict, dry_run: bool
) -> bool:
    """Fetch cover art from iTunes API using metadata from the first audio file.
    Returns True if downloaded successfully (or would have in dry-run)."""
    filepath = os.path.join(dirpath, audio_files[0])
    tags = deps["get_all_tags"](filepath)

    if not tags or not tags.artist or not tags.album:
        ui.tqdm.write(
            ui.warn(
                f"Skipping fetch for {os.path.basename(dirpath)}: missing artist/album tags"
            )
        )
        return False

    query = urllib.parse.quote(f"{tags.artist} {tags.album}")
    url = f"https://itunes.apple.com/search?term={query}&entity=album&limit=1"
    req = urllib.request.Request(url, headers={"User-Agent": f"Lattice/{__version__}"})

    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            results = data.get("results", [])
            if not results:
                ui.tqdm.write(
                    ui.warn(
                        f"No iTunes artwork found for: {tags.artist} - {tags.album}"
                    )
                )
                return False

            art_url = results[0].get("artworkUrl100")
            if not art_url:
                return False

            # Request 600x600 instead of 100x100 thumbnail
            high_res_url = art_url.replace("100x100bb", "600x600bb")

            if dry_run:
                ui.tqdm.write(
                    ui.dry_run(
                        f"would fetch cover for {tags.artist} - {tags.album} from iTunes"
                    )
                )
                return True

            # Download the image
            img_req = urllib.request.Request(
                high_res_url, headers={"User-Agent": f"Lattice/{__version__}"}
            )
            with urllib.request.urlopen(img_req) as img_resp:
                img_data = img_resp.read()

            out_path = os.path.join(dirpath, "cover.jpg")
            with open(out_path, "wb") as f:
                f.write(img_data)

            ui.tqdm.write(
                ui.success(f"Fetched cover.jpg for: {tags.artist} - {tags.album}")
            )
            return True
    except Exception as e:  # noqa: BLE001
        ui.tqdm.write(
            ui.error(f"Failed to fetch artwork for {os.path.basename(dirpath)}: {e}")
        )
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
        "--report",
        action="store_true",
        help="Report directories completely missing both folder and embedded art",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Fetch missing covers from iTunes API and save as cover.jpg",
    )
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
    worklist: list[tuple[str, str, bytes, str]] = []  # (filepath, ext, img_data, mime)
    report_list: list[str] = []

    print(ui.info("Scanning directories..."))

    for _root, dirpath, _dirs, files in deps["iter_audio_dirs"](target_dir):
        audio_files = [f for f in files if deps["is_audio"](f)]
        if not audio_files:
            continue

        cover_file = deps["find_cover"](dirpath)

        # If no folder image exists, check if ANY file has embedded art
        if not cover_file:
            has_embedded = False
            for f in audio_files:
                ext = os.path.splitext(f)[1].lower()
                filepath = os.path.join(dirpath, f)
                if has_embedded_art(filepath, ext, deps):
                    has_embedded = True
                    break

            if not has_embedded:
                report_list.append(dirpath)
                if args.fetch:
                    downloaded = fetch_art_for_dir(
                        dirpath, audio_files, deps, args.dry_run
                    )
                    if downloaded and not args.dry_run:
                        cover_file = deps["find_cover"](dirpath)

        if args.report:
            continue

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

    # Handle pure report mode
    if args.report:
        if not report_list:
            print(ui.success("No directories are completely missing cover art."))
        else:
            print(
                ui.warn(
                    f"Found {len(report_list)} directory(ies) completely missing cover art:"
                )
            )
            for d in report_list:
                print(f"  {d}")
        return 0

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
