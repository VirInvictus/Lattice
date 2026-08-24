import re
from datetime import datetime
import os

date_str = datetime.now().strftime("%Y-%m-%d")

# pyproject.toml
with open("pyproject.toml", "r") as f: content = f.read()
content = re.sub(r'version = "4\.15\.0"', 'version = "4.16.0"', content)
with open("pyproject.toml", "w") as f: f.write(content)

# patchnotes.md
patchnotes = f"""# 4.16.0 ({date_str})
- **Refactor**: Eradicated 1,267 lines of duplicated TUI framework code. Lattice now delegates completely to `vir-tui` v2.0.0.
- **Dependency**: Pointed `vir-tui` reference to local path for editable testing, then restored remote URL on 2.0.0 bump.

"""
with open("patchnotes.md", "r") as f: current_pn = f.read()
with open("patchnotes.md", "w") as f: f.write(patchnotes + current_pn)

# spec.md
with open("spec.md", "r") as f: spec = f.read()
spec = spec.replace("Lattice maintains its own internal TUI layout in `src/lattice/tui.py`", "Lattice defines domain layouts but fully delegates terminal manipulation to `vir-tui`.")
with open("spec.md", "w") as f: f.write(spec)

# README.md
with open("README.md", "r") as f: readme = f.read()
readme = readme.replace("4.15.0", "4.16.0")
with open("README.md", "w") as f: f.write(readme)

# CLAUDE.md
with open("CLAUDE.md", "r") as f: claude = f.read()
claude = claude.replace("v4.15.0", "v4.16.0")
if "vir-tui" not in claude:
    claude += "\n- UI logic is delegated to `vir-tui`. Do not recreate curses primitives here.\n"
with open("CLAUDE.md", "w") as f: f.write(claude)

if os.path.exists("VERSION"):
    with open("VERSION", "w") as f: f.write("4.16.0")

