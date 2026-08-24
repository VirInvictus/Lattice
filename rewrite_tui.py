import os
import re

lattice_tui = '/home/bdkl/.gitrepos/Lattice/src/lattice/tui.py'
with open(lattice_tui, 'r') as f:
    content = f.read()

# We need to extract the parts that we want to keep.
# 1. Imports at the top (excluding curses/sys/io/traceback, just keep what's needed for the modes)
imports = """import os
from contextlib import contextmanager
from typing import Any

from vir_tui import (
    tui_select,
    reset_terminal,
    open_screen,
    close_screen,
    ask,
    ask_yn,
    prompt_int,
    prompt_out,
    run_with_capture,
    CancelledError,
    notify
)

from lattice.modes.flac import run_flac_mode
from lattice.modes.mp3 import run_mp3_mode
from lattice.modes.opus import run_opus_mode
from lattice.modes.wav import run_wav_mode
from lattice.modes.wma import run_wma_mode
from lattice.modes.extract_art import run_extract_art
from lattice.modes.missing_art import run_missing_art
from lattice.modes.art_quality import run_art_quality_audit
from lattice.modes.duplicates import run_duplicates
from lattice.modes.tags import run_tag_audit
from lattice.modes.bitrates import run_bitrate_audit
from lattice.modes.replaygain import run_replaygain_audit
from lattice.modes.stats import run_stats
from lattice.modes.wings import write_all_wings
from lattice.modes.ai_wings import write_ai_wings
from lattice.modes.library_tree import write_library_tree
from lattice.modes.ai_library_export import write_ai_library_export
from lattice.modes.smart_playlist import write_smart_playlist

from lattice.config import (
    get_library_root,
    get_library_roots,
    set_library_root,
    get_layout,
)

DEFAULT_FLAC_OUTPUT = "lattice_flac_errors.txt"
DEFAULT_MP3_OUTPUT = "lattice_mp3_errors.txt"
DEFAULT_OPUS_OUTPUT = "lattice_opus_errors.txt"
DEFAULT_WAV_OUTPUT = "lattice_wav_errors.txt"
DEFAULT_WMA_OUTPUT = "lattice_wma_errors.txt"
DEFAULT_MISSING_ART_OUTPUT = "lattice_missing_art.txt"
DEFAULT_ART_QUALITY_OUTPUT = "lattice_art_quality.txt"
DEFAULT_DUPLICATES_OUTPUT = "lattice_duplicates.txt"
DEFAULT_TAG_AUDIT_OUTPUT = "lattice_tag_audit.txt"
DEFAULT_BITRATE_AUDIT_OUTPUT = "lattice_bitrate_audit.txt"
DEFAULT_REPLAYGAIN_AUDIT_OUTPUT = "lattice_replaygain_audit.txt"

def _out_note(path: str | None) -> str:
    return f"Report written to {os.path.abspath(path)}" if path else ""
"""

# Extract _MAIN_SECTIONS to the end of _LIB_ALIASES
match = re.search(r'(_MAIN_SECTIONS = \[.*?)def _build_fallback', content, re.DOTALL)
if not match:
    print("Could not find sections")
    exit(1)
sections_code = match.group(1)

