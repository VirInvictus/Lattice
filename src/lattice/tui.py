import io
import os
import sys
import traceback
from contextlib import contextmanager
from typing import Any

try:
    import curses

    HAVE_CURSES = True
except ImportError:
    HAVE_CURSES = False

import lattice.utils as utils
from lattice.utils import _reset_terminal
from lattice.config import (
    VERSION,
    get_layout,
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
    DEFAULT_REPLAYGAIN_AUDIT_OUTPUT,
    DEFAULT_PLAYLIST_OUTPUT,
)

from lattice.modes.library import (
    write_music_library_tree,
    write_ai_library,
    write_all_wings,
    write_ai_wings,
)
from lattice.modes.playlists import generate_playlist
from lattice.modes.stats import run_stats
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
from lattice.modes.audit import (
    run_duplicates,
    run_tag_audit,
    run_bitrate_audit,
    run_replaygain_audit,
)

# =====================================
# Curses TUI / Fallbacks
# =====================================

from vir_tui import (
    tui_select as _tui_select,
    _reset_terminal,
    _open_screen,
    _close_screen,
    ask as _ask,
    ask_yn as _ask_yn,
    prompt_int as _prompt_int,
    prompt_out as _prompt_out,
    run_with_capture as _run_with_capture,
    _Cancelled,
    notify as _notify,
    tui_page as _tui_page,
    _box_menu,
    _fallback_input,
)
from vir_tui.menu import _prompt_str, _USE_CURSES, _SCREEN, _pause

def _out_note(path: str | None) -> str:
    return f"Report written to {os.path.abspath(path)}" if path else ""

_MAIN_SECTIONS = [
    (
        "LIBRARY",
        [
            "Library tree & exports                  \u2192",
            "Library statistics",
        ],
    ),
    (
        "INTEGRITY",
        [
            "Test FLAC files",
            "Test MP3 files",
            "Test Opus files",
            "Test WAV files",
            "Test WMA files",
        ],
    ),
    (
        "ARTWORK",
        [
            "Extract cover art",
            "Report missing art",
            "Audit art quality",
        ],
    ),
    (
        "METADATA",
        [
            "Find duplicate albums",
            "Audit tags",
            "Audit bitrates",
            "Audit ReplayGain",
        ],
    ),
    (
        "SETTINGS",
        [
            "Change library root",
        ],
    ),
    ("", ["Quit"]),
]

_LIB_SECTIONS = [
    (
        "",
        [
            "Build music library tree",
            "AI-readable library export",
            "Generate all wings (per-genre)",
            "Generate AI wings (per-genre flat)",
            "Generate smart playlist (.m3u)",
        ],
    ),
    ("", ["Back to main menu"]),
]

# Items that get a letter key instead of a number in the fallback menu.
# Matched on the cleaned label so the mapping follows the sections.
_LETTER_KEYS = {
    "Quit": ("q", None),
    "Back to main menu": ("b", None),
    "Change library root": ("s", "self"),  # "self": maps to its own (si, ii)
}

_MAIN_ALIASES: dict[str, tuple | None] = {
    "l": (0, 0),
    "lib": (0, 0),
    "library": (0, 0),
    "stats": (0, 1),
    "flac": (1, 0),
    "mp3": (1, 1),
    "opus": (1, 2),
    "wav": (1, 3),
    "wma": (1, 4),
    "art": (2, 0),
    "extract": (2, 0),
    "missing": (2, 1),
    "quality": (2, 2),
    "dup": (3, 0),
    "dupes": (3, 0),
    "tags": (3, 1),
    "audit": (3, 1),
    "bitrate": (3, 2),
    "rg": (3, 3),
    "replaygain": (3, 3),
    "settings": (4, 0),
    "config": (4, 0),
    "c": (4, 0),
    "quit": None,
    "exit": None,
}

