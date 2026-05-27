import os
import sys
import shutil
import subprocess

from lattice.config import (
    AUDIO_EXTENSIONS,
    COVER_NAMES,
    RE_CLEAN_PREFIX,
    RE_CLEAN_PATTERNS,
)

try:
    from tqdm import tqdm

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


def count_audio_files(root_dir: str) -> int:
    total = 0
    for _, _, files in os.walk(root_dir):
        total += sum(1 for f in files if is_audio(f))
    return total


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


class _TUIPbar:
    """A progress bar that renders in a curses box to match the TUI style."""

    def __init__(self, total: int, desc: str):
        self.total = total
        self.desc = desc
        self.current = 0
        self.draw()

    def update(self, n: int = 1) -> None:
        self.current += n
        self.draw()

    def draw(self) -> None:
        import curses

        try:
            # The TUI already has a screen active; initscr() returns that same
            # window without re-initializing, and we never call endwin() here.
            s = curses.initscr()
            s.erase()
            h, w = s.getmaxyx()
            box_w = 46  # Same width as TUI
            inner = box_w - 2
            bx = max(0, (w - box_w) // 2)
            y = max(0, (h - 5) // 2)

            s.addstr(y, bx, "╔" + "═" * inner + "╗", curses.color_pair(1))
            s.addstr(y + 1, bx, "║", curses.color_pair(1))
            s.addstr(
                y + 1,
                bx + 1,
                f" {self.desc}".ljust(inner),
                curses.color_pair(3) | curses.A_BOLD,
            )
            s.addstr(y + 1, bx + box_w - 1, "║", curses.color_pair(1))
            s.addstr(y + 2, bx, "╠" + "═" * inner + "╣", curses.color_pair(1))

            percent = self.current / max(1, self.total)
            bar_len = inner - 10
            filled = int(bar_len * percent)
            bar = "█" * filled + "░" * (bar_len - filled)
            pct_str = f"{int(percent * 100):3d}%"

            s.addstr(y + 3, bx, "║", curses.color_pair(1))
            s.addstr(y + 3, bx + 1, f" {bar} {pct_str} ".ljust(inner))
            s.addstr(y + 3, bx + box_w - 1, "║", curses.color_pair(1))
            s.addstr(y + 4, bx, "╚" + "═" * inner + "╝", curses.color_pair(1))
            s.refresh()
        except curses.error:
            pass

    def close(self) -> None:
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
