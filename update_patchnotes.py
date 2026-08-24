with open("patchnotes.md", "r") as f:
    lines = f.readlines()

new_lines = [
    "# Lattice Patch Notes\n",
    "\n",
    "## v4.15.0 (2026-08-23)\n",
    "- **TUI Extraction (`vir-tui`):** The generic CLI/curses interface has been extracted into a shared library, `vir-tui`. Lattice now delegates to `vir-tui` for all interactive menus and text prompts, sharing this layer with `CalibreQuarry`.\n",
    "\n"
]

if lines[0].startswith("# Lattice Patch Notes"):
    lines = lines[1:]

with open("patchnotes.md", "w") as f:
    f.writelines(new_lines + lines)
