"""Select a real SDK worker interpreter, preserving virtual-environment paths."""

import os
from pathlib import Path
import sys


def worker_interpreter(value=None):
    executable = Path(os.path.abspath(os.path.expanduser(str(value or sys.executable))))
    if getattr(sys, "frozen", False) and (not value or executable == Path(sys.executable)):
        raise ValueError("A frozen launcher cannot run an SDK worker. Select the bundled real Python interpreter explicitly.")
    return executable
