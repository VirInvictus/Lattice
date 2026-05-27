import os
import sys

from lattice.utils import (
    count_audio_files,
    _make_pbar,
    is_audio,
    parse_layout,
    iter_audio_dirs,
    as_roots,
)
from lattice.tags import get_all_tags

# =====================================
# Mode: Playlist generation (.m3u)
# =====================================


def _evaluate_rule(rule: str, t, parsed_layout: dict) -> bool:
    """Evaluate a dynamic smart playlist rule against a track's metadata."""
    if not rule:
        return True

    # Field values exposed to the rule. eval runs with no builtins, so the
    # rule can only reference these names and basic operators.
    safe_locals = {
        "rating": t.rating or 0.0,
        "genre": t.genre or "",
        "artist": t.artist or parsed_layout.get("artist", ""),
        "album": t.album or parsed_layout.get("album", ""),
        "title": t.title or parsed_layout.get("title", ""),
        "duration": t.duration_s or 0.0,
        "bitrate": t.bitrate_kbps or 0,
    }

    try:
        # Accept SQL-style AND/OR as a convenience for Python's and/or.
        py_rule = rule.replace(" AND ", " and ").replace(" OR ", " or ")
        return bool(eval(py_rule, {"__builtins__": {}}, safe_locals))
    except Exception as e:
        print(f"Error evaluating rule '{rule}': {e}", file=sys.stderr)
        return False


def generate_playlist(
    root_dir: str | list[str],
    output_file: str,
    rule: str,
    layout: str = "{artist}/{album}",
    quiet: bool = False,
) -> int:
    """Generate an .m3u playlist based on a smart rule filter."""
    roots = as_roots(root_dir)
    total_files = count_audio_files(roots)

    if total_files == 0:
        if not quiet:
            print(f"No audio files found under: {', '.join(roots)}")
        return 0

    if not quiet:
        print(f"Scanning {total_files} files for playlist generation...")

    pbar = _make_pbar(total_files, "Building playlist", quiet)

    playlist_entries: list[str] = []

    for src_root, dirpath, _dirs, files in iter_audio_dirs(roots):
        # Sort files to keep album tracks in order
        for f in sorted(files):
            if is_audio(f):
                filepath = os.path.join(dirpath, f)
                rel_path = os.path.relpath(filepath, src_root)
                parsed = parse_layout(rel_path, layout)
                t = get_all_tags(filepath)

                if _evaluate_rule(rule, t, parsed):
                    # For .m3u, we can write #EXTINF if we have duration and title
                    duration = int(t.duration_s) if t.duration_s else -1
                    artist = t.artist or parsed.get("artist", "Unknown")
                    title = t.title or f
                    display = f"{artist} - {title}" if artist != "Unknown" else title

                    playlist_entries.append(f"#EXTINF:{duration},{display}")
                    # Use absolute paths for the playlist
                    playlist_entries.append(filepath)

                pbar.update(1)

    pbar.close()

    if not playlist_entries:
        if not quiet:
            print(f"No tracks matched the rule: {rule}")
        return 0

    out_path = os.path.abspath(output_file)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for entry in playlist_entries:
                f.write(f"{entry}\n")
    except OSError as e:
        print(f"Failed to write playlist: {e}", file=sys.stderr)
        return 1

    if not quiet:
        track_count = len(playlist_entries) // 2
        print(f"\nWrote playlist with {track_count} tracks to: {out_path}")

    return 0
