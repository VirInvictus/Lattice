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

_USE_CURSES = HAVE_CURSES and sys.stdin.isatty()

# T7: one persistent curses screen per interactive session. interactive_menu
# opens it once and every widget draws into it, so multi-prompt flows no
# longer flash to the shell between widgets (each widget used to be its own
# curses.wrapper init/teardown). None when no session owns a screen — widgets
# invoked directly then fall back to a one-shot wrapper session.
_SCREEN = None


def _with_screen(fn):
    """Run a widget body against the session's persistent screen, or in a
    one-shot curses.wrapper session when no session owns one."""
    if _SCREEN is not None:
        return fn(_SCREEN)
    return curses.wrapper(fn)


def _open_screen():
    """Start the session screen (initscr + the modes curses.wrapper would
    set). Returns the screen, or None when curses can't start on this
    terminal — the caller degrades the whole session to the text menu."""
    try:
        stdscr = curses.initscr()
        curses.noecho()
        curses.cbreak()
        stdscr.keypad(True)
        _init_tui_colors()
        return stdscr
    except curses.error:
        # initscr may have partially engaged the terminal; put it back.
        try:
            if not curses.isendwin():
                curses.endwin()
        except curses.error:
            pass
        return None


def _close_screen() -> None:
    """End the session screen. Idempotent and guarded, so it is safe after a
    mid-session degrade already ended the screen."""
    global _SCREEN
    _SCREEN = None
    utils.set_shared_screen(None)
    if not HAVE_CURSES:
        return
    try:
        if not curses.isendwin():
            try:
                curses.echo()
                curses.nocbreak()
            except curses.error:
                pass
            curses.endwin()
    except curses.error:
        pass


def _degrade_to_text() -> None:
    """A mid-session curses failure (terminal died, capability lost): suspend
    the screen and flip the whole session to the text fallback. endwin puts
    the terminal back in normal mode and nothing refreshes it afterwards, so
    plain print/input work from here on."""
    global _USE_CURSES
    _USE_CURSES = False
    utils.IN_TUI = False
    _close_screen()


class _Cancelled(Exception):
    """Raised when the user cancels a prompt (Esc in the TUI, Ctrl-C/EOF at a
    text prompt); the active prompt chain unwinds back to the menu instead of
    launching a mode with defaults."""


def _prompt_str(label: str, default: str | None) -> str | None:
    """One prompt. Returns the entered value (the default on bare Enter), or
    None when the user cancelled."""
    if _USE_CURSES:
        return _tui_prompt_str(label, default)
    try:
        raw = input(f"  {label} [{default}]: ").strip()
    except EOFError, KeyboardInterrupt:
        print()
        return None
    return raw or (default or "")


def _ask(label: str, default: str | None) -> str:
    """_prompt_str that raises _Cancelled instead of returning None, so a
    multi-prompt handler aborts as one unit."""
    val = _prompt_str(label, default)
    if val is None:
        raise _Cancelled
    return val


def _ask_yn(label: str, default: str = "N") -> bool:
    return _ask(label, default).lower().startswith("y")


def _prompt_out(label: str, default: str) -> str:
    """Output-path prompt: expands ~ (no shell is there to do it) but is not
    made absolute, so relative paths keep their current meaning."""
    return os.path.expanduser(_ask(label, default) or default)


def _out_note(path: str | None) -> str:
    """Results-pager footer saying where a report landed, so 'where did my
    report go' answers itself."""
    return f"Report written to {os.path.abspath(path)}" if path else ""


def _prompt_path(label: str, default: str = ".") -> str | None:
    """Prompt for a filesystem path, expanding ~ and making absolute. None when
    cancelled or left blank with no default (a blank answer is never silently
    absolutized into the CWD)."""
    raw = _prompt_str(label, default)
    if raw is None or not raw.strip():
        return None
    return os.path.abspath(os.path.expanduser(raw))


