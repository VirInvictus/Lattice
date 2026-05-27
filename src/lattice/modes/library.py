import os
import sys
import re
from collections import defaultdict
from typing import NamedTuple, TextIO

from lattice.utils import (
    count_audio_files,
    _make_pbar,
    is_audio,
    clean_song_name,
    format_rating,
    parse_layout,
    iter_audio_dirs,
    as_roots,
)
from lattice.tags import get_all_tags, TagBundle

Song = tuple[str, str, TagBundle]  # (filename, filepath, tags)
ArtistAlbums = dict[str, dict[str, list[Song]]]


class _AlbumDir(NamedTuple):
    """One audio-containing directory, reduced to its dominant artist/album/genre.

    `artist`, `album`, and `genre` are the most-common tag value across the
    directory's files (`genre` is "" when no file carries one). The same
    aggregation feeds every library/wing mode.
    """

    path: str
    artist: str
    album: str
    genre: str
    songs: list[Song]


def _most_common(counts: dict[str, int], default: str) -> str:
    """Return the most frequent key, breaking ties by first insertion; `default`
    when empty. Matches the directory-dominant selection used across modes."""
    return max(counts, key=lambda k: counts[k]) if counts else default


def _scan_album_dirs(roots, layout: str, pbar) -> list[_AlbumDir]:
    """Walk one or more roots, collapsing each audio directory to an `_AlbumDir`.
    The layout is parsed against whichever root the directory lives under, so
    multi-root scans key artist/album off the correct relative path."""
    results: list[_AlbumDir] = []
    for root, dirpath, _dirs, files in iter_audio_dirs(roots):
        audio_in_dir = [f for f in files if is_audio(f)]
        if not audio_in_dir:
            continue

        artists_count: dict[str, int] = defaultdict(int)
        albums_count: dict[str, int] = defaultdict(int)
        genres_count: dict[str, int] = defaultdict(int)
        songs: list[Song] = []

        for f in audio_in_dir:
            filepath = os.path.join(dirpath, f)
            parsed = parse_layout(os.path.relpath(filepath, root), layout)
            t = get_all_tags(filepath)
            artist = t.artist or parsed.get("artist", "Unknown Artist")
            album = t.album or parsed.get("album", "Unknown Album")

            artists_count[artist] += 1
            albums_count[album] += 1
            if t.genre:
                genres_count[t.genre] += 1
            songs.append((f, filepath, t))
            pbar.update(1)

        results.append(
            _AlbumDir(
                dirpath,
                _most_common(artists_count, "Unknown Artist"),
                _most_common(albums_count, "Unknown Album"),
                _most_common(genres_count, ""),
                songs,
            )
        )

    return results


def _song_display_name(song_filename: str, t: TagBundle, album_artist: str) -> str:
    """Build a track's display label from tags, falling back to a cleaned filename.

    The artist is shown only when it differs from the album artist (so
    compilations stay legible without repeating the headline artist).
    """
    if not (t.title or t.artist):
        return clean_song_name(song_filename)

    guest = t.artist if (t.artist and t.artist != album_artist) else None
    parts: list[str] = []
    if t.trackno:
        parts.append(f"{int(t.trackno):02d}.")
    if guest is not None:
        parts.append(guest)
    if t.title:
        if guest is not None:
            parts.append("—")
        parts.append(t.title)
    return " ".join(parts).strip()


