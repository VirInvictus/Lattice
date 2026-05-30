# Lattice Roadmap

What's done, what's next, what's deferred. Sequenced for maximum utility as a standalone library management suite. Updated as of v4.5.0.

---

## Phase 1: Foundation & Extraction (Data Layer)
- [x] **Unified Tag Reader**: `get_all_tags` returning `TagBundle` from a single `MutagenFile()` open.
- [x] **Universal Audio Support**: Extract metadata across MP3, FLAC, OGG, Opus, M4A, WAV, WMA, AAC.
- [x] **Rating Normalization**: Convert POPM, TXXX, and Vorbis comments (0-100, 0-255 scales) into a standard 0-5 float.
- [x] **Cover Art Priority Extraction**: Extract embedded art prioritizing FLAC > Opus > M4A > MP3.
- [x] **Front Cover Preference**: Prefer ID3 APIC/FLAC type 3 over generic embedded images.
- [x] **Modern Architecture**: Migrate from monolithic script to a modular `src/lattice` package via Hatch (`pyproject.toml`).
- [x] **Configurable Layout**: Support non-standard directory structures via pattern argument instead of strict `ARTIST/ALBUM/Track.ext`.
- [x] **Fix Close button trying to change music root location** - This should just exit.

## Phase 2: Integrity & Auditing (Validation Layer)
- [x] **Parallel FLAC Verification**: Spawn parallel workers to run `flac -t` or `ffmpeg` to detect corruption.
- [x] **Unified Decode Scanner**: Run parallel FFmpeg decodes for MP3 and Opus formats to detect decode errors.
- [x] **Metadata Audit**: Report audio files missing critical tags (title, artist, track number, genre).
- [x] **Duplicate Detection**: Detect the same artist + album combination appearing across multiple directories or formats.
- [x] **Missing Art Reporting**: Distinguish between directories with absolutely no art and those with only embedded art.
- [x] **Case-Insensitive Cover Detection**: Detect `cover.jpg`, `Folder.png`, etc. without case sensitivity issues.
- [x] **WAV/WMA Integrity**: Extend the unified decode scanner to cover remaining legacy formats.
- [x] **Art Quality Audit**: Report extracted/folder covers below a resolution threshold (e.g., < 500x500).
- [x] **Bitrate Floor Audit**: Flag audio files falling below a configurable bitrate floor (e.g., < 192 kbps).

## Phase 3: Reporting & Generation (Output Layer)
- [x] **Library Tree Generation**: Build beautiful ASCII/Unicode trees displaying artists, albums, tracks, and ratings.
- [x] **Genre Appending**: Optional `--genres` flag to inline genre tags into the library tree.
- [x] **AI-Readable Export**: Generate a token-efficient, pipe-delimited library dump designed for LLM prompts.
- [x] **Genre Wings**: Generate separate library `.txt` files for every detected genre in the collection.
- [x] **AI Wings**: Generate separate, token-efficient AI library files for each genre (Artist | Album | Genre | Location).
- [x] **Library Statistics**: Generate reports on formats, bitrates, top artists, top genres, and rating distributions.
- [x] **Playlist Export (.m3u)**: Generate standard `.m3u` playlists from library tree filters (e.g., 5-star only, specific genre).
- [x] **Smart Playlists**: Dynamic rule-based `.m3u` generation (e.g., "rating >= 4 AND genre == 'Jazz'").
- [x] **Rating Distribution per Genre**: Cross-tabulate rating spread specific to each genre inside `--stats`.

## Phase 4: Interface & Experience (UX Layer)
- [x] **Full-Screen Curses TUI**: Arrow-key navigation, color-coded section groups, and styled Unicode box drawing.
- [x] **Immersive TUI Operations**: In-menu progress bars and integrated text pagers that never drop the user back to a raw terminal shell.
- [x] **Graceful Fallbacks**: Text-based menu and typed input for environments without `curses` support.
- [x] **Persistent Configuration**: Save library root to `~/.config/lattice/config.json` on first run to eliminate redundant prompts.
- [x] **Submenus**: Nested library tree/export modes inside the TUI.
- [x] **Standalone Binary**: Support compiling a self-contained, native binary using PyInstaller.
- [x] **CLI Flag Parity**: Extensive command-line arguments mapping 1:1 with TUI capabilities (including `--dry-run` and `--version`).
- [ ] **Progress Persistence**: Resume interrupted large-scale integrity scans (FLAC/MP3) from where they left off without restarting.
- [x] **Color Output in CLI**: Status summaries are colorized (green all-clear, yellow suspect, red corrupt). Gated on an interactive TTY: off in the TUI, off when piped or redirected, off under `NO_COLOR`, so reports and pipes stay clean. (v4.6.0)
- [x] **Multi-Root Scanning**: `--root` is repeatable; several libraries scan together in one pass and de-dupe a repeated path. Cross-library duplicate detection groups an album that lives in two libraries, with each entry prefixed by its root's basename. An optional `library_roots` array in the JSON config supplies default roots; the first-run prompt still saves only the single `library_root`. (v4.6.0)
- [x] **Configurable Path Layout**: A `layout` config key (and `--layout`) sets the pattern Lattice uses to recover artist/album/genre from a path, so a genre-first tree (`{genre}/{artist}/{album}`) is fully supported; genre now falls back to the path like artist/album already did. Default stays `{artist}/{album}`. Pairs with the `genre_foldermap.py` companion script that builds such a tree. (v4.7.0)

## Found Bugs

- [x] **FOUND BUG (2026-05-25): `cleaner.py` apostrophe normalization gap.** A fragmented album survived the initial consolidation pass: JPEGMAFIA's *I LAY DOWN MY LIFE FOR YOU (Director's Cut)* was split across two sibling folders, `I LAY DOWN MY LIFE FOR YOU (DIRECTOR's CUT)/` (12 tracks, mp3+opus) and `I LAY DOWN MY LIFE FOR YOU (DIRECTORS CUT)/` (4 unique tracks, mp3). `cleaner.py` did not merge them because its normalization folded curly to straight quotes but did not equate apostrophe-present vs apostrophe-absent: `director's cut` and `directors cut` hashed to different keys. **Fixed (v4.6.0):** `normalize_name` now strips apostrophes and collapses whitespace after the NFKC/dash/quote/case folding, so `X's` and `Xs` collapse to the same key. Confirmed on the real library (it also caught a Damian "Jr. Gong" Marley quote variant).