_LIB_ALIASES: dict[str, tuple | None] = {
    "tree": (0, 0),
    "lib": (0, 0),
    "ai": (0, 1),
    "wings": (0, 2),
    "ai-wings": (0, 3),
    "playlist": (0, 4),
    "back": None,
    "": None,
}


def _build_fallback(sections: list, extra_aliases: dict[str, tuple | None]):
    """Derive the no-curses fallback menu rows and input map from the same
    sections the curses menu renders, so the two can never drift apart (the
    numbered map used to be maintained by hand and went stale)."""
    mapping: dict[str, tuple | None] = dict(extra_aliases)
    display: list[tuple[str, list[str]]] = []
    n = 0
    for si, (hdr, items) in enumerate(sections):
        rows = []
        for ii, label in enumerate(items):
            clean = " ".join(label.split())
            letter = _LETTER_KEYS.get(clean)
            if letter is not None:
                key, target = letter
                rows.append(f"{key}) {clean}")
                mapping[key] = (si, ii) if target == "self" else target
            else:
                n += 1
                rows.append(f"{n}) {clean}")
                mapping[str(n)] = (si, ii)
        display.append((hdr, rows))
    return display, mapping, n


_MAIN_FALLBACK_DISPLAY, _MAIN_FALLBACK_MAP, _MAIN_FALLBACK_MAX = _build_fallback(
    _MAIN_SECTIONS, _MAIN_ALIASES
)
_LIB_FALLBACK_DISPLAY, _LIB_FALLBACK_MAP, _LIB_FALLBACK_MAX = _build_fallback(
    _LIB_SECTIONS, _LIB_ALIASES
)

# Named (section, item) results for the non-mode rows, so the dispatch below
# reads without cross-referencing _MAIN_SECTIONS/_LIB_SECTIONS indices.
_SEL_CHANGE_ROOT = (4, 0)
_SEL_QUIT = (5, 0)
_SEL_LIB_BACK = (1, 0)


def _select_main(title: str) -> tuple | None:
    if _USE_CURSES:
        return _tui_select(title, _MAIN_SECTIONS)
    _box_menu(title, _MAIN_FALLBACK_DISPLAY)
    return _fallback_input(
        f"  Select [1-{_MAIN_FALLBACK_MAX}/s/q]: ", _MAIN_FALLBACK_MAP
    )


def _select_library() -> tuple | None:
    if _USE_CURSES:
        return _tui_select(
            "Library Tree & Exports",
            _LIB_SECTIONS,
            hints="\u2191\u2193 Navigate  \u23ce Select  Esc Back",
        )
    _box_menu("Library Tree & Exports", _LIB_FALLBACK_DISPLAY)
    return _fallback_input(f"  Select [1-{_LIB_FALLBACK_MAX}/b]: ", _LIB_FALLBACK_MAP)


