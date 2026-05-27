import io
import os
import sys
from contextlib import contextmanager
from typing import Any

try:
    import curses

    HAVE_CURSES = True
except ImportError:
    HAVE_CURSES = False

from lattice.utils import _reset_terminal
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
from lattice.modes.audit import run_duplicates, run_tag_audit, run_bitrate_audit

# =====================================
# Curses TUI / Fallbacks
# =====================================

_USE_CURSES = HAVE_CURSES and sys.stdin.isatty()


def _prompt_str(label: str, default: str | None) -> str:
    if _USE_CURSES:
        return _tui_prompt_str(label, default)
    try:
        raw = input(f"  {label} [{default}]: ").strip()
    except EOFError, KeyboardInterrupt:
        sys.exit(130)
    return raw or (default or "")


def _prompt_path(label: str, default: str = ".") -> str:
    """Prompt for a filesystem path, expanding ~ and making absolute."""
    return os.path.abspath(os.path.expanduser(_prompt_str(label, default)))


def _prompt_int(label: str, default: int) -> int:
    s = _prompt_str(label, str(default))
    try:
        return int(s)
    except ValueError:
        return default


def _box_menu(title: str, sections: list, width: int = 44) -> None:
    """Fallback text menu for environments without curses."""
    iw = width - 4
    print(f"\n  ╔{'═' * (width - 2)}╗")
    print(f"  ║ {title:^{iw}} ║")
    print(f"  ╠{'═' * (width - 2)}╣")
    first = True
    for header, items in sections:
        if not first:
            print(f"  ╟{'─' * (width - 2)}╢")
        first = False
        if header:
            print(f"  ║  {header:<{iw - 1}} ║")
        for item in items:
            print(f"  ║    {item:<{iw - 3}} ║")
    print(f"  ╚{'═' * (width - 2)}╝")


def _pause() -> None:
    """Wait for user acknowledgement before redrawing."""
    if _USE_CURSES:
        _tui_pause()
        return
    try:
        input("\n  Press Enter to continue...")
    except EOFError, KeyboardInterrupt:
        pass


_CP_FRAME = 1
_CP_TITLE = 2
_CP_HEADER = 3
_CP_ITEM = 4
_CP_SELECTED = 5
_CP_HINT = 6


