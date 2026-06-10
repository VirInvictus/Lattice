# Lattice Patch Notes

## v4.8.1 (2026-06-10)

Bugfix release from a full code review; no new features.

- **Fix: the no-curses fallback menu dispatched the wrong modes.** The typed-input menu shown when curses is unavailable (or stdin is not a TTY) had a hand-maintained key map that drifted as modes were added: "6" was labelled "Test WAV files" but ran Extract cover art (which writes `cover.jpg` files if the dry-run prompt is declined), "7"–"9" were each shifted one mode group over, "10"–"13" were rejected as invalid, and the WAV/WMA tests, art-quality audit, bitrate audit, and ReplayGain audit were unreachable by number. The fallback listing and key map are now both generated from the same `_MAIN_SECTIONS`/`_LIB_SECTIONS` data the arrow-key menu renders, so they cannot drift again, and new word aliases (`wav`, `wma`, `quality`, `bitrate`, `rg`/`replaygain`) cover the previously unreachable modes. The curses menu itself was never affected. Pinned by the new `tests/test_tui.py`.
- **Fix: smart-playlist AND/OR no longer rewrites string literals.** The SQL-style convenience was a plain substring replace, so `genre == 'Drum AND Bass'` was silently rewritten to compare against `'Drum and Bass'` and could never match. The fold is now word-bounded and applied only outside quoted segments; as a side effect, `(rating >= 4)AND(...)` without padding spaces now also works.
- **Fix: art-quality audit no longer truncates folder covers.** Folder art was read 8 KB at a time "for the header", but a large EXIF/ICC block can push the JPEG size marker past any fixed prefix; such covers parsed as unreadable and were silently skipped instead of flagged. The whole file is read now (embedded art always was).
- **Fix: FLAC report header now includes the Metadata tier count**, matching the other integrity reports, so the per-tier counts always sum to Scanned (previously METADATA-tier files, mostly from the ffmpeg fallback path, were invisible in the report).
- **Hardening/cleanup:** `_parse_track_number` tolerates a malformed MP4 `trkn` atom containing `None` (previously an uncaught `TypeError` aborted the rest of that file's tag parse); the rule evaluator's comparison walk uses `zip(strict=True)`; an unreachable duplicated `return` in `run_replaygain_audit` was removed; `.gitignore` now covers the default report outputs so runs from the repo root stay out of `git status`.
- **Known limitation (deferred):** the curses prompt accepts ASCII-only typed input, so a non-ASCII path or output name cannot be typed into the TUI (the CLI and the prompt defaults are unaffected).

## genre_foldermap.py v1.2.0 (2026-06-01)

Companion-script fix and hardening (no package change):

- **Wrong-root guard.** `classify` collapsed any directory to its last two path components regardless of depth, and its docstring's promise to "flag" deeper directories was never implemented. Pointing the tool at the parent of an already-organized `Genre/Artist/Album` library (e.g. `/mnt/SharedData` instead of `/mnt/SharedData/Music`) therefore read every album one level too high, discarded the genre level, and planned to move and prune the entire tree. A directory deeper than `Genre/Artist/Album` is now flagged `TOO DEEP` and skipped. On a real ~1810-album library the parent-root dry-run went from 1820 planned moves plus 930 prunes to zero moves and a wall of `TOO DEEP` flags.
- **Placement gated by the library's existing genres.** The tool now learns the genre vocabulary from the folders that already hold a `Genre/Artist/Album` tree and only files a stray into one of those. A stray whose dominant tag genre isn't already in use is flagged `UNKNOWN GENRE` and skipped instead of spawning a new top-level folder from a typo or junk tag; `--allow-new-genre` lifts the gate. A flat library with no genre folders yet has an empty vocabulary, so the gate is off and the original `Artist/Album` → `Genre/Artist/Album` conversion is unchanged. The same gate is what makes a wrong-root run inert: nothing matches the (absent) vocabulary.
- **Organized albums are never silently re-filed.** An album already at `Genre/Artist/Album` whose folder genre disagrees with its tags is reported as a `NOTE` and left in place, rather than being moved across genre folders without warning.
- Tested by `tests/test_genre_foldermap.py` (depth classification, gating known/unknown/greenfield/`--allow-new-genre`, in-place skip and mismatch NOTE, the too-deep flag).

## replaygain.py v1.1.1 (2026-06-01)

Companion-script hardening (no package change):

- **Cross-platform robustness.** The rsgain subprocess output is now decoded with `errors="replace"`, so a stray undecodable byte (cp1252 on Windows, or a non-UTF-8 Linux locale) is logged rather than crashing the run. The "rsgain not found" message now points to the right installer per OS (`dnf`/package manager, `brew`, `winget`/scoop/choco) instead of Fedora only. The script was already portable Python (paths via `os.path`, no shell, UTF-8 logging); these close the two remaining rough edges. Documented under the script's new "Cross-platform" note in the README.

## v4.8.0 (2026-06-01)

- **Feature: ReplayGain audit (`--auditReplayGain`).** A new read-only mode reports per-album ReplayGain coverage, sorting every album into one of four buckets: MISSING (no track tagged), PARTIAL (some tracks tagged, some bare), NO ALBUM GAIN (every track has track gain but album gain is absent, so album-mode playback has nothing to apply), and OK (fully tagged, summarized and listed only with `--verbose`). It is format-aware: Opus stores gain as `R128_TRACK_GAIN`/`R128_ALBUM_GAIN` rather than the `replaygain_*_gain` text tags MP3 and FLAC use, and both are recognized, so an R128-tagged Opus album is not mis-flagged as untagged. Added to the CLI, the TUI (Metadata section), `spec.md`, and the test suite (format detection and bucket classification as pure-helper tests, plus an end-to-end mode test over a generated FLAC library). New `read_replaygain` reader in `tags.py` and a `DEFAULT_REPLAYGAIN_AUDIT_OUTPUT` constant.
- **Companion script: `replaygain.py` v1.1.0.** The writing counterpart to the audit. It wraps `rsgain` (libebur128, ReplayGain 2.0, the -18 LUFS / 89 dB reference foobar2000 uses) to scan and write track + album gain/peak tags album-by-album, the way foobar's "Scan selection as album" does. Album = one folder, rescanned as a whole so album gain is correct; `--skip-tagged` skips a fully-tagged album as a unit (never half-skips, which would corrupt album gain). `--dry-run` lists every album and its current coverage without invoking rsgain; a real run prints the worklist and asks for confirmation before writing (`--yes` to skip). After each album the written tags are read back and logged. `--target-lufs N` (v1.1.0) targets a louder or quieter result than the 89 dB / -18 LUFS standard (e.g. -14 ≈ 93 dB, the streaming-loudness range): it switches rsgain to custom mode and writes standard replaygain_* tags for every format including Opus (the R128 convention is fixed at -23 LUFS and cannot carry a custom target, so it is not used for that path); valid range -30 to -5, default stays the 89 dB standard. Lives in `scripts/` (outside the package's read-only contract, like the other companions) and imports `lattice` for the format-aware ReplayGain reader. Requires `rsgain` on PATH (not bundled). Tested by `tests/test_replaygain.py` (pure helpers; the rsgain call is mocked).
- **Cleanup**: `read_tags_concurrent` now delegates to a small general `map_concurrent` helper in `utils.py` (the ReplayGain audit reuses it); behavior is unchanged. `run_bitrate_audit` now returns `0` explicitly to match its `-> int` annotation and the sibling audits (the exit code was already 0).

