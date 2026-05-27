import os
from collections import Counter, defaultdict

from lattice.utils import count_audio_files, _make_pbar, iter_audio_dirs, as_roots
from lattice.tags import get_all_tags
from lattice.config import AUDIO_EXTENSIONS

# =====================================
# Mode: Library statistics
# =====================================


_RATING_LABELS = (
    "★★★★★ (5)",
    "★★★★☆ (4)",
    "★★★☆☆ (3)",
    "★★☆☆☆ (2)",
    "★☆☆☆☆ (1)",
)
_UNRATED = "unrated"


def _rating_label(rating: float | None) -> str:
    """Bucket a 0–5 rating into its star label; None is "unrated". A 0 rating
    falls into the 1-star bucket, matching the original tally."""
    if rating is None:
        return _UNRATED
    stars = max(1, min(5, int(rating)))
    return _RATING_LABELS[5 - stars]


def _empty_rating_tally() -> dict[str, int]:
    return {label: 0 for label in (*_RATING_LABELS, _UNRATED)}


def _format_size(size_bytes: int) -> str:
    """Format byte count into human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024**2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024**3:
        return f"{size_bytes / (1024**2):.1f} MB"
    else:
        return f"{size_bytes / (1024**3):.2f} GB"


def run_stats(root: str | list[str], output: str | None, *, quiet: bool = False) -> str:
    """Generate a library-wide statistics report (combined across all roots)."""
    roots = as_roots(root)

    total_files = count_audio_files(roots)
    if total_files == 0:
        import lattice.utils as utils

        if not quiet and not utils.IN_TUI:
            print(f"No audio files found under: {', '.join(roots)}")
        return ""

    import lattice.utils as utils

    if not quiet and not utils.IN_TUI:
        print(f"Scanning {total_files} files under: {', '.join(roots)}")

    pbar = _make_pbar(total_files, "Gathering stats", quiet)

    # Accumulators
    format_counts: Counter = Counter()
    format_sizes: Counter = Counter()
    genre_counts: Counter = Counter()
    artist_counts: Counter = Counter()
    rating_counts: dict[str, int] = _empty_rating_tally()
    genre_ratings: dict[str, dict[str, int]] = defaultdict(_empty_rating_tally)
    total_size = 0
    total_duration = 0.0
    album_dirs: set = set()
    artist_dirs: set = set()
    bitrates: list[int] = []
    fully_tagged = 0  # has title + artist + track + genre

    for src_root, dirpath, _dirs, files in iter_audio_dirs(roots):
        for f in sorted(files):
            ext = os.path.splitext(f)[1].lower()
            if ext not in AUDIO_EXTENSIONS:
                continue

            filepath = os.path.join(dirpath, f)
            format_counts[ext] += 1

            try:
                fsize = os.path.getsize(filepath)
                total_size += fsize
                format_sizes[ext] += fsize
            except OSError:
                fsize = 0

            t = get_all_tags(filepath)

            # Artist/album tracking from directory structure, relative to the
            # root this file lives under (so multi-root counts stay correct).
            rel = os.path.relpath(dirpath, src_root)
            parts = rel.split(os.sep)
            if len(parts) >= 1:
                artist_dirs.add(parts[0])
            if len(parts) >= 2:
                album_dirs.add(rel)

            # Artist from tags (prefer tag, fall back to directory)
            artist_name = t.artist or (parts[0] if parts else None)
            if artist_name:
                artist_counts[artist_name] += 1

            if t.genre:
                genre_counts[t.genre] += 1

            label = _rating_label(t.rating)
            rating_counts[label] += 1
            if t.genre:
                genre_ratings[t.genre][label] += 1

            # Duration and bitrate — now carried by TagBundle
            if t.duration_s:
                total_duration += t.duration_s
            if t.bitrate_kbps:
                bitrates.append(t.bitrate_kbps)

            # Fully tagged check
            has_all = all([t.title, t.artist, t.trackno is not None, t.genre])
            if has_all:
                fully_tagged += 1

            pbar.update(1)

    pbar.close()

    # Build report
    lines: list[str] = []
    lines.append("LIBRARY STATISTICS")
    lines.append(f"Root: {', '.join(roots)}")
    lines.append("=" * 60)
    lines.append("")

    # Overview
    lines.append("OVERVIEW")
    lines.append("-" * 40)
    lines.append(f"  Total files:    {total_files}")
    lines.append(f"  Total size:     {_format_size(total_size)}")
    if total_duration > 0:
        hours = int(total_duration // 3600)
        mins = int((total_duration % 3600) // 60)
        lines.append(f"  Total duration: {hours}h {mins}m")
    lines.append(f"  Artists:        {len(artist_dirs)}")
    lines.append(f"  Albums:         {len(album_dirs)}")
    pct_tagged = (fully_tagged / total_files * 100) if total_files else 0
    lines.append(f"  Fully tagged:   {fully_tagged}/{total_files} ({pct_tagged:.0f}%)")
    lines.append("")

    # Format breakdown
    lines.append("FORMAT BREAKDOWN")
    lines.append("-" * 40)
    for ext, count in format_counts.most_common():
        pct = count / total_files * 100
        size_str = _format_size(format_sizes[ext])
        lines.append(f"  {ext:<8} {count:>6} files  ({pct:>5.1f}%)  {size_str:>10}")
    lines.append("")

    # Bitrate summary
    if bitrates:
        lines.append("BITRATE")
        lines.append("-" * 40)
        avg_br = sum(bitrates) / len(bitrates)
        min_br = min(bitrates)
        max_br = max(bitrates)
        lines.append(f"  Average: {avg_br:.0f} kbps")
        lines.append(f"  Range:   {min_br}–{max_br} kbps")
        # Flag low-quality files
        low_quality = sum(1 for b in bitrates if b < 192)
        if low_quality:
            lines.append(f"  Below 192 kbps: {low_quality} files")
        lines.append("")

    # Rating distribution
    rated = total_files - rating_counts["unrated"]
    lines.append(f"RATINGS ({rated} rated, {rating_counts['unrated']} unrated)")
    lines.append("-" * 40)
    for label in _RATING_LABELS:
        count = rating_counts[label]
        if count > 0:
            bar_len = min(30, int(count / max(1, total_files) * 150))
            bar = "█" * bar_len
            lines.append(f"  {label}  {count:>5}  {bar}")
    lines.append("")

    # Genre distribution (top 15)
    if genre_counts:
        lines.append(f"GENRES (top 15 of {len(genre_counts)})")
        lines.append("-" * 40)
        for genre, count in genre_counts.most_common(15):
            pct = count / total_files * 100
            lines.append(f"  {genre:<30} {count:>5}  ({pct:.1f}%)")
        lines.append("")

    # Rating distribution per genre
    if genre_ratings:
        lines.append("RATING DISTRIBUTION PER GENRE")
        lines.append("-" * 40)
        for genre, _ in genre_counts.most_common(15):
            lines.append(f"  {genre}:")
            for label in (*_RATING_LABELS, _UNRATED):
                count = genre_ratings[genre][label]
                if count > 0:
                    lines.append(f"    {label}  {count:>5}")
        lines.append("")

    # Top artists (top 15)
    if artist_counts:
        lines.append(f"TOP ARTISTS (by track count, top 15 of {len(artist_counts)})")
        lines.append("-" * 40)
        for artist, count in artist_counts.most_common(15):
            lines.append(f"  {artist:<35} {count:>5} tracks")
        lines.append("")

    report = "\n".join(lines) + "\n"

    # Write to file if output specified, otherwise stdout
    if output:
        out_path = os.path.abspath(output)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as out_file:
            out_file.write(report)
        import lattice.utils as utils

        if not quiet and not utils.IN_TUI:
            print(f"\nStatistics written to: {out_path}")
    else:
        import lattice.utils as utils

        if not quiet and not utils.IN_TUI:
            print()
            print(report)

    return report
