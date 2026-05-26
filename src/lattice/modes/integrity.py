import os
import re
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
# Decode-result classification
# =====================================

TIER_OK = "OK"
TIER_METADATA = "METADATA"
TIER_SUSPECT = "SUSPECT"
TIER_CORRUPT = "CORRUPT"
TIER_ORDER = (TIER_CORRUPT, TIER_SUSPECT, TIER_METADATA, TIER_OK)

_RE_FLAC_LOSTSYNC = re.compile(r"LOST_SYNC after processing (\d+) samples")

# Tag/container parse complaints — the audio stream is unaffected. (-vn already
# suppresses most embedded-cover lines before they reach us.)
_METADATA_MARKERS = (
    "Incorrect BOM value",
    "Error reading frame",
    "Error reading comment",
    "[png",
    "chunk too big",
)
# Decoder hiccups that also appear on files which play start to finish (a
# truncated MP3 and a healthy one can produce these identically), so on their
# own they are not evidence of damage.
_BENIGN_MARKERS = (
    "Header missing",
    "invalid new backstep",
)


def _matches(line: str, markers: Tuple[str, ...]) -> bool:
    return any(m in line for m in markers)


def classify_decode(
    rc: int, stderr: str, declared_samples: Optional[int] = None
) -> Tuple[str, str]:
    """Map a decode tool's (exit code, stderr) into a severity tier and reason.

    Conservative by design. A decode that ran to completion (rc == 0) is never
    CORRUPT, however many decoder complaints it emitted. CORRUPT is reserved for
    a tool that could not decode through (rc != 0) or a FLAC that lost sync
    before its declared sample count (true truncation, which `flac -t` reports
    but ffmpeg cannot reliably detect for MP3)."""
    text = stderr or ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # FLAC sync loss carries the decoded sample count; compare it to the
    # header's declared total to separate truncation from a trailing-junk tail.
    m = _RE_FLAC_LOSTSYNC.search(text)
    if m:
        decoded = int(m.group(1))
        if declared_samples and decoded < declared_samples:
            return (
                TIER_CORRUPT,
                f"truncated: decoded {decoded} of {declared_samples} samples",
            )
        return (
            TIER_SUSPECT,
            f"trailing data after {decoded} samples (audio intact, not byte-clean)",
        )

    if rc != 0:
        return TIER_CORRUPT, (lines[0] if lines else f"decoder exit code {rc}")

    if not lines:
        return TIER_OK, "decode ok"

    # Completed decode with complaints. METADATA only if every line is a known
    # tag-parse or benign-decoder marker; an unknown line is treated as SUSPECT
    # rather than hidden.
    if all(_matches(ln, _METADATA_MARKERS + _BENIGN_MARKERS) for ln in lines):
        return TIER_METADATA, "tag/benign decoder warnings only; audio decodes"
    n = len(lines)
    return TIER_SUSPECT, f"{n} decoder warning{'s' if n != 1 else ''}; decoded to end"


# =====================================
# Mode: FLAC integrity
# =====================================


def _flac_declared_samples(filepath: str) -> Optional[int]:
    """Total sample count from the FLAC STREAMINFO, used to tell a truncated
    file (decoded < declared) from a trailing-junk tail (decoded >= declared)."""
    try:
        from mutagen.flac import FLAC

        return FLAC(filepath).info.total_samples or None
    except Exception:
        return None


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


