import re
import os
import json

VERSION = "4.5.0"

DEFAULT_LIBRARY_OUTPUT = "music_library.txt"
DEFAULT_FLAC_OUTPUT = "flac_errors.txt"
DEFAULT_MP3_OUTPUT = "mp3_scan_results.txt"
DEFAULT_OPUS_OUTPUT = "opus_scan_results.txt"
DEFAULT_WAV_OUTPUT = "wav_scan_results.txt"
DEFAULT_WMA_OUTPUT = "wma_scan_results.txt"
DEFAULT_MISSING_ART_OUTPUT = "missing_art.txt"
DEFAULT_ART_QUALITY_OUTPUT = "art_quality_audit.txt"
DEFAULT_DUPLICATES_OUTPUT = "duplicates.txt"
DEFAULT_TAG_AUDIT_OUTPUT = "tag_audit.txt"
DEFAULT_BITRATE_AUDIT_OUTPUT = "bitrate_audit.txt"
DEFAULT_AI_LIBRARY_OUTPUT = "library_ai.txt"
DEFAULT_STATS_OUTPUT = "library_stats.txt"
DEFAULT_PLAYLIST_OUTPUT = "smart_playlist.m3u"

AUDIO_EXTENSIONS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".wav", ".wma", ".aac"}

COVER_NAMES = {
    "cover.jpg",
    "cover.jpeg",
    "cover.png",
    "folder.jpg",
    "folder.jpeg",
    "folder.png",
    "front.jpg",
    "front.jpeg",
    "front.png",
    "album.jpg",
    "album.jpeg",
    "album.png",
}

ART_FORMAT_PRIORITY = [".flac", ".opus", ".ogg", ".m4a", ".mp3"]

RE_CLEAN_PREFIX = re.compile(r"^[^\-\d]*-\s*")
RE_CLEAN_PATTERNS = [
    re.compile(r"^(?:\d+\s*[-–—]\s*)?(\d+)\.?\s*[-–—]?\s*(.+)$"),
    re.compile(r"^[Tt]rack\s*(\d+)\.?\s*[-–—]?\s*(.+)$"),
    re.compile(r"^(\d+)\s+(.+)$"),
]

CONFIG_FILE = os.path.expanduser("~/.config/lattice/config.json")


def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(config: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)


def get_library_root() -> str | None:
    return load_config().get("library_root")


def get_library_roots() -> list[str]:
    """Configured root(s) used as the default when no --root is passed: the
    optional `library_roots` array if present, else the single `library_root`,
    else empty. Lets a user pin several permanent libraries by hand-editing the
    config; the first-run prompt still saves only the one `library_root`."""
    config = load_config()
    roots = config.get("library_roots")
    if isinstance(roots, list) and roots:
        return [os.path.abspath(os.path.expanduser(r)) for r in roots if r]
    single = config.get("library_root")
    return [single] if single else []


def set_library_root(root: str) -> None:
    config = load_config()
    config["library_root"] = os.path.abspath(os.path.expanduser(root))
    save_config(config)
