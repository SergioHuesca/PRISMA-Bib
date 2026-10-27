"""Windows only (loaded through PYTHONPATH by the dynamic workflow test).

On Windows ``subprocess`` cannot run an extensionless ``gh`` wrapper found through PATH and would
start the real gh.exe, so a ``gh`` argv is rewritten to the fake. On POSIX the PATH wrapper
works and this file is not used.
"""

import os
import subprocess
import sys

_orig = subprocess.Popen.__init__


def _init(self, args, *a, **k):  # type: ignore[no-untyped-def]
    if isinstance(args, list) and args and args[0] == "gh" and os.environ.get("FAKE_GH_PY"):
        args = [sys.executable, os.environ["FAKE_GH_PY"], *args[1:]]
    _orig(self, args, *a, **k)


subprocess.Popen.__init__ = _init  # type: ignore[method-assign]