## v4.7.1 (2026-05-30)

Hardening pass from a full code audit.

- **Security: smart-playlist rules no longer use `eval`.** `--playlist` rules were evaluated with `eval(rule, {"__builtins__": {}}, ...)`, which is not a sandbox: a rule like `genre.__class__.__mro__[-1].__subclasses__()` escapes it to arbitrary code. Rules are now evaluated by walking a restricted AST (comparisons, boolean and arithmetic operators, the exposed field names, literals); attribute access, calls, and subscripts are refused. Same rule syntax, no behavior change for valid rules.
- **Fix: `--stats` artist count on a genre-first tree.** The artist tally was derived from the top-level directory, so on a Genre/Artist/Album layout it counted genres, not artists. `run_stats` now takes the path `layout` (like the other modes) and reads the artist from the correct component. Album counts were already layout-independent.
- **Performance: read-heavy modes read tags concurrently.** New `read_tags_concurrent` helper (a small thread pool, since tag reads are I/O-bound) now backs duplicate detection, the tag and bitrate audits, and statistics, matching the integrity scanners' existing concurrency. Duplicate detection also drops a redundant whole-library tag cache, roughly halving its peak memory.
- **Fix: bitrate audit default output path** now uses `DEFAULT_BITRATE_AUDIT_OUTPUT` instead of string-rewriting the tag-audit constant.
- **`retag.py` (v1.1.0)** now writes genres to `.wma` (ASF `WM/Genre`); raw ADTS `.aac` stays unsupported (no tag container) and is documented as such.
- **`genre_foldermap.py` (v1.1.0)**: artist-level sidecar files (e.g. an `Artist/cover.jpg` beside album subfolders) now follow the artist to its dominant genre instead of being orphaned in an emptied folder; the dry-run now predicts directory pruning instead of reading the unchanged disk.
- **Cleanups**: `rerate.py` shares one read-only scan between preview and apply; lazier POPM rating fallback in `tags.py`; `cleaner.py`/`rerate.py` exception handlers collapsed to a single base class where one subsumed the other.

## v4.7.0 (2026-05-30)

