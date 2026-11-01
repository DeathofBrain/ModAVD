#!/usr/bin/env python3
"""Compile full production native sources; use only private AppKit/CGL contexts."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def run(command, env):
    return subprocess.run(list(map(str, command)), env=env, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gfxstream", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.gfxstream.resolve() / "host/gl/glestranslator/egl"
    here = Path(__file__).resolve().parent
    env = dict(os.environ)
    env.setdefault("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
    with tempfile.TemporaryDirectory(prefix="modavd-native-bind-") as temporary:
        binary = Path(temporary) / "verify-bind"
        command = ["xcrun", "--sdk", "macosx", "clang++", "-arch", "arm64",
                   "-Wno-deprecated-declarations", "-I", source,
                   source / "mac_native.m", source / "mac_pixel_formats_attribs.m",
                   here / "native-context-bind-test.mm", "-framework", "Cocoa",
                   "-framework", "OpenGL", "-framework", "IOSurface", "-o", binary]
        compiled = run(command, env)
        actual = run([binary], env)
        checks = json.loads(actual.stdout)
        if not checks.get("ok"):
            raise RuntimeError("native bind checks did not report success")
    report = {
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": str(source),
        "sources_sha256": {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                           for name in ("mac_native.m", "mac_native.h",
                                        "mac_pixel_formats_attribs.m", "egl_os_api_darwin.cpp")},
        "checks": checks,
        "compile_stderr": compiled.stderr,
        "run_stderr": actual.stderr,
        "scope": "Full native sources, actual private NSOpenGL/CGL contexts; refusing subclass simulates a silent void bind failure.",
        "limits": ["No existing app/window/context or emulator guest accessed.",
                   "No pixel output, renderer reset, context loss, device OOM or full EGL API lifecycle tested.",
                   "Native non-nil deprecated pbuffers not covered by this core-profile test."]
    }
    with args.output.open("x") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