def _write_tree(
    f: TextIO,
    artist_albums: ArtistAlbums,
    *,
    show_genre: bool,
    album_paths: dict[tuple[str, str], str] | None = None,
) -> None:
    """Write an ARTIST → ALBUM → SONG tree. When `album_paths` is given, the
    album line is annotated with its absolute directory path."""
    for artist in sorted(artist_albums):
        f.write(f"ARTIST: {artist}\n")
        albums = sorted(artist_albums[artist])

        for i, album in enumerate(albums):
            songs = sorted(artist_albums[artist][album], key=lambda x: x[0])
            connector = "└──" if i == len(albums) - 1 else "├──"

            genre_str = ""
            if show_genre and songs and songs[0][2].genre:
                genre_str = f" ({songs[0][2].genre})"

            path_str = ""
            if album_paths is not None:
                album_path = album_paths.get((artist, album), "")
                path_str = f" [{album_path}]" if album_path else ""

            f.write(f"  {connector} ALBUM: {album}{genre_str}{path_str}\n")

            for j, (song, _song_path, t) in enumerate(songs):
                display_name = _song_display_name(song, t, artist)
                ext = os.path.splitext(song)[1].lower().strip(".")
                song_connector = "└──" if j == len(songs) - 1 else "├──"
                f.write(
                    f"      {song_connector} SONG: {display_name} ({ext}){format_rating(t.rating)}\n"
                )
            f.write("\n")


# =====================================
# Mode: Library tree
# =====================================


def write_music_library_tree(
    root_dir: str | list[str],
    output_file: str,
    *,
    layout: str = "{artist}/{album}",
    quiet: bool = False,
    show_genre: bool = False,
) -> None:
    roots = as_roots(root_dir)
    total_files = count_audio_files(roots)
    if not quiet:
        print(f"Found {total_files} audio files to process under: {', '.join(roots)}\n")

    pbar = _make_pbar(total_files, "Scanning library", quiet)
    album_dirs = _scan_album_dirs(roots, layout, pbar)
    pbar.close()

    # Group same-artist albums together for display.
    tree: ArtistAlbums = defaultdict(lambda: defaultdict(list))
    for ad in album_dirs:
        tree[ad.artist][ad.album].extend(ad.songs)

    output_file = os.path.abspath(output_file)
    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)

    try:
        with open(output_file, "w", encoding="utf-8") as f:
            _write_tree(f, tree, show_genre=show_genre)
    except KeyboardInterrupt:
        if not quiet:
            print("\nInterrupted by user. Library scan cancelled.")
        return


# =====================================
# Mode: AI-readable library export
# =====================================


def write_ai_library(
    root_dir: str | list[str],
    output_file: str,
    *,
    layout: str = "{artist}/{album}",
    quiet: bool = False,
) -> None:
    """Write a flat, token-efficient library summary for LLM consumption."""
    roots = as_roots(root_dir)
    total = count_audio_files(roots)

    if not quiet:
        print(f"Scanning {total} files under: {', '.join(roots)}")

    pbar = _make_pbar(total, "Building AI library", quiet)
    album_dirs = _scan_album_dirs(roots, layout, pbar)
    pbar.close()

    albums: list[tuple[str, str, str, str, int]] = []
    for ad in album_dirs:
        ratings = [t.rating for _f, _p, t in ad.songs if t.rating is not None]
        rating_str = f"{sum(ratings) / len(ratings):.1f}" if ratings else ""
        albums.append((ad.artist, ad.album, ad.genre, rating_str, len(ad.songs)))

    albums.sort(key=lambda x: (x[0].lower(), x[1].lower()))

    out_path = os.path.abspath(output_file)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("Artist | Album | Genre | Rating | Tracks\n")
        f.write("-" * 50 + "\n")
        for artist, album, genre, rating, tracks in albums:
            f.write(f"{artist} | {album} | {genre} | {rating} | {tracks}\n")

    if not quiet:
        rated = sum(1 for _, _, _, r, _ in albums if r)
        print(f"\nWrote {len(albums)} albums ({rated} rated) to: {out_path}")


# =====================================
# Mode: All wings (genre-based library files)
# =====================================


def _safe_wing_name(genre: str) -> str:
    """Turn a genre label into a filesystem-safe filename stem."""
    return re.sub(r"[^\w\s-]", "_", genre).strip().replace(" ", "_")


