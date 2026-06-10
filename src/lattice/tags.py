import os
from typing import NamedTuple

from lattice.utils import normalize_rating, _looks_numeric


class TagBundle(NamedTuple):
    """All metadata we care about, extracted once per file."""

    title: str | None = None
    artist: str | None = None
    trackno: int | None = None
    album: str | None = None
    genre: str | None = None
    rating: float | None = None
    duration_s: float | None = None
    bitrate_kbps: int | None = None


class ReplayGainStatus(NamedTuple):
    """Whether a file carries track- and album-level ReplayGain gain tags."""

    has_track_gain: bool = False
    has_album_gain: bool = False


# Opus stores gain as R128_*_GAIN (EBU R128, integer Q7.8) instead of the
# replaygain_*_gain text tags MP3/FLAC use; both count as ReplayGain here, so an
# R128-tagged Opus file is not mistaken for untagged. Matched on the key's
# suffix so format prefixes (ID3 "TXXX:", iTunes "----:com.apple.iTunes:") fold
# in without per-format branches.
_RG_TRACK_SUFFIXES = ("replaygain_track_gain", "r128_track_gain")
_RG_ALBUM_SUFFIXES = ("replaygain_album_gain", "r128_album_gain")


def _rg_flags(keys) -> tuple[bool, bool]:
    kl = [str(k).lower() for k in keys]
    has_track = any(k.endswith(s) for k in kl for s in _RG_TRACK_SUFFIXES)
    has_album = any(k.endswith(s) for k in kl for s in _RG_ALBUM_SUFFIXES)
    return has_track, has_album


def read_replaygain(file_path: str) -> ReplayGainStatus:
    """Report ReplayGain coverage for a file in a single open. An unreadable or
    untagged file reports no gain, so it surfaces as missing in the audit."""
    if not HAVE_MUTAGEN_BASE:
        return ReplayGainStatus()
    try:
        audio = MutagenFile(file_path)
        tags = getattr(audio, "tags", None) if audio is not None else None
        if not tags:
            return ReplayGainStatus()
        has_track, has_album = _rg_flags(tags.keys())
        return ReplayGainStatus(has_track, has_album)
    except Exception:
        return ReplayGainStatus()


# --- Mutagen imports ---
# This module centralizes mutagen imports for the package; `Picture` and
# `MUTAGEN_MP3` are unused here but re-exported for modes/artwork.py.
HAVE_MUTAGEN_BASE = False
try:
    from mutagen import File as MutagenFile
    from mutagen.flac import FLAC, Picture  # noqa: F401  (re-export)
    from mutagen.oggvorbis import OggVorbis
    from mutagen.mp4 import MP4
    from mutagen.asf import ASF

    try:
        from mutagen.oggopus import OggOpus
    except ImportError:

        class OggOpus:  # type: ignore[no-redef]
            pass

    HAVE_MUTAGEN_BASE = True
except ImportError:
    pass

try:
    from mutagen.mp3 import MP3 as MUTAGEN_MP3  # noqa: F401  (re-export)

    HAVE_MUTAGEN_MP3 = True
except ImportError:
    HAVE_MUTAGEN_MP3 = False


def _first_text(val) -> str | None:
    if val is None:
        return None
    if isinstance(val, (list, tuple)):
        val = val[0] if val else None

    # Handle Mutagen ID3 frames which store strings in a .text list
    if hasattr(val, "text") and isinstance(val.text, list) and val.text:
        # Join multiple values with a slash instead of mutagen's default null byte
        val = "/".join(str(v) for v in val.text)

    try:
        if hasattr(val, "value"):
            val = val.value
    except Exception:
        pass

    if val is not None:
        # Strip string and explicitly replace any remaining null bytes
        s = str(val).replace("\x00", "/").strip()
        return s if s else None
    return None


def _parse_track_number(val) -> int | None:
    if val is None:
        return None
    if isinstance(val, list) and val:
        if isinstance(val[0], tuple):
            try:
                num = int(val[0][0])
                return num if num > 0 else None
            except ValueError, IndexError, TypeError:
                return None
    s = _first_text(val)
    if not s:
        return None
    s = s.split("/")[0]
    try:
        n = int(s)
        return n if n > 0 else None
    except ValueError:
        return None