def _init_tui_colors() -> None:
    """Set up curses color pairs for the TUI menus."""
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(_CP_FRAME, curses.COLOR_CYAN, -1)
    curses.init_pair(_CP_TITLE, curses.COLOR_WHITE, -1)
    curses.init_pair(_CP_HEADER, curses.COLOR_YELLOW, -1)
    curses.init_pair(_CP_ITEM, curses.COLOR_WHITE, -1)
    curses.init_pair(_CP_SELECTED, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(_CP_HINT, curses.COLOR_WHITE, -1)


_TUI_BOX_W = 46
_TUI_INNER = _TUI_BOX_W - 2  # chars between the two ║ borders


def _safe_addstr(stdscr, y: int, x: int, text: str, attr: int) -> None:
    """Write to curses screen, silently ignoring out-of-bounds errors."""
    try:
        stdscr.addstr(y, x, text, attr)
    except curses.error:
        pass


def _tui_select(
    title: str,
    sections: list,
    hints: str = "\u2191\u2193 Navigate  \u23ce Select  q Quit",
) -> tuple | None:
    """Full-screen arrow-key menu using curses."""
    BOX_W = _TUI_BOX_W
    INNER = _TUI_INNER

    flat: list[tuple[int, int]] = []
    for si, (_, items) in enumerate(sections):
        for ii in range(len(items)):
            flat.append((si, ii))

    def _draw(stdscr, cur: int) -> None:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        bx = max(0, (w - BOX_W) // 2)
        fa = curses.color_pair(_CP_FRAME)

        box_h = 3
        for si, (hdr, items) in enumerate(sections):
            if si > 0:
                box_h += 1
            if hdr:
                box_h += 1
            box_h += len(items)
        box_h += 1

        y = max(0, (h - box_h - 2) // 2)

        _safe_addstr(stdscr, y, bx, "\u2554" + "\u2550" * INNER + "\u2557", fa)
        y += 1

        _safe_addstr(stdscr, y, bx, "\u2551", fa)
        _safe_addstr(
            stdscr,
            y,
            bx + 1,
            f" {title:^{INNER - 2}} ",
            curses.color_pair(_CP_TITLE) | curses.A_BOLD,
        )
        _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
        y += 1

        _safe_addstr(stdscr, y, bx, "\u2560" + "\u2550" * INNER + "\u2563", fa)
        y += 1

        idx = 0
        for si, (hdr, items) in enumerate(sections):
            if si > 0:
                _safe_addstr(stdscr, y, bx, "\u255f" + "\u2500" * INNER + "\u2562", fa)
                y += 1

            if hdr:
                content = f"  {hdr}" + " " * (INNER - len(hdr) - 2)
                _safe_addstr(stdscr, y, bx, "\u2551", fa)
                _safe_addstr(
                    stdscr,
                    y,
                    bx + 1,
                    content,
                    curses.color_pair(_CP_HEADER) | curses.A_BOLD,
                )
                _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
                y += 1

            for ii, label in enumerate(items):
                is_sel = idx == cur
                if is_sel:
                    text = f" \u25ba {label}"
                    attr = curses.color_pair(_CP_SELECTED) | curses.A_BOLD
                else:
                    text = f"   {label}"
                    attr = curses.color_pair(_CP_ITEM)
                padded = text + " " * max(0, INNER - len(text))
                _safe_addstr(stdscr, y, bx, "\u2551", fa)
                _safe_addstr(stdscr, y, bx + 1, padded[:INNER], attr)
                _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
                y += 1
                idx += 1

        _safe_addstr(stdscr, y, bx, "\u255a" + "\u2550" * INNER + "\u255d", fa)
        y += 2

        hx = max(0, (w - len(hints)) // 2)
        _safe_addstr(stdscr, y, hx, hints, curses.color_pair(_CP_HINT) | curses.A_DIM)

        stdscr.refresh()

    def _run(stdscr) -> tuple | None:
        _init_tui_colors()
        curses.curs_set(0)
        cur = 0
        while True:
            _draw(stdscr, cur)
            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k")):
                cur = (cur - 1) % len(flat)
            elif key in (curses.KEY_DOWN, ord("j")):
                cur = (cur + 1) % len(flat)
            elif key in (curses.KEY_ENTER, 10, 13):
                return flat[cur]
            elif key in (ord("q"), ord("Q"), 27):
                return None
            elif key == curses.KEY_RESIZE:
                pass

    try:
        return curses.wrapper(_run)
    except curses.error:
        return None


def _tui_prompt_str(label: str, default: str | None) -> str:
    BOX_W = _TUI_BOX_W
    INNER = _TUI_INNER

    def _run(stdscr) -> str:
        _init_tui_colors()
        curses.curs_set(1)
        buf = list(default or "")

        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            bx = max(0, (w - BOX_W) // 2)
            fa = curses.color_pair(_CP_FRAME)

            y = max(0, (h - 8) // 2)

            _safe_addstr(stdscr, y, bx, "\u2554" + "\u2550" * INNER + "\u2557", fa)
            y += 1

            lbl = f"  {label}"
            padded_lbl = lbl + " " * max(0, INNER - len(lbl))
            _safe_addstr(stdscr, y, bx, "\u2551", fa)
            _safe_addstr(
                stdscr,
                y,
                bx + 1,
                padded_lbl[:INNER],
                curses.color_pair(_CP_HEADER) | curses.A_BOLD,
            )
            _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
            y += 1

            _safe_addstr(stdscr, y, bx, "\u255f" + "\u2500" * INNER + "\u2562", fa)
            y += 1

            display = "".join(buf)
            max_input = INNER - 4
            if len(display) > max_input:
                visible = "\u2026" + display[-(max_input - 1) :]
            else:
                visible = display
            input_text = f" > {visible}" + " " * max(0, INNER - len(visible) - 3)
            _safe_addstr(stdscr, y, bx, "\u2551", fa)
            _safe_addstr(
                stdscr, y, bx + 1, input_text[:INNER], curses.color_pair(_CP_ITEM)
            )
            _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
            input_y = y
            y += 1

            _safe_addstr(stdscr, y, bx, "\u255a" + "\u2550" * INNER + "\u255d", fa)
            y += 2

            hints = "\u23ce Confirm  Esc Default"
            hx = max(0, (w - len(hints)) // 2)
            _safe_addstr(
                stdscr, y, hx, hints, curses.color_pair(_CP_HINT) | curses.A_DIM
            )

            cursor_x = bx + 4 + min(len(display), max_input)
            try:
                stdscr.move(input_y, min(cursor_x, bx + BOX_W - 2))
            except curses.error:
                pass
            stdscr.refresh()

            key = stdscr.getch()
            if key in (curses.KEY_ENTER, 10, 13):
                result = "".join(buf).strip()
                return result if result else (default or "")
            elif key == 27:
                return default or ""
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                if buf:
                    buf.pop()
            elif key == curses.KEY_RESIZE:
                pass
            elif 32 <= key <= 126:
                buf.append(chr(key))

    try:
        return curses.wrapper(_run)
    except curses.error:
        try:
            raw = input(f"  {label} [{default}]: ").strip()
        except EOFError, KeyboardInterrupt:
            sys.exit(130)
        return raw or (default or "")


def _tui_pause() -> None:
    BOX_W = _TUI_BOX_W
    INNER = _TUI_INNER

    def _run(stdscr) -> None:
        _init_tui_colors()
        curses.curs_set(0)

        stdscr.erase()
        h, w = stdscr.getmaxyx()
        bx = max(0, (w - BOX_W) // 2)
        fa = curses.color_pair(_CP_FRAME)

        y = max(0, (h - 5) // 2)

        _safe_addstr(stdscr, y, bx, "\u2554" + "\u2550" * INNER + "\u2557", fa)
        y += 1

        msg = "Press Enter to continue\u2026"
        padded = f" {msg:^{INNER - 2}} "
        _safe_addstr(stdscr, y, bx, "\u2551", fa)
        _safe_addstr(
            stdscr,
            y,
            bx + 1,
            padded[:INNER],
            curses.color_pair(_CP_TITLE) | curses.A_BOLD,
        )
        _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
        y += 1

        _safe_addstr(stdscr, y, bx, "\u255a" + "\u2550" * INNER + "\u255d", fa)
        stdscr.refresh()

        while True:
            key = stdscr.getch()
            if key in (curses.KEY_ENTER, 10, 13, ord("q"), ord("Q"), 27):
                return

    try:
        curses.wrapper(_run)
    except curses.error:
        try:
            input("\n  Press Enter to continue...")
        except EOFError, KeyboardInterrupt:
            pass


_MAIN_FALLBACK_MAP: dict[str, tuple | None] = {
    "1": (0, 0),
    "l": (0, 0),
    "lib": (0, 0),
    "library": (0, 0),
    "2": (0, 1),
    "stats": (0, 1),
    "3": (1, 0),
    "flac": (1, 0),
    "4": (1, 1),
    "mp3": (1, 1),
    "5": (1, 2),
    "opus": (1, 2),
    "6": (2, 0),
    "art": (2, 0),
    "extract": (2, 0),
    "7": (2, 1),
    "missing": (2, 1),
    "8": (3, 0),
    "dup": (3, 0),
    "dupes": (3, 0),
    "9": (3, 1),
    "audit": (3, 1),
    "tags": (3, 1),
    "s": (4, 0),
    "settings": (4, 0),
    "config": (4, 0),
    "c": (4, 0),
    "q": None,
    "quit": None,
    "exit": None,
}

_LIB_FALLBACK_MAP: dict[str, tuple | None] = {
    "1": (0, 0),
    "tree": (0, 0),
    "lib": (0, 0),
    "2": (0, 1),
    "ai": (0, 1),
    "3": (0, 2),
    "wings": (0, 2),
    "4": (0, 3),
    "ai-wings": (0, 3),
    "5": (0, 4),
    "playlist": (0, 4),
    "b": None,
    "back": None,
    "": None,
}


def _fallback_input(prompt: str, mapping: dict) -> Any:
    try:
        ch = input(prompt).strip().lower()
    except EOFError, KeyboardInterrupt:
        return None
    return mapping.get(ch, "invalid")


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


def _select_main() -> tuple | None:
    if _USE_CURSES:
        return _tui_select(f"Lattice v{VERSION}", _MAIN_SECTIONS)
    _box_menu(
        f"Lattice v{VERSION}",
        [
            (
                "LIBRARY",
                ["1) Library tree & exports          \u2192", "2) Library statistics"],
            ),
            (
                "INTEGRITY",
                [
                    "3) Test FLAC files",
                    "4) Test MP3 files",
                    "5) Test Opus files",
                    "6) Test WAV files",
                    "7) Test WMA files",
                ],
            ),
            (
                "ARTWORK",
                [
                    "8) Extract cover art",
                    "9) Report missing art",
                    "10) Audit art quality",
                ],
            ),
            (
                "METADATA",
                ["11) Find duplicate albums", "12) Audit tags", "13) Audit bitrates"],
            ),
            ("SETTINGS", ["s) Change library root"]),
            ("", ["q) Quit"]),
        ],
    )
    return _fallback_input("  Select [1-13/s/q]: ", _MAIN_FALLBACK_MAP)


def _select_library() -> tuple | None:
    if _USE_CURSES:
        return _tui_select(
            "Library Tree & Exports",
            _LIB_SECTIONS,
            hints="\u2191\u2193 Navigate  \u23ce Select  Esc Back",
        )
    _box_menu(
        "Library Tree & Exports",
        [
            (
                "",
                [
                    "1) Build music library tree",
                    "2) AI-readable library export",
                    "3) Generate all wings (per-genre)",
                    "4) Generate AI wings (per-genre flat)",
                    "5) Generate smart playlist (.m3u)",
                ],
            ),
            ("", ["b) Back to main menu"]),
        ],
    )
    return _fallback_input("  Select [1-5/b]: ", _LIB_FALLBACK_MAP)


def _tui_page(title: str, content: str) -> None:
    if not _USE_CURSES:
        print(content)
        _pause()
        return

    lines = content.replace("\x00", "").expandtabs(4).split("\n")

    def _run(stdscr):
        _init_tui_colors()
        curses.curs_set(0)
        top = 0
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            fa = curses.color_pair(_CP_FRAME)

            # Width follows the longest line (up to the terminal width) so wide
            # reports — long duplicate paths, say — are not chopped at 80 columns.
            max_line_len = max((len(ln) for ln in lines), default=0)
            content_w = min(w, max(_TUI_BOX_W, max_line_len + 4))
            bx = max(0, (w - content_w) // 2)
            max_lines = max(1, h - 3)
            last_top = max(0, len(lines) - max_lines)
            top = min(top, last_top)  # keep the view valid across resizes

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

            hints = "↑↓ Scroll  PgUp/Dn  g/G Top/Bottom  q/Esc Close"
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
                    _safe_addstr(
                        stdscr,
                        i + 1,
                        bx + 2,
                        lines[top + i][: content_w - 4],
                        curses.color_pair(_CP_ITEM),
                    )
                _safe_addstr(stdscr, i + 1, bx + content_w - 1, "║", fa)

            stdscr.refresh()

            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k")):
                top = max(0, top - 1)
            elif key in (curses.KEY_DOWN, ord("j")):
                top = min(last_top, top + 1)
            elif key == curses.KEY_PPAGE:
                top = max(0, top - max_lines)
            elif key == curses.KEY_NPAGE:
                top = min(last_top, top + max_lines)
            elif key in (curses.KEY_HOME, ord("g")):
                top = 0
            elif key in (curses.KEY_END, ord("G")):
                top = last_top
            elif key in (ord("q"), ord("Q"), 27, curses.KEY_ENTER, 10, 13):
                break
            elif key == curses.KEY_RESIZE:
                pass

    try:
        curses.wrapper(_run)
    except curses.error:
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


def _run_with_capture(title: str, func, *args, **kwargs):
    with capture_output() as (out, err):
        result = func(*args, **kwargs)

    text = ""
    if isinstance(result, str) and result:
        text += result + "\n"

    out_text = out.getvalue().strip()
    if out_text:
        text += out_text + "\n"

    err_text = err.getvalue().strip()
    if err_text:
        text += "\n[Errors/Warnings]:\n" + err_text + "\n"

    text = text.strip()
    if text:
        _tui_page(title, text)
    else:
        _pause()


def _library_submenu(root: str) -> None:
    while True:
        result = _select_library()

        if result == "invalid":
            if not _USE_CURSES:
                print("  Invalid selection.")
            continue

        if result is None or result == (1, 0):
            return

        _reset_terminal()

        if result == (0, 0):
            output = (
                _prompt_str("Output file", DEFAULT_LIBRARY_OUTPUT)
                or DEFAULT_LIBRARY_OUTPUT
            )
            layout = (
                _prompt_str("Path extraction layout", "{artist}/{album}")
                or "{artist}/{album}"
            )
            show_g = _prompt_str("Include genres? (y/N)", "N").lower().startswith("y")

            def _wrap():
                write_music_library_tree(
                    root, output, layout=layout, quiet=False, show_genre=show_g
                )
                print(f"\n  Library written to {output}")

            _run_with_capture("Build music library tree", _wrap)

        elif result == (0, 1):
            output = (
                _prompt_str("Output file", DEFAULT_AI_LIBRARY_OUTPUT)
                or DEFAULT_AI_LIBRARY_OUTPUT
            )
            layout = (
                _prompt_str("Path extraction layout", "{artist}/{album}")
                or "{artist}/{album}"
            )

            def _wrap2():
                write_ai_library(root, output, layout=layout, quiet=False)
                print(f"\n  Library written to {output}")

            _run_with_capture("AI-readable library export", _wrap2)

        elif result == (0, 2):
            outdir = _prompt_str("Output directory", "wings") or "wings"
            layout = (
                _prompt_str("Path extraction layout", "{artist}/{album}")
                or "{artist}/{album}"
            )
            show_g = _prompt_str("Include genres? (y/N)", "N").lower().startswith("y")
            show_p = _prompt_str("Include paths? (y/N)", "N").lower().startswith("y")

            def _wrap3():
                write_all_wings(
                    root,
                    outdir,
                    layout=layout,
                    quiet=False,
                    show_genre=show_g,
                    show_paths=show_p,
                )
                print(f"\n  Wings generated in {outdir}")

            _run_with_capture("Generate all wings (per-genre)", _wrap3)

        elif result == (0, 3):
            outdir = _prompt_str("Output directory", "wings_ai") or "wings_ai"
            layout = (
                _prompt_str("Path extraction layout", "{artist}/{album}")
                or "{artist}/{album}"
            )

            def _wrap_ai():
                write_ai_wings(root, outdir, layout=layout, quiet=False)
                print(f"\n  AI Wings generated in {outdir}")

            _run_with_capture("Generate AI wings (per-genre flat)", _wrap_ai)

        elif result == (0, 4):
            output = (
                _prompt_str("Output file", DEFAULT_PLAYLIST_OUTPUT)
                or DEFAULT_PLAYLIST_OUTPUT
            )
            rule = _prompt_str(
                "Smart rule (e.g. \"rating >= 4 and genre == 'Jazz'\")", ""
            )
            layout = (
                _prompt_str("Path extraction layout", "{artist}/{album}")
                or "{artist}/{album}"
            )

            def _wrap4():
                generate_playlist(root, output, rule, layout=layout, quiet=False)

            _run_with_capture("Generate smart playlist", _wrap4)


def interactive_menu() -> int:
    import lattice.utils as utils
    from lattice.config import get_library_root, set_library_root

    utils.IN_TUI = True

    while True:
        root = get_library_root()
        if not root or not os.path.exists(root):
            root = _prompt_path("First run: Enter path to your music library")
            set_library_root(root)
            continue

        _reset_terminal()
        result = _select_main()

        if result == "invalid":
            if not _USE_CURSES:
                print("  Invalid selection.")
            continue

        if result is None or result == (5, 0):  # Quit (was 4, 0)
            return 0

        if result == (4, 0):  # Change root
            new_root = _prompt_path(f"Change library root (current: {root})")
            set_library_root(new_root)
            continue

        if result == (0, 0):
            _library_submenu(root)

        elif result == (0, 1):
            output = (
                _prompt_str("Output file (leave blank for screen)", "").strip() or None
            )
            _run_with_capture(
                "Library Statistics", run_stats, root, output, quiet=False
            )

        elif result == (1, 0):
            output = (
                _prompt_str("Output file", DEFAULT_FLAC_OUTPUT) or DEFAULT_FLAC_OUTPUT
            )
            workers = _prompt_int("Workers", 4)
            pref = _prompt_str("Preferred tool (flac/ffmpeg)", "flac").lower()
            _run_with_capture(
                "Test FLAC files",
                run_flac_mode,
                root,
                output,
                workers,
                pref,
                quiet=False,
            )

        elif result == (1, 1):
            output = (
                _prompt_str("Output file", DEFAULT_MP3_OUTPUT) or DEFAULT_MP3_OUTPUT
            )
            workers = _prompt_int("Workers", 4)
            include_ok = (
                _prompt_str("Include OK rows? (y/N)", "N").lower().startswith("y")
            )
            _run_with_capture(
                "Test MP3 files",
                run_mp3_mode,
                root,
                output,
                workers,
                None,
                only_errors=not include_ok,
                verbose=include_ok,
                quiet=False,
            )

        elif result == (1, 2):
            output = (
                _prompt_str("Output file", DEFAULT_OPUS_OUTPUT) or DEFAULT_OPUS_OUTPUT
            )
            workers = _prompt_int("Workers", 4)
            include_ok = (
                _prompt_str("Include OK rows? (y/N)", "N").lower().startswith("y")
            )
            _run_with_capture(
                "Test Opus files",
                run_opus_mode,
                root,
                output,
                workers,
                None,
                only_errors=not include_ok,
                verbose=include_ok,
                quiet=False,
            )

        elif result == (1, 3):
            output = (
                _prompt_str("Output file", DEFAULT_WAV_OUTPUT) or DEFAULT_WAV_OUTPUT
            )
            workers = _prompt_int("Workers", 4)
            include_ok = (
                _prompt_str("Include OK rows? (y/N)", "N").lower().startswith("y")
            )
            _run_with_capture(
                "Test WAV files",
                run_wav_mode,
                root,
                output,
                workers,
                None,
                only_errors=not include_ok,
                verbose=include_ok,
                quiet=False,
            )

        elif result == (1, 4):
            output = (
                _prompt_str("Output file", DEFAULT_WMA_OUTPUT) or DEFAULT_WMA_OUTPUT
            )
            workers = _prompt_int("Workers", 4)
            include_ok = (
                _prompt_str("Include OK rows? (y/N)", "N").lower().startswith("y")
            )
            _run_with_capture(
                "Test WMA files",
                run_wma_mode,
                root,
                output,
                workers,
                None,
                only_errors=not include_ok,
                verbose=include_ok,
                quiet=False,
            )

        elif result == (2, 0):
            dry = _prompt_str("Dry run? (y/N)", "N").lower().startswith("y")
            _run_with_capture(
                "Extract cover art", run_extract_art, root, quiet=False, dry_run=dry
            )

        elif result == (2, 1):
            output = (
                _prompt_str("Output file", DEFAULT_MISSING_ART_OUTPUT)
                or DEFAULT_MISSING_ART_OUTPUT
            )
            _run_with_capture(
                "Report missing art", run_missing_art, root, output, quiet=False
            )

        elif result == (2, 2):
            output = (
                _prompt_str("Output file", DEFAULT_ART_QUALITY_OUTPUT)
                or DEFAULT_ART_QUALITY_OUTPUT
            )
            min_res = _prompt_int("Minimum resolution floor", 500)
            _run_with_capture(
                "Audit art quality",
                run_art_quality_audit,
                root,
                output,
                min_res,
                quiet=False,
            )

        elif result == (3, 0):
            output = (
                _prompt_str("Output file", DEFAULT_DUPLICATES_OUTPUT)
                or DEFAULT_DUPLICATES_OUTPUT
            )
            _run_with_capture(
                "Find duplicate albums", run_duplicates, root, output, quiet=False
            )

        elif result == (3, 1):
            output = (
                _prompt_str("Output file", DEFAULT_TAG_AUDIT_OUTPUT)
                or DEFAULT_TAG_AUDIT_OUTPUT
            )
            _run_with_capture("Audit tags", run_tag_audit, root, output, quiet=False)

        elif result == (3, 2):
            output = (
                _prompt_str("Output file", DEFAULT_BITRATE_AUDIT_OUTPUT)
                or DEFAULT_BITRATE_AUDIT_OUTPUT
            )
            min_kbps = _prompt_int("Minimum bitrate floor (kbps)", 192)
            _run_with_capture(
                "Audit bitrates", run_bitrate_audit, root, output, min_kbps, quiet=False
            )
