# Lattice Application Specification

**Version:** 4.8.0  
**Language:** Python 3.14+  
**Dependencies:** mutagen, tqdm  
**License:** MIT

---

## 1. Mission Statement

Lattice is a CLI toolkit for music collectors who manage their own libraries
outside of any player's database. It reads tags directly via mutagen, and is
player-agnostic by design. Every operation works from the filesystem and
embedded metadata, not from a proprietary database or cloud service.

Design philosophy: **one toolkit, every library maintenance task.** The standard collector
layout (`~/Music/ARTIST/ALBUM/01 - Track.flac`) is the default assumption. It is
configurable: the `layout` config key (or `--layout`) sets the pattern Lattice
uses to recover artist/album/genre from a path, so a genre-first tree
(`{genre}/{artist}/{album}`) is fully supported.

---

## 2. Architecture

### 2.1 Layer-Based Package Design

The codebase is structured as a proper Python package (`src/lattice/`) managed by `pyproject.toml` (via Hatch). It is split by logical layers:
- `cli.py`: Command routing and argparse definitions.
- `tui.py`: Full-screen interactive curses interface.
- `tags.py`: Extraction logic (`TagBundle`) over mutagen.
- `utils.py`: Shared utilities (progress bars, terminal formatting).
- `config.py`: Default constants, the default path-extraction `layout`, and persistent library root configuration (`~/.config/lattice/config.json`).
- `modes/`: The individual operation features (e.g., `library.py`, `integrity.py`, `artwork.py`).

### 2.2 Tag Reading

All tag extraction runs through `get_all_tags()`, which returns a `TagBundle`
named tuple from a single `MutagenFile()` open. Format-specific tag field
mapping (ID3 for MP3, VorbisComment for FLAC/Opus/OGG, MP4 atoms for M4A)
is handled internally. Ratings are read from POPM, TXXX, or Vorbis comment
fields, compatible with foobar2000's `foo_quicktag` and most other taggers.
When a file is missing an artist, album, or genre tag, that field is recovered
from the file's path according to the configured `layout`.

### 2.3 Supported Formats

`.mp3` · `.flac` · `.ogg` · `.opus` · `.m4a` · `.wav` · `.wma` · `.aac`

### 2.4 Standalone Binary

Lattice can be compiled into a standalone native executable using **PyInstaller**.
This encapsulates the Python interpreter, dependencies (`mutagen`, `tqdm`), and the package code into a single self-contained binary, eliminating the need for end-users to install Python or configure `pip`.

### 2.5 Interactive TUI

When run with no arguments, the tool launches a full-screen curses TUI with:
- Arrow-key navigation with highlighted selection cursor
- Color-coded section groups (Library, Integrity, Artwork, Metadata)
- Styled Unicode box drawing for menus, prompts, and pause screens
- Fallback to typed numbered input if curses is unavailable

### 2.6 CLI Interface

Every mode is accessible via flags (`--library`, `--testFLAC`, etc.) for
scripting and automation. All modes accept `--root`, `--output`, `--workers`,
`--quiet`, and `--verbose` where applicable. `--root` is repeatable: several
libraries scan together in one pass and results aggregate across them (a
repeated path is de-duped). An optional `library_roots` array in the config
supplies default roots; the first-run prompt persists only the single
`library_root`.

---

## 3. Modes

| Mode | Flag | Description |
|------|------|-------------|
| Library tree | `--library` | Formatted text tree with artist/album/track/rating/genre |
| AI export | `--ai-library` | Token-efficient flat export for LLM recommendation prompts |
| AI wings | `--ai-wings` | Separate flat library files per genre for AI processing |
| Genre wings | `--all-wings` | Separate library tree file per genre |
| Statistics | `--stats` | Format breakdown, bitrate, ratings, genres, top artists |
| FLAC integrity | `--testFLAC` | Verify via `flac -t` (authoritative) or FFmpeg; classify each file into a severity tier |
| MP3 integrity | `--testMP3` | Decode via FFmpeg (demuxer forced from extension); classify into severity tiers |
| Opus integrity | `--testOpus` | Decode via FFmpeg; classify into severity tiers |
| WAV integrity | `--testWAV` | Decode via FFmpeg; classify into severity tiers |
| WMA integrity | `--testWMA` | Decode via FFmpeg; classify into severity tiers |
| Cover extraction | `--extractArt` | Extract embedded art with format priority ranking |
| Missing art | `--missingArt` | Report directories with no cover art |
| Art quality audit | `--auditArtQuality` | Report folder/embedded covers below a resolution threshold |
| Duplicates | `--duplicates` | Four-section report: exact album dupes (cross-directory), within-folder multi-format pairs, fuzzy similar-name candidates, and track-level cross-library duplicates filtered by duration |
| Tag audit | `--auditTags` | Report files missing title, artist, track number, or genre |
| Bitrate audit | `--auditBitrate` | Report files below a configurable bitrate floor |
| ReplayGain audit | `--auditReplayGain` | Report per-album ReplayGain coverage (missing / partial / no album gain / OK); Opus R128 gain counts as tagged |
| Smart playlist | `--playlist` | Generate an `.m3u` from a dynamic rule (e.g. `rating >= 4 and genre == 'Jazz'`) |

---

## 4. Output

All output modes write `.txt` reports (not CSV). Results are grouped by
severity or category with headers and relative paths. Designed for human
reading, not spreadsheet import.

### 4.1 Integrity Severity Tiers

The integrity scanners classify every file rather than emitting a binary
pass/fail, because a decoder complaint is not by itself evidence of damaged
audio (ffmpeg reports decode errors on files that play start to finish):

- **CORRUPT**: could not decode through (tool exited non-zero / could not open),
  or a FLAC that lost sync *before* its declared sample count (true truncation).
- **SUSPECT**: decoded to the end but the tool complained; usually plays. Also
  a FLAC that decoded every declared sample and then hit trailing data.
- **METADATA**: only tag/container parse warnings; the audio is unaffected.
- **OK**: clean decode.

CORRUPT and SUSPECT are always listed; METADATA and OK are summarized and listed
only with `--verbose`. The process exit code is `1` only when at least one file
is CORRUPT.

---

## 5. What Lattice Is Not

- **Not a player.** It reads tags; it does not play audio.
- **Not a tagger.** It reads metadata; it does not write it.
- **Not a database.** It walks the filesystem every time; there is no index.
- **Not a sync tool.** It does not interact with cloud services or devices.