def get_all_tags(file_path: str) -> TagBundle:
    """Extract all metadata in a single file open."""
    if not HAVE_MUTAGEN_BASE:
        return TagBundle()

    title = artist = album = genre = None
    trackno: int | None = None
    rating: float | None = None
    duration_s: float | None = None
    bitrate_kbps: int | None = None

    try:
        audio = MutagenFile(file_path)
        if not audio:
            return TagBundle()

        # Extract duration and bitrate from audio.info
        info = getattr(audio, "info", None)
        if info:
            length = getattr(info, "length", 0.0) or 0.0
            if length > 0:
                duration_s = round(length, 3)
            br = getattr(info, "bitrate", 0) or 0
            if br > 0:
                bitrate_kbps = int(br / 1000)

        ext = os.path.splitext(file_path)[1].lower()
        tags = getattr(audio, "tags", {}) or {}

        if ext == ".mp3":
            # ID3 tags — accessible via audio.tags from MutagenFile
            if not tags:
                return TagBundle(
                    title,
                    artist,
                    trackno,
                    album,
                    genre,
                    rating,
                    duration_s,
                    bitrate_kbps,
                )

            if hasattr(tags, "get"):
                tit2 = tags.get("TIT2")
                if tit2:
                    title = _first_text(tit2.text)
                tpe1 = tags.get("TPE1")
                tpe2 = tags.get("TPE2")
                if tpe2:
                    artist = _first_text(tpe2.text)
                elif tpe1:
                    artist = _first_text(tpe1.text)
                trck = tags.get("TRCK")
                if trck:
                    trackno = _parse_track_number(trck.text)
                talb = tags.get("TALB")
                if talb:
                    album = _first_text(talb.text)

            if hasattr(tags, "getall"):
                tcon = tags.getall("TCON")
                if tcon:
                    genre = _first_text(tcon[0])

                # Rating: POPM (prefer WMP, then any) / TXXX
                for popm in tags.getall("POPM"):
                    if getattr(popm, "email", "") == "Windows Media Player 9 Series":
                        wmp_map = {1: 1.0, 64: 2.0, 128: 3.0, 196: 4.0, 255: 5.0}
                        rating = wmp_map.get(popm.rating)
                        if rating is None:
                            rating = normalize_rating(popm.rating)
                        break
                if rating is None:
                    for popm in tags.getall("POPM"):
                        if popm.rating > 0:
                            rating = normalize_rating(popm.rating)
                            break
                if rating is None:
                    for txxx in tags.getall("TXXX"):
                        desc = (txxx.desc or "").lower()
                        if "rating" in desc or desc in ("rate", "score", "stars"):
                            val = txxx.text[0] if txxx.text else None
                            if _looks_numeric(val):
                                rating = normalize_rating(val)
                                break

        elif isinstance(audio, MP4):
            title = _first_text(tags.get("\xa9nam"))
            artist = _first_text(tags.get("aART")) or _first_text(tags.get("\xa9ART"))
            trackno = _parse_track_number(tags.get("trkn"))
            album = _first_text(tags.get("\xa9alb"))
            for k in ("\xa9gen", "gnre"):
                v = tags.get(k)
                if v:
                    genre = _first_text(v)
                    break
            for k, v in tags.items():
                kl = k.lower() if isinstance(k, str) else str(k).lower()
                if "rate" in kl or "rating" in kl:
                    v = v[0] if isinstance(v, list) else v
                    if _looks_numeric(v):
                        rating = normalize_rating(v)
                        break

        elif isinstance(audio, (FLAC, OggVorbis, OggOpus)):
            keys = {k.lower(): k for k in tags.keys()}
            if "title" in keys:
                title = _first_text(tags[keys["title"]])
            if "albumartist" in keys:
                artist = _first_text(tags[keys["albumartist"]])
            elif "artist" in keys:
                artist = _first_text(tags[keys["artist"]])
            if "tracknumber" in keys:
                trackno = _parse_track_number(tags[keys["tracknumber"]])
            if "album" in keys:
                album = _first_text(tags[keys["album"]])
            if "genre" in keys:
                genre = _first_text(tags[keys["genre"]])
            for key, val in tags.items():
                if (
                    "rating" in key.lower()
                    or "score" in key.lower()
                    or "stars" in key.lower()
                ):
                    val = val[0] if isinstance(val, list) else val
                    if _looks_numeric(val):
                        rating = normalize_rating(val)
                        break

        elif isinstance(audio, ASF):
            name_map = {k_name.lower(): k_name for k_name in tags.keys()}
            if key_name := name_map.get("title"):
                title = _first_text(tags.get(key_name))
            if key_name := name_map.get("wm/albumartist") or name_map.get("author"):
                artist = _first_text(tags.get(key_name))
            if key_name := name_map.get("wm/tracknumber") or name_map.get(
                "tracknumber"
            ):
                trackno = _parse_track_number(tags.get(key_name))
            if key_name := name_map.get("wm/albumtitle"):
                album = _first_text(tags.get(key_name))
            if key_name := name_map.get("wm/genre"):
                genre = _first_text(tags.get(key_name))
            for key, val in tags.items():
                if "rating" in key.lower():
                    val = val[0] if isinstance(val, list) else val
                    if _looks_numeric(val):
                        rating = normalize_rating(val)
                        break

        # Fallback: generic tag iteration for album/genre if still missing
        if album is None or genre is None:
            for k, v in tags.items():
                kl = str(k).lower()
                if album is None and kl == "album":
                    album = _first_text(v)
                if genre is None and kl in ("genre", "wm/genre"):
                    genre = _first_text(v)
            if album is None:
                getall_fn = getattr(tags, "getall", None)
                if getall_fn:
                    talb = getall_fn("TALB")
                    if talb:
                        album = _first_text(talb[0])

    except Exception:
        pass

    return TagBundle(
        title, artist, trackno, album, genre, rating, duration_s, bitrate_kbps
    )