def write_all_wings(
    root_dir: str | list[str],
    outdir: str,
    *,
    layout: str = "{artist}/{album}",
    quiet: bool = False,
    show_genre: bool = False,
    show_paths: bool = False,
) -> int:
    """Generate a separate library tree file for each genre."""
    roots = as_roots(root_dir)
    total = count_audio_files(roots)
    if not quiet:
        print(f"Scanning {total} files for genre tags...")

    pbar = _make_pbar(total, "Scanning genres", quiet)
    album_dirs = _scan_album_dirs(roots, layout, pbar)
    pbar.close()

    if not album_dirs:
        print("No albums found under root.", file=sys.stderr)
        return 1

    # Re-bucket by genre -> artist -> album. A "/"-joined genre lands in each.
    final_wings: dict[str, ArtistAlbums] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    album_paths: dict[tuple[str, str], str] = {}

    for ad in album_dirs:
        genre_str = ad.genre or "Uncategorized"
        for genre in (g.strip() for g in genre_str.split("/") if g.strip()):
            final_wings[genre][ad.artist][ad.album].extend(ad.songs)
        album_paths[(ad.artist, ad.album)] = ad.path

    os.makedirs(outdir, exist_ok=True)

    if not quiet:
        print(f"\nFound {len(final_wings)} genres. Writing wings...\n")

    for genre_name in sorted(final_wings):
        artist_albums = final_wings[genre_name]
        output = os.path.join(outdir, f"{_safe_wing_name(genre_name)}_Library.txt")

        if not quiet:
            album_count = sum(len(albums) for albums in artist_albums.values())
            print(f"→ {genre_name} ({album_count} albums)")

        with open(output, "w", encoding="utf-8") as f:
            _write_tree(
                f,
                artist_albums,
                show_genre=show_genre,
                album_paths=album_paths if show_paths else None,
            )

    if not quiet:
        total_albums = sum(
            sum(len(albums) for albums in artist_albums.values())
            for artist_albums in final_wings.values()
        )
        print(
            f"\n{len(final_wings)} wings ({total_albums} albums) written to: {outdir}"
        )
    return 0


# =====================================
# Mode: AI wings (per-genre flat files)
# =====================================


def write_ai_wings(
    root_dir: str | list[str],
    outdir: str,
    *,
    layout: str = "{artist}/{album}",
    quiet: bool = False,
) -> int:
    """Generate separate, token-efficient AI library files for each genre."""
    roots = as_roots(root_dir)
    total = count_audio_files(roots)
    if not quiet:
        print(f"Scanning {total} files for AI wings...")

    pbar = _make_pbar(total, "Scanning genres", quiet)
    album_dirs = _scan_album_dirs(roots, layout, pbar)
    pbar.close()

    if not album_dirs:
        print("No albums found under root.", file=sys.stderr)
        return 1

    # genre -> list of (artist, album, genre, path)
    wings: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    for ad in album_dirs:
        genre_str = ad.genre or "Uncategorized"
        for genre in (g.strip() for g in genre_str.split("/") if g.strip()):
            wings[genre].append((ad.artist, ad.album, genre, ad.path))

    os.makedirs(outdir, exist_ok=True)

    if not quiet:
        print(f"\nFound {len(wings)} genres. Writing AI wings...\n")

    for genre_name in sorted(wings):
        albums = sorted(wings[genre_name])
        output = os.path.join(outdir, f"{_safe_wing_name(genre_name)}_AI.txt")

        if not quiet:
            print(f"→ {genre_name} ({len(albums)} albums)")

        with open(output, "w", encoding="utf-8") as f:
            f.write("Artist | Album | Genre | Location\n")
            f.write("-" * 60 + "\n")
            for artist, album, genre, path in albums:
                f.write(f"{artist} | {album} | {genre} | {path}\n")

    if not quiet:
        total_albums = sum(len(a) for a in wings.values())
        print(f"\n{len(wings)} AI wings ({total_albums} albums) written to: {outdir}")
    return 0
