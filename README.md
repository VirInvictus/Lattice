<p align="center">
  <img src="logo.svg" alt="Lattice" width="420">
</p>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.14%2B-blue" alt="Python 3.14+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
</p>

A CLI/TUI toolkit for music collectors who manage their own libraries. Lattice handles library visualization, integrity verification, cover art extraction, and metadata auditing, built on `mutagen` and `tqdm`, with `flac` and `ffmpeg` shelled out for integrity checks.

> **Lattice is read-only.** It reads tags and decodes audio, and it writes only reports, playlists, and extracted cover art. It never modifies the metadata inside your audio files. The optional companion scripts in `scripts/` are the deliberate exception: they **do** modify files (tags, rating bytes, folder layout) and must be used with caution. See [Companion scripts](#companion-scripts).

> **Note:** This is considered completed software. It is effectively feature complete; bug fixes will be addressed as they come, but no new features are planned. It has been thoroughly tested and is known to be fully functional on the primary development environment: **Fedora Linux 44 (Workstation Edition)**, kernel `7.0.9-205.fc44.x86_64`, on **Python 3.14**, with `flac` and `ffmpeg` from the Fedora repositories. While it is pure Python and should be cross-platform, this specific setup is the only officially tested environment.

## Contents

- [Why this exists](#why-this-exists)
- [Features](#features) · [Sample output](#sample-output)
- [Installation](#installation) · [Requirements](#requirements)
- [Usage](#usage)
- Modes: [AI library export](#ai-library-export) · [Genre wings](#genre-wings) · [Multi-root scanning](#multi-root-scanning) · [Integrity checks](#integrity-checks) · [Library statistics](#library-statistics) · [Cover art extraction](#cover-art-extraction) · [Color output](#color-output) · [Supported formats](#supported-formats)
- [Architecture](#architecture)
- [Full help output](#full-help-output)
- [Companion scripts](#companion-scripts) (destructive): [`retag.py`](#retagpy) · [`genre_tidy.py`](#genre_tidypy) · [`rerate.py`](#reratepy) · [`cleaner.py`](#cleanerpy) · [`genre_foldermap.py`](#genre_foldermappy)
- [Credits & Acknowledgements](#credits--acknowledgements) · [Support](#support)

## Why this exists

Modern music players often hide your library behind proprietary databases. Lattice is built for collectors who treat the filesystem as the source of truth. It reads tags directly via `mutagen`, ensuring your library is portable and player-agnostic.

## Features

| Mode | Flag | Description |
|------|------|-------------|
| **Library tree** | `--library` | Builds a formatted text tree with artist/album/track/rating/genre |
| **AI library export** | `--ai-library` | Token-efficient flat export for LLM recommendation prompts |
| **Genre wings** | `--all-wings` | Generates a separate library tree file for each genre |
| **AI wings** | `--ai-wings` | Generates separate AI-friendly flat library files per genre |
| **Smart Playlist** | `--playlist` | Generates an .m3u playlist based on a dynamic rule (e.g. `rating >= 4`) |
| **Library statistics** | `--stats` | Library-wide statistics: format breakdown, bitrate, ratings, genres, top artists |
| **FLAC integrity** | `--testFLAC` | Verifies FLAC via `flac -t` (authoritative) or FFmpeg; sorts files into severity tiers |
| **MP3 integrity** | `--testMP3` | Decodes MP3 through FFmpeg (demuxer forced); sorts files into severity tiers |
| **Opus integrity** | `--testOpus` | Decodes Opus through FFmpeg; sorts files into severity tiers |
| **WAV integrity** | `--testWAV` | Decodes WAV through FFmpeg; sorts files into severity tiers |
| **WMA integrity** | `--testWMA` | Decodes WMA through FFmpeg; sorts files into severity tiers |
| **Cover art extraction** | `--extractArt` | Extracts embedded art to `cover.jpg` with format priority ranking |
| **Missing art report** | `--missingArt` | Lists directories with no cover art (folder or embedded) to text |
| **Art quality audit** | `--auditArtQuality` | Reports extracted/folder covers below a resolution threshold |
| **Duplicate detection** | `--duplicates` | Four-section report: exact album dupes across directories, within-folder multi-format pairs, fuzzy similar-name candidates, and track-level dupes filtered by duration |
| **Tag audit** | `--auditTags` | Reports files missing title, artist, track number, or genre to text |
| **Bitrate audit** | `--auditBitrate` | Reports files falling below a minimum bitrate floor |
| **ReplayGain audit** | `--auditReplayGain` | Reports per-album ReplayGain coverage (missing, partial, no album gain, OK); Opus R128 gain counts as tagged |
| **Version** | `--version` | Prints version and exits |

Running with no arguments launches an interactive TUI: a full-screen curses interface with arrow-key navigation, color-coded section groups (Library, Integrity, Artwork, Metadata), and a highlighted selection cursor. Menus, parameter prompts, and pause screens all render inside styled Unicode boxes for a consistent experience. Library tree, AI export, and genre wings live in a dedicated submenu. Falls back to typed input if curses is unavailable.

## Sample output

```
ARTIST: Ólafur Arnalds
  ├── ALBUM: Found Songs (Neo-Classical)
      ├── SONG: 01. Ólafur Arnalds — Erla's Waltz (flac) [★★★★★ 5.0/5]
      ├── SONG: 02. Ólafur Arnalds — Raein (flac) [★★★★★ 5.0/5]
      ├── SONG: 03. Ólafur Arnalds — Romance (flac) [★★★★★ 5.0/5]
      ├── SONG: 04. Ólafur Arnalds — Allt varð hljótt (flac) [★★★★★ 5.0/5]
      ├── SONG: 05. Ólafur Arnalds — Lost Song (flac) [★★★★★ 5.0/5]
      ├── SONG: 06. Ólafur Arnalds — Faun (flac) [★★★★★ 5.0/5]
      └── SONG: 07. Ólafur Arnalds — Ljósið (flac) [★★★★★ 5.0/5]
```

Genre tags are optional (`--genres`). If your genre metadata is inconsistent, leave them off; the tree gets unwieldy fast.

## Installation

Lattice installs as a Python package, or compiles into a standalone binary (PyInstaller, `hatch run build-bin`).

**Option 1: pipx (recommended)**
```bash
pipx install .
# now you can run `lattice` globally
```

**Option 2: pip (virtual environment)**
```bash
python -m venv .venv
source .venv/bin/activate
pip install .
```

## Requirements

Runtime dependencies are `mutagen` and `tqdm` (installed automatically). The integrity modes shell out to system tools:

- [`flac`](https://xiph.org/flac/): used by `--testFLAC` (preferred)
- [`ffmpeg`](https://ffmpeg.org/): used by `--testMP3`, `--testOpus`, `--testWAV`, `--testWMA`, and as a fallback for `--testFLAC`

```bash
# Fedora/RHEL
sudo dnf install flac ffmpeg-free
# Debian/Ubuntu
sudo apt install flac ffmpeg
# Windows
winget install flac ffmpeg
```

**Tests.** The suite is stdlib `unittest` (no extra dependencies): pure-helper unit tests plus integration tests that run the report modes, and the companion scripts, against a committed fixture library. Run it from the repo root:

```bash
python -m unittest discover
```

## Usage

Lattice remembers your library location. On first run (TUI or CLI) it asks for your music library path and saves it to `~/.config/lattice/config.json`; after that, `--root` is optional. Repeat `--root` to scan several libraries together in one pass (see [Multi-root scanning](#multi-root-scanning)).

```bash
# Build a library tree with genre tags
lattice --library --output library.txt --genres

# Export library for AI/LLM recommendation prompts
lattice --ai-library --output library_ai.txt

# Generate per-genre library files (add --genres to label each album)
lattice --all-wings --output wings/

# Generate per-genre AI-friendly library files
lattice --ai-wings --output wings_ai/

# Library statistics (prints to screen, or --output for file)
lattice --stats
lattice --stats --output library_stats.txt

# Verify FLAC integrity (4 parallel workers)
lattice --testFLAC --output flac_errors.txt --workers 4

# Verify MP3s for decode errors
lattice --testMP3 --output mp3_errors.txt --workers 4

# Verify Opus files for decode errors
lattice --testOpus --output opus_errors.txt --workers 4

# Extract cover art (FLAC > Opus > M4A > MP3 priority)
lattice --extractArt

# Preview art extraction without writing files
lattice --extractArt --dry-run

# Report directories missing cover art
lattice --missingArt --output missing_art.txt

# Find duplicates: exact, multi-format, similar-name, track-level
lattice --duplicates --output duplicates.txt

# Scan two libraries together (repeat --root); surfaces cross-library duplicates
lattice --duplicates --root ~/Music --root /mnt/usb/Albums --output duplicates.txt

# Audit tags for missing metadata
lattice --auditTags --output tag_audit.txt

# Audit ReplayGain coverage per album (add --verbose to also list fully-tagged albums)
lattice --auditReplayGain --output replaygain_audit.txt
```

## AI library export

The `--ai-library` mode generates a flat, pipe-delimited summary designed to fit inside an LLM context window for music recommendations:

```
Artist | Album | Genre | Rating | Tracks
--------------------------------------------------
Converge | Jane Doe | Metalcore | 4.8 | 12
Ólafur Arnalds | Found Songs | Neo-Classical | 5.0 | 7
```

**Rating** is the average across all rated tracks. **Tracks** is the number of audio files in the album directory. If you've culled 3-star-and-below tracks from disk, this is your survivor count. Paste the output into a prompt and ask for recommendations against your actual library.

## Genre wings

`--all-wings` groups albums by genre and writes one library tree file per genre into the output directory:

```bash
lattice --all-wings --root ~/Music --output wings/
```

Produces `Alternative_Rock_Library.txt`, `East_Coast_Rap_Library.txt`, and so on; untagged albums land in `Uncategorized_Library.txt`. Add `--genres` to label each album header.

## Multi-root scanning

`--root` is repeatable, so a single invocation can span more than one library:

```bash
lattice --duplicates --root ~/Music --root /mnt/usb/Albums --output duplicates.txt
```

Every mode aggregates across the roots: combined statistics, one merged library tree, genre wings that span both, and so on. A path passed twice is de-duped. The payoff for `--duplicates` is cross-library detection: an album that lives in both libraries is grouped as a single exact duplicate, and each entry is prefixed by its root's basename (`Music/…` vs `Albums/…`) so you can tell the copies apart.

To make several roots permanent, add a `library_roots` array to `~/.config/lattice/config.json`:

```json
{ "library_roots": ["/home/you/Music", "/mnt/usb/Albums"] }
```

The first-run prompt still saves only the single `library_root`, so a throwaway `--root` is never written to config.

## Integrity checks

The integrity modes (`--testFLAC`, `--testMP3`, `--testOpus`, `--testWAV`, `--testWMA`) decode every file and sort the results into four tiers rather than a flat pass/fail, because a decoder complaint is not by itself proof of damaged audio:

- **CORRUPT**: could not decode through, or a FLAC truncated before its declared length.
- **SUSPECT**: decoded to the end but the tool complained (these usually still play), or a FLAC with trailing data after a complete stream.
- **METADATA**: only tag/container parse warnings; the audio is fine.
- **OK**: clean decode.

CORRUPT and SUSPECT are always listed in the report; METADATA and OK are summarized and listed only with `--verbose`. The exit code is `1` only when something is CORRUPT, so a clean-but-chatty library still exits `0`. FFmpeg is invoked with the demuxer forced from the file extension (so a large ID3v2 tag is never mis-read as a corrupt container) and with embedded cover art skipped.

## Library statistics

`--stats` reports file counts, total size and duration, a per-format breakdown, a bitrate summary, the rating distribution, top genres, and top artists. Prints to screen, or `--output` to save.

## Cover art extraction

`--extractArt` writes embedded art to `cover.jpg`, pulling from the highest-quality source in each directory (FLAC → Opus/OGG → M4A → MP3) and preferring the "Front Cover" picture type. It checks for existing covers case-insensitively (`cover`/`folder`/`front`/`album` in `.jpg`/`.jpeg`/`.png`), so it won't duplicate art. Reads FLAC pictures, Opus/OGG `METADATA_BLOCK_PICTURE`, M4A `covr` atoms, and MP3 `APIC` frames.

## Color output

The status summary that each integrity mode prints is colorized: green for an all-clear, yellow for suspect counts, red for corrupt counts. Color appears only on an interactive terminal. It is suppressed inside the TUI, when output is piped or redirected, and when `NO_COLOR` is set, so report files and pipes stay clean.

## Supported formats

`.mp3` · `.flac` · `.ogg` · `.opus` · `.m4a` · `.wav` · `.wma` · `.aac`

## Architecture

Lattice is a modular Python package under `src/lattice/`:

- `tags.py`: unified abstraction layer for format-agnostic metadata extraction (returns a `TagBundle` from a single `mutagen` open).
- `modes/`: per-mode implementation of auditing and visualization logic (library, integrity, artwork, audit, stats, playlists).
- `cli.py` / `tui.py`: the argparse dispatch and the full-screen curses interface; both call the same mode functions.

The filesystem is the source of truth: Lattice walks the tree on every invocation and keeps no index or database.

## Full help output

<details>
<summary>Full <code>lattice --help</code></summary>

```
usage: lattice [-h] [--version] [--library | --ai-library | --all-wings | --ai-wings | --testFLAC | --testMP3 | --testOpus | --testWAV |
               --testWMA | --extractArt | --missingArt | --auditArtQuality | --duplicates | --auditTags | --auditBitrate | --auditReplayGain |
               --playlist | --stats]
               [--root DIR] [--output OUTPUT] [--rule RULE] [--layout LAYOUT] [--min-art-res MIN_ART_RES] [--min-bitrate MIN_BITRATE]
               [--workers WORKERS] [--prefer {flac,ffmpeg}] [--quiet] [--genres] [--paths] [--dry-run] [--only-errors | --no-only-errors]
               [--ffmpeg FFMPEG] [--verbose]
               [pos_root]

Music library toolkit: tree, integrity, art, duplicates, tag audit

positional arguments:
  pos_root              Root directory (positional fallback)

options:
  -h, --help            show this help message and exit
  --version             show program's version number and exit
  --library             Generate library tree
  --ai-library          Generate token-efficient library for AI recommendations
  --all-wings           Generate separate library files for each genre
  --ai-wings            Generate separate AI-friendly library files for each genre
  --testFLAC            Verify FLAC files
  --testMP3             Verify MP3 files
  --testOpus            Verify Opus files via FFmpeg decode
  --testWAV             Verify WAV files via FFmpeg decode
  --testWMA             Verify WMA files via FFmpeg decode
  --extractArt          Extract embedded cover art to folder
  --missingArt          Report directories missing cover art
  --auditArtQuality     Report extracted/folder covers below a resolution threshold
  --duplicates          Detect duplicates: exact albums, within-folder multi-format, similar names, track-level
  --auditTags           Report files with incomplete tags
  --auditBitrate        Report files below a certain bitrate floor
  --auditReplayGain     Report per-album ReplayGain coverage (missing, partial, no album gain)
  --playlist            Generate a smart .m3u playlist based on a rule
  --stats               Library-wide statistics summary
  --root DIR            Root directory; repeat --root to scan several libraries
                        together (default: read from config or current dir)
  --output OUTPUT       Output path
  --rule RULE           Smart playlist rule (e.g. "rating >= 4 and genre == 'Jazz'")
  --layout LAYOUT       Directory structure pattern for extracting tags from path (default: {artist}/{album})
  --min-art-res MIN_ART_RES
                        Minimum resolution in pixels for --auditArtQuality (default: 500)
  --min-bitrate MIN_BITRATE
                        Minimum bitrate in kbps for --auditBitrate (default: 192)
  --workers WORKERS     Parallel workers (integrity modes)
  --prefer {flac,ffmpeg}
                        Preferred tool (FLAC mode)
  --quiet               Minimize output
  --genres              Include album genres in library tree
  --paths               Include absolute directory paths at the album level
  --dry-run             Preview changes without writing (extractArt)
  --only-errors, --no-only-errors
                        Write only errors/warns (MP3/Opus modes)
  --ffmpeg FFMPEG       Path to ffmpeg
  --verbose             Verbose output
```

</details>

## Companion scripts

The `scripts/` directory holds six standalone maintenance tools. They are **not** part of the `lattice` package and deliberately sit **outside its read-only contract**: unlike Lattice itself, they **modify your files in place**, rewriting tags, rewriting rating bytes, or moving and renaming folders. Run them directly with `python3`.

**Use them with caution.** Have a backup or snapshot first, always preview with `--dry-run`, and read the log before applying. Each writes an append-only timestamped log and is idempotent, so a second run on an already-clean library is a no-op.

| Script | What it changes | Scope |
|--------|-----------------|-------|
| [`retag.py`](#retagpy) | Genre tags on one album directory | manual, per-album |
| [`genre_tidy.py`](#genre_tidypy) | Genre tags library-wide (through `retag.py`) | policy map, then apply |
| [`rerate.py`](#reratepy) | MP3 POPM rating bytes | reconcile DeaDBeeF / foobar |
| [`cleaner.py`](#cleanerpy) | Folder names and layout (moves, merges, renames) | filesystem |
| [`genre_foldermap.py`](#genre_foldermappy) | Restructures the tree into Genre/Artist/Album | filesystem |
| [`replaygain.py`](#replaygainpy) | Writes ReplayGain 2.0 gain/peak tags (via `rsgain`) | album-by-album |

### `retag.py`

> **Destructive: writes genre tags in place.** Always preview with `--dry-run`; pass `--log` to keep an append-only record.

A universal genre tagger designed to work directly with the `--all-wings --paths` output.

Audio metadata formats handle multiple genres entirely differently (ID3 uses null bytes or slashes, Vorbis uses multiple `GENRE=` pairs, Apple uses specific custom atoms). `retag.py` abstracts this container chaos away, allowing you to safely hard-overwrite genres on an entire album directory simultaneously.

**The Workflow:**
1. Generate your wings with paths: `lattice --all-wings --root ~/Music --output wings/ --paths`
   *(If you are using the compiled binary, replace `lattice` with `./dist/lattice`)*
2. Open a generated wing (e.g., `Uncategorized_Library.txt`) and copy the bracketed `[/path/to/album]` from an album header.
3. Preview the change first with `--dry-run` (prints `old -> new` per file, writes nothing):
   ```bash
   ./scripts/retag.py "/mnt/SharedData/Music/Kanye West/Yeezus" "Alternative Rap" "Industrial" --dry-run
   ```
4. When it looks right, drop `--dry-run` to apply it:
   ```bash
   ./scripts/retag.py "/mnt/SharedData/Music/Kanye West/Yeezus" "Alternative Rap" "Industrial"
   ```

### `genre_tidy.py`

> **Destructive on `apply`.** `build` is read-only; `apply` rewrites genre tags through `retag.py`. Preview `apply` with `--dry-run` first.

A two-phase tool for libraries whose genre tags have drifted: it builds an **artist to genre authority map**, then collapses any album that disagrees with it. It pairs Lattice with `retag.py`: the `build` phase only reads (through lattice's scanner), and the `apply` phase does every write through `retag.py`. It imports `lattice`, so it needs the package importable: installed via `pip`/`pipx`, or run from a checkout with `PYTHONPATH=src`.

This is aimed at the messy general library, not a meticulously tagged one. Because `build` records every genre an artist already uses, `apply` does nothing until you edit the map; on a cleanly tagged library it reports everything compliant.

**The map.** `build` writes an editable tab-separated file (default `<library>/genre_map.tsv`), one line per artist listing every genre that artist is allowed to carry:

```
Artist<TAB>Genre<TAB>Second Genre<TAB>...
```

- Every genre on the line is **allowed**: albums tagged with any of them are left untouched.
- The **first** genre is the fix target: `apply` retags any of that artist's albums whose genre is *not* on the line to this first genre.
- `build` seeds the line with all the genres the artist currently uses (most-common first), so the map starts as a faithful snapshot and `apply` is a no-op. **To tidy, remove a stray genre from a line**; its albums then collapse to the first genre. Reorder the line to change which genre is the target.
- Leave only the artist (nothing after it) to skip that artist entirely.
- Multi-genre artists get a `#` comment above their line with the per-genre counts, so low-count strays worth trimming stand out (e.g. `# Eminem: 3 genres: Hardcore Hip Hop×13, Boom Bap×1, Horrorcore×1`).
- **Compilations are excluded.** An album whose album-artist is `Various Artists` (or `VA`/`Various`) gets a flagged `EXCLUDED` comment, never an enforceable row, and `apply` always skips it: a compilation collects unrelated tracks with no single canonical genre, so there is nothing to enforce.

Matching is by the **artist tag** (normalized for quote, dash, and case variants), not the folder name. Lattice's tag layer prefers the album-artist, so a compilation is keyed under its `Various Artists` album-artist and caught by the exclusion above.

**Safety.** Seeding the map from the library's current state means `apply` changes nothing you have not asked for: a retag happens only where you removed a genre from a line. `apply` is otherwise guarded like the other companions: `--dry-run` previews every `retag.py` call and writes nothing (log lines prefixed `[DRY]`), an append-only timestamped log records every decision (default `<library>/genre_tidy.log`), and the operation is idempotent (a second `apply` is all no-ops). Re-running `build` over an existing map preserves your edits and only appends artists new to the library.

**The Workflow:**
1. Build the map (read-only):
   ```bash
   ./scripts/genre_tidy.py build /mnt/SharedData/Music
   ```
2. Open `genre_map.tsv`. Each line lists an artist's current genres; remove the strays you consider mistakes (the `#`-commented lines with low counts are the usual suspects), reorder to change a fix target, or blank a line to leave an artist alone.
3. Preview the changes (writes nothing):
   ```bash
   ./scripts/genre_tidy.py apply /mnt/SharedData/Music --dry-run
   ```
4. Inspect `genre_tidy.log`; every retag it would perform is recorded with `[DRY]`.
5. Apply for real:
   ```bash
   ./scripts/genre_tidy.py apply /mnt/SharedData/Music
   ```

**Relationship to `retag.py`.** `retag.py` is the manual, one-album tool; `genre_tidy.py` is the library-wide policy layer on top of it, calling it once per album you have tidied out of compliance. Reach for `retag.py` for a one-off fix, `genre_tidy.py` to enforce a whole-collection rule.

A real, `build`-generated map from a roughly 877-artist library ships at [`artist_genre_defaults.tsv`](artist_genre_defaults.tsv) in the repo root. It doubles as a worked example of the format (single- and multi-genre lines, the `#`-flagged counts, the blank-to-skip pattern) and as a maintained authority: point the tool at it with `--map artist_genre_defaults.tsv`. Keep it current by re-running `build`, which appends artists new to the library under a dated marker while preserving every line you have edited; hand-edit a line to accept a new genre for an existing artist.

### `rerate.py`

> **Destructive: rewrites MP3 rating bytes in place.** Preview with `--dry-run`; every change is logged, so a run is reversible.

Reconciles MP3 star ratings between DeaDBeeF and foobar2000. Both store ratings in an ID3 POPM frame (a 0–255 byte), but on different scales, so a rating set in one reads shifted in the other. Measured on a real library:

- DeaDBeeF 2★ writes byte `127`, which foobar reads as **3★**.
- DeaDBeeF 4★ writes byte `254`, which foobar reads as **5★**.

foobar's own values are read the same by both players (byte `196` shows 4★ in DeaDBeeF and foobar alike). So `rerate.py` rewrites DeaDBeeF's odd bytes to the equivalent foobar value, making the two agree without changing what DeaDBeeF shows: `127 → 64` (both 2★) and `254 → 196` (both 4★).

It touches only those exact bytes. foobar's canonical values, MusicBee's bytes (`186`/`242`, which already read correctly), unrated files, and every non-MP3 file are left alone; Vorbis/Opus ratings are clean 0–5 integers and are unaffected. It writes an append-only timestamped log (default `<directory>/rerate.log`) recording every `old -> new` change, so a run is fully auditable and reversible. Idempotent.

**The Workflow:**
1. Preview:
   ```bash
   ./scripts/rerate.py /mnt/SharedData/Music --dry-run
   ```
2. Inspect `rerate.log` (each change is logged as `<file>: 254 -> 196`).
3. Apply:
   ```bash
   ./scripts/rerate.py /mnt/SharedData/Music
   ```

**Scope.** `rerate.py` is MP3/POPM-only and remaps a fixed set of byte values (`REMAP` in the script). The diagnosis behind it is simply "which byte does each player read as which star"; if your players use a different scale, edit that map.

### `cleaner.py`

> **Destructive: moves, merges, and renames folders.** Preview with `--dry-run` and read the log before applying.

A one-shot consolidator for **album folders that have fragmented across two paths because of inconsistent metadata**. The pattern looks like this:

```
Music/Modern Baseball/You're Gonna Miss It All/   ← 3 mp3s (straight quote)
Music/Modern Baseball/You’re Gonna Miss It All/   ← 3 opus (curly quote)
```

Same album, no track overlap, scattered between two folders by filesystem accident. The same artifact shows up at the artist level (`Jay-Z & Kanye West/` vs `JAY‐Z & Kanye West/`, different hyphen codepoints) and across casing variants (`BONES/` vs `Bones/`).

`cleaner.py` walks the library, finds every sibling pair of folders whose names normalize to the same key (after folding curly→straight quotes, en/em-dashes→ASCII hyphen, NFKC, lowercase, strip), and merges the smaller into the larger.

**Safety contract.**
- **`mv` only** on the same filesystem: an atomic rename, so audio bytes are never read or rewritten.
- **Audio collisions never auto-delete.** If a track of the same name exists in both folders with *different* file sizes, the source copy is kept under a `<stem>.from-fragment.<ext>` suffix instead of being overwritten. Identical-size copies (true duplicates) are dropped from the source.
- **Cover-art collisions keep the better image.** When a `.jpg`/`.png` exists in both folders, the higher-resolution file wins (ties, or images it cannot parse, fall back to the larger byte size). Other non-audio collisions (`.nfo`, `.cue`) drop the source; the canonical copy wins.
- **The survivor is normalized.** The folder with the most files becomes canonical, so its name can be the less-standard variant; after merging, the survivor is renamed to its normalized form (broken hyphens, curly quotes/apostrophes folded to ASCII; en/em dashes, the ellipsis glyph, and prime marks preserved so names stay legal on NTFS/exFAT).
- **Conservative matching.** Only sibling folders whose normalized names match are merged. Cases like `Domestica` vs `Cursive's Domestica (Deluxe Edition)` (different prefix, not just quote variation) are left alone for manual review.
- **`--dry-run` flag** previews every action without touching the filesystem (log lines prefixed `[DRY]`) and faithfully predicts the real run, including which folders get removed.
- **Per-file logging** to `<directory>/cleanup.log` (or `--log` override): every move, drop, collision, rename, and `rmdir` is timestamped and audit-trailed.
- **Idempotent**: running on an already-clean library is a no-op.

**The Workflow:**
1. Preview first:
   ```bash
   ./scripts/cleaner.py /mnt/SharedData/Music --dry-run
   ```
2. Inspect `/mnt/SharedData/Music/cleanup.log`; every action it would take is recorded with `[DRY]` prefixes.
3. If the plan looks right, apply for real:
   ```bash
   ./scripts/cleaner.py /mnt/SharedData/Music
   ```
4. Re-run `lattice --duplicates` afterward to confirm the consolidated state.

**Two passes.** Pass 1 collapses artist-folder duplicates (e.g., merges `JAY‐Z & Kanye West/` into `Jay-Z & Kanye West/`). Pass 2 then runs album-level consolidation inside each artist folder. The order matters: collapsing the artist split first means album-level matching can find pairs that would otherwise be hidden under the duplicate artist directory.

**Normalizing lone folders (`--normalize-names`).** The merge passes only touch *duplicate* folders. Libraries often also carry lone, non-duplicate folders whose names use non-standard characters (e.g. `At the Drive‐In` with a unicode hyphen, or a curly apostrophe). With `--normalize-names`, a third pass renames every folder whose name differs from its normalized form, folding the same classes as the survivor rename (broken hyphens, curly quotes/apostrophes; en/em dashes and the ellipsis preserved). It is off by default and can touch many folders at once, so preview with `--dry-run` first.

**What it does not do.** `cleaner.py` is intentionally narrow. It does not:
- Rewrite tags (use `retag.py` for that)
- Re-encode or transcode audio (filesystem operations only)
- Match albums by tag content (folder name only, by design, so the operation is auditable from the log alone)
- Touch the source-of-truth import pipeline. If the same fragmentation pattern keeps reappearing, the upstream tagger or downloader needs a curly-quote normalization rule.

### `genre_foldermap.py`

> **Destructive: moves folders.** Dry-run is the **default**; nothing moves until you pass `--apply`. Every move is recorded to a manifest that `--revert` replays in reverse.

> **Tidy your genre tags first.** Placement uses each album's *dominant* genre, so an album whose tracks disagree on genre lands under whichever value wins the count, and the rest are not reflected in the tree. For predictable results, run strict tag hygiene before this script: a single, consistent genre per album is ideal. [`genre_tidy.py`](#genre_tidypy) is built for exactly that (enforce one canonical genre per artist/album), so a sensible order is `genre_tidy.py` first, then `genre_foldermap.py`.

Restructures a flat `Artist/Album/Song` library into `Genre/Artist/Album/Song`, moving each album folder under a top-level genre directory. The genre is the album's dominant embedded genre tag, read through Lattice's scanner (the same aggregation every library/wing mode uses), so placement matches what Lattice reports. Folder names are preserved verbatim; nothing is retagged. It imports `lattice`, so it needs the package importable: installed via `pip`/`pipx`, or run from a checkout with `PYTHONPATH=src`.

Two directory shapes are handled:
- `Artist/Album` → `Genre/Artist/Album` (the whole album directory is moved).
- An artist folder with **loose tracks** sitting directly inside (no album subfolder) → `Genre/Artist/Singles/`. Only the loose files move; any album subfolders are separate albums placed under their own genre.

Artist-level sidecar files (e.g. an `Artist/cover.jpg` beside the album subfolders) follow the artist to its dominant genre, so they are never orphaned in an emptied folder.

**Safety contract.**
- **`mv` only** on the same filesystem: an atomic rename, so audio bytes (and embedded tags/ratings) are never read or rewritten.
- **Dry-run by default.** Without `--apply` the tool only prints the plan; `--apply` performs it and writes the manifest.
- **Reversible.** Every move is appended to a manifest TSV (`src<TAB>dst<TAB>time`); `genre_foldermap.py --revert <manifest>` undoes the run.
- **Never overwrites.** A destination that already exists is reported and skipped; collisions are flagged before anything moves.
- **Genre names are folded** to a filesystem-legal form (Windows/NTFS-forbidden characters become spaces), so a stray `:` or `/` in a tag can't break the tree.
- **Idempotent**: running on an already-organized library is a no-op.

**The Workflow:**
1. Preview the full plan (writes nothing):
   ```bash
   ./scripts/genre_foldermap.py /mnt/SharedData/Music
   ```
2. Smoke-test one genre, verify it landed, then do the rest:
   ```bash
   ./scripts/genre_foldermap.py /mnt/SharedData/Music --only-genre "Comedy Rock" --apply
   ./scripts/genre_foldermap.py /mnt/SharedData/Music --apply --log ~/foldermap.manifest.tsv
   ```
3. If you change your mind, replay the manifest in reverse:
   ```bash
   ./scripts/genre_foldermap.py --revert ~/foldermap.manifest.tsv
   ```
4. Point Lattice at the new shape by setting `"layout": "{genre}/{artist}/{album}"` in `~/.config/lattice/config.json` (or pass `--layout`), then regenerate your wings.

### `replaygain.py`

> **Destructive: writes ReplayGain tags in place.** Preview with `--dry-run`; a real run prints the worklist and asks for confirmation before writing (skip with `--yes`). Every album scanned, and the exact values written, are logged.

The companion to the [`--auditReplayGain`](#features) audit: where the audit *reports* which albums lack ReplayGain, `replaygain.py` *writes* it. It wraps [`rsgain`](https://github.com/complexlogic/rsgain) (libebur128, ReplayGain 2.0, the `-18 LUFS` / `89 dB` reference foobar2000 uses) to do what foobar's "Scan selection as album" does: compute one album gain plus album peak per album folder and a per-track gain plus peak, then write them into the files. rsgain leaves the audio stream untouched; only metadata changes. It imports `lattice` for the format-aware ReplayGain reader, so it needs the package importable (installed via `pip`/`pipx`, or run from a checkout with `PYTHONPATH=src`).

**Requires `rsgain`.** It is not bundled. On Fedora: `sudo dnf install rsgain`. Other platforms: see the [rsgain releases](https://github.com/complexlogic/rsgain/releases).

**Safety contract.**
- **Album = one folder.** The whole folder is rescanned together so album gain is correct.
- **No half-scanned albums.** A partial album is rescanned in full; `--skip-tagged` skips an already-fully-tagged album *as a unit* (skipping only its tagged tracks would compute album gain over a subset and corrupt it).
- **`--dry-run`** lists every album and its current coverage without invoking rsgain at all.
- **Confirmation before writing.** A real run shows the worklist and prompts, unless `--yes` is passed or stdin is not a TTY.
- **Read-back logging.** After each album, the tags just written are read back and logged, so the log is a record of exactly what landed on disk.
- **Format-aware** through rsgain: MP3 (`TXXX`), FLAC/Ogg (Vorbis), Opus (the `R128_*_GAIN` convention), M4A, WMA, WAV.

**The Workflow:**
1. See what is missing (read-only, from the package):
   ```bash
   lattice --auditReplayGain --root /mnt/SharedData/Music --output rg_audit.txt
   ```
2. Preview the scan plan (writes nothing):
   ```bash
   ./scripts/replaygain.py /mnt/SharedData/Music --dry-run
   ```
3. Apply, skipping already-tagged albums and scanning 4 albums in parallel:
   ```bash
   ./scripts/replaygain.py /mnt/SharedData/Music --skip-tagged --threads 4
   ```

## Credits & Acknowledgements

Lattice is built upon several excellent open-source libraries and tools:

- **[Mutagen](https://github.com/quodlibet/mutagen)**: Handles all audio metadata extraction and tagging logic.
- **[tqdm](https://github.com/tqdm/tqdm)**: Powers the extensible progress bars for library scanning and integrity checks.
- **[FFmpeg](https://ffmpeg.org/)**: The heavy lifter for multi-format audio decoding and integrity verification.
- **[FLAC](https://xiph.org/flac/)**: Used for high-speed native FLAC verification.

## Support

If Lattice's useful to you and you'd like to chip in:

```
bc1qkge6zr45tzqfwfmvma2ylumt6mg7wlwmhr05yv
```
