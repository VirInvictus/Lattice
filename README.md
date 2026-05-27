<p align="center">
  <img src="logo.svg" alt="Lattice" width="420">
</p>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.14%2B-blue" alt="Python 3.14+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
</p>

---

# Lattice

A CLI/TUI toolkit for music collectors who manage their own libraries. Lattice handles library visualization, integrity verification, cover art extraction, and metadata auditing, built on `mutagen` and `tqdm`, with `flac` and `ffmpeg` shelled out for integrity checks.

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

## Architecture

Lattice is a modular Python package:

- `tags.py`: unified abstraction layer for format-agnostic metadata extraction.
- `modes/`: per-mode implementation of auditing and visualization logic.
- `tui.py`: full-screen curses interface for interactive maintenance.

## Installation & Requirements

Lattice can be installed as a Python package or compiled into a standalone binary.

**Option 1: Install via pipx (Recommended)**
```bash
pipx install .
# Now you can run `lattice` globally
```

**Option 2: Install via pip (Virtual Environment)**
```bash
python -m venv .venv
source .venv/bin/activate
pip install .
```

**System tools (integrity modes):**

- [`flac`](https://xiph.org/flac/): used by `--testFLAC` (preferred)
- [`ffmpeg`](https://ffmpeg.org/): used by `--testMP3`, `--testOpus`, and as a fallback for `--testFLAC`

On Windows: `winget install flac ffmpeg`
On Fedora/RHEL: `sudo dnf install flac ffmpeg-free`
On Debian/Ubuntu: `sudo apt install flac ffmpeg`

**Tests:**

The test suite is stdlib `unittest` (no extra dependencies): pure-helper unit tests plus integration tests that run the report modes against a committed fixture library. Run it from the repo root:

```bash
python -m unittest discover
```

## Usage

Lattice remembers your library location. On first run (TUI or CLI) it asks for your music library path and saves it to `~/.config/lattice/config.json`; after that, `--root` is optional.

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

# Audit tags for missing metadata
lattice --auditTags --output tag_audit.txt
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

## Companion Script: `retag.py`

Included in `scripts/` is `retag.py`, a universal genre tagger designed to work directly with the `--all-wings --paths` output. 

Audio metadata formats handle multiple genres entirely differently (ID3 uses null bytes or slashes, Vorbis uses multiple `GENRE=` pairs, Apple uses specific custom atoms). `retag.py` abstracts this container chaos away, allowing you to safely hard-overwrite genres on an entire album directory simultaneously.

**The Workflow:**
1. Generate your wings with paths: `lattice --all-wings --root ~/Music --output wings/ --paths`
   *(If you are using the compiled binary, replace `lattice` with `./dist/lattice`)*
2. Open a generated wing (e.g., `Uncategorized_Library.txt`) and copy the bracketed `[/path/to/album]` from an album header.
3. Pass that path and your desired new genre(s) to `retag.py`:
   ```bash
   ./scripts/retag.py "/mnt/SharedData/Music/Kanye West/Yeezus" "Alternative Rap" "Industrial"
   ```

## Companion Script: `cleaner.py`

Also in `scripts/` is `cleaner.py`, a one-shot consolidator for **album folders that have fragmented across two paths because of inconsistent metadata**. The pattern looks like this:

```
Music/Modern Baseball/You're Gonna Miss It All/   ← 3 mp3s (straight quote)
Music/Modern Baseball/You’re Gonna Miss It All/   ← 3 opus (curly quote)
```

Same album, no track overlap, scattered between two folders by filesystem accident. The same artifact shows up at the artist level (`Jay-Z & Kanye West/` vs `JAY‐Z & Kanye West/`, different hyphen codepoints) and across casing variants (`BONES/` vs `Bones/`).

`cleaner.py` walks the library, finds every sibling pair of folders whose names normalize to the same key (after folding curly→straight quotes, en/em-dashes→ASCII hyphen, NFKC, lowercase, strip), and merges the smaller into the larger.

**Safety contract.**
- **`mv` only** on the same filesystem: an atomic rename, so audio bytes are never read or rewritten.
- **Audio collisions never auto-delete.** If a track of the same name exists in both folders with *different* file sizes, the source copy is kept under a `<stem>.from-fragment.<ext>` suffix instead of being overwritten. Identical-size copies (true duplicates) are dropped from the source.
- **Non-audio collisions** (`cover.jpg`, `.nfo`, etc.) drop the source; the canonical folder's copy wins.
- **Conservative matching.** Only sibling folders whose normalized names match are merged. Cases like `Domestica` vs `Cursive's Domestica (Deluxe Edition)` (different prefix, not just quote variation) are left alone for manual review.
- **`--dry-run` flag** previews every move without touching the filesystem; log lines are prefixed `[DRY]`.
- **Per-file logging** to `<directory>/cleanup.log` (or `--log` override): every move, drop, collision, and `rmdir` is timestamped and audit-trailed.
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

**What it does not do.** `cleaner.py` is intentionally narrow. It does not:
- Rewrite tags (use `retag.py` for that)
- Re-encode or transcode audio (filesystem operations only)
- Match albums by tag content (folder name only, by design, so the operation is auditable from the log alone)
- Touch the source-of-truth import pipeline. If the same fragmentation pattern keeps reappearing, the upstream tagger or downloader needs a curly-quote normalization rule.

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

## Supported formats

`.mp3` · `.flac` · `.ogg` · `.opus` · `.m4a` · `.wav` · `.wma` · `.aac`

## Full help output

<details>
<summary>Full <code>lattice --help</code></summary>

```
usage: lattice [-h] [--version] [--library | --ai-library | --all-wings | --ai-wings | --testFLAC | --testMP3 | --testOpus | --testWAV |
               --testWMA | --extractArt | --missingArt | --auditArtQuality | --duplicates | --auditTags | --auditBitrate | --playlist | --stats]
               [--root ROOT] [--output OUTPUT] [--rule RULE] [--layout LAYOUT] [--min-art-res MIN_ART_RES] [--min-bitrate MIN_BITRATE]
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
  --playlist            Generate a smart .m3u playlist based on a rule
  --stats               Library-wide statistics summary
  --root ROOT           Root directory (default: read from config or current dir)
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
