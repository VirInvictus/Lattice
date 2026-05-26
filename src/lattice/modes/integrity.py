import os
import sys
import subprocess
import time
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Tuple, List, Optional, Dict, Any

from lattice.utils import run_proc, has_tool, _make_pbar
from lattice.tags import HAVE_MUTAGEN_MP3, MUTAGEN_MP3
from lattice.config import (
    DEFAULT_MP3_OUTPUT,
    DEFAULT_OPUS_OUTPUT,
    DEFAULT_WAV_OUTPUT,
    DEFAULT_WMA_OUTPUT,
)

# =====================================
# Mode: FLAC integrity
# =====================================


def test_with_flac(filepath: str) -> Tuple[bool, str]:
    code, out, err = run_proc(["flac", "-t", "-s", str(filepath)])
    if code == 0:
        return True, ""
    return False, err or out or f"flac exited with code {code}"


# ffmpeg's format autodetection can mis-probe a valid file (e.g. an MP3 with a
# large ID3v2 tag scored as RIFF), reporting a bogus decode failure. Forcing the
# demuxer from the extension sidesteps that. Files outside this map fall back to
# autodetection.
_FFMPEG_DEMUXER = {
    ".mp3": "mp3",
    ".opus": "ogg",
    ".ogg": "ogg",
    ".flac": "flac",
    ".wav": "wav",
    ".wma": "asf",
    ".m4a": "mov",
}


def test_with_ffmpeg(filepath: str) -> Tuple[bool, str]:
    # -f flac forces the demuxer; -vn skips the embedded cover so a malformed
    # picture is never mistaken for an audio fault.
    code, out, err = run_proc(
        [
            "ffmpeg",
            "-v",
            "error",
            "-nostats",
            "-f",
            "flac",
            "-i",
            str(filepath),
            "-vn",
            "-f",
            "null",
            "-",
        ]
    )
    if code == 0 and not err:
        return True, ""
    if code == 0 and err:
        return False, err
    return False, err or out or f"ffmpeg exited with code {code}"


def test_flac(filepath: str, prefer: str) -> Tuple[bool, str, str]:
    have_flac = has_tool("flac")
    have_ffmpeg = has_tool("ffmpeg")

    # Build tool order based on preference
    tools: List[Tuple[str, Any]] = []
    if prefer == "ffmpeg":
        if have_ffmpeg:
            tools.append(("ffmpeg", test_with_ffmpeg))
        if have_flac:
            tools.append(("flac", test_with_flac))
    else:
        if have_flac:
            tools.append(("flac", test_with_flac))
        if have_ffmpeg:
            tools.append(("ffmpeg", test_with_ffmpeg))

    if not tools:
        return False, "none", "Neither 'ffmpeg' nor 'flac' found in PATH."

    failures: List[Tuple[str, str]] = []
    for name, func in tools:
        ok, msg = func(filepath)
        if ok:
            return True, name, ""
        failures.append((name, msg))
    # All tools failed — report the preferred (first) tool's result, which
    # carries the more informative message (e.g. libFLAC's "LOST_SYNC after
    # N samples" vs ffmpeg's terse "invalid sync code").
    return False, failures[0][0], failures[0][1]


