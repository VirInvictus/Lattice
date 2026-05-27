import os
import re
import sys
import unicodedata
from collections import defaultdict, Counter
from difflib import SequenceMatcher
from typing import NamedTuple
from collections.abc import Sequence

from lattice.utils import (
    is_audio,
    count_audio_files,
    _make_pbar,
    iter_audio_dirs,
    as_roots,
    relpath_under,
)
from lattice.tags import get_all_tags, HAVE_MUTAGEN_BASE, TagBundle
from lattice.config import (
    AUDIO_EXTENSIONS,
    DEFAULT_DUPLICATES_OUTPUT,
    DEFAULT_TAG_AUDIT_OUTPUT,
)

# =====================================
# Mode: Duplicate detection
# =====================================

# Mirrors cleaner.py's fold table — kept in-package because spec §5 keeps
# cleaner.py outside the lattice package.
_QUOTE_DASH_FOLD = {
    "‘": "'",
    "’": "'",
    "ʼ": "'",
    "“": '"',
    "”": '"',
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "–": "-",
    "—": "-",
    "―": "-",
}

_WS_RUN = re.compile(r"\s+")
_PAREN_TAIL = re.compile(r"\s*[\(\[][^\(\[\)\]]*[\)\]]\s*$")
_FEAT = re.compile(r"\s+(?:feat\.?|featuring|ft\.?)\s+.+$", re.IGNORECASE)


def _norm_key(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    for k, v in _QUOTE_DASH_FOLD.items():
        s = s.replace(k, v)
    return _WS_RUN.sub(" ", s).strip().lower()


def _loose_key(s: str | None) -> str:
    """`_norm_key` plus stripping of trailing parentheticals and 'feat.' clauses;
    used only for fuzzy similarity matching, not exact lookup."""
    s = _norm_key(s)
    if not s:
        return ""
    s = _FEAT.sub("", s)
    while True:
        new = _PAREN_TAIL.sub("", s).strip()
        if new == s:
            break
        s = new
    return s


class _DirInfo(NamedTuple):
    path: str
    artist: str
    album: str
    norm_artist: str
    norm_album: str
    loose_album: str
    total_bytes: int
    formats: dict[str, int]
    fmt_bitrate: dict[str, int]
    files: list[tuple[str, TagBundle, int]]


def _aggregate_dir(
    dirpath: str, audio_files: list[str], tag_cache: dict[str, TagBundle]
) -> _DirInfo:
    files: list[tuple[str, TagBundle, int]] = []
    artists: Counter = Counter()
    albums: Counter = Counter()
    formats: Counter = Counter()
    fmt_kbps: dict[str, list[int]] = defaultdict(list)
    total_bytes = 0

    for fname in audio_files:
        fpath = os.path.join(dirpath, fname)
        t = tag_cache[fpath]
        try:
            sz = os.path.getsize(fpath)
        except OSError:
            sz = 0
        ext = os.path.splitext(fname)[1].lower()
        total_bytes += sz
        files.append((fname, t, sz))
        formats[ext] += 1
        if t.bitrate_kbps:
            fmt_kbps[ext].append(t.bitrate_kbps)
        if t.artist:
            artists[t.artist] += 1
        if t.album:
            albums[t.album] += 1

    artist = (
        artists.most_common(1)[0][0]
        if artists
        else os.path.basename(os.path.dirname(dirpath))
    )
    album = albums.most_common(1)[0][0] if albums else os.path.basename(dirpath)

    fmt_bitrate = {ext: int(sum(v) / len(v)) for ext, v in fmt_kbps.items() if v}

    return _DirInfo(
        path=dirpath,
        artist=artist,
        album=album,
        norm_artist=_norm_key(artist),
        norm_album=_norm_key(album),
        loose_album=_loose_key(album),
        total_bytes=total_bytes,
        formats=dict(formats),
        fmt_bitrate=fmt_bitrate,
        files=files,
    )


def _fmt_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB"):
        if f < 1024:
            return f"{int(f)} B" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} GB"


def _fmt_duration(secs: float | None) -> str:
    if not secs:
        return "--:--"
    m, s = divmod(int(secs), 60)
    return f"{m}:{s:02d}"