def _tui_page(title: str, content: str) -> None:
    if not _USE_CURSES:
        print(content)
        _pause()
        return

    lines = content.replace("\x00", "").expandtabs(4).split("\n")
    # Computed once, not per keypress: the content never changes while paging.
    max_line_len = max((len(ln) for ln in lines), default=0)

    def _run(stdscr):
        _curs_set(0)
        top = 0
        left = 0
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            fa = curses.color_pair(_CP_FRAME)

            # Width follows the longest line (up to the terminal width) so wide
            # reports — long duplicate paths, say — are not chopped at 80 columns.
            content_w = min(w, max(_TUI_BOX_W, max_line_len + 4))
            bx = max(0, (w - content_w) // 2)
            max_lines = max(1, h - 3)
            last_top = max(0, len(lines) - max_lines)
            top = min(top, last_top)  # keep the view valid across resizes
            visible_w = content_w - 4
            max_left = max(0, max_line_len - visible_w)
            left = min(left, max_left)

            # Title on the top border, hints on the last row; content fills the
            # full height between them.
            _safe_addstr(stdscr, 0, bx, "╔" + "═" * (content_w - 2) + "╗", fa)
            _safe_addstr(
                stdscr,
                0,
                bx + 2,
                f" {title} ",
                curses.color_pair(_CP_TITLE) | curses.A_BOLD,
            )
            _safe_addstr(stdscr, h - 2, bx, "╚" + "═" * (content_w - 2) + "╝", fa)

            hints = "↑↓ Scroll  ←→ Pan  PgUp/Dn  g/G Top/Bottom  q/Esc Close"
            _safe_addstr(
                stdscr,
                h - 1,
                max(0, (w - len(hints)) // 2),
                hints,
                curses.color_pair(_CP_HINT) | curses.A_DIM,
            )

            for i in range(max_lines):
                _safe_addstr(stdscr, i + 1, bx, "║", fa)
                if top + i < len(lines):
                    ln = lines[top + i]
                    seg = ln[left : left + visible_w]
                    # Ellipsis markers show that a line continues off-screen.
                    if len(ln) - left > visible_w and seg:
                        seg = seg[:-1] + "…"
                    if left and seg:
                        seg = "…" + seg[1:]
                    _safe_addstr(
                        stdscr,
                        i + 1,
                        bx + 2,
                        seg,
                        curses.color_pair(_CP_ITEM),
                    )
                _safe_addstr(stdscr, i + 1, bx + content_w - 1, "║", fa)

            stdscr.refresh()

            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k")):
                top = max(0, top - 1)
            elif key in (curses.KEY_DOWN, ord("j")):
                top = min(last_top, top + 1)
            elif key in (curses.KEY_LEFT, ord("h")):
                left = max(0, left - 8)
            elif key in (curses.KEY_RIGHT, ord("l")):
                left = min(max_left, left + 8)
            elif key == curses.KEY_PPAGE:
                top = max(0, top - max_lines)
            elif key == curses.KEY_NPAGE:
                top = min(last_top, top + max_lines)
            elif key in (curses.KEY_HOME, ord("g")):
                top = 0
                left = 0
            elif key in (curses.KEY_END, ord("G")):
                top = last_top
            elif key in (ord("q"), ord("Q"), 27, curses.KEY_ENTER, 10, 13):
                break
            elif key == curses.KEY_RESIZE:
                pass

    try:
        _with_screen(_run)
    except KeyboardInterrupt:
        pass  # Ctrl-C just closes the pager
    except curses.error:
        _degrade_to_text()
        print(content)
        _pause()


@contextmanager
def capture_output():
    old_out, old_err = sys.stdout, sys.stderr
    out, err = io.StringIO(), io.StringIO()
    sys.stdout, sys.stderr = out, err
    try:
        yield out, err
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def _run_with_capture(title: str, func, *args, footer: str = "", **kwargs):
    result = None
    note = ""
    with capture_output() as (out, err):
        try:
            result = func(*args, **kwargs)
        except KeyboardInterrupt:
            note = "[Cancelled]"
        except Exception:
            # A mode error must not escape as a raw traceback with the screen
            # stuck in curses mode; page it (plus whatever was captured).
            note = "[Error]\n" + traceback.format_exc().rstrip()
    # With a session screen the mode's _TUIPbar drew into it and nothing needs
    # tearing down. Without one (direct invocation) the pbar initscr()'d a
    # screen of its own; end it before paging, even (especially) when the mode
    # died mid-run.
    if _SCREEN is None:
        if _USE_CURSES:
            try:
                if not curses.isendwin():
                    curses.endwin()
            except curses.error:
                pass
        _reset_terminal()

    text = ""
    if note:
        text += note + "\n"
    if isinstance(result, str) and result:
        text += result + "\n"

    out_text = out.getvalue().strip()
    if out_text:
        text += out_text + "\n"

    err_text = err.getvalue().strip()
    if err_text:
        text += "\n[Errors/Warnings]:\n" + err_text + "\n"

    if footer and not note:
        # The "Report written to ..." footer must not assert a file exists
        # when the mode died or was cancelled before finishing.
        text += "\n" + footer + "\n"

    text = text.strip()
    if text:
        _tui_page(title, text)
    else:
        _pause()


def _library_submenu(root) -> None:
    while True:
        result = _select_library()

        if result == "fallback":
            continue  # curses init failed; the next pass renders the text menu

        if result == "invalid":
            if not _USE_CURSES:
                print("  Invalid selection.")
            continue

        if result is None or result == _SEL_LIB_BACK:
            return

        _reset_terminal()

        try:
            if result == (0, 0):
                output = _prompt_out("Output file", DEFAULT_LIBRARY_OUTPUT)
                layout = _ask("Path extraction layout", get_layout())
                show_g = _ask_yn("Include genres? (y/N)")

                def _wrap():
                    write_music_library_tree(
                        root, output, layout=layout, quiet=False, show_genre=show_g
                    )
                    print(f"\n  Library written to {os.path.abspath(output)}")

                _run_with_capture("Build music library tree", _wrap)

            elif result == (0, 1):
                output = _prompt_out("Output file", DEFAULT_AI_LIBRARY_OUTPUT)
                layout = _ask("Path extraction layout", get_layout())

                def _wrap2():
                    write_ai_library(root, output, layout=layout, quiet=False)
                    print(f"\n  Library written to {os.path.abspath(output)}")

                _run_with_capture("AI-readable library export", _wrap2)

            elif result == (0, 2):
                outdir = _prompt_out("Output directory", "wings")
                layout = _ask("Path extraction layout", get_layout())
                show_g = _ask_yn("Include genres? (y/N)")
                show_p = _ask_yn("Include paths? (y/N)")

                def _wrap3():
                    write_all_wings(
                        root,
                        outdir,
                        layout=layout,
                        quiet=False,
                        show_genre=show_g,
                        show_paths=show_p,
                    )
                    print(f"\n  Wings generated in {os.path.abspath(outdir)}")

                _run_with_capture("Generate all wings (per-genre)", _wrap3)

            elif result == (0, 3):
                outdir = _prompt_out("Output directory", "wings_ai")
                layout = _ask("Path extraction layout", get_layout())

                def _wrap_ai():
                    write_ai_wings(root, outdir, layout=layout, quiet=False)
                    print(f"\n  AI Wings generated in {os.path.abspath(outdir)}")

                _run_with_capture("Generate AI wings (per-genre flat)", _wrap_ai)

            elif result == (0, 4):
                output = _prompt_out("Output file", DEFAULT_PLAYLIST_OUTPUT)
                rule = _ask("Smart rule (e.g. \"rating >= 4 and genre == 'Jazz'\")", "")
                layout = _ask("Path extraction layout", get_layout())

                def _wrap4():
                    generate_playlist(root, output, rule, layout=layout, quiet=False)

                _run_with_capture("Generate smart playlist", _wrap4)
        except _Cancelled:
            continue  # Esc in a prompt: back to the menu, nothing launched


def _integrity_prompts() -> tuple[int, str | None, bool]:
    """The shared decode-scan prompt chain: (workers, ffmpeg path, include_ok).
    The OK-rows answer also drives the verbose flag (one question, both knobs,
    matching the CLI's coupling of --verbose to showing OK rows)."""
    workers = _prompt_int("Workers", 4)
    ffmpeg = _ask("ffmpeg path (blank = auto)", "").strip() or None
    include_ok = _ask_yn("Include OK rows (verbose report)? (y/N)")
    return workers, ffmpeg, include_ok


def interactive_menu() -> int:
    """Run one interactive session. Owns the persistent curses screen (T7):
    it is opened once here, every widget draws into it, and it is torn down
    once on the way out — no per-widget init/teardown flash. When curses
    can't start (or isn't available), the whole session runs the text menu."""
    global _SCREEN, _USE_CURSES
    if _USE_CURSES:
        stdscr = _open_screen()
        if stdscr is None:
            _USE_CURSES = False
            utils.IN_TUI = False
        else:
            _SCREEN = stdscr
            utils.set_shared_screen(stdscr)
            try:
                return _menu_session()
            except KeyboardInterrupt:
                return 130
            finally:
                _close_screen()
    try:
        return _menu_session()
    except KeyboardInterrupt:
        print()
        return 130


def _menu_session() -> int:
    from lattice.config import get_library_root, get_library_roots, set_library_root

    while True:
        # Re-evaluated every pass: a fallback session must never hand progress
        # to _TUIPbar, and a mid-session curses failure flips _USE_CURSES off.
        utils.IN_TUI = _USE_CURSES

        single = get_library_root()
        roots = [r for r in get_library_roots() if r and os.path.isdir(r)]
        if not roots:
            # True first run (nothing configured) or every configured root is
            # missing. Nothing is persisted until an existing directory is
            # named explicitly; a blank answer never silently becomes the CWD.
            if single:
                label = f"Configured root missing: {single}. New library root"
            else:
                label = "First run: Enter path to your music library"
            raw = _prompt_str(label, "")
            if raw is None:
                return 0
            raw = raw.strip()
            if not raw:
                _notify(
                    "A library path is required "
                    "(enter '.' to use the current directory)."
                )
                continue
            new_root = os.path.abspath(os.path.expanduser(raw))
            if not os.path.isdir(new_root):
                _notify(f"Not a directory: {new_root}")
                continue
            set_library_root(new_root)
            continue

        # Multi-root configs (a `library_roots` array) scan together, exactly
        # as cli.py passes its roots list to the modes.
        root = roots if len(roots) > 1 else roots[0]
        title = f"Lattice v{VERSION}"
        if len(roots) > 1:
            title += f"  [{len(roots)} roots]"

        if _SCREEN is None:
            _reset_terminal()
        result = _select_main(title)

        if result == "fallback":
            continue  # curses died mid-session; the next pass renders the text menu

        if result == "invalid":
            if not _USE_CURSES:
                print("  Invalid selection.")
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
                raw = _prompt_str(
                    f"Change library root (current: {single}){note}", single or ""
                )
                if raw is None or not raw.strip():
                    continue  # cancelled: the saved root stays as it was
                new_root = os.path.abspath(os.path.expanduser(raw.strip()))
                if not os.path.isdir(new_root):
                    _notify(f"Not a directory: {new_root} — root unchanged.")
                    continue
                set_library_root(new_root)
                continue

            if result == (0, 0):
                _library_submenu(root)

            elif result == (0, 1):
                output = _ask("Output file (leave blank for screen)", "").strip()
                output = os.path.expanduser(output) if output else None
                layout = _ask("Path extraction layout", get_layout())
                _run_with_capture(
                    "Library Statistics",
                    run_stats,
                    root,
                    output,
                    layout=layout,
                    quiet=False,
                    footer=_out_note(output),
                )

            elif result == (1, 0):
                output = _prompt_out("Output file", DEFAULT_FLAC_OUTPUT)
                workers = _prompt_int("Workers", 4)
                pref = _ask("Preferred tool (flac/ffmpeg)", "flac").strip().lower()
                while pref not in ("flac", "ffmpeg"):
                    pref = (
                        _ask("Preferred tool must be flac or ffmpeg", "flac")
                        .strip()
                        .lower()
                    )
                _run_with_capture(
                    "Test FLAC files",
                    run_flac_mode,
                    root,
                    output,
                    workers,
                    pref,
                    quiet=False,
                    footer=_out_note(output),
                )

            elif result == (1, 1):
                output = _prompt_out("Output file", DEFAULT_MP3_OUTPUT)
                workers, ffmpeg, include_ok = _integrity_prompts()
                _run_with_capture(
                    "Test MP3 files",
                    run_mp3_mode,
                    root,
                    output,
                    workers,
                    ffmpeg,
                    only_errors=not include_ok,
                    verbose=include_ok,
                    quiet=False,
                    footer=_out_note(output),
                )

            elif result == (1, 2):
                output = _prompt_out("Output file", DEFAULT_OPUS_OUTPUT)
                workers, ffmpeg, include_ok = _integrity_prompts()
                _run_with_capture(
                    "Test Opus files",
                    run_opus_mode,
                    root,
                    output,
                    workers,
                    ffmpeg,
                    only_errors=not include_ok,
                    verbose=include_ok,
                    quiet=False,
                    footer=_out_note(output),
                )

            elif result == (1, 3):
                output = _prompt_out("Output file", DEFAULT_WAV_OUTPUT)
                workers, ffmpeg, include_ok = _integrity_prompts()
                _run_with_capture(
                    "Test WAV files",
                    run_wav_mode,
                    root,
                    output,
                    workers,
                    ffmpeg,
                    only_errors=not include_ok,
                    verbose=include_ok,
                    quiet=False,
                    footer=_out_note(output),
                )

            elif result == (1, 4):
                output = _prompt_out("Output file", DEFAULT_WMA_OUTPUT)
                workers, ffmpeg, include_ok = _integrity_prompts()
                _run_with_capture(
                    "Test WMA files",
                    run_wma_mode,
                    root,
                    output,
                    workers,
                    ffmpeg,
                    only_errors=not include_ok,
                    verbose=include_ok,
                    quiet=False,
                    footer=_out_note(output),
                )

            elif result == (2, 0):
                dry = _ask_yn("Dry run? (y/N)")
                _run_with_capture(
                    "Extract cover art", run_extract_art, root, quiet=False, dry_run=dry
                )

            elif result == (2, 1):
                output = _prompt_out("Output file", DEFAULT_MISSING_ART_OUTPUT)
                _run_with_capture(
                    "Report missing art",
                    run_missing_art,
                    root,
                    output,
                    quiet=False,
                    footer=_out_note(output),
                )

            elif result == (2, 2):
                output = _prompt_out("Output file", DEFAULT_ART_QUALITY_OUTPUT)
                min_res = _prompt_int("Minimum resolution floor", 500)
                _run_with_capture(
                    "Audit art quality",
                    run_art_quality_audit,
                    root,
                    output,
                    min_res,
                    quiet=False,
                    footer=_out_note(output),
                )

            elif result == (3, 0):
                output = _prompt_out("Output file", DEFAULT_DUPLICATES_OUTPUT)
                _run_with_capture(
                    "Find duplicate albums",
                    run_duplicates,
                    root,
                    output,
                    quiet=False,
                    footer=_out_note(output),
                )

            elif result == (3, 1):
                output = _prompt_out("Output file", DEFAULT_TAG_AUDIT_OUTPUT)
                _run_with_capture(
                    "Audit tags",
                    run_tag_audit,
                    root,
                    output,
                    quiet=False,
                    footer=_out_note(output),
                )

            elif result == (3, 2):
                output = _prompt_out("Output file", DEFAULT_BITRATE_AUDIT_OUTPUT)
                min_kbps = _prompt_int("Minimum bitrate floor (kbps)", 192)
                _run_with_capture(
                    "Audit bitrates",
                    run_bitrate_audit,
                    root,
                    output,
                    min_kbps,
                    quiet=False,
                    footer=_out_note(output),
                )

            elif result == (3, 3):
                output = _prompt_out("Output file", DEFAULT_REPLAYGAIN_AUDIT_OUTPUT)
                include_ok = _ask_yn("List fully-tagged albums? (y/N)")
                _run_with_capture(
                    "Audit ReplayGain",
                    run_replaygain_audit,
                    root,
                    output,
                    verbose=include_ok,
                    quiet=False,
                    footer=_out_note(output),
                )
        except _Cancelled:
            continue  # Esc in a prompt chain: back to the menu, nothing launched
