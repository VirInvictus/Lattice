# Lattice — Roadmap

What's done, what's next, what's deferred. Sequenced for maximum utility as a standalone library management suite. Updated as of v4.4.1.

---

## Phase 1: Foundation & Extraction (Data Layer)
- [x] **Unified Tag Reader** — `get_all_tags` returning `TagBundle` from a single `MutagenFile()` open.
- [x] **Universal Audio Support** — Extract metadata across MP3, FLAC, OGG, Opus, M4A, WAV, WMA, AAC.
- [x] **Rating Normalization** — Convert POPM, TXXX, and Vorbis comments (0-100, 0-255 scales) into a standard 0-5 float.
- [x] **Cover Art Priority Extraction** — Extract embedded art prioritizing FLAC > Opus > M4A > MP3.
- [x] **Front Cover Preference** — Prefer ID3 APIC/FLAC type 3 over generic embedded images.
- [x] **Modern Architecture** — Migrate from monolithic script to a modular `src/lattice` package via Hatch (`pyproject.toml`).
- [x] **Configurable Layout** — Support non-standard directory structures via pattern argument instead of strict `ARTIST/ALBUM/Track.ext`.
- [x] **Fix Close button trying to change music root location** - This should just exit.

## Phase 2: Integrity & Auditing (Validation Layer)
- [x] **Parallel FLAC Verification** — Spawn parallel workers to run `flac -t` or `ffmpeg` to detect corruption.
- [x] **Unified Decode Scanner** — Run parallel FFmpeg decodes for MP3 and Opus formats to detect decode errors.
- [x] **Metadata Audit** — Report audio files missing critical tags (title, artist, track number, genre).
- [x] **Duplicate Detection** — Detect the same artist + album combination appearing across multiple directories or formats.
- [x] **Missing Art Reporting** — Distinguish between directories with absolutely no art and those with only embedded art.
- [x] **Case-Insensitive Cover Detection** — Detect `cover.jpg`, `Folder.png`, etc. without case sensitivity issues.
- [x] **WAV/WMA Integrity** — Extend the unified decode scanner to cover remaining legacy formats.
- [x] **Art Quality Audit** — Report extracted/folder covers below a resolution threshold (e.g., < 500x500).
- [x] **Bitrate Floor Audit** — Flag audio files falling below a configurable bitrate floor (e.g., < 192 kbps).

## Phase 3: Reporting & Generation (Output Layer)
- [x] **Library Tree Generation** — Build beautiful ASCII/Unicode trees displaying artists, albums, tracks, and ratings.
- [x] **Genre Appending** — Optional `--genres` flag to inline genre tags into the library tree.
- [x] **AI-Readable Export** — Generate a token-efficient, pipe-delimited library dump designed for LLM prompts.
- [x] **Genre Wings** — Generate separate library `.txt` files for every detected genre in the collection.
- [x] **AI Wings** — Generate separate, token-efficient AI library files for each genre (Artist | Album | Genre | Location).
- [x] **Library Statistics** — Generate reports on formats, bitrates, top artists, top genres, and rating distributions.
- [x] **Playlist Export (.m3u)** — Generate standard `.m3u` playlists from library tree filters (e.g., 5-star only, specific genre).
- [x] **Smart Playlists** — Dynamic rule-based `.m3u` generation (e.g., "rating >= 4 AND genre == 'Jazz'").
- [x] **Rating Distribution per Genre** — Cross-tabulate rating spread specific to each genre inside `--stats`.

## Phase 4: Interface & Experience (UX Layer)
- [x] **Full-Screen Curses TUI** — Arrow-key navigation, color-coded section groups, and styled Unicode box drawing.
- [x] **Immersive TUI Operations** — In-menu progress bars and integrated text pagers that never drop the user back to a raw terminal shell.
- [x] **Graceful Fallbacks** — Text-based menu and typed input for environments without `curses` support.
- [x] **Persistent Configuration** — Save library root to `~/.config/lattice/config.json` on first run to eliminate redundant prompts.
- [x] **Submenus** — Nested library tree/export modes inside the TUI.
- [x] **Standalone Binary** — Support compiling a self-contained, native binary using PyInstaller.
- [x] **CLI Flag Parity** — Extensive command-line arguments mapping 1:1 with TUI capabilities (including `--dry-run` and `--version`).
- [ ] **Progress Persistence** — Resume interrupted large-scale integrity scans (FLAC/MP3) from where they left off without restarting.
- [ ] **Color Output in CLI** — Inject ANSI color codes for terminal output outside of the TUI environment.
- [ ] **Multi-Root Scanning** — Accept multiple `--root` paths in a single invocation or configure an array of roots in the JSON config.

## Found Bugs

- [ ] **FOUND BUG (2026-05-25): `cleaner.py` apostrophe normalization gap.** A fragmented album survived the initial consolidation pass: JPEGMAFIA's *I LAY DOWN MY LIFE FOR YOU (Director's Cut)* is split across two sibling folders, `I LAY DOWN MY LIFE FOR YOU (DIRECTOR's CUT)/` (12 tracks, mp3+opus) and `I LAY DOWN MY LIFE FOR YOU (DIRECTORS CUT)/` (4 unique tracks, mp3). `cleaner.py` did not merge them because its normalization folds curly to straight quotes but does not equate apostrophe-present vs apostrophe-absent: `director's cut` and `directors cut` hash to different keys. Fix: in the folder-name normalization key, also strip or fold apostrophes (or, more broadly, drop all non-alphanumeric characters after the existing NFKC/dash/quote/case folding) so `X's` and `Xs` collapse to the same key. Surfaced by `lattice --duplicates` (exact-album-duplicates section) on the 2026-05-25 library pass.
