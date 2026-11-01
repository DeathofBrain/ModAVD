#!/usr/bin/env python3
"""Compile the actual model and, optionally, patched QEMU translation units.

The existing build is read only. Every output belongs to --output-dir.
"""
import argparse
import json
import pathlib
import shlex
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu-source", type=pathlib.Path, required=True)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--build-dir", type=pathlib.Path)
    parser.add_argument("--ninja", type=pathlib.Path)
    args = parser.parse_args()
    source = args.qemu_source.resolve()
    output = args.output_dir.resolve()
    if args.build_dir:
        build = args.build_dir.resolve()
        original_source = (build.parent / "external/qemu").resolve()
        if source == original_source or output.is_relative_to(build) or output.is_relative_to(original_source):
            parser.error("Use an isolated source and outputs outside the existing source/build")
    output.mkdir(parents=True, exist_ok=True)
    results = []

    def run(name, command, cwd=None):
        result = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
        (output / (name + ".log")).write_text(result.stdout + result.stderr)
        results.append({"name": name, "returncode": result.returncode,
                        "command": command})
        (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        print(("PASS" if result.returncode == 0 else "FAIL") + ": " + name,
              flush=True)
        if result.returncode:
            print(result.stdout + result.stderr, file=sys.stderr)
            raise SystemExit(result.returncode)

    test = pathlib.Path(__file__).with_name("test-ram-reclaim-audit-model.c")
    executable = output / "audit-model-test"
    run("model-compile", ["clang", "-std=c11", "-Wall", "-Wextra", "-Werror",
                          "-pthread", "-fsanitize=address,undefined", "-g",
                          "-I" + str(source / "include"), str(test),
                          "-o", str(executable)])
    run("model-run", [str(executable)])
    if not args.build_dir:
        return

    ninja = args.ninja or build.parent / "prebuilts/cmake/darwin-x86/bin/ninja"
    files = ["exec.c", "memory.c", "hw/virtio/virtio.c",
             "hw/virtio/virtio-balloon.c", "migration/ram.c", "numa.c",
             "target/arm/hvf.c"]
    for file in files:
        target = "CMakeFiles/qemu-system-aarch64.dir/" + file + ".o"
        commands = subprocess.check_output(
            [str(ninja), "-t", "commands", "-s", target], cwd=build, text=True)
        command = [line for line in commands.splitlines()
                   if line.endswith(" -c " + str(original_source / file))][-1]
        argv, rewritten, index = shlex.split(command), [], 0
        while index < len(argv):
            arg = argv[index]
            if arg in ("-MD", "-MMD"):
                index += 1
                continue
            if arg in ("-MT", "-MF"):
                index += 2
                continue
            if arg == "-o":
                rewritten += [arg, str(output / (file.replace("/", "_") + ".o"))]
                index += 2
                continue
            prefix = "-I" if arg.startswith("-I") and len(arg) > 2 else ""
            value = arg[len(prefix):]
            if value.startswith("/"):
                resolved = pathlib.Path(value).resolve()
                if resolved.is_relative_to(original_source):
                    value = str(source / resolved.relative_to(original_source))
            rewritten.append(prefix + value)
            index += 1
        run(file.replace("/", "_"), rewritten, build)


if __name__ == "__main__":
    main()
