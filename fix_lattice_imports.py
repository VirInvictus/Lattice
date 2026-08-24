import re

path = 'src/lattice/tui.py'
with open(path, 'r') as f:
    content = f.read()

bad_imports = """from lattice.modes.flac import run_flac_mode
from lattice.modes.mp3 import run_mp3_mode
from lattice.modes.opus import run_opus_mode
from lattice.modes.wav import run_wav_mode
from lattice.modes.wma import run_wma_mode
from lattice.modes.extract_art import run_extract_art
from lattice.modes.missing_art import run_missing_art
from lattice.modes.art_quality import run_art_quality_audit
from lattice.modes.duplicates import run_duplicates
from lattice.modes.tags import run_tag_audit
from lattice.modes.bitrates import run_bitrate_audit
from lattice.modes.replaygain import run_replaygain_audit
from lattice.modes.stats import run_stats
from lattice.modes.wings import write_all_wings
from lattice.modes.ai_wings import write_ai_wings
from lattice.modes.library_tree import write_library_tree
from lattice.modes.ai_library_export import write_ai_library_export
from lattice.modes.smart_playlist import write_smart_playlist"""

good_imports = """
from lattice.modes.integrity import run_flac_mode, run_mp3_mode, run_opus_mode, run_wav_mode, run_wma_mode
from lattice.modes.artwork import run_extract_art, run_missing_art, run_art_quality_audit
from lattice.modes.audit import run_duplicates, run_tag_audit, run_bitrate_audit, run_replaygain_audit
from lattice.modes.stats import run_stats
from lattice.modes.library import write_library_tree, write_ai_library_export, write_all_wings, write_ai_wings
from lattice.modes.playlists import generate_playlist
"""

content = content.replace(bad_imports, good_imports)
content = content.replace("write_smart_playlist", "generate_playlist")

with open(path, 'w') as f:
    f.write(content)