def _flac_verdict(
    filepath: str, *, use_flac: bool, ffmpeg_path: Optional[str]
) -> Tuple[str, str, str]:
    """Return (tool, tier, reason) for one FLAC. libFLAC is authoritative when
    available (its message carries the decoded sample count); ffmpeg is the
    fallback when flac is absent."""
    declared = _flac_declared_samples(filepath)
    if use_flac:
        rc, out, err = run_proc(["flac", "-t", "-s", str(filepath)])
        tier, reason = classify_decode(rc, err or out, declared)
        return "flac", tier, reason
    rc, stderr = _ffmpeg_decode_check(ffmpeg_path or "ffmpeg", Path(filepath))
    tier, reason = classify_decode(rc, stderr, declared)
    return "ffmpeg", tier, reason


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

    # libFLAC is preferred and authoritative; ffmpeg is the fallback.
    use_flac = (not have_ffmpeg) if prefer == "ffmpeg" else have_flac
    ffmpeg_path = shutil.which("ffmpeg")
    if not use_flac and prefer != "ffmpeg" and not quiet:
        print(
            "[warn] 'flac' not found; using ffmpeg for FLAC verification. "
            "ffmpeg's decoder is stricter and may flag valid files.",
            file=sys.stderr,
        )

    if not quiet:
        print(f"Found {total} FLAC files under: {root}")

    counts = {tier: 0 for tier in TIER_ORDER}
    flagged: List[Tuple[str, str, str, str]] = []  # (path, tool, tier, reason)

    def worker(path: Path) -> Tuple[str, str, str, str]:
        try:
            tool, tier, reason = _flac_verdict(
                str(path), use_flac=use_flac, ffmpeg_path=ffmpeg_path
            )
            return str(path), tool, tier, reason
        except KeyboardInterrupt:
            raise
        except Exception as e:
            return str(path), "exception", TIER_CORRUPT, repr(e)

    pbar = _make_pbar(total, "Testing FLACs", quiet)
    ex: Optional[ThreadPoolExecutor] = None
    futures: Dict = {}
    try:
        ex = ThreadPoolExecutor(max_workers=max(1, workers))
        futures = {ex.submit(worker, p): p for p in flacs}
        for fut in as_completed(futures):
            path, tool, tier, reason = fut.result()
            counts[tier] = counts.get(tier, 0) + 1
            if tier in (TIER_CORRUPT, TIER_SUSPECT):
                flagged.append((path, tool, tier, reason))
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

    out_path = os.path.abspath(output)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("FLAC INTEGRITY REPORT\n")
        f.write(f"Root: {root}\n")
        f.write(
            f"Scanned: {total}  OK: {counts[TIER_OK]}  "
            f"Suspect: {counts[TIER_SUSPECT]}  Corrupt: {counts[TIER_CORRUPT]}\n"
        )
        f.write("=" * 60 + "\n\n")
        for tier in (TIER_CORRUPT, TIER_SUSPECT):
            rows = sorted((r for r in flagged if r[2] == tier), key=lambda r: r[0])
            if not rows:
                continue
            f.write(f"{tier} ({len(rows)})\n")
            f.write("-" * 40 + "\n")
            for i, (path, tool, _tier, reason) in enumerate(rows, 1):
                rel = os.path.relpath(path, root)
                f.write(f"  {i:>3}. {rel}\n")
                f.write(f"       Tool: {tool}\n")
                f.write(f"       {reason}\n\n")

    if not quiet:
        if counts[TIER_CORRUPT] or counts[TIER_SUSPECT]:
            print(
                f"Scanned {total}. Corrupt: {counts[TIER_CORRUPT]}  "
                f"Suspect: {counts[TIER_SUSPECT]}. Details: {out_path}"
            )
        else:
            print("✅ All FLAC files passed integrity checks.")
    return 1 if counts[TIER_CORRUPT] > 0 else 0


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


def _ffmpeg_decode_check(ffmpeg_path: str, path: Path) -> Tuple[int, str]:
    """Run a full decode and return (returncode, stderr). Judgment is left to
    classify_decode; this only produces the raw signal."""
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
        return -1, f"FFmpeg invocation failed: {e!r}"
    return proc.returncode, (proc.stderr or "").strip()