# Modify dispatch functions to use vir-tui
dispatch_code = """
_SEL_CHANGE_ROOT = (4, 0)
_SEL_QUIT = (5, 0)
_SEL_LIB_BACK = (1, 0)

def _select_main(title: str) -> tuple | None:
    return tui_select(title, _MAIN_SECTIONS, aliases=_MAIN_ALIASES, letter_keys=_LETTER_KEYS)

def _select_library() -> tuple | None:
    return tui_select("Library Tree & Exports", _LIB_SECTIONS, hints="\u2191\u2193 Navigate  \u23ce Select  Esc Back", aliases=_LIB_ALIASES, letter_keys=_LETTER_KEYS)

def interactive_menu() -> int:
    open_screen()
    try:
        return _menu_session()
    except KeyboardInterrupt:
        return 130
    finally:
        close_screen()

def _integrity_prompts() -> tuple[int, bool, bool]:
    workers = prompt_int("Workers", 4)
    ffmpeg = ask_yn("Use ffmpeg instead of native tools? (y/N)")
    include_ok = ask_yn("List passing files too? (y/N)")
    return workers, ffmpeg, include_ok

def _library_submenu(root: str) -> None:
    while True:
        reset_terminal()
        result = _select_library()
        if result == "fallback":
            continue
        if result == "invalid":
            continue
        if result is None or result == _SEL_LIB_BACK:
            return

        try:
            if result == (0, 0):
                output = prompt_out("Output file (leave blank for screen)", "").strip()
                output = os.path.expanduser(output) if output else None
                run_with_capture("Build music library tree", write_library_tree, root, output, quiet=False, footer=_out_note(output))
            elif result == (0, 1):
                output = prompt_out("Output file", "library.txt")
                run_with_capture("AI-readable library export", write_ai_library_export, root, output, quiet=False, footer=_out_note(output))
            elif result == (0, 2):
                outdir = prompt_out("Output directory", "wings")
                run_with_capture("Generate all wings", write_all_wings, root, outdir, quiet=False, footer=f"Wings written to {os.path.abspath(outdir)}")
            elif result == (0, 3):
                outdir = prompt_out("Output directory", "ai_wings")
                run_with_capture("Generate AI wings", write_ai_wings, root, outdir, quiet=False, footer=f"AI Wings written to {os.path.abspath(outdir)}")
            elif result == (0, 4):
                output = prompt_out("Output file", "smart_playlist.m3u")
                run_with_capture("Generate smart playlist (.m3u)", write_smart_playlist, root, output, quiet=False, footer=_out_note(output))
        except CancelledError:
            continue

def _menu_session() -> int:
    while True:
        single = get_library_root()
        root = single
        if not root:
            roots = get_library_roots()
            if not roots:
                return 1
            root = roots[0]
        if not os.path.isdir(root):
            notify(f"Configured library root not found: {root}")
            return 1
        
        title = f"Lattice (root: {single})" if single else "Lattice"
        
        reset_terminal()
        result = _select_main(title)

        if result == "fallback":
            continue

        if result == "invalid":
            continue

        if result is None or result == _SEL_QUIT:
            return 0

        try:
            if result == _SEL_CHANGE_ROOT:
                note = (
                    " — edits library_root only; the library_roots list is untouched"
                    if len(get_library_roots()) > 1
                    else ""
                )
                try:
                    raw = ask(f"Change library root (current: {single}){note}", single or "")
                except CancelledError:
                    raw = None
                if raw is None or not raw.strip():
                    continue
                new_root = os.path.abspath(os.path.expanduser(raw.strip()))
                if not os.path.isdir(new_root):
                    notify(f"Not a directory: {new_root} — root unchanged.")
                    continue
                set_library_root(new_root)
                continue

            if result == (0, 0):
                _library_submenu(root)

            elif result == (0, 1):
                output = ask("Output file (leave blank for screen)", "").strip()
                output = os.path.expanduser(output) if output else None
                layout = ask("Path extraction layout", get_layout())
                run_with_capture("Library Statistics", run_stats, root, output, layout=layout, quiet=False, footer=_out_note(output))

            elif result == (1, 0):
                output = prompt_out("Output file", DEFAULT_FLAC_OUTPUT)
                workers = prompt_int("Workers", 4)
                pref = ask("Preferred tool (flac/ffmpeg)", "flac").strip().lower()
                while pref not in ("flac", "ffmpeg"):
                    pref = ask("Preferred tool must be flac or ffmpeg", "flac").strip().lower()
                run_with_capture("Test FLAC files", run_flac_mode, root, output, workers, pref, quiet=False, footer=_out_note(output))

            elif result == (1, 1):
                output = prompt_out("Output file", DEFAULT_MP3_OUTPUT)
                workers, ffmpeg, include_ok = _integrity_prompts()
                run_with_capture("Test MP3 files", run_mp3_mode, root, output, workers, ffmpeg, only_errors=not include_ok, verbose=include_ok, quiet=False, footer=_out_note(output))

            elif result == (1, 2):
                output = prompt_out("Output file", DEFAULT_OPUS_OUTPUT)
                workers, ffmpeg, include_ok = _integrity_prompts()
                run_with_capture("Test Opus files", run_opus_mode, root, output, workers, ffmpeg, only_errors=not include_ok, verbose=include_ok, quiet=False, footer=_out_note(output))

            elif result == (1, 3):
                output = prompt_out("Output file", DEFAULT_WAV_OUTPUT)
                workers, ffmpeg, include_ok = _integrity_prompts()
                run_with_capture("Test WAV files", run_wav_mode, root, output, workers, ffmpeg, only_errors=not include_ok, verbose=include_ok, quiet=False, footer=_out_note(output))

            elif result == (1, 4):
                output = prompt_out("Output file", DEFAULT_WMA_OUTPUT)
                workers, ffmpeg, include_ok = _integrity_prompts()
                run_with_capture("Test WMA files", run_wma_mode, root, output, workers, ffmpeg, only_errors=not include_ok, verbose=include_ok, quiet=False, footer=_out_note(output))

            elif result == (2, 0):
                dry = ask_yn("Dry run? (y/N)")
                run_with_capture("Extract cover art", run_extract_art, root, quiet=False, dry_run=dry)

            elif result == (2, 1):
                output = prompt_out("Output file", DEFAULT_MISSING_ART_OUTPUT)
                run_with_capture("Report missing art", run_missing_art, root, output, quiet=False, footer=_out_note(output))

            elif result == (2, 2):
                output = prompt_out("Output file", DEFAULT_ART_QUALITY_OUTPUT)
                min_res = prompt_int("Minimum resolution floor", 500)
                run_with_capture("Audit art quality", run_art_quality_audit, root, output, min_res, quiet=False, footer=_out_note(output))

            elif result == (3, 0):
                output = prompt_out("Output file", DEFAULT_DUPLICATES_OUTPUT)
                run_with_capture("Find duplicate albums", run_duplicates, root, output, quiet=False, footer=_out_note(output))

            elif result == (3, 1):
                output = prompt_out("Output file", DEFAULT_TAG_AUDIT_OUTPUT)
                run_with_capture("Audit tags", run_tag_audit, root, output, quiet=False, footer=_out_note(output))

            elif result == (3, 2):
                output = prompt_out("Output file", DEFAULT_BITRATE_AUDIT_OUTPUT)
                min_kbps = prompt_int("Minimum bitrate floor (kbps)", 192)
                run_with_capture("Audit bitrates", run_bitrate_audit, root, output, min_kbps, quiet=False, footer=_out_note(output))

            elif result == (3, 3):
                output = prompt_out("Output file", DEFAULT_REPLAYGAIN_AUDIT_OUTPUT)
                include_ok = ask_yn("List fully-tagged albums? (y/N)")
                run_with_capture("Audit ReplayGain", run_replaygain_audit, root, output, verbose=include_ok, quiet=False, footer=_out_note(output))
        except CancelledError:
            continue
"""

with open(lattice_tui, 'w') as f:
    f.write(imports + "\n" + sections_code + "\n" + dispatch_code)