def run_flac_mode(
    root: str, output: str, workers: int, prefer: str, *, quiet: bool = False
) -> int:
    root = os.path.abspath(root)
    flacs = _find_files_by_ext_path(Path(root), ".flac")
    total = len(flacs)

    if total == 0:
        if not quiet:
            print(f"No FLAC files found under: {root}")
        return 0

    have_flac = has_tool("flac")
    have_ffmpeg = has_tool("ffmpeg")
    if not (have_flac or have_ffmpeg):
        if not quiet:
            print("ERROR: Neither 'flac' nor 'ffmpeg' found in PATH.", file=sys.stderr)
        return 2
    if not have_flac and prefer != "ffmpeg" and not quiet:
        print(
            "[warn] 'flac' not found; using ffmpeg for FLAC verification. "
            "ffmpeg's decoder is stricter and may flag valid files.",
            file=sys.stderr,
        )

    if not quiet:
        print(f"Found {total} FLAC files under: {root}")

    errors: List[Tuple[str, str, str]] = []

    def worker(path: Path) -> Tuple[str, bool, str, str]:
        try:
            ok, method, msg = test_flac(str(path), prefer)
            return str(path), ok, method, msg
        except KeyboardInterrupt:
            raise
        except Exception as e:
            return str(path), False, "exception", repr(e)

    pbar = _make_pbar(total, "Testing FLACs", quiet)
    ex: Optional[ThreadPoolExecutor] = None
    futures: Dict = {}
    try:
        ex = ThreadPoolExecutor(max_workers=max(1, workers))
        futures = {ex.submit(worker, p): p for p in flacs}
        for fut in as_completed(futures):
            path, ok, method, msg = fut.result()
            if not ok:
                errors.append((path, method, msg))
            pbar.update(1)
    except KeyboardInterrupt:
        if not quiet:
            print("\nInterrupted by user. Cancelling FLAC checks...")
        if ex is not None:
            for f in futures:
                f.cancel()
            ex.shutdown(cancel_futures=True)
        return 130
    finally:
        if ex is not None:
            ex.shutdown(wait=True)
        pbar.close()

    if errors:
        out_path = os.path.abspath(output)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("FLAC INTEGRITY REPORT\n")
            f.write(f"Root: {root}\n")
            f.write(f"Scanned: {total}  Errors: {len(errors)}\n")
            f.write("=" * 60 + "\n\n")
            for i, (path, method, msg) in enumerate(errors, 1):
                rel = os.path.relpath(path, root)
                f.write(f"  {i:>3}. {rel}\n")
                f.write(f"       Tool: {method}\n")
                f.write(f"       Error: {msg}\n\n")
        if not quiet:
            print(
                f"❗ Found {len(errors)} problematic FLAC file(s). Wrote details to: {out_path}"
            )
    elif not quiet:
        print("✅ All FLAC files passed integrity checks.")
    return 1 if errors else 0


# =====================================
# Mode: MP3 decode check
# =====================================


def _find_ffmpeg(explicit_path: Optional[str]) -> Optional[str]:
    if explicit_path:
        p = Path(explicit_path)
        return str(p) if p.exists() else None
    return shutil.which("ffmpeg")


def _find_files_by_ext_path(root: Path, ext: str) -> List[Path]:
    """Walk tree and return all files matching extension as Path objects."""
    out: List[Path] = []
    root = root.expanduser().resolve()
    if root.is_file() and root.suffix.lower() == ext:
        return [root]
    for dirpath, _, files in os.walk(root):
        for fn in files:
            if os.path.splitext(fn)[1].lower() == ext:
                out.append(Path(dirpath) / fn)
    return out


def _mutagen_header_info(path: Path) -> Dict[str, Any]:
    if not HAVE_MUTAGEN_MP3:
        return {}
    try:
        audio = MUTAGEN_MP3(path)
        info = getattr(audio, "info", None)
        if not info:
            return {}
        return {
            "duration_s": round(getattr(info, "length", 0.0) or 0.0, 3),
            "bitrate_kbps": int((getattr(info, "bitrate", 0) or 0) / 1000),
            "sample_rate_hz": getattr(info, "sample_rate", None),
            "mode": getattr(info, "mode", None),
            "vbr_mode": getattr(info, "bitrate_mode", None).__class__.__name__
            if getattr(info, "bitrate_mode", None)
            else None,
        }
    except Exception:
        return {}


def _ffmpeg_decode_check(ffmpeg_path: Optional[str], path: Path) -> Tuple[bool, str]:
    if not ffmpeg_path:
        return True, "FFmpeg not available; skipped decode check (status=warn)"
    cmd = [ffmpeg_path, "-v", "error", "-nostats", "-hide_banner"]
    # Force the demuxer from the extension so format autodetection can't
    # mis-probe a valid file and report a false failure.
    demuxer = _FFMPEG_DEMUXER.get(path.suffix.lower())
    if demuxer:
        cmd += ["-f", demuxer]
    # -vn drops non-audio streams (e.g. an embedded cover) so a malformed
    # picture is never mistaken for an audio fault.
    cmd += ["-i", str(path), "-vn", "-f", "null", "-"]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as e:
        return False, f"FFmpeg invocation failed: {e!r}"
    stderr = (proc.stderr or "").strip()
    if stderr:
        return False, stderr
    return True, "decode ok"


