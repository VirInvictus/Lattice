"""Test suite for Lattice.

Tests target the pure, logic-dense helpers (rating/key normalization,
duration clustering, image-header parsing, filename cleanup) that have no
filesystem or mutagen dependency. Run from the repo root with:

    python -m unittest discover

This shim puts ``src/`` on the path so the suite runs from a checkout
without an editable install.
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))