def _fmt_dir_summary(d: _DirInfo, roots: list[str]) -> str:
    rel = relpath_under(d.path, roots)
    parts = []
    for ext in sorted(d.formats):
        count = d.formats[ext]
        br = d.fmt_bitrate.get(ext)
        tag = ext.lstrip(".")
        parts.append(f"{tag}×{count} {br}kbps" if br else f"{tag}×{count}")
    fmt_str = ", ".join(parts)
    return f"       {rel}/  [{fmt_str}]  {_fmt_size(d.total_bytes)}"


def _section_exact(dirs: list[_DirInfo], roots: list[str], out) -> tuple[int, set]:
    groups: dict[tuple[str, str], list[_DirInfo]] = defaultdict(list)
    for d in dirs:
        # Require both keys non-empty: grouping folders by ("metallica", "")
        # would mass-match every album-less folder for that artist.
        if not d.norm_artist or not d.norm_album:
            continue
        groups[(d.norm_artist, d.norm_album)].append(d)
    dupes = {k: v for k, v in groups.items() if len(v) > 1}
    if not dupes:
        out.write("[EXACT ALBUM DUPLICATES]    (none)\n\n")
        return 0, set()
    total_dirs = sum(len(v) for v in dupes.values())
    out.write(
        f"[EXACT ALBUM DUPLICATES]    ({len(dupes)} album(s), {total_dirs} directories)\n\n"
    )
    for i, (_, locs) in enumerate(
        sorted(dupes.items(), key=lambda kv: (kv[0][0], kv[0][1])), 1
    ):
        first = locs[0]
        out.write(f"  {i}. {first.artist} — {first.album}\n")
        for d in sorted(locs, key=lambda x: x.path):
            out.write(_fmt_dir_summary(d, roots) + "\n")
        out.write("\n")
    return len(dupes), set(dupes.keys())


def _section_multiformat(dirs: list[_DirInfo], roots: list[str], out) -> int:
    # Each value: ext -> (filename, size, tag_title, original_stem)
    hits: list[
        tuple[
            _DirInfo,
            dict[
                tuple[int | None, str],
                dict[str, tuple[str, int, str | None, str]],
            ],
        ]
    ] = []
    for d in dirs:
        if len(d.formats) < 2:
            continue
        by_key: dict[
            tuple[int | None, str], dict[str, tuple[str, int, str | None, str]]
        ] = defaultdict(dict)
        for fname, t, sz in d.files:
            ext = os.path.splitext(fname)[1].lower()
            stem = os.path.splitext(fname)[0]
            title_for_key = t.title or stem
            key = (t.trackno, _norm_key(title_for_key))
            by_key[key][ext] = (fname, sz, t.title, stem)
        matched = {k: v for k, v in by_key.items() if len(v) > 1}
        if matched:
            hits.append((d, matched))

    if not hits:
        out.write("[WITHIN-DIRECTORY MULTI-FORMAT]    (none)\n\n")
        return 0

    out.write(f"[WITHIN-DIRECTORY MULTI-FORMAT]    ({len(hits)} directories)\n\n")
    for i, (d, matched) in enumerate(sorted(hits, key=lambda x: x[0].path), 1):
        rel = relpath_under(d.path, roots)
        out.write(f"  {i}. {rel}/\n")
        for (trackno, _title_key), fmts in sorted(
            matched.items(),
            key=lambda x: (x[0][0] if x[0][0] is not None else 9999, x[0][1]),
        ):
            # Prefer a tag title; otherwise fall back to one of the original
            # filename stems (case-preserved), never the lowercased key.
            display_title = (
                next((info[2] for info in fmts.values() if info[2]), None)
                or next(iter(fmts.values()))[3]
            )
            tn = f"{trackno:02d}" if trackno else "--"
            out.write(f"       track {tn}  {display_title}\n")
            for ext in sorted(fmts):
                fname, sz, _, _ = fmts[ext]
                out.write(
                    f"           {ext.lstrip('.'):<5} {fname}  ({_fmt_size(sz)})\n"
                )
        out.write("\n")
    return len(hits)


