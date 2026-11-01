#!/usr/bin/env python3
"""Run only the private reclaim QA runtime on its separate loopback ports."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import zipfile

ENTRY = Path(__file__).resolve()
SOURCE = ENTRY.parents[3] / "ports/macos/runtime/runtime.py"
spec = importlib.util.spec_from_file_location("modavd_reclaim_runtime", SOURCE)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
runtime.ADB_PORT = 20150
runtime.CONSOLE_PORT = 20160
# The storage watchdog must re-enter this wrapper to preserve private ports.
runtime.__file__ = str(ENTRY)

if __name__ == "__main__":
    try:
        runtime.main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError,
            zipfile.BadZipFile) as error:
        print(f"失败：{error}", file=sys.stderr)
        sys.exit(1)
