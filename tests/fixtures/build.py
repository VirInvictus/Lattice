#!/usr/bin/env python3
"""Regenerate the committed test fixture library under tests/fixtures/library/.

Run from the repo root: `python tests/fixtures/build.py`. Needs ffmpeg (to
synthesize short tones) and mutagen (to write deterministic tags). The output
is committed so the test suite itself needs neither tool for the read-only
mode tests. Keep the tones short; these files live in git.
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "library")

sys.path.insert(0, os.path.join(HERE, "..", "..", "src"))
from mutagen.flac import FLAC  # noqa: E402
from mutagen.mp3 import MP3  # noqa: E402
from mutagen.id3 import TIT2, TPE1, TALB, TCON, TRCK, POPM  # noqa: E402


def gen(path, dur, *, lo=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}"]
    if lo:
        cmd += ["-b:a", "96k"]
    cmd += [path]
    subprocess.run(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
    )


def tag_flac(path, *, title, artist, album, genre, trackno, rating100=None):
    f = FLAC(path)
    f["title"], f["artist"], f["album"] = title, artist, album
    f["genre"], f["tracknumber"] = genre, str(trackno)
    if rating100 is not None:
        f["rating"] = str(rating100)
    f.save()


def tag_mp3(path, *, title, artist, album, trackno, genre=None, popm=None):
    a = MP3(path)
    if a.tags is None:
        a.add_tags()
    a.tags.add(TIT2(encoding=3, text=title))
    a.tags.add(TPE1(encoding=3, text=artist))
    a.tags.add(TALB(encoding=3, text=album))
    a.tags.add(TRCK(encoding=3, text=str(trackno)))
    if genre is not None:
        a.tags.add(TCON(encoding=3, text=genre))
    if popm is not None:
        a.tags.add(POPM(email="Windows Media Player 9 Series", rating=popm, count=0))
    a.save()


def main():
    shutil.rmtree(LIB, ignore_errors=True)

    # Aphex Twin / Selected Ambient Works: 3 FLAC, genre Electronic, two 5-star.
    for i, (t, r) in enumerate([("Xtal", 100), ("Tha", 100), ("Pulsewidth", 80)], 1):
        p = f"{LIB}/Aphex Twin/Selected Ambient Works/{i:02d} - {t}.flac"
        gen(p, 1.0 + i * 0.1)
        tag_flac(
            p,
            title=t,
            artist="Aphex Twin",
            album="Selected Ambient Works",
            genre="Electronic",
            trackno=i,
            rating100=r,
        )

    # Same album under a second parent: exact cross-directory duplicate.
    for i, t in enumerate([("Xtal"), ("Tha")], 1):
        p = f"{LIB}/Compilations/Aphex Twin/Selected Ambient Works/{i:02d} - {t}.flac"
        gen(p, 1.0 + i * 0.1)
        tag_flac(
            p,
            title=t,
            artist="Aphex Twin",
            album="Selected Ambient Works",
            genre="Electronic",
            trackno=i,
        )

    # Cursive / Domestica: 2 MP3; track 2 is low-bitrate and missing a genre.
    p = f"{LIB}/Cursive/Domestica/01 - The Casualty.mp3"
    gen(p, 1.1)
    tag_mp3(
        p,
        title="The Casualty",
        artist="Cursive",
        album="Domestica",
        trackno=1,
        genre="Rock",
        popm=255,
    )
    p = f"{LIB}/Cursive/Domestica/02 - The Martyr.mp3"
    gen(p, 1.2, lo=True)
    tag_mp3(p, title="The Martyr", artist="Cursive", album="Domestica", trackno=2)

    # Modern Baseball / Sports: same track as FLAC + MP3 (within-folder multi-format).
    base = f"{LIB}/Modern Baseball/Sports"
    gen(f"{base}/01 - Re-do.flac", 1.2)
    tag_flac(
        f"{base}/01 - Re-do.flac",
        title="Re-do",
        artist="Modern Baseball",
        album="Sports",
        genre="Indie",
        trackno=1,
    )
    gen(f"{base}/01 - Re-do.mp3", 1.2)
    tag_mp3(
        f"{base}/01 - Re-do.mp3",
        title="Re-do",
        artist="Modern Baseball",
        album="Sports",
        trackno=1,
        genre="Indie",
    )

    print("fixture written to", LIB)


if __name__ == "__main__":
    main()