def _section_similar(
    dirs: list[_DirInfo],
    exact_keys: set,
    roots: list[str],
    out,
    threshold: float = 0.85,
) -> int:
    by_artist: dict[str, list[_DirInfo]] = defaultdict(list)
    for d in dirs:
        if not d.norm_artist:
            continue
        if (d.norm_artist, d.norm_album) in exact_keys:
            continue
        if not d.loose_album:
            continue
        by_artist[d.norm_artist].append(d)

    pairs: list[tuple[float, _DirInfo, _DirInfo]] = []
    for items in by_artist.values():
        if len(items) < 2:
            continue
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                if a.loose_album == b.loose_album:
                    ratio = 1.0
                else:
                    ratio = SequenceMatcher(None, a.loose_album, b.loose_album).ratio()
                if ratio >= threshold:
                    pairs.append((ratio, a, b))

    if not pairs:
        out.write(
            f"[SIMILAR-NAME CANDIDATES]    (threshold ≥ {threshold:.2f}, none)\n\n"
        )
        return 0

    pairs.sort(key=lambda x: (-x[0], x[1].norm_artist, x[1].norm_album))
    out.write(
        f"[SIMILAR-NAME CANDIDATES]    "
        f"(threshold ≥ {threshold:.2f}, {len(pairs)} pair(s))\n\n"
    )
    for i, (ratio, a, b) in enumerate(pairs, 1):
        out.write(f"  {i}. [{ratio:.2f}]  {a.artist}\n")
        out.write(f'       "{a.album}"  ({relpath_under(a.path, roots)})\n')
        out.write(f'       "{b.album}"  ({relpath_under(b.path, roots)})\n')
        out.write("\n")
    return len(pairs)


def _cluster_by_duration(
    entries: Sequence[tuple[_DirInfo, str, TagBundle]], delta: float
) -> list[list[tuple[_DirInfo, str, TagBundle]]]:
    """Partition `entries` into duration-clusters where each cluster's spread
    fits within `delta` seconds. Greedy: a new cluster starts when the next
    entry exceeds `delta` past the cluster's first entry. Entries with no
    duration form one best-effort cluster. Returns only clusters with 2+
    entries spanning 2+ distinct directories — so a studio cluster and a
    live cluster for the same title each surface separately."""
    durs = [(e, e[2].duration_s) for e in entries if e[2].duration_s is not None]
    no_dur = [e for e in entries if e[2].duration_s is None]

    clusters: list[list[tuple[_DirInfo, str, TagBundle]]] = []
    if durs:
        durs.sort(key=lambda x: x[1])
        current = [durs[0][0]]
        anchor = durs[0][1]
        for entry, dur in durs[1:]:
            if dur - anchor <= delta:
                current.append(entry)
            else:
                clusters.append(current)
                current = [entry]
                anchor = dur
        clusters.append(current)
    if len(no_dur) >= 2:
        clusters.append(no_dur)

    return [c for c in clusters if len(c) >= 2 and len({e[0].path for e in c}) >= 2]


def _section_track_dupes(
    dirs: list[_DirInfo], roots: list[str], out, duration_delta: float = 2.0
) -> int:
    track_map: dict[tuple[str, str], list[tuple[_DirInfo, str, TagBundle]]] = (
        defaultdict(list)
    )
    for d in dirs:
        for fname, t, _sz in d.files:
            artist_src = t.artist or d.artist
            title_src = t.title
            if not artist_src or not title_src:
                continue
            key = (_norm_key(artist_src), _norm_key(title_src))
            if not key[0] or not key[1]:
                continue
            track_map[key].append((d, fname, t))

    hits: list[tuple[tuple[str, str], list[tuple[_DirInfo, str, TagBundle]]]] = []
    for key, entries in track_map.items():
        if len({e[0].path for e in entries}) < 2:
            continue
        for cluster in _cluster_by_duration(entries, duration_delta):
            hits.append((key, cluster))

    if not hits:
        out.write(
            f"[TRACK-LEVEL DUPLICATES]    "
            f"(duration delta ≤ {duration_delta:.0f}s, none)\n\n"
        )
        return 0

    hits.sort(key=lambda x: (x[0][0], x[0][1]))
    out.write(
        f"[TRACK-LEVEL DUPLICATES]    "
        f"(duration delta ≤ {duration_delta:.0f}s, {len(hits)} track(s))\n\n"
    )
    for i, (_, entries) in enumerate(hits, 1):
        first_d, first_fname, first_t = entries[0]
        artist_display = first_t.artist or first_d.artist
        title_display = first_t.title or os.path.splitext(first_fname)[0]
        out.write(
            f"  {i}. {artist_display} — {title_display}  ({len(entries)} copies)\n"
        )
        for d, fname, t in sorted(entries, key=lambda e: e[0].path):
            rel = relpath_under(os.path.join(d.path, fname), roots)
            dur = _fmt_duration(t.duration_s)
            br = f"{t.bitrate_kbps}kbps" if t.bitrate_kbps else "--"
            out.write(f"       {rel}    {dur}  {br}\n")
        out.write("\n")
    return len(hits)