def _scan_one_file(
    path: Path, ffmpeg_path: Optional[str], *, enrich: bool = False
) -> Dict[str, Any]:
    """Scan a single audio file for decode errors. If enrich=True, also pull
    mutagen header info (bitrate, duration, sample rate, VBR mode)."""
    row: Dict[str, Any] = {
        "path": str(path),
        "size_bytes": None,
        "status": "ok",
        "details": "",
    }
    if enrich:
        row.update(
            {
                "duration_s": None,
                "bitrate_kbps": None,
                "sample_rate_hz": None,
                "mode": None,
                "vbr_mode": None,
            }
        )

    try:
        row["size_bytes"] = path.stat().st_size
    except Exception as e:
        row["status"] = "error"
        row["details"] = f"stat failed: {e!r}"
        return row

    if enrich:
        row.update({k: v for k, v in _mutagen_header_info(path).items() if k in row})

    ok, msg = _ffmpeg_decode_check(ffmpeg_path, path)
    if "FFmpeg not available" in msg:
        row["status"] = "warn"
        row["details"] = msg
    elif not ok:
        row["status"] = "error"
        row["details"] = msg
    else:
        row["details"] = msg
    return row


def _format_row_meta(row: Dict[str, Any]) -> str:
    """Format metadata fields into a compact summary string."""
    parts: List[str] = []
    if row.get("bitrate_kbps"):
        parts.append(f"{row['bitrate_kbps']}kbps")
    if row.get("sample_rate_hz"):
        parts.append(f"{row['sample_rate_hz']}Hz")
    if row.get("duration_s"):
        parts.append(f"{row['duration_s']}s")
    if row.get("vbr_mode") and row["vbr_mode"] != "None":
        parts.append(row["vbr_mode"])
    return "  ".join(parts)


def _run_decode_scan(
    root: str,
    output: str,
    workers: int,
    ffmpeg: Optional[str],
    *,
    ext: str,
    report_title: str,
    default_output: str,
    ffmpeg_required: bool,
    enrich: bool,
    only_errors: bool,
    verbose: bool,
    quiet: bool,
) -> int:
    """Unified decode-check scanner for MP3, Opus, and future formats."""
    root_path = Path(os.path.abspath(root))
    ffmpeg_path = _find_ffmpeg(ffmpeg)

    if not ffmpeg_path:
        if ffmpeg_required:
            if not quiet:
                print(
                    f"[warn] FFmpeg not found. Required for {ext.strip('.')} decode testing.",
                    file=sys.stderr,
                )
            return 2
        elif not quiet:
            print(
                "[warn] FFmpeg not found. Install it or pass --ffmpeg /path/to/ffmpeg",
                file=sys.stderr,
            )

    targets = _find_files_by_ext_path(root_path, ext)

    if not targets:
        if not quiet:
            print(f"No {ext} files found.", file=sys.stderr)
        return 0

    label = ext.strip(".").upper()
    started = time.time()
    oks = warns = errs = 0
    results: List[Dict[str, Any]] = []

    pbar = _make_pbar(len(targets), f"Scanning {label}", quiet)
    ex: Optional[ThreadPoolExecutor] = None
    futures: Dict = {}

    if verbose:
        only_errors = False
        quiet = False

    try:
        ex = ThreadPoolExecutor(max_workers=max(1, workers))
        futures = {
            ex.submit(_scan_one_file, p, ffmpeg_path, enrich=enrich): p for p in targets
        }

        for fut in as_completed(futures):
            row = fut.result()
            status = row.get("status")
            if status == "ok":
                oks += 1
            elif status == "warn":
                warns += 1
            else:
                errs += 1

            if not (only_errors and status == "ok"):
                results.append(row)

            pbar.update(1)

    except KeyboardInterrupt:
        if not quiet:
            print(f"\nInterrupted by user. Cancelling {label} scan…", file=sys.stderr)
        if ex is not None:
            for f in futures:
                f.cancel()
            ex.shutdown(cancel_futures=True)
        return 130
    finally:
        if ex is not None:
            ex.shutdown(wait=True)
        pbar.close()

    elapsed = time.time() - started
    out_path = Path(output or default_output).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"{report_title}\n")
        f.write(f"Root: {root_path}\n")
        f.write(f"Scanned: {len(targets)}  OK: {oks}  Warn: {warns}  Error: {errs}\n")
        f.write(f"Elapsed: {elapsed:.1f}s\n")
        f.write("=" * 60 + "\n\n")

        error_rows = [r for r in results if r["status"] == "error"]
        warn_rows = [r for r in results if r["status"] == "warn"]
        ok_rows = [r for r in results if r["status"] == "ok"]

        if error_rows:
            f.write(f"ERRORS ({len(error_rows)})\n")
            f.write("-" * 40 + "\n")
            for r in error_rows:
                rel = os.path.relpath(r["path"], str(root_path))
                f.write(f"  {rel}\n")
                if r.get("details"):
                    f.write(f"    {r['details']}\n")
                if enrich:
                    meta = _format_row_meta(r)
                    if meta:
                        f.write(f"    {meta}\n")
                f.write("\n")

        if warn_rows:
            f.write(f"WARNINGS ({len(warn_rows)})\n")
            f.write("-" * 40 + "\n")
            for r in warn_rows:
                rel = os.path.relpath(r["path"], str(root_path))
                f.write(f"  {rel}\n")
                if r.get("details"):
                    f.write(f"    {r['details']}\n")
                f.write("\n")

        if ok_rows:
            f.write(f"OK ({len(ok_rows)})\n")
            f.write("-" * 40 + "\n")
            for r in ok_rows:
                rel = os.path.relpath(r["path"], str(root_path))
                if enrich:
                    meta = _format_row_meta(r)
                    if meta:
                        f.write(f"  {rel}  [{meta}]\n")
                        continue
                f.write(f"  {rel}\n")

    if not quiet:
        print(f"\nScanned: {len(targets)} files in {elapsed:.1f}s")
        print(f"ok: {oks}  warn: {warns}  error: {errs}")
        print(f"Report written to: {out_path}")
    return 1 if errs > 0 else 0


