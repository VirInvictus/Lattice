import sys

# PyInstaller + Python 3.14 multiprocessing.resource_tracker fix
# The resource tracker launches sys.executable with '-B', '-S', '-I', '-c', '...'
# Since sys.executable is our binary, we need to intercept and execute it.
if len(sys.argv) > 1 and "-c" in sys.argv:
    c_idx = sys.argv.index("-c")
    if c_idx + 1 < len(sys.argv):
        code = sys.argv[c_idx + 1]
        exec(code)
        sys.exit(0)

import multiprocessing
from lattice.cli import main
from lattice.utils import _reset_terminal

if __name__ == "__main__":
    multiprocessing.freeze_support()
    try:
        sys.exit(main())
    finally:
        _reset_terminal()