def run_duplicates(root: str | list[str], output: str, *, quiet: bool = False) -> int:
    """Detect duplicate albums, within-folder multi-format duplicates, similar
    album names, and track-level cross-library duplicates. Emits a single
    sectioned text report. With several roots, duplicates are detected across
    them (e.g. the same album living in two separate libraries)."""
    if not HAVE_MUTAGEN_BASE:
        print("ERROR: mutagen is required for duplicate detection.", file=sys.stderr)
        return 2

    roots = as_roots(root)
    if not quiet:
        print(f"Scanning for duplicates under: {', '.join(roots)}")

    total = count_audio_files(roots)
    pbar = _make_pbar(total, "Reading tags", quiet)

    tag_cache: dict[str, TagBundle] = {}
    dirs: list[_DirInfo] = []

    for _src_root, dirpath, _subdirs, files in iter_audio_dirs(roots):
        audio_files = sorted(f for f in files if is_audio(f))
        if not audio_files:
            continue
        for fname in audio_files:
            fpath = os.path.join(dirpath, fname)
            tag_cache[fpath] = get_all_tags(fpath)
            pbar.update(1)
        dirs.append(_aggregate_dir(dirpath, audio_files, tag_cache))

    pbar.close()

    out_path = os.path.abspath(output or DEFAULT_DUPLICATES_OUTPUT)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("DUPLICATE REPORT\n")
        f.write(f"Root: {', '.join(roots)}\n")
        f.write(f"Directories: {len(dirs)}    Audio files: {total}\n")
        f.write("=" * 70 + "\n\n")

        exact_count, exact_keys = _section_exact(dirs, roots, f)
        mf_count = _section_multiformat(dirs, roots, f)
        sim_count = _section_similar(dirs, exact_keys, roots, f)
        trk_count = _section_track_dupes(dirs, roots, f)

    if not quiet:
        print(f"\nReport written to: {out_path}")
        print(f"  Exact album duplicates:       {exact_count}")
        print(f"  Within-folder multi-format:   {mf_count}")
        print(f"  Similar-name candidates:      {sim_count}")
        print(f"  Track-level duplicates:       {trk_count}")
    return 0


# =====================================
# Mode: Tag audit
# =====================================