def run_mp3_mode(
    root: str,
    output: str,
    workers: int,
    ffmpeg: Optional[str],
    *,
    only_errors: bool,
    verbose: bool,
    quiet: bool,
) -> int:
    return _run_decode_scan(
        root,
        output,
        workers,
        ffmpeg,
        ext=".mp3",
        report_title="MP3 INTEGRITY REPORT",
        default_output=DEFAULT_MP3_OUTPUT,
        ffmpeg_required=False,
        enrich=True,
        only_errors=only_errors,
        verbose=verbose,
        quiet=quiet,
    )


def run_opus_mode(
    root: str,
    output: str,
    workers: int,
    ffmpeg: Optional[str],
    *,
    only_errors: bool,
    verbose: bool,
    quiet: bool,
) -> int:
    return _run_decode_scan(
        root,
        output,
        workers,
        ffmpeg,
        ext=".opus",
        report_title="OPUS INTEGRITY REPORT",
        default_output=DEFAULT_OPUS_OUTPUT,
        ffmpeg_required=True,
        enrich=False,
        only_errors=only_errors,
        verbose=verbose,
        quiet=quiet,
    )


def run_wav_mode(
    root: str,
    output: str,
    workers: int,
    ffmpeg: Optional[str],
    *,
    only_errors: bool,
    verbose: bool,
    quiet: bool,
) -> int:
    return _run_decode_scan(
        root,
        output,
        workers,
        ffmpeg,
        ext=".wav",
        report_title="WAV INTEGRITY REPORT",
        default_output=DEFAULT_WAV_OUTPUT,
        ffmpeg_required=True,
        enrich=False,
        only_errors=only_errors,
        verbose=verbose,
        quiet=quiet,
    )


def run_wma_mode(
    root: str,
    output: str,
    workers: int,
    ffmpeg: Optional[str],
    *,
    only_errors: bool,
    verbose: bool,
    quiet: bool,
) -> int:
    return _run_decode_scan(
        root,
        output,
        workers,
        ffmpeg,
        ext=".wma",
        report_title="WMA INTEGRITY REPORT",
        default_output=DEFAULT_WMA_OUTPUT,
        ffmpeg_required=True,
        enrich=False,
        only_errors=only_errors,
        verbose=verbose,
        quiet=quiet,
    )
