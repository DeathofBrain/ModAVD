#!/usr/bin/env python3
"""Incrementally compile/link a cloned build without running build generators.

Reads the original Ninja command/dependency graph. Recompiles every consumer of
an experiment-modified header/source, then propagates changed objects through
archives and the requested executable. Every command output path is rewritten
to the isolated cloned build. CMake/Ninja build scheduling is never invoked.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--original-source", type=Path, required=True)
    parser.add_argument("--original-build", type=Path, required=True)
    parser.add_argument("--ninja", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--target", default="qemu-system-aarch64")
    parser.add_argument("--overrides", type=Path, action="append", default=[],
                        help="JSON original/override paths and includeDirs; repeatable")
    args = parser.parse_args()
    args.ninja = args.ninja.resolve(strict=True)
    source, build, original, original_build = [p.resolve(strict=True) for p in (args.source, args.build, args.original_source, args.original_build)]
    if source == original or build == original_build or source.is_relative_to(original_build) or build.is_relative_to(original_build):
        parser.error("Source and build must be isolated from the normal checkout/build")
    if args.target not in ("qemu-system-aarch64", "qemu-system-aarch64-headless", "gfxstream_backend") or not 1 <= args.jobs <= 4:
        parser.error("Only aarch64 emulator/gfxstream targets and 1..4 jobs are supported")
    output = build / "ram-audit-build"
    output.mkdir(exist_ok=True)
    changed = subprocess.check_output(["git", "diff", "--name-only", "HEAD"], cwd=source, text=True).splitlines()
    overrides = {str(original / p): str(source / p) for p in changed if (source / p).is_file()}
    include_dirs = []
    external_hashes = {}
    for manifest_path in args.overrides:
        manifest = json.loads(manifest_path.read_text())
        for entry in manifest["overrides"]:
            original_file = Path(entry["original"]).resolve(strict=True)
            override_file = Path(entry["override"]).resolve(strict=True)
            if original_file == override_file or override_file.is_relative_to(original_build):
                parser.error("Overrides must not write the original source or build")
            actual_hash = hashlib.sha256(original_file.read_bytes()).hexdigest()
            if entry.get("original_sha256", actual_hash) != actual_hash:
                parser.error("Original override input changed: " + str(original_file))
            overrides[str(original_file)] = str(override_file)
            external_hashes[str(override_file)] = hashlib.sha256(override_file.read_bytes()).hexdigest()
        for directory in manifest.get("includeDirs", []):
            directory = Path(directory).resolve(strict=True)
            include_dirs.append(str(directory))
            for header in directory.rglob("*"):
                if header.is_file():
                    external_hashes[str(header)] = hashlib.sha256(header.read_bytes()).hexdigest()
    tracked = set(overrides)
    tracked_names = {Path(p).name for p in tracked}
    print("Reading dependency graph for %d modified/overridden experiment files" % len(tracked), flush=True)
    commands_path, deps_path = output / "original-commands.txt", output / "original-deps.txt"
    with commands_path.open("w") as stream:
        subprocess.run([str(args.ninja), "-t", "commands", args.target], cwd=build, text=True, stdout=stream, check=True)
    if not deps_path.exists():
        with deps_path.open("w") as stream:
            subprocess.run([str(args.ninja), "-t", "deps"], cwd=build, text=True, stdout=stream, check=True)
    affected, current = set(), None
    with deps_path.open() as stream:
        for line in stream:
            if not line.startswith(" ") and ": #deps " in line:
                current = line.partition(": #deps ")[0]
            elif current:
                dependency = line.strip()
                dependency_path = Path(dependency)
                if not dependency_path.is_absolute():
                    dependency_path = original_build / dependency_path
                if dependency_path.name in tracked_names and str(dependency_path.resolve()) in tracked:
                    affected.add(current)
    commands = commands_path.read_text().splitlines()
    compile_jobs, later = [], []
    for command in commands:
        argv = shlex.split(command)
        if "-c" in argv and "-o" in argv:
            source_file = argv[argv.index("-c") + 1]
            target = argv[argv.index("-o") + 1]
            if target in affected or str(Path(source_file).resolve()) in tracked:
                compile_jobs.append((target, command))
        else:
            later.append((argv, command))
    print("Compiling %d affected translation units (jobs=%d)" % (len(compile_jobs), args.jobs), flush=True)
    results, dirty = [], set()

    def execute(target, command):
        rewritten = command.replace(str(original_build), str(build))
        # The private checkout does not duplicate AOSP third-party submodules.
        # Read those unchanged includes from the normal source, while every
        # modified public header and translation unit comes from the experiment.
        for token in shlex.split(command):
            if token.startswith("-I"):
                include = Path(token[2:]).resolve()
                if include.is_relative_to(original / "include"):
                    private_include = source / "include" / include.relative_to(original / "include")
                    rewritten = rewritten.replace(token, "-I" + str(private_include))
        for old, new in overrides.items():
            rewritten = rewritten.replace(" -c " + old, " -c " + new)
        if " -c " in rewritten:
            # Prefix every compile, so copied ABI-compatible public headers and
            # the renderer extension resolve before unchanged third-party dirs.
            options = ["-I" + shlex.quote(p) for p in include_dirs]
            original_argv = shlex.split(command)
            original_input = Path(original_argv[original_argv.index("-c") + 1])
            if str(original_input) in overrides:
                options += ["-iquote", shlex.quote(str(original_input.parent))]
            options = " ".join(options)
            compiler, separator, arguments = rewritten.partition(" ")
            rewritten = compiler + separator + options + " " + arguments
        if str(original_build) in rewritten or "--regenerate-during-build" in rewritten:
            raise RuntimeError("Normal output/generator escaped command isolation")
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", target)
        started = time.time()
        with (output / (name + ".log")).open("w") as stream:
            completed = subprocess.run(rewritten, cwd=build, shell=True, executable="/bin/sh", stdout=stream, stderr=subprocess.STDOUT)
        record = {"target": target, "returncode": completed.returncode, "elapsedSeconds": time.time() - started, "command": rewritten}
        if completed.returncode:
            print("FAIL %s; see %s" % (target, output / (name + ".log")), flush=True)
        return record

    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        pending = {executor.submit(execute, target, command): target for target, command in compile_jobs}
        for future in as_completed(pending):
            record = future.result()
            results.append(record)
            (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
            if record["returncode"]:
                raise SystemExit(record["returncode"])
            dirty.add(record["target"])
            if len(results) % 10 == 0 or len(results) == len(compile_jobs):
                print("Compiled %d/%d" % (len(results), len(compile_jobs)), flush=True)
    for argv, command in later:
        if not any(any(target == token or target in token.split(",") for token in argv) for target in dirty):
            continue
        target = None
        if "-o" in argv:
            target = argv[argv.index("-o") + 1]
        else:
            for index, token in enumerate(argv):
                if Path(token).name in ("ar", "llvm-ar") and index + 2 < len(argv):
                    target = argv[index + 2]
                    break
        if not target:
            raise RuntimeError("Cannot prove isolated output of dependent command: " + command[:200])
        print("Link/archive " + target, flush=True)
        record = execute(target, command)
        results.append(record)
        (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        if record["returncode"]:
            raise SystemExit(record["returncode"])
        dirty.add(target)
    binary = (build / "lib64/libgfxstream_backend.dylib" if args.target == "gfxstream_backend"
              else build / "qemu/darwin-aarch64" / args.target)
    if str(binary.relative_to(build)) not in dirty:
        raise RuntimeError("Executable was not relinked from the experiment objects")
    checksum = hashlib.sha256(binary.read_bytes()).hexdigest()
    metadata = json.dumps({"binary": str(binary), "sha256": checksum,
        "modifiedFiles": changed, "compiledUnits": len(compile_jobs), "mainBuildWritten": False,
        "sourceContentsSha256": {p: hashlib.sha256((source / p).read_bytes()).hexdigest() for p in changed if (source / p).is_file()},
        "overrideContentsSha256": external_hashes,
        "limitations": ["Experimental binary; no production reclaim eligibility or benefit established."]}, indent=2) + "\n"
    (output / "binary.json").write_text(metadata)
    (output / ("binary-" + args.target + ".json")).write_text(metadata)
    print("READY %s sha256=%s" % (binary, checksum), flush=True)


if __name__ == "__main__":
    main()