def run_tag_audit(root: str | list[str], output: str, *, quiet: bool = False) -> int:
    """Report audio files missing title, artist, track number, or genre."""
    if not HAVE_MUTAGEN_BASE:
        print("ERROR: mutagen is required for tag auditing.", file=sys.stderr)
        return 2

    roots = as_roots(root)
    issues: list[dict[str, str]] = []

    if not quiet:
        print(f"Auditing tags under: {', '.join(roots)}")

    total = count_audio_files(roots)
    pbar = _make_pbar(total, "Auditing tags", quiet)

    for _src_root, dirpath, _dirs, files in iter_audio_dirs(roots):
        for f in sorted(files):
            ext = os.path.splitext(f)[1].lower()
            if ext not in AUDIO_EXTENSIONS:
                continue

            pbar.update(1)

            filepath = os.path.join(dirpath, f)
            t = get_all_tags(filepath)

            missing_fields: list[str] = []
            if not t.title:
                missing_fields.append("title")
            if not t.artist:
                missing_fields.append("artist")
            if t.trackno is None:
                missing_fields.append("tracknumber")
            if not t.genre:
                missing_fields.append("genre")

            if missing_fields:
                issues.append(
                    {
                        "path": filepath,
                        "format": ext.strip("."),
                        "missing": ", ".join(missing_fields),
                    }
                )

    pbar.close()

    out_path = os.path.abspath(output or DEFAULT_TAG_AUDIT_OUTPUT)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    # Build breakdown counts
    field_counts: Counter = Counter()
    for issue in issues:
        for field in issue["missing"].split(", "):
            field_counts[field] += 1

    # Group issues by directory for readability
    by_dir: dict[str, list[dict[str, str]]] = defaultdict(list)
    for issue in issues:
        parent = os.path.dirname(issue["path"])
        by_dir[parent].append(issue)

    with open(out_path, "w", encoding="utf-8") as out_file:
        out_file.write("TAG AUDIT REPORT\n")
        out_file.write(f"Root: {', '.join(roots)}\n")
        out_file.write(f"Scanned: {total}  Incomplete: {len(issues)}\n")
        if field_counts:
            breakdown = "  ".join(
                f"{field}: {count}" for field, count in field_counts.most_common()
            )
            out_file.write(f"Breakdown: {breakdown}\n")
        out_file.write("=" * 60 + "\n\n")

        for directory in sorted(by_dir.keys()):
            rel_dir = relpath_under(directory, roots)
            out_file.write(f"  {rel_dir}/\n")
            for issue in by_dir[directory]:
                filename = os.path.basename(issue["path"])
                out_file.write(
                    f"    {filename}  [{issue['format']}]  missing: {issue['missing']}\n"
                )
            out_file.write("\n")

    if not quiet:
        print(f"\nAudited {total} files. Found {len(issues)} with incomplete tags.")
        print(f"Results written to: {out_path}")
        if field_counts:
            print("  Breakdown:")
            for field, count in field_counts.most_common():
                print(f"    {field}: {count}")

    return 0


# =====================================
# Mode: Bitrate floor audit
# =====================================


def run_bitrate_audit(
    root: str | list[str], output: str, min_kbps: int, *, quiet: bool = False
) -> int:
    """Report audio files falling below a specified bitrate floor."""
    if not HAVE_MUTAGEN_BASE:
        print("ERROR: mutagen is required for bitrate auditing.", file=sys.stderr)
        return 2

    roots = as_roots(root)
    issues: list[dict[str, str]] = []

    if not quiet:
        print(f"Auditing bitrates (< {min_kbps} kbps) under: {', '.join(roots)}")

    total = count_audio_files(roots)
    pbar = _make_pbar(total, "Auditing bitrates", quiet)

    for _src_root, dirpath, _dirs, files in iter_audio_dirs(roots):
        for f in sorted(files):
            ext = os.path.splitext(f)[1].lower()
            if ext not in AUDIO_EXTENSIONS:
                continue

            pbar.update(1)

            filepath = os.path.join(dirpath, f)
            t = get_all_tags(filepath)

            if (
                t.bitrate_kbps is not None
                and t.bitrate_kbps > 0
                and t.bitrate_kbps < min_kbps
            ):
                issues.append(
                    {
                        "path": filepath,
                        "format": ext.strip("."),
                        "bitrate": str(t.bitrate_kbps),
                    }
                )

    pbar.close()

    out_path = os.path.abspath(
        output or DEFAULT_TAG_AUDIT_OUTPUT.replace("tag_audit", "bitrate_audit")
    )
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    by_dir: dict[str, list[dict[str, str]]] = defaultdict(list)
    for issue in issues:
        parent = os.path.dirname(issue["path"])
        by_dir[parent].append(issue)

    with open(out_path, "w", encoding="utf-8") as out_file:
        out_file.write("BITRATE AUDIT REPORT\n")
        out_file.write(f"Root: {', '.join(roots)}\n")
        out_file.write(f"Floor: < {min_kbps} kbps\n")
        out_file.write(f"Scanned: {total}  Below floor: {len(issues)}\n")
        out_file.write("=" * 60 + "\n\n")

        for directory in sorted(by_dir.keys()):
            rel_dir = relpath_under(directory, roots)
            out_file.write(f"  {rel_dir}/\n")
            for issue in by_dir[directory]:
                filename = os.path.basename(issue["path"])
                out_file.write(
                    f"    {filename}  [{issue['format']}]  {issue['bitrate']} kbps\n"
                )
            out_file.write("\n")

    if not quiet:
        print(f"\nAudited {total} files. Found {len(issues)} below {min_kbps} kbps.")
        print(f"Results written to: {out_path}")

    return 0