def _scan_one_file(
    path: Path, ffmpeg_path: Optional[str], *, enrich: bool = False
) -> Dict[str, Any]:
    """Scan a single audio file for decode errors. If enrich=True, also pull
    mutagen header info (bitrate, duration, sample rate, VBR mode)."""
    row: Dict[str, Any] = {
        "path": str(path),
        "size_bytes": None,
        "tier": TIER_OK,
        "reason": "decode ok",
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
        row["tier"] = TIER_CORRUPT
        row["reason"] = f"stat failed: {e!r}"
        return row

    if enrich:
        row.update({k: v for k, v in _mutagen_header_info(path).items() if k in row})

    if not ffmpeg_path:
        # Cannot assess the audio without a decoder; do not flag it.
        row["reason"] = "decode check skipped (ffmpeg unavailable)"
        return row

    rc, stderr = _ffmpeg_decode_check(ffmpeg_path, path)
    row["tier"], row["reason"] = classify_decode(rc, stderr)
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
    counts = {tier: 0 for tier in TIER_ORDER}
    results: List[Dict[str, Any]] = []

    pbar = _make_pbar(len(targets), f"Scanning {label}", quiet)
    ex: Optional[ThreadPoolExecutor] = None
    futures: Dict = {}

    if verbose:
        quiet = False
    # CORRUPT and SUSPECT are always listed; METADATA and OK only when the user
    # asks (keeps a clean library's report short and bounds memory on big runs).
    list_benign = verbose or not only_errors

    try:
        ex = ThreadPoolExecutor(max_workers=max(1, workers))
        futures = {
            ex.submit(_scan_one_file, p, ffmpeg_path, enrich=enrich): p for p in targets
        }

        for fut in as_completed(futures):
            row = fut.result()
            tier = row.get("tier", TIER_OK)
            counts[tier] = counts.get(tier, 0) + 1
            if tier in (TIER_CORRUPT, TIER_SUSPECT) or list_benign:
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

    def _section(
        handle, tier: str, rows: List[Dict[str, Any]], *, compact: bool = False
    ):
        if not rows:
            return
        handle.write(f"{tier} ({len(rows)})\n")
        handle.write("-" * 40 + "\n")
        for r in rows:
            rel = os.path.relpath(r["path"], str(root_path))
            meta = _format_row_meta(r) if enrich else ""
            if compact:
                handle.write(f"  {rel}{('  [' + meta + ']') if meta else ''}\n")
                continue
            handle.write(f"  {rel}\n")
            if r.get("reason"):
                handle.write(f"    {r['reason']}\n")
            if meta:
                handle.write(f"    {meta}\n")
            handle.write("\n")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"{report_title}\n")
        f.write(f"Root: {root_path}\n")
        f.write(
            f"Scanned: {len(targets)}  OK: {counts[TIER_OK]}  "
            f"Metadata: {counts[TIER_METADATA]}  Suspect: {counts[TIER_SUSPECT]}  "
            f"Corrupt: {counts[TIER_CORRUPT]}\n"
        )
        f.write(f"Elapsed: {elapsed:.1f}s\n")
        if not list_benign and (counts[TIER_METADATA] or counts[TIER_OK]):
            f.write("(METADATA and OK omitted; re-run with --verbose to list them)\n")
        f.write("=" * 60 + "\n\n")

        by_tier = {t: [r for r in results if r["tier"] == t] for t in TIER_ORDER}
        _section(f, TIER_CORRUPT, by_tier[TIER_CORRUPT])
        _section(f, TIER_SUSPECT, by_tier[TIER_SUSPECT])
        if list_benign:
            _section(f, TIER_METADATA, by_tier[TIER_METADATA])
            _section(f, TIER_OK, by_tier[TIER_OK], compact=True)

    if not quiet:
        print(f"\nScanned: {len(targets)} files in {elapsed:.1f}s")
        print(
            f"ok: {counts[TIER_OK]}  metadata: {counts[TIER_METADATA]}  "
            f"suspect: {counts[TIER_SUSPECT]}  corrupt: {counts[TIER_CORRUPT]}"
        )
        print(f"Report written to: {out_path}")
    return 1 if counts[TIER_CORRUPT] > 0 else 0


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