- **Feature: configurable path-extraction layout.** The layout Lattice uses to recover artist/album/genre from a file's path (when a tag is missing) is now settable. A new `layout` key in `~/.config/lattice/config.json` becomes the default for every scanning mode, and `--layout` overrides it per-run. A genre-first library can pin `"{genre}/{artist}/{album}"` once instead of passing the flag each time. The default remains `{artist}/{album}`, so existing setups are unaffected. New `config.DEFAULT_LAYOUT` constant and `config.get_layout()` helper back this.
- **Feature: genre falls back to the path.** The library scanner already recovered a missing artist/album from the directory layout; it now does the same for a missing genre (`t.genre or parsed.get("genre")`). With a `{genre}/...` layout, an untagged file is still placed in the right wing.
- **Companion script: `genre_foldermap.py` v1.0.0.** A new destructive tool in `scripts/` that restructures a flat `Artist/Album` library into `Genre/Artist/Album`, moving each album folder under its dominant genre (read through Lattice's scanner). Dry-run by default, `--apply` to perform, an append-only manifest with `--revert`, and `--only-genre` for a staged rollout. Loose single tracks are wrapped in a `Singles/` folder, and artist-level cover art follows the artist to its genre rather than being orphaned. `mv`-only on one filesystem, so audio bytes and embedded ratings are never rewritten.

## v4.6.1 (2026-05-29)

- **Fixes**: Fixed a flaky `test_plain_when_not_a_tty` test that failed when the suite was run under a pseudoterminal.
- **Typing**: Added a missing `type: ignore` for `tqdm` in `utils.py` and correctly scoped the ignore for `OggOpus` in `tags.py` to ensure `mypy` runs completely clean.

## Companion script: `genre_tidy.py` v1.2.0 (2026-05-27)

*(Companion-script change; no package version bump.)*

---

- **Compilations are excluded from the genre authority.** An album whose album-artist is `Various Artists` (or `VA`/`Various`) has no single canonical genre, so enforcing one would wrongly flatten the disc. `build` now writes such artists as a flagged `# ... EXCLUDED (compilation)` comment instead of an enforceable row, and `apply` hard-skips them even if a stale row exists. The shipped `artist_genre_defaults.tsv` had its `Various Artists` row converted to the exclusion comment. (Lattice's tag layer already prefers the album-artist, so this keys off album-artist.)

## Companion script: `rerate.py` (2026-05-27)

*(Companion-script addition; no package version bump.)*

---

- **New `scripts/rerate.py`: reconcile DeaDBeeF and foobar2000 MP3 ratings.** The two players store MP3 ratings in an ID3 POPM byte on different scales, so a rating set in DeaDBeeF reads one star too high in foobar: 2★ (byte 127) shows as 3★, 4★ (byte 254) shows as 5★. `rerate.py` rewrites those bytes to the foobar values that *both* players read identically (`127→64`, `254→196`), so DeaDBeeF ratings sift correctly in foobar without changing DeaDBeeF's own display (byte 196 reads 4★ in both, verified on real files). MP3/POPM-only; foobar's own bytes, MusicBee's `186/242`, unrated, and non-MP3 files are left alone, and Vorbis/Opus ratings already agree. `--dry-run`, append-only `rerate.log` recording every change (so a run is auditable and reversible), idempotent. Covered by `tests/test_rerate.py`.

## Companion script: `cleaner.py` v1.1.1 (2026-05-27)

*(Companion-script change; no package version bump. Refinements learned from a real-library run.)*

---

- **Accurate `--dry-run`.** A dry-run now tracks virtual removals, so its `RMDIR`/retain lines and summary counts match the real run instead of misreporting emptied folders as retained.
- **Survivor names are normalized.** The folder with the most files still wins as canonical, but its name is now rewritten to a normalized form afterward (so merging a unicode-hyphen `Drive‐By Truckers` no longer leaves the non-standard name as the survivor). Uses a new narrow `canonical_render` fold deliberately kept **filesystem-safe** for NTFS/exFAT targets shared with Windows: only broken hyphens (U+2010/11/12/15) and curly single-quotes/apostrophes go to ASCII. En/em dashes (correct in ranges like `85–92`), curly double quotes (straight `"` is forbidden on Windows), the ellipsis glyph (`...` would end a name in dots, which NTFS rejects), and prime marks are all preserved. Distinct from `normalize_name`, which stays aggressive for duplicate matching.
- **Rename safety.** A rename whose target would be illegal on Windows/NTFS/exFAT (forbidden `<>:"/\|?*`, or a trailing `.`/space) is skipped and logged; any `OSError` during a rename is caught and logged so a single bad name can never abort the run mid-way.
- **Higher-resolution cover wins.** On a cover-image collision (`.jpg`/`.png`), the higher-resolution file is kept rather than blindly keeping the canonical folder's copy (ties or unparseable images fall back to larger bytes). Dimensions are read with a stdlib JPEG/PNG parser ported from the package. Other non-audio collisions are unchanged.
- **`--normalize-names` (opt-in).** A third pass renames lone, non-duplicate folders whose names carry non-standard characters (e.g. `At the Drive‐In` → `At the Drive-In`) to the same normalized form. Off by default; preview with `--dry-run`. Covered by expanded `tests/test_cleaner.py`.

## Companion script: `genre_tidy.py` (2026-05-27)

*(Companion-script addition; no package version bump.)*

---

- **New `scripts/genre_tidy.py`: artist→genre authority + reconciler.** A two-phase companion for libraries whose genre tags have drifted. `build` scans (read-only, through lattice) and writes an editable tab-separated `genre_map.tsv`: one `Artist<TAB>Genre<TAB>Genre2<TAB>...` line per artist listing every genre that artist currently uses (most-common first), with `#` comments giving per-genre counts for multi-genre artists. Every listed genre is allowed, so a fresh map makes `apply` a no-op; you tidy by removing a stray genre from a line, and `apply` then calls `retag.py` to collapse that artist's albums tagged with the removed genre to the first (canonical) genre. Reorder to retarget, blank a line to skip an artist. `--dry-run` and append-only logging like the other companions, idempotent. The lattice package stays read-only; every write goes through `retag.py`. Keys on the normalized artist tag, not folder name. A real ~877-artist map ships at `artist_genre_defaults.tsv` as a worked example and maintained authority (re-run `build` to append new artists; hand-edit to accept new genres). Covered by `tests/test_genre_tidy.py` (21 cases); the same work backfilled `tests/test_retag.py` and `tests/test_cleaner.py`, so all three `scripts/` companions now have tests.

## v4.6.0 (2026-05-27)

---

### Multi-root scanning

- **`--root` is now repeatable.** `lattice --duplicates --root /mnt/A --root /mnt/B` walks both libraries in one pass; a path passed twice is de-duped. Every mode (trees, wings, stats, audits, art, playlists) aggregates across the roots.
- **Cross-library duplicate detection.** With more than one root, an album that lives in two libraries is grouped as a single exact duplicate. Each entry is prefixed by its root's basename (`Music/...` vs `Rin's Music/...`) so the two copies are distinguishable; single-root reports are unchanged.
- **Optional config array.** A `library_roots` array in `~/.config/lattice/config.json` supplies default roots when no `--root` is given. The first-run prompt still saves only the single `library_root`, so a throwaway `--root` is never persisted.

### Color output

- **Colorized status summaries.** Integrity summaries print a green all-clear, yellow suspect counts, and red corrupt counts. Color is gated on an interactive terminal: off inside the TUI, off when piped or redirected, off under `NO_COLOR`, so report files and pipes stay byte-clean.

### Companion scripts

- **`retag.py`: stale genres fully cleared (the deadbeef trap).** A genre can hide in more than the standard ID3 `TCON` frame: an APEv2 tag, the ID3v1 genre byte, and a bare custom `TXXX:GENRE` frame. The old `EasyID3` path left those overrides in place, so players like deadbeef kept showing the old value. The MP3 path now clears all of them and writes one clean `TCON` (v2.3, refreshed ID3v1), while deliberately preserving qualified `TXXX` frames (AcousticBrainz `AB:*`, `ALBUMGENRE`, MusicBrainz).
- **`cleaner.py`: apostrophe-fold fix.** `normalize_name` now strips apostrophes and collapses whitespace after the existing NFKC/dash/quote/case folding, so `Director's Cut` and `Directors Cut` consolidate. (Closes the 2026-05-25 found bug.)

## v4.5.0 (2026-05-26)

---

### TUI
- **Richer report pager.** The in-TUI viewer every report passes through now supports PageUp/PageDown, Home/End, and vim g/G on top of line scrolling, uses the full terminal height, and widens to the longest line (up to the terminal width) instead of a fixed 80 columns so long paths are no longer truncated. Folded in from the CalibreQuarry TUI.

### Internal
- **Python 3.14 modernization.** With the floor at 3.14, legacy `typing` generics were dropped for builtin generics and PEP 604 unions (`Dict`/`List`/`Tuple[...]` to `dict`/`list`/`tuple[...]`, `Optional[X]` to `X | None`) across the package and scripts; the redundant `from __future__ import annotations` was removed from `cleaner.py` and one `subprocess.run` simplified to `capture_output=True`.
- **Expanded test suite.** Added a committed fixture library (`tests/fixtures/`) and integration tests (`tests/test_modes.py`) that run the report modes end to end, plus decode-classifier tests. 78 stdlib `unittest` tests; run with `python -m unittest discover`.

### Repository
- **Companion scripts moved to `scripts/`.** Invoke `cleaner.py` and `retag.py` as `./scripts/cleaner.py` and `./scripts/retag.py`. They remain outside the `lattice` package (spec §5).
- **Documentation pass.** Recast em-dashes out of the prose, trimmed marketing language, fixed an unclosed README code fence, and condensed the per-mode sections.

## v4.4.2 (2026-05-26)

---

### Integrity scanning: fewer false alarms, severity tiers

Running every mode against a real 8,400-file library exposed the integrity
scanners crying wolf: they treated any line ffmpeg wrote to stderr as a failure,
producing roughly 187 flags where essentially no file had damaged audio. This
release reworks them.

- **Forced demuxer.** `--testMP3`, `--testOpus`, `--testWAV`, `--testWMA`, and the
  FLAC ffmpeg fallback now force the demuxer from the file extension. ffmpeg's
  format autodetection had been mis-probing valid MP3s with large ID3v2 tags as
  RIFF and reporting bogus failures; forcing `-f mp3` (and so on) eliminates the
  whole class. On the test library this turned dozens of false positives into
  zero.
- **Cover art ignored.** `-vn` drops non-audio streams, so a malformed embedded
  image is no longer decoded and counted as an audio fault.
- **Severity tiers.** Each file is classified CORRUPT / SUSPECT / METADATA / OK
  instead of pass/fail. CORRUPT means the decoder could not get through the file,
  or a FLAC lost sync before its declared sample count (true truncation). SUSPECT
  means it decoded to the end but the tool complained (these usually play).
  METADATA means only tag-parse warnings; the audio is fine. CORRUPT and SUSPECT
  are always listed; METADATA and OK are summarized and listed only with
  `--verbose`.
- **Exit code change.** Integrity modes now exit `1` only when a file is CORRUPT,
  not whenever the decoder printed anything. Scripts relying on the old "exit 1
  means any complaint" behavior should read the tier counts in the report
  instead.
- **FLAC reporting.** A failed verification now shows the preferred tool's
  message (libFLAC's "decoded N of M samples" rather than ffmpeg's terse "invalid
  sync code"), and the mode warns when `flac` is absent and the stricter ffmpeg
  fallback is used. The FLAC report is now always written, not only on failure.

### Tests
- Added `tests/test_integrity.py` covering the decode classifier across all four
  tiers using real ffmpeg and libFLAC stderr signatures.

## v4.4.1 (2026-05-26)

---

### Bug Fixes
- **Latent `Tuple` import in the art-quality audit:** `modes/artwork.py` annotated `_get_image_size` with `Tuple` without importing it. The code ran only because Python 3.14 defers annotation evaluation; `typing.get_type_hints`, a type checker, or any pre-3.14 interpreter would have raised `NameError`. The import is now present.

### Internal
- **Test suite added.** A stdlib `unittest` suite under `tests/` (no third-party dependencies) covers the pure helpers: rating and key normalization, duration clustering, JPEG/PNG header parsing, filename cleanup, and track-number parsing. Run it from the repo root with `python -m unittest discover`.
- **Library and stats refactor.** The three duplicated walk-and-aggregate loops and two identical tree-writers in `modes/library.py` were unified behind `_scan_album_dirs`, `_song_display_name`, and `_write_tree`; the file shrank by roughly a third with byte-identical output. The repeated rating-bucketing block in `modes/stats.py` was replaced with a single label helper. Output across every mode was verified unchanged against a captured baseline.
- **Formatting and lint.** The package was run through `ruff format`, unused imports were removed, and the misplaced mid-file imports in `tui.py` were moved to the top.

### Documentation
- `spec.md` now lists all shipped modes; `--testWAV`, `--testWMA`, `--auditArtQuality`, `--auditBitrate`, and `--playlist` were missing from its table. A stale `python Lattice.py` example in the README was corrected, and the test suite is now documented.

## v4.4.0 (2026-05-14)

---

### New Features
- **Expanded `--duplicates`:** The duplicate detection mode was rewritten to emit a four-section report instead of a single album-level list. Section 1 (exact album duplicates) continues to flag the same artist/album pair across multiple directories, but now aggregates the most-common artist and album across every track in the folder rather than sampling the first audio file; per-location lines include the format breakdown, average bitrate, and total size to support keep/discard decisions. Section 2 (within-directory multi-format) reports folders that hold the same track in two or more formats (e.g., `01 - Track.flac` next to `01 - Track.mp3`), listed track-by-track so partial overlaps stay visible. Section 3 (similar-name candidates) flags within-artist album pairs whose names match at a `difflib` ratio of 0.85 or higher after stripping trailing parentheticals (`(Deluxe Edition)`, `(Remastered)`) and `feat.` clauses; this catches cases like `Domestica` vs `Domestica (Deluxe Edition)` that exact matching misses. Section 4 (track-level duplicates) reports the same artist + title appearing in two or more directories, partitioned into duration-clusters within a 2-second window so a studio cluster and a live cluster for the same song surface as separate rows instead of being lumped together (or one of them silently dropped).
- **Quote / dash normalization in matching:** Album and artist keys now apply NFKC normalization plus the same curly-quote and dash-variant fold table that `cleaner.py` uses (`'` → `'`, `‐` / `–` / `—` → `-`, etc.). `JAY‐Z` and `Jay-Z` collapse to the same key, so the two are reported together instead of slipping past as separate albums.

### Requirements
- **Minimum Python is now 3.14.** Lattice was previously declared `>=3.9`. The bump is for runtime quality, not language sugar: end users get faster CLI cold starts (cumulative ~25% startup improvement since 3.11, with continued specializing-interpreter gains through 3.14), fine-grained tracebacks (PEP 657) so tag-read or subprocess failures point at the exact column rather than just the line, and faster general bytecode performance from the 3.11+ specializing interpreter. No 3.14-specific language features (template strings, free-threading, tail-call interpreter, etc.) were adopted because they either require a non-default build or have no use case in Lattice's read-walk-report workload.

### Bug Fixes
- **`run_duplicates` first-file bias:** Reading album/artist tags from only the first audio file in a directory would mis-key entire folders when track 1 had bad tags, was a hidden track, or the album was a compilation with per-track artists. The new aggregation reads tags from every file and takes the mode across the directory.
- **Empty-album false positives:** The exact-duplicate section accepted directories with an empty album key (e.g., singles or weirdly tagged folders), grouping every album-less folder for an artist together as "duplicates." Both `norm_artist` and `norm_album` are now required to be non-empty for inclusion in the exact group.
- **Multi-format display title lowercasing:** When tracks lacked title tags, the within-folder multi-format section fell back to the normalized lookup key for display, which had been lowercased. Filename stems are now carried alongside the key and used as the display fallback, so case is preserved.
- **Track-level cluster dropping:** Track-level dupe detection previously returned only the single largest duration cluster per `(artist, title)` key, silently discarding a second valid cluster when the same title legitimately existed as both a studio version (in two albums) and a live version (in two more). Replaced with a partitioning helper that returns every cluster with 2+ entries spanning 2+ directories.
- **Removed dead code:** `_DirInfo.track_count` was computed but never read; removed. `_fmt_size` had an unreachable final `return` after a loop that always returned in its last iteration; loop refactored to only iterate over B/KB/MB with GB as the natural fallthrough. `argparse.BooleanOptionalAction` fallback in `cli.py` predates 3.9 and was dead even at the prior 3.9 floor; removed.

---

## Repo addition (2026-05-04)

---

**Companion Script: `cleaner.py`.** Added a standalone consolidator for fragmented album folders to the repository. It detects sibling directories whose names differ only in quote rendering (`'` vs `'`), dash/hyphen variant, case, or whitespace (the typical artifact of inconsistent metadata across import sources), and merges them via filesystem `mv` only. Audio collisions where sizes differ keep both copies (source renamed with a `.from-fragment` suffix), never overwriting user audio. Includes a `--dry-run` preview mode and per-file logging to `<directory>/cleanup.log`. Intentionally narrow scope: it does not rewrite tags or re-encode audio. Lives outside the `lattice` package alongside `retag.py`, preserving the package's read-only contract.

---

## v4.3.4 (2026-04-14)

---

### Bug Fixes
- **TUI Artwork Submenu:** Restored missing "Audit art quality" option to the interactive TUI menu.
- **TUI Integrity Submenu:** Added missing "Test WAV files" and "Test WMA files" options to the interactive TUI menu.
- **TUI Metadata Submenu:** Added missing "Audit bitrates" option to the interactive TUI menu.
- **WAV/WMA Integrity Modes:** Fixed a crash caused by missing `DEFAULT_WAV_OUTPUT` and `DEFAULT_WMA_OUTPUT` imports during `--testWAV` and `--testWMA` runs.
- **Genre Split Formatting:** Refined genre splitting logic in `write_all_wings` to correctly extract multiple genres without splitting literal paths or bracketed tags when saving library files.

---

## v4.3.3 (2026-04-14)

---

### New Features
- **Dual-Genre Wing Splitting:** The `--ai-wings` and `--all-wings` modes now intelligently split dual-tagged items (e.g., `Coke Rap/Midwest Rap`). Instead of creating a single, combined `.txt` file for the multi-genre string, Lattice now separates the genres and correctly filters the album into *both* respective genre text files, ensuring accurate categorization across the library.

---

## v4.3.2 (2026-04-14)

---

### Bug Fixes
- **Retag Tool Overhaul:** Fixed an issue where `retag.py` was duplicating genre tags instead of replacing them. The tool now safely clears APEv2 tags from MP3s, correctly pops all existing standard/custom genre keys across formats, and explicitly forces ID3v1 synchronization to prevent ghost tags in older media players.
- **Null Byte Sanitization:** Fixed an issue in all library generation modes (`--library`, `--ai-library`, `--ai-wings`, `--all-wings`) where non-legible null bytes (`\x00`) from ID3v2.4 multi-value frames were being printed into the text output. Multiple values (e.g., dual genres) are now properly joined with a slash (`/`).
- **Wing File Names:** Improved file name generation for `--ai-wings` and `--all-wings` to preserve word boundaries when encountering slashes or special characters (e.g., `Coke_Rap_Midwest_Rap_AI.txt` instead of `Coke_RapMidwest_Rap_AI.txt`).

---

## v4.3.1 (2026-04-13)

---

### Bug Fixes
- **Album Overcounting Fix:** Resolved an issue where tracks in "Various Artists" or soundtrack directories were being counted as separate albums. All library generation modes (`--library`, `--ai-library`, `--all-wings`, `--ai-wings`) now correctly group tracks by their containing directory.
- **Improved Metadata Consolidation:** For each directory, the toolkit now automatically determines the most frequent artist, album title, and genre to use for headers, ensuring accurate representation even when track-level tags vary.

---

## v4.3.0 (2026-04-13)

---

### New Features
- **AI Wings:** Added `--ai-wings` to generate separate, token-efficient library files for each genre. These files hide individual songs and only include Artist, Album, Genre, and Directory Location, making them ideal for large-scale LLM processing or quick library overviews.
- **TUI Submenu Expansion:** The Library Tree & Exports submenu now includes both "Generate AI wings" and the previously omitted "Generate smart playlist" options.

---

## v4.2.2 (2026-04-13)

---

### Bug Fixes
- **TUI Persistence Fix:** Fixed an issue where the TUI would exit or "blink" back to the menu when running background tasks (like Stats). This was caused by the progress bar calling `curses.endwin()`, which terminated the curses session prematurely.
- **Improved Progress Bar:** The TUI progress bar now correctly updates within the existing curses session without corrupting the terminal state.

---

## v4.2.1 (2026-04-13)

---

### Bug Fixes
- **Stats Page Fix:** Fixed a `NameError` in the statistics module where `genre_ratings` was not properly initialized.
- **Missing Report:** Properly implemented the "Rating Distribution per Genre" report section in `--stats` which was previously omitted.
- **Version Synchronization:** Corrected version mismatches across the repository.

---

## v4.2.0 (2026-04-13)

---

### Major Overhaul: Configurable Layout & Smart Playlists

Lattice now supports dynamic directory structures via the `--layout` flag, completely decoupling library generation from the strict `ARTIST/ALBUM` assumption. You can now generate `.m3u` playlists using rule-based filters.

### New Features & Improvements
- **Configurable Layout:** A new `--layout` argument specifies your directory structure (e.g. `{genre}/{artist}/{album}`). `write_music_library_tree`, `write_ai_library`, and `write_all_wings` now intelligently parse paths according to this structure if tags are missing. They no longer fail or produce garbage output on flat folders.
- **Smart Playlists:** Generate `.m3u` playlists based on dynamic evaluation rules using `--playlist` and `--rule` (e.g. `"rating >= 4 and genre == 'Jazz'"`).
- **WAV & WMA Support:** Extended the unified FFmpeg decode scanner to verify WAV (`--testWAV`) and WMA (`--testWMA`) files.
- **Art Quality Audit:** Added `--auditArtQuality` (with configurable `--min-art-res`) to parse and report extracted or embedded covers falling below a minimum resolution threshold (default: 500x500).
- **Bitrate Floor Audit:** Added `--auditBitrate` (with configurable `--min-bitrate`) to report audio files falling below a designated kbps floor (default: 192).
- **Rating Distribution per Genre:** The library statistics page (`--stats`) now cross-tabulates rating distributions (e.g., 5-star vs 1-star spread) independently per genre.

### Bug Fixes
- **TUI Close Button Fix:** Fixed an indexing error in the interactive menu where selecting "Quit" would accidentally trigger the "Change library root" prompt due to a missing settings group in the main `_MAIN_SECTIONS` list.

---

## v4.1.3 (2026-04-12)

---

### Bug Fixes
- Fixed an issue in the TUI main menu where selecting "Quit" (or pressing 'q') would unintentionally trigger the "Change library root" prompt due to a mismatched menu array index.

---

## v4.1.2 (2026-04-12)

---

### Bug Fixes & Improvements
- **Fully Immersive TUI:** Addressed an issue where background operations (such as cover art extraction or tree generation) would write their output directly to the terminal stdout and pause, which dropped the user out of the full-screen curses environment.
  - The TUI now features a global output capture wrapper (`_run_with_capture`) using an `io.StringIO` buffer.
  - Standard output and error output are automatically intercepted while a background task executes, allowing progress bars to draw undisturbed.
  - Upon task completion, any logged output (e.g., dry-run details, success messages, errors) is formatted and displayed within the `_tui_page` viewer, ensuring the user never leaves the curses application.

---

## v4.1.1 (2026-04-12)

---

### Bug Fixes
- Fixed a rendering bug where `_TUIPbar` did not erase the screen on its first draw, causing overlapping text from previous prompts in the curses interface.
- Fixed a crash (`ValueError: embedded null character`) when scrolling through the library statistics TUI page by sanitizing null bytes from the output report.

---

## v4.1.0 (2026-04-12)

---

### New Features & Improvements
- **First-Run Configuration:** Added a persistent configuration file stored at `~/.config/lattice/config.json`.
  - The CLI and TUI now save the root music library location upon first run, eliminating the need to repeatedly specify `--root` or manually enter the path in the interactive menu.
  - A new "Change library root" option has been added under the `SETTINGS` section in the TUI main menu.
  - If no `--root` is provided, the CLI gracefully falls back to the configured location (or prompts if unconfigured).
- **TUI Immersion Enhancements:**
  - Progress bars now render inside a stylized curses box when running from the TUI, preventing screen tearing and keeping the interface consistent.
  - The library statistics page now displays its full report in an integrated, scrollable curses pager (`_tui_page`), rather than dropping you back into standard terminal output.

---

## v4.0.2 (2026-04-12)

---

### Bug Fixes & Improvements
- **PyInstaller Multiprocessing Fix:** Fixed an issue where the standalone binary would crash (`unrecognized arguments: -B -S -I -c`) on Python 3.14 due to the `multiprocessing.resource_tracker` trying to spawn a new process using the executable as the Python interpreter. The executable now properly intercepts `-c` command strings from the tracker.
- **Positional Root Argument:** The CLI now supports providing the root directory as an optional positional argument. You can run commands like `lattice --library .` instead of explicitly using `--root .`.

---

## v4.0.0 (2026-04-11)

---

### Major Overhaul: Package Restructure & Standalone Binary

Lattice has been completely refactored from a single ~2500-line monolithic script (`Lattice.py`) into a proper, modern Python package architecture.

**Layer-Based Package Design.** The codebase is now housed in `src/lattice/` and split by logical functionality (`cli.py`, `tui.py`, `tags.py`, `utils.py`, `config.py`, and a `modes/` directory for individual feature operations). This dramatically improves maintainability while preserving the exact same functionality and CLI interface.

**Modern Build System (Hatch).** Lattice now uses `pyproject.toml` managed by Hatch, replacing the need for manual `pip install mutagen tqdm` commands. You can now cleanly install Lattice via `pipx install .` and have the `lattice` command available globally in your terminal.

**Standalone Native Executable.** We have integrated **PyInstaller** support to compile Lattice into a self-contained standalone binary. This means end-users no longer need to install Python or external packages (like `mutagen`) on their machines. The compiled binary (`lattice`) can be dropped into any directory in your PATH.

---

## v3.1.0 (2026-04-09)

---

### Enhancements

**Absolute Paths for Genre Wings.** The `--all-wings` mode now accepts a `--paths` flag. When enabled, the absolute directory path is appended to the album header in the generated text files (e.g., `ALBUM: Jane Doe [/path/to/Music/Converge/Jane Doe]`). 
- This bridges the gap between visualization and execution. It eliminates the need to write brittle shell scripts that guess file locations by scraping artist and album strings. You can now pipe the generated wing files directly into command-line tagging utilities.
- The interactive TUI's Library submenu has been updated to prompt for path inclusion (`Include paths? (y/N)`) when generating genre wings.
**Companion Script: `retag.py`.** Added a standalone universal genre tagger to the repository. It abstracts away container-specific tagging differences (ID3, Vorbis, Apple atoms) and is designed to cleanly consume the absolute paths generated by the `--all-wings --paths` flag. This allows for safe, bulk-overwriting of genres at the album-directory level.

## v3.0.1 (2026-04-08)

---

### Bug Fixes

**Album Artist Prioritization.** Fixed an issue where albums were being split up due to featured artists on individual tracks. The tag extractor now consistently prioritizes "Album Artist" over "Artist" across all supported formats:
- MP3: `TPE2` > `TPE1`
- FLAC/Ogg/Opus: `albumartist` > `artist`
- MP4/M4A: `aART` > `\xa9ART`
- ASF: `wm/albumartist` > `author`

---

## v3.0.0 (2026-04-06)

---

### Consistent Full-Screen TUI

The entire interactive experience now stays in curses. Previously, selecting a
menu item dropped to raw `input()` calls for parameter prompts (root directory,
output file, worker count, etc.) and the post-operation pause, breaking the
visual flow. All prompts and the pause screen now render inside the same styled
Unicode boxes as the menus.

**Curses prompts.** `_tui_prompt_str` draws a centered box with a yellow header
label and a cursor-visible input field. Typing, backspace, Enter to confirm,
Esc to accept the default, all within the curses session. Since `_prompt_path`
and `_prompt_int` call `_prompt_str` internally, every parameter prompt in the
interactive menu gets the TUI treatment automatically.

**Curses pause.** `_tui_pause` replaces the raw `input("Press Enter…")` with a
styled box. Accepts Enter, q, or Esc to dismiss.

**Fallback preserved.** If curses is unavailable or stdin is not a TTY,
prompts and pause fall back to plain `input()`, same as before.

All CLI flags (`--library`, `--ai-library`, `--all-wings`, etc.) are unchanged.

---

## v2.4.0 (2026-04-06)

---

### TUI Overhaul: Arrow-Key Navigation

The interactive menu is now a full-screen curses TUI with arrow-key navigation,
color-coded sections, and a highlighted selection cursor (`►`). No more typing
numbers; just `↑`/`↓` to move, `Enter` to select, `q` or `Esc` to quit.

The menu is drawn as a centered Unicode box with labeled section groups:
**Library** (yellow), **Integrity**, **Artwork**, and **Metadata**, separated
by ruled dividers. The selected item is highlighted in bold cyan reverse video.
A hint bar at the bottom shows available controls.

**Library submenu.** AI-readable library export and genre wings (all-wings)
are now nested under a "Library tree & exports" submenu (marked with `→`)
alongside the standard library tree builder. Selecting it opens a second
curses menu; `Esc` returns to the main menu. This trims the top-level menu
from 11 flat items to 10 navigable entries and groups the three library-output
modes where they logically belong.

**Curses colors:**
- Cyan box frame
- Bold yellow section headers
- Bold cyan-on-black highlight for selected item
- Dim hint bar

**Fallback path.** If `curses` is unavailable (e.g. `windows-curses` not
installed) or stdin is not a TTY, the menu falls back to a static boxed
text display with numbered options and typed input, the same layout, just without
arrow-key navigation.

**Post-operation pause.** Every mode now waits for Enter before redrawing
the menu, so results aren't immediately scrolled off screen.

**Indented prompts.** All interactive prompts are visually aligned with the
menu box for a tighter feel.

All CLI flags (`--library`, `--ai-library`, `--all-wings`, etc.) are unchanged.

---

## v2.3.0 (2026-04-05)

---

### New Feature: Genre Wings

**`--all-wings`** scans genre tags across the entire library, groups albums by
genre, and writes a separate library tree file for each genre into an output
directory, analogous to virtual library wings in Calibre's getBooks.

```bash
lattice --all-wings --root ~/Music --output wings/
```

Produces files like `Alternative_Rock_Library.txt`, `East_Coast_Rap_Library.txt`,
etc. Albums with no genre tag land in `Uncategorized_Library.txt`. Each file
uses the same tree format as `--library`. Pass `--genres` to include the genre
label in album headers. Available from both CLI and interactive menu (option 11).

### AI Library: Removed Album Artist Fallback

The `--ai-library` export no longer overrides the directory-based artist name
with tag data. Previously, the artist field fell back through TPE1 → TPE2
(ALBUMARTIST) from tags, which added noise without value; the AI export
doesn't distinguish album artist from track artist, and the directory name is
the canonical artist in a well-organized library. This keeps the output cleaner
and more predictable.

---

## v2.2.0 (2026-04-05)

---

### New Feature: AI-Readable Library Export

**`--ai-library`** generates a flat, token-efficient summary of the music
library for use in LLM recommendation prompts. One line per album in
pipe-delimited format:

```
Artist | Album | Genre | Rating | Tracks
--------------------------------------------------
Converge | Jane Doe | Metalcore | 4.8 | 12
```

- **Rating** is the average of all rated tracks in the album, rounded to one
  decimal. Blank if no tracks are rated.
- **Tracks** is the number of audio files surviving in the album directory:
  the post-cull headcount. An AI reading `5.0 | 1` vs `4.6 | 12` gets the
  density signal without extra framing.
- Genre is sampled from the first track with a genre tag.
- Output defaults to `library_ai.txt`. Available from both CLI and interactive
  menu (option 10).

### Performance

**`get_all_tags` reduced to a single `MutagenFile` open per file.** The v2.1.0
unified reader still opened each file twice, once via the EasyID3 abstraction
pass, once via the full format-specific path (because rating, duration, and
bitrate aren't available through the easy interface). The easy pass is now
eliminated entirely; all tag extraction runs against the single full object.
The MP3 branch also had a separate `ID3(file_path)` call on top of the
`MutagenFile` open; that call is now removed, and tags are read from `audio.tags` directly.

On a 6,300-track library, this eliminates ~12,600 redundant file opens per
full-library mode.

**`TagBundle` extended with `duration_s` and `bitrate_kbps`.** These fields are
extracted from `audio.info` during the same single open. `run_stats` previously
opened every file a second time just to read duration and bitrate; that
redundant open is gone.

**First-song double-read eliminated in `--library --genres`.** Genre was read
from the first song before the per-track loop, then the loop re-read the same
file. The album header is now deferred until the first track's tags are available
inside the loop.

**`count_audio_files` was called twice in `--ai-library`.** Once for the console
message, once for the progress bar. Now called once, result reused.

**`_has_embedded_art` duplicated `_extract_best_art`'s directory scan logic.**
Collapsed to a one-liner: `return _extract_best_art(directory) is not None`.

**Low-quality bitrate count in `--stats`** used a list comprehension just to
call `len()`. Replaced with a generator sum.

### Bug Fixes

**`--root ~/Music` didn't work from the CLI.** `main()` was missing
`os.path.expanduser()`; tilde expansion only worked in the interactive menu.

**`--library --output subdir/file.txt` crashed.** `write_music_library_tree`
opened the output file directly without creating parent directories, unlike
every other mode. Added `os.makedirs`.

**`_find_files_by_ext_path` matched false extensions.** Used
`filename.endswith('.flac')` which would match a hypothetical file named
`notflac`. Replaced with `os.path.splitext` for exact extension matching.

**Rating bucketing in `--stats` used Python's `round()` (banker's rounding).**
A 4.5 rating rounded to 4, but so did 3.5. Replaced with `int()` (truncate)
for consistent behavior matching the star display logic in `format_rating`.

### Structural Improvements

**Unified MP3/Opus decode scanner.** `_scan_one_mp3`, `_scan_one_opus`,
`run_mp3_mode`, `run_opus_mode`, and `_format_mp3_meta` collapsed into three
shared functions: `_scan_one_file`, `_run_decode_scan`, and `_format_row_meta`.
Format-specific behavior is parameterized via `ext`, `enrich`, and
`ffmpeg_required` flags. Adding a new format is now a three-line wrapper.

**`_FallbackProgress` class replaces all `if pbar:` conditionals.** `_make_pbar`
now always returns an object with `.update()` and `.close()`, whether tqdm is
installed or not. Eliminated 6 conditional blocks and 4 dead counter variables
(`current_file`, `checked`, `scanned_count`, `current`) that existed only to
feed the manual fallback.

**`is_audio()` helper.** Replaced 6 inline
`os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS` patterns. Two callsites
where `ext` was already extracted for other purposes were left as-is.

**`_looks_numeric()` helper.** Replaced 4 inline
`str(val).replace('.', '').isdigit()` patterns in rating extraction code.

**`_prompt_path()` helper.** Consolidated 8 identical
`os.path.abspath(os.path.expanduser(_prompt_str(...)))` patterns in the
interactive menu.

**`test_flac` simplified.** Two mirrored if/elif branches (one per tool
preference) collapsed into a priority-ordered tool list with a single loop.

### Dead Code Removed

**Four standalone tag functions removed (~190 lines).** `get_title_artist_track`,
`get_album`, `get_genre`, `get_rating`, all superseded by `get_all_tags` in
v2.1.0 but left in the codebase. No internal callers remained.

**`_get_cover_file_path`**: defined but never called by any mode.

**`_scan_one_mp3`, `_scan_one_opus`, `_format_mp3_meta`**: replaced by the
unified scanner.

**`ID3` and `ID3NoHeaderError` imports**: no longer needed after the MP3
branch was rewritten to use `audio.tags` from `MutagenFile`.

**Stale `(NEW)` markers** removed from section headers.

---

## v2.1.0 (2026-04-04)

---

### Bug Fixes

**Opus mode wrote to `mp3_scan_results.csv`.** Copy-paste bug in `_write_header`
hardcoded `DEFAULT_MP3_OUTPUT` as the fallback filename for all modes. Opus scans
silently wrote results to the wrong file.

**`_extract_art_from_mp3` called `MUTAGEN_MP3` without checking
`HAVE_MUTAGEN_MP3`.** If `mutagen` installed but `mutagen.mp3` failed to import,
art extraction threw `NameError`.

**`verbose` flag mutated `only_errors` and `quiet` inside the MP3 scan loop.**
Dead writes on every iteration after the first. Moved above the loop. Opus mode
now has the same `verbose` behavior for consistency.

**Terminal corruption after subprocess modes.** Running FLAC/MP3/Opus integrity
checks from the interactive menu left the terminal with `icrnl` disabled; Enter
sent `^M` instead of newline, and input froze. Caused by `run_proc` using raw
bytes mode while `flac -t` wrote binary diagnostic data to stderr, colliding
with tqdm's cursor manipulation. Fixed with `_reset_terminal()` (`stty sane`)
called at the top of every menu loop, and in a `finally` block on CLI exit.

### Structural Improvements

**Unified tag reader: `get_all_tags()` → `TagBundle`.** The old code opened each
file up to 4× via independent `MutagenFile()` calls (`get_title_artist_track`,
`get_album`, `get_genre`, `get_rating`). Consolidated into a single function
returning a `TagBundle` named tuple. ~19,000 fewer file opens per `--library`
run on a 6,300-track library. Callers updated: `write_music_library_tree`,
`run_tag_audit`, `run_duplicates`. Original standalone functions preserved for
any external imports but no longer called internally.

**Duplicate file-finder eliminated.** `find_files_by_ext` (string generator) and
`_find_files_by_ext_path` (Path list) did the same job. Removed the former,
pointed FLAC mode at the latter.

**Vestigial `paths` list removed from `run_mp3_mode`.** Leftover from a
multi-root design that never shipped. Replaced with a plain `root_path`.

**`Counter` import consolidated.** Was at module level via `defaultdict` but then
re-imported locally in `run_tag_audit`. One import, one location.

**Removed unused `Iterable` from typing imports.**

### Output Format: CSV → Formatted Text

All output modes now write `.txt` reports instead of `.csv`. None of these
outputs were destined for spreadsheets; they're checklists and diagnostics
read by one person, and the format now respects that.

- **FLAC/MP3/Opus integrity**: Header with scan totals, results grouped by
  severity (ERRORS → WARNINGS → OK). Relative paths, tool/error details,
  compact metadata where relevant (bitrate, sample rate, duration).
- **Missing art**: Two sections: no art at all, embedded only. Relative paths
  with file counts.
- **Duplicates**: Grouped by artist/album pair with directories nested
  underneath showing format sets.
- **Tag audit**: Grouped by directory, each file showing format and missing
  fields. Header includes field-level breakdown counts.

### Dead Code Removed

**`_write_header` / `_close_writer` / `_rotated_path`**: Entire CSV writer
infrastructure gone. These managed `csv.DictWriter` lifecycle via a
monkey-patched `_file_handle` attribute. With text output, file writes are
straightforward `open()` calls.

**`import csv`**: No longer imported.

---

## v2.0.0 (2026-03-15)

---

Lattice.py is now a single unified toolkit. The standalone
`extract_opus_art.py` and `extract_mp3_art.py` scripts are retired; their
functionality lives in the main script as `--extractArt`, with improvements.

### New Modes

- **`--testOpus`**: Opus file integrity checking via FFmpeg decode (same
  pattern as `--testMP3`).
- **`--extractArt`**: Extract embedded cover art to `cover.jpg` with format
  priority ranking (FLAC > Opus > M4A > MP3) and `--dry-run` support.
- **`--missingArt`**: Report directories with no cover art (distinguishes
  "no art at all" from "embedded only").
- **`--duplicates`**: Detect same artist+album appearing across multiple
  directories or formats.
- **`--auditTags`**: Report files missing title, artist, track number, or
  genre with a summary breakdown.

### Bug Fixes

- **Fixed cover.jpg collision**: Cover detection is now case-insensitive.
  Running art extraction in a folder with both Opus and MP3 files no longer
  produces both `cover.jpg` and `Cover.jpg`.

### Improvements

- Art extraction prefers front cover (type 3) over generic embedded images.
- Art extraction supports four formats: FLAC, Opus/OGG, M4A, MP3.
- Interactive menu updated with all eight modes.
- All existing CLI invocations remain backward-compatible.

### Removed

- `extract_opus_art.py` (folded into `--extractArt`).
- `extract_mp3_art.py` (folded into `--extractArt`).
les no longer
  produces both `cover.jpg` and `Cover.jpg`.

### Improvements

- Art extraction prefers front cover (type 3) over generic embedded images.
- Art extraction supports four formats: FLAC, Opus/OGG, M4A, MP3.
- Interactive menu updated with all eight modes.
- All existing CLI invocations remain backward-compatible.

### Removed

- `extract_opus_art.py` (folded into `--extractArt`).
- `extract_mp3_art.py` (folded into `--extractArt`).
rt`).
