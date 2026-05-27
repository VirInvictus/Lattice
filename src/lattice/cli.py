import argparse
import os
import sys

from lattice.config import (
    VERSION,
    DEFAULT_LIBRARY_OUTPUT,
    DEFAULT_AI_LIBRARY_OUTPUT,
    DEFAULT_FLAC_OUTPUT,
    DEFAULT_MP3_OUTPUT,
    DEFAULT_OPUS_OUTPUT,
    DEFAULT_WAV_OUTPUT,
    DEFAULT_WMA_OUTPUT,
    DEFAULT_MISSING_ART_OUTPUT,
    DEFAULT_ART_QUALITY_OUTPUT,
    DEFAULT_DUPLICATES_OUTPUT,
    DEFAULT_TAG_AUDIT_OUTPUT,
    DEFAULT_BITRATE_AUDIT_OUTPUT,
    DEFAULT_PLAYLIST_OUTPUT,
)

from lattice.modes.library import (
    write_music_library_tree,
    write_ai_library,
    write_all_wings,
    write_ai_wings,
)
from lattice.modes.playlists import generate_playlist
from lattice.modes.integrity import (
    run_flac_mode,
    run_mp3_mode,
    run_opus_mode,
    run_wav_mode,
    run_wma_mode,
)
from lattice.modes.artwork import (
    run_extract_art,
    run_missing_art,
    run_art_quality_audit,
)
from lattice.modes.audit import run_duplicates, run_tag_audit, run_bitrate_audit
from lattice.modes.stats import run_stats
from lattice.tui import interactive_menu


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lattice",
        description="Music library toolkit: tree, integrity, art, duplicates, tag audit",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--library", action="store_true", help="Generate library tree")
    group.add_argument(
        "--ai-library",
        dest="ai_library",
        action="store_true",
        help="Generate token-efficient library for AI recommendations",
    )
    group.add_argument(
        "--all-wings",
        dest="all_wings",
        action="store_true",
        help="Generate separate library files for each genre",
    )
    group.add_argument(
        "--ai-wings",
        dest="ai_wings",
        action="store_true",
        help="Generate separate AI-friendly library files for each genre",
    )
    group.add_argument("--testFLAC", action="store_true", help="Verify FLAC files")
    group.add_argument("--testMP3", action="store_true", help="Verify MP3 files")
    group.add_argument(
        "--testOpus", action="store_true", help="Verify Opus files via FFmpeg decode"
    )
    group.add_argument(
        "--testWAV", action="store_true", help="Verify WAV files via FFmpeg decode"
    )
    group.add_argument(
        "--testWMA", action="store_true", help="Verify WMA files via FFmpeg decode"
    )
    group.add_argument(
        "--extractArt", action="store_true", help="Extract embedded cover art to folder"
    )
    group.add_argument(
        "--missingArt", action="store_true", help="Report directories missing cover art"
    )
    group.add_argument(
        "--auditArtQuality",
        action="store_true",
        help="Report extracted/folder covers below a resolution threshold",
    )
    group.add_argument(
        "--duplicates",
        action="store_true",
        help="Four-section dupe report: exact albums, within-folder multi-format, similar names, track-level",
    )
    group.add_argument(
        "--auditTags", action="store_true", help="Report files with incomplete tags"
    )
    group.add_argument(
        "--auditBitrate",
        action="store_true",
        help="Report files below a certain bitrate floor",
    )
    group.add_argument(
        "--playlist",
        action="store_true",
        help="Generate a smart .m3u playlist based on a rule",
    )
    group.add_argument(
        "--stats", action="store_true", help="Library-wide statistics summary"
    )

    p.add_argument(
        "--root",
        action="append",
        default=None,
        metavar="DIR",
        help="Root directory; repeat --root to scan several libraries together "
        "(default: read from config or current dir)",
    )
    p.add_argument(
        "pos_root", nargs="?", default=None, help="Root directory (positional fallback)"
    )
    p.add_argument("--output", default=None, help="Output path")
    p.add_argument(
        "--rule",
        default="",
        help="Smart playlist rule (e.g. \"rating >= 4 and genre == 'Jazz'\")",
    )
    p.add_argument(
        "--layout",
        default="{artist}/{album}",
        help="Directory structure pattern for extracting tags from path (default: {artist}/{album})",
    )
    p.add_argument(
        "--min-art-res",
        type=int,
        default=500,
        help="Minimum resolution in pixels for --auditArtQuality (default: 500)",
    )
    p.add_argument(
        "--min-bitrate",
        type=int,
        default=192,
        help="Minimum bitrate in kbps for --auditBitrate (default: 192)",
    )
    p.add_argument(
        "--workers", type=int, default=4, help="Parallel workers (integrity modes)"
    )
    p.add_argument(
        "--prefer",
        choices=["flac", "ffmpeg"],
        default="flac",
        help="Preferred tool (FLAC mode)",
    )
    p.add_argument("--quiet", action="store_true", help="Minimize output")
    p.add_argument(
        "--genres", action="store_true", help="Include album genres in library tree"
    )
    p.add_argument(
        "--paths",
        action="store_true",
        help="Include absolute directory paths at the album level",
    )
    p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Preview changes without writing (extractArt)",
    )

    p.add_argument(
        "--only-errors",
        dest="only_errors",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write only errors/warns (MP3/Opus modes)",
    )

    p.add_argument("--ffmpeg", default=None, help="Path to ffmpeg")
    p.add_argument("--verbose", action="store_true", help="Verbose output")
    return p


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) == 0:
        return interactive_menu()

    try:
        args = build_parser().parse_args(argv)

        # Every named root (positional + each --root) is scanned together;
        # de-dupe so the same path passed twice isn't walked twice.
        raw_roots = list(args.root or [])
        if args.pos_root is not None:
            raw_roots.append(args.pos_root)

        if raw_roots:
            seen: set[str] = set()
            root: list[str] = []
            for r in raw_roots:
                ar = os.path.abspath(os.path.expanduser(r))
                if ar not in seen:
                    seen.add(ar)
                    root.append(ar)
        else:
            from lattice.config import get_library_roots, set_library_root

            config_roots = [r for r in get_library_roots() if r and os.path.exists(r)]
            if config_roots:
                root = config_roots
            elif sys.stdin.isatty():
                print("First run: No library root configured.")
                raw_input_root = input(
                    "Enter path to your music library (or press Enter for current directory): "
                ).strip()
                if raw_input_root:
                    single = os.path.abspath(os.path.expanduser(raw_input_root))
                    set_library_root(single)
                    print(f"Library root saved to {single}")
                    root = [single]
                else:
                    root = [os.path.abspath(".")]
            else:
                root = [os.path.abspath(".")]

        if args.library:
            output = args.output or DEFAULT_LIBRARY_OUTPUT
            write_music_library_tree(
                root,
                output,
                layout=args.layout,
                quiet=args.quiet,
                show_genre=args.genres,
            )
            return 0

        if args.ai_library:
            output = args.output or DEFAULT_AI_LIBRARY_OUTPUT
            write_ai_library(root, output, layout=args.layout, quiet=args.quiet)
            return 0

        if args.all_wings:
            outdir = args.output or "wings"
            return write_all_wings(
                root,
                outdir,
                layout=args.layout,
                quiet=args.quiet,
                show_genre=args.genres,
                show_paths=args.paths,
            )

        if args.ai_wings:
            outdir = args.output or "wings_ai"
            return write_ai_wings(root, outdir, layout=args.layout, quiet=args.quiet)

        if args.testFLAC:
            output = args.output or DEFAULT_FLAC_OUTPUT
            return run_flac_mode(
                root, output, args.workers, args.prefer, quiet=args.quiet
            )

        if args.testMP3:
            output = args.output or DEFAULT_MP3_OUTPUT
            return run_mp3_mode(
                root,
                output,
                args.workers,
                args.ffmpeg,
                only_errors=args.only_errors,
                verbose=args.verbose,
                quiet=args.quiet,
            )

        if args.testOpus:
            output = args.output or DEFAULT_OPUS_OUTPUT
            return run_opus_mode(
                root,
                output,
                args.workers,
                args.ffmpeg,
                only_errors=args.only_errors,
                verbose=args.verbose,
                quiet=args.quiet,
            )

        if args.testWAV:
            output = args.output or DEFAULT_WAV_OUTPUT
            return run_wav_mode(
                root,
                output,
                args.workers,
                args.ffmpeg,
                only_errors=args.only_errors,
                verbose=args.verbose,
                quiet=args.quiet,
            )

        if args.testWMA:
            output = args.output or DEFAULT_WMA_OUTPUT
            return run_wma_mode(
                root,
                output,
                args.workers,
                args.ffmpeg,
                only_errors=args.only_errors,
                verbose=args.verbose,
                quiet=args.quiet,
            )

        if args.extractArt:
            return run_extract_art(root, quiet=args.quiet, dry_run=args.dry_run)

        if args.missingArt:
            output = args.output or DEFAULT_MISSING_ART_OUTPUT
            return run_missing_art(root, output, quiet=args.quiet)

        if args.auditArtQuality:
            output = args.output or DEFAULT_ART_QUALITY_OUTPUT
            return run_art_quality_audit(
                root, output, args.min_art_res, quiet=args.quiet
            )

        if args.duplicates:
            output = args.output or DEFAULT_DUPLICATES_OUTPUT
            return run_duplicates(root, output, quiet=args.quiet)

        if args.auditTags:
            output = args.output or DEFAULT_TAG_AUDIT_OUTPUT
            return run_tag_audit(root, output, quiet=args.quiet)

        if args.auditBitrate:
            output = args.output or DEFAULT_BITRATE_AUDIT_OUTPUT
            return run_bitrate_audit(root, output, args.min_bitrate, quiet=args.quiet)

        if args.playlist:
            output = args.output or DEFAULT_PLAYLIST_OUTPUT
            return generate_playlist(
                root, output, args.rule, layout=args.layout, quiet=args.quiet
            )

        if args.stats:
            run_stats(root, args.output, quiet=args.quiet)
            return 0

        build_parser().print_help()
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        return 130