def _prompt_int(label: str, default: int) -> int:
    prompt = label
    while True:
        s = _ask(prompt, str(default)).strip()
        try:
            return int(s)
        except ValueError:
            prompt = f"{label} (not a number, try again)"


def _notify(msg: str) -> None:
    """A notice the user must see before the next menu redraw."""
    if _USE_CURSES:
        _tui_page("Notice", msg)
    else:
        print(f"  {msg}")


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
    """Set up curses color pairs for the TUI menus. Non-fatal: a terminal
    without color support gets a monochrome TUI instead of a dead one."""
    try:
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(_CP_FRAME, curses.COLOR_CYAN, -1)
        curses.init_pair(_CP_TITLE, curses.COLOR_WHITE, -1)
        curses.init_pair(_CP_HEADER, curses.COLOR_YELLOW, -1)
        curses.init_pair(_CP_ITEM, curses.COLOR_WHITE, -1)
        curses.init_pair(_CP_SELECTED, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(_CP_HINT, curses.COLOR_WHITE, -1)
    except curses.error:
        pass


def _curs_set(visibility: int) -> None:
    """curs_set raises on terminals without cursor-visibility support; the
    cursor is cosmetic, so never let it kill a widget."""
    try:
        curses.curs_set(visibility)
    except curses.error:
        pass


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
        sel_row = 3  # offset of the selected item from the box top
        idx0 = 0
        for si, (hdr, items) in enumerate(sections):
            if si > 0:
                box_h += 1
            if hdr:
                box_h += 1
            if idx0 <= cur < idx0 + len(items):
                sel_row = box_h + (cur - idx0)
            idx0 += len(items)
            box_h += len(items)
        box_h += 1

        y = max(0, (h - box_h - 2) // 2)
        if y + sel_row >= h - 1:
            # Terminal shorter than the menu: shift the box up so the selected
            # row stays visible (rows scrolled off the top just don't draw).
            y = (h - 2) - sel_row

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

            for _ii, label in enumerate(items):
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
        _curs_set(0)
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
        return _with_screen(_run)
    except curses.error:
        # A real curses failure (dumb terminal, TERM=vt100), not a user Quit:
        # degrade the whole session to the text fallback and hand the menu
        # loop a sentinel it re-enters on, instead of silently exiting 0.
        _degrade_to_text()
        return "fallback"


def _tui_prompt_str(label: str, default: str | None) -> str | None:
    """Boxed single-line prompt. Enter accepts (bare Enter = the default);
    Esc cancels and returns None; Ctrl-U clears the field."""
    BOX_W = _TUI_BOX_W
    INNER = _TUI_INNER

    def _run(stdscr) -> str | None:
        _init_tui_colors()
        _curs_set(1)
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

            hints = "\u23ce Accept  Esc Cancel  Ctrl-U Clear"
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
                return None  # Esc cancels; it must never launch with defaults
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                if buf:
                    buf.pop()
            elif key == 21:  # Ctrl-U: clear the field (pre-filled defaults)
                buf.clear()
            elif key == curses.KEY_RESIZE:
                pass
            elif 32 <= key <= 126:
                buf.append(chr(key))

    try:
        return _with_screen(_run)
    except KeyboardInterrupt:
        return None  # Ctrl-C at a prompt cancels, exactly like Esc
    except curses.error:
        _degrade_to_text()
        try:
            raw = input(f"  {label} [{default}]: ").strip()
        except EOFError, KeyboardInterrupt:
            print()
            return None
        return raw or (default or "")


def _tui_pause() -> None:
    BOX_W = _TUI_BOX_W
    INNER = _TUI_INNER

    def _run(stdscr) -> None:
        _init_tui_colors()
        _curs_set(0)

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
        _with_screen(_run)
    except KeyboardInterrupt:
        pass
    except curses.error:
        _degrade_to_text()
        try:
            input("\n  Press Enter to continue...")
        except EOFError, KeyboardInterrupt:
            pass


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
        _init_tui_colors()
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

    if footer:
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
