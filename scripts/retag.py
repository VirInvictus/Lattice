#!/usr/bin/env python3
import os
import sys
import argparse
import mutagen
from mutagen.easyid3 import EasyID3
from mutagen.mp4 import MP4

# Dropped wav, wma, and bare aac to match the handled logic
AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.mp4'}

def apply_genres(filepath: str, new_genres: list) -> bool:
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext == '.mp3':
            # Remove APEv2 tags if present (they often cause conflicting dual-genres)
            try:
                from mutagen.apev2 import APEv2
                APEv2(filepath).delete()
            except Exception:
                pass

            try:
                audio = EasyID3(filepath)
            except mutagen.id3.ID3NoHeaderError:
                audio = mutagen.File(filepath, easy=True)
                audio.add_tags()
            
            audio.pop("genre", None)
            audio["genre"] = new_genres
            # Force v2.3 for widespread player compatibility, sync v1 tags
            audio.save(v2_version=3, v1=2)

        elif ext in ['.flac', '.opus', '.ogg']:
            audio = mutagen.File(filepath)
            if audio is None:
                return False
            # Vorbis comments natively support multiple tags with the same name.
            # Clear existing genres first (handling both case variations)
            audio.pop("genre", None)
            audio.pop("GENRE", None)
            # Passing a list creates multiple 'GENRE' tags perfectly.
            audio["genre"] = new_genres
            audio.save()

        elif ext in ['.m4a', '.mp4']:
            audio = MP4(filepath)
            # Clear existing standard (gnre) and custom (\xa9gen) genres
            audio.pop("gnre", None)
            audio.pop("\xa9gen", None)
            # Mutagen expects a list of strings for the custom genre atom
            audio["\xa9gen"] = new_genres
            audio.save()
            
        else:
            return False

        return True
    except Exception as e:
        print(f"  [!] Failed to tag {os.path.basename(filepath)}: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Apply universal genre tags to a directory of audio files.")
    parser.add_argument("directory", help="Absolute path to the album directory")
    parser.add_argument("genres", nargs='+', help="One or more genres to apply")
    args = parser.parse_args()

    target_dir = args.directory
    genres = args.genres

    if not os.path.isdir(target_dir):
        print(f"[!] Directory not found: {target_dir}")
        sys.exit(1)

    print(f"Tagging: {target_dir}")
    print(f"Genres:  {genres}")

    success_count = 0
    files = sorted(os.listdir(target_dir))
    
    for f in files:
        ext = os.path.splitext(f)[1].lower()
        if ext in AUDIO_EXTENSIONS:
            filepath = os.path.join(target_dir, f)
            if apply_genres(filepath, genres):
                success_count += 1

    if success_count == 0:
        print("  -> No valid audio files updated.")
    else:
        print(f"  -> Successfully updated {success_count} files.")

if __name__ == "__main__":
    main()
