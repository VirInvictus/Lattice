import os
import sys
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from lattice.config import (
    AUDIO_EXTENSIONS,
    COVER_NAMES,
    RE_CLEAN_PREFIX,
    RE_CLEAN_PATTERNS,
)

try:
    from tqdm import tqdm  # type: ignore[import-untyped]

    HAVE_TQDM = True
except ImportError:
    HAVE_TQDM = False

IN_TUI = False


def _use_color() -> bool:
    """ANSI color only for an interactive terminal: never in the TUI, never when
    piped or redirected (so reports/pipes stay clean), never when NO_COLOR is
    set. Evaluated per call because the TUI swaps stdout at runtime."""
    return not IN_TUI and "NO_COLOR" not in os.environ and sys.stdout.isatty()


def color(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _use_color() else text


def green(s: str) -> str:
    return color(s, "32")


def red(s: str) -> str:
    return color(s, "31")


def yellow(s: str) -> str:
    return color(s, "33")


def is_audio(filename: str) -> bool:
    """Check if a filename has a recognized audio extension."""
    return os.path.splitext(filename)[1].lower() in AUDIO_EXTENSIONS


def _reset_terminal() -> None:
    """Restore sane terminal state after subprocess runs.

    Subprocesses (flac, ffmpeg) in raw-bytes mode can corrupt the terminal's
    line discipline — most commonly turning off icrnl so Enter sends \\r
    (displayed as ^M) instead of \\n.  This resets to sane defaults.
    """
    if not sys.stdin.isatty():
        return
    try:
        subprocess.run(["stty", "sane"], stdin=sys.stdin, check=False)
    except Exception:
        pass


def clean_song_name(filename: str) -> str:
    name_without_ext = os.path.splitext(filename)[0]
    name_without_ext = RE_CLEAN_PREFIX.sub("", name_without_ext)
    for pattern in RE_CLEAN_PATTERNS:
        match = pattern.match(name_without_ext.strip())
        if match:
            track_num = match.group(1).zfill(2)
            title = match.group(2).strip()
            return f"{track_num}. {title}"
    return name_without_ext.strip()


def normalize_rating(val) -> float | None:
    """Normalizes various rating scales (0-100, 0-255, 0-5) to a float 0-5."""
    try:
        val = float(val)
        if val <= 5:
            return val
        elif val <= 10:
            return val / 2.0
        elif val <= 100:
            return val / 20.0
        elif val <= 255:
            return (val / 255.0) * 5.0
    except ValueError, TypeError:
        pass
    return None


def _looks_numeric(val) -> bool:
    """Check if a value looks like a number (int or float)."""
    return bool(val) and str(val).replace(".", "").isdigit()


def format_rating(rating: float | None) -> str:
    if rating is None:
        return ""
    full_stars = int(rating)
    half_star = rating - full_stars >= 0.5
    empty_stars = 5 - full_stars - (1 if half_star else 0)
    stars = "★" * full_stars
    if half_star:
        stars += "☆"
    stars += "☆" * empty_stars
    return f" [{stars} {rating:.1f}/5]"


def update_progress(current: int, total: int, prefix: str = "Progress") -> None:
    if total == 0:
        return
    percent = (current / total) * 100
    bar_length = 40
    filled_length = int(bar_length * current // total)
    bar = "█" * filled_length + "░" * (bar_length - filled_length)
    sys.stdout.write(f"\r{prefix}: |{bar}| {current}/{total} ({percent:.1f}%)")
    sys.stdout.flush()
    if current == total:
        print()


def as_roots(root) -> list[str]:
    """Normalize a single root (str) or a list of roots into a list of absolute
    paths. Lets every mode accept one root or several without changing callers."""
    roots = [root] if isinstance(root, (str, os.PathLike)) else list(root)
    return [os.path.abspath(os.path.expanduser(r)) for r in roots]


def iter_audio_dirs(root):
    """Walk one or more roots top-down, pruning hidden directories, yielding
    (root, dirpath, dirnames, filenames). The yielded root lets callers compute
    a display path relative to whichever root a file actually lives under."""
    for r in as_roots(root):
        for dirpath, dirs, files in os.walk(r):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            yield r, dirpath, dirs, files


def relpath_under(path: str, root) -> str:
    """Display path relative to whichever root contains it. With a single root
    this is a plain relpath. With several roots the owning root's basename is
    prefixed, so an album living in two libraries renders distinguishably
    instead of as two identical relative paths."""
    roots = as_roots(root)
    for r in roots:
        if path == r or path.startswith(r + os.sep):
            rel = os.path.relpath(path, r)
            if len(roots) > 1:
                return os.path.join(os.path.basename(r.rstrip(os.sep)), rel)
            return rel
    return path


def count_audio_files(root) -> int:
    total = 0
    for _r, _dp, _dirs, files in iter_audio_dirs(root):
        total += sum(1 for f in files if is_audio(f))
    return total


def default_tag_workers() -> int:
    """Thread count for tag reads. Tag parsing is largely I/O-bound (open the
    file, read a header), so a small pool overlaps that latency without
    oversubscribing; capped to stay friendly on shared/network filesystems."""
    return max(1, min(16, (os.cpu_count() or 4) * 2))


def map_concurrent(fn, paths, pbar=None, workers: int | None = None) -> dict:
    """Apply `fn` to each path concurrently, returning {path: fn(path)}. Read
    order is non-deterministic, so callers must group/sort their own output.
    `pbar.update(1)` is called as each path completes."""
    paths = list(paths)
    n = workers or default_tag_workers()
    result: dict = {}
    if n <= 1 or len(paths) <= 1:
        for p in paths:
            result[p] = fn(p)
            if pbar is not None:
                pbar.update(1)
        return result

    with ThreadPoolExecutor(max_workers=n) as ex:
        futures = {ex.submit(fn, p): p for p in paths}
        for fut in as_completed(futures):
            result[futures[fut]] = fut.result()
            if pbar is not None:
                pbar.update(1)
    return result


def read_tags_concurrent(paths, pbar=None, workers: int | None = None) -> dict:
    """Read a TagBundle for each path, concurrently, returning {path: TagBundle}.

    get_all_tags is imported lazily here: tags.py imports from utils, so a
    top-level import would be circular."""
    from lattice.tags import get_all_tags

    return map_concurrent(get_all_tags, paths, pbar=pbar, workers=workers)


def _decode_bytes(b: bytes) -> str:
    for enc in ("utf-8", "mbcs", "latin-1"):
        try:
            return b.decode(enc, errors="strict")
        except UnicodeDecodeError, LookupError:
            continue
    return b.decode("latin-1", errors="replace")


def run_proc(args: list[str]) -> tuple[int, str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("LANG", "C.UTF-8")
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        env=env,
    )
    try:
        out_b, err_b = proc.communicate()
    except KeyboardInterrupt:
        try:
            proc.kill()
        finally:
            proc.wait()
        raise
    return proc.returncode, _decode_bytes(out_b).strip(), _decode_bytes(err_b).strip()


def has_tool(name: str) -> bool:
    return shutil.which(name) is not None


def _has_cover_file(directory: str) -> bool:
    """Case-insensitive check for existing cover art files in a directory."""
    try:
        existing = {f.lower() for f in os.listdir(directory)}
    except OSError:
        return False
    return bool(existing & COVER_NAMES)


def parse_layout(rel_path: str, layout: str) -> dict:
    """Extract metadata from a relative path based on a layout pattern like {artist}/{album}"""
    parts = os.path.dirname(rel_path).split(os.sep)
    layout_parts = [p for p in layout.replace("\\", "/").split("/") if p]
    result = {}
    for i, l_part in enumerate(layout_parts):
        if i < len(parts):
            key = l_part.strip("{}").lower()
            result[key] = parts[i]
    return result


# Mirrors tui.py's _TUI_BOX_W and the _CP_FRAME/_CP_HEADER color-pair ids (the
# TUI initialises those pairs before any mode runs). Mirrored by hand with this
# cross-reference because utils can't import tui (tui imports utils).
_TUI_BOX_W = 46
_CP_FRAME = 1
_CP_HEADER = 3

# The persistent TUI screen, when an interactive session owns one (published
# by tui.interactive_menu; see T7). _TUIPbar draws into it instead of
# initscr()'ing a screen of its own, so a mode's progress no longer tears the
# session's terminal state down and back up.
_SHARED_SCREEN = None


def set_shared_screen(scr) -> None:
    global _SHARED_SCREEN
    _SHARED_SCREEN = scr


class _TUIPbar:
    """A progress bar that renders in a curses box to match the TUI style."""

    # Redraws are throttled (a full-screen erase per file on a 100k-file scan
    # is visible flicker and wasted work); the final update always draws.
    _MIN_REDRAW_S = 0.1

    def __init__(self, total: int, desc: str):
        self.total = total
        self.desc = desc
        self.current = 0
        self._last_draw = 0.0
        self.draw()

    def update(self, n: int = 1) -> None:
        self.current += n
        if (
            self.current >= self.total
            or time.monotonic() - self._last_draw >= self._MIN_REDRAW_S
        ):
            self.draw()

    def draw(self) -> None:
        self._last_draw = time.monotonic()
        try:
            import curses

            # Draw into the session's persistent screen when one is active.
            # Without one (a mode invoked outside interactive_menu), initscr()
            # starts a standalone screen; close() ends that one so what
            # follows starts from a sane terminal.
            s = _SHARED_SCREEN
            if s is None:
                s = curses.initscr()
            s.erase()
            h, w = s.getmaxyx()
            box_w = _TUI_BOX_W
            inner = box_w - 2
            bx = max(0, (w - box_w) // 2)
            y = max(0, (h - 6) // 2)

            s.addstr(y, bx, "╔" + "═" * inner + "╗", curses.color_pair(_CP_FRAME))
            s.addstr(y + 1, bx, "║", curses.color_pair(_CP_FRAME))
            s.addstr(
                y + 1,
                bx + 1,
                f" {self.desc}".ljust(inner),
                curses.color_pair(_CP_HEADER) | curses.A_BOLD,
            )
            s.addstr(y + 1, bx + box_w - 1, "║", curses.color_pair(_CP_FRAME))
            s.addstr(y + 2, bx, "╠" + "═" * inner + "╣", curses.color_pair(_CP_FRAME))

            percent = self.current / max(1, self.total)
            bar_len = inner - 10
            filled = int(bar_len * percent)
            bar = "█" * filled + "░" * (bar_len - filled)
            pct_str = f"{int(percent * 100):3d}%"

            s.addstr(y + 3, bx, "║", curses.color_pair(_CP_FRAME))
            s.addstr(y + 3, bx + 1, f" {bar} {pct_str} ".ljust(inner))
            s.addstr(y + 3, bx + box_w - 1, "║", curses.color_pair(_CP_FRAME))
            info = f" {self.current}/{self.total} · Ctrl-C cancels"
            s.addstr(y + 4, bx, "║", curses.color_pair(_CP_FRAME))
            s.addstr(y + 4, bx + 1, info[:inner].ljust(inner))
            s.addstr(y + 4, bx + box_w - 1, "║", curses.color_pair(_CP_FRAME))
            s.addstr(y + 5, bx, "╚" + "═" * inner + "╝", curses.color_pair(_CP_FRAME))
            s.refresh()
        except Exception:
            # curses.error, or curses missing entirely: progress is cosmetic.
            pass

    def close(self) -> None:
        # End the standalone screen draw() started so well-behaved modes hand
        # back a sane terminal as soon as their bar closes. A session screen
        # is the session's to tear down, not the bar's.
        if _SHARED_SCREEN is not None:
            return
        try:
            import curses

            if not curses.isendwin():
                curses.endwin()
        except Exception:
            pass


class _FallbackProgress:
    """Simple progress bar for when tqdm is not installed."""

    __slots__ = ("_current", "_total", "_desc", "_quiet")

    def __init__(self, total: int, desc: str, quiet: bool):
        self._current = 0
        self._total = total
        self._desc = desc
        self._quiet = quiet

    def update(self, n: int = 1) -> None:
        self._current += n
        if not self._quiet:
            update_progress(self._current, self._total, self._desc)

    def close(self) -> None:
        pass


def _make_pbar(total: int, desc: str, quiet: bool):
    """Create a progress bar — tqdm if available, else a simple fallback."""
    if IN_TUI:
        return _TUIPbar(total, desc)
    if HAVE_TQDM and not quiet:
        return tqdm(total=total, unit="file", desc=desc, dynamic_ncols=True)
    return _FallbackProgress(total, desc, quiet)
