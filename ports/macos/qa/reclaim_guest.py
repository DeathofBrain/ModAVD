#!/usr/bin/env python3
"""Owned disposable ARM guest for RAM experiments, isolated from Manager ports."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path

BASE = Path(__file__).resolve().parents[3]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runtime = load("modavd_reclaim_runtime", BASE / "ports/macos/runtime/runtime.py")
assembler = load("modavd_reclaim_assembler", BASE / "ports/macos/package.py")
runtime.ADB_PORT = 20150
runtime.CONSOLE_PORT = 20160
# Runtime's watchdog re-executes __file__. Keep it in this isolated port profile.
runtime.__file__ = str(Path(__file__).resolve())


def prepare(instance, source, cores):
    instance.initialize()
    profile = instance.root / "reclaim-profile.json"
    expected = {"schema": 1, "adb": 20150, "console": 20160, "kind": "disposable-reclaim-audit"}
    if profile.exists() and json.loads(profile.read_text()) != expected:
        raise RuntimeError("实验目录属于其他端口配置")
    runtime.write_json(profile, expected)
    stats = {"clonedFiles": 0, "copiedFiles": 0}
    for name in ("platform-tools", "system-image"):
        config = runtime.LOCK["components"][name]
        receipt = json.loads((source / "receipts" / (name + ".json")).read_text())
        if receipt.get("archiveSha1") != config["sha1"]:
            raise RuntimeError("源SDK收据与固定组件不符")
        original = source / "sdk" / config["destination"]
        destination = instance.sdk / config["destination"]
        assembler.verify_receipt(original, receipt["files"])
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            assembler.copy_tree(original, destination, stats)
        assembler.verify_receipt(destination, receipt["files"])
        runtime.write_json(instance.root / "receipts" / (name + ".json"), receipt)
    for name in ("android-sdk-license", "android-sdk-arm-dbt-license"):
        source_receipt = source / "receipts" / (name + ".json")
        receipt = json.loads(source_receipt.read_text())
        if receipt.get("id") != name or not receipt.get("acceptedAt"):
            raise RuntimeError("源许可没有用户明确接受收据")
        runtime.write_json(instance.root / "receipts" / (name + ".json"), receipt)
    directory = instance.create_avd()
    state = instance.root / "emulator.json"
    if state.exists() and runtime.same_process(json.loads(state.read_text()), instance.name()):
        raise RuntimeError("实验客体运行中，拒绝改配置")
    config = directory / "config.ini"
    data = dict(line.split("=", 1) for line in config.read_text().splitlines() if "=" in line)
    data["hw.cpu.ncore"] = str(cores)
    config.write_text("".join(key + "=" + value + "\n" for key, value in data.items()))
    print(json.dumps({"root": str(instance.root), "name": instance.name(), **expected, **stats}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=BASE / ".macos-work/reclaim-runtime")
    parser.add_argument("command", choices=("prepare", "start", "wait", "status", "stop", "stop-server", "watch"))
    parser.add_argument("--sdk-source", type=Path, default=BASE / ".macos-work/runtime")
    parser.add_argument("--emulator", type=Path)
    parser.add_argument("--memory", type=int, choices=(2048, 3072, 4096), default=2048)
    parser.add_argument("--cores", type=int, choices=(2, 4, 8), default=2)
    args = parser.parse_args()
    instance = runtime.Runtime(args.root)
    os.environ["MODAVD_HVF_RAM_AUDIT"] = "1"
    if args.command == "prepare":
        prepare(instance, args.sdk_source.resolve(strict=True), args.cores)
        return
    expected = {"schema": 1, "adb": 20150, "console": 20160, "kind": "disposable-reclaim-audit"}
    profile = instance.root / "reclaim-profile.json"
    if not profile.is_file() or json.loads(profile.read_text()) != expected:
        parser.error("先在独立目录执行prepare；拒绝访问未标记实例")
    if args.command == "start":
        if not args.emulator:
            parser.error("start必须指定独立实验emulator")
        instance.start(args.emulator, True, args.memory)
    elif args.command == "wait":
        instance.wait_boot(180)
    elif args.command == "stop":
        instance.stop()
    elif args.command == "stop-server":
        instance.stop_server()
    elif args.command == "watch":
        instance.watch()
    else:
        state = instance.root / "emulator.json"
        record = json.loads(state.read_text()) if state.is_file() else {}
        alive = bool(record and runtime.same_process(record, instance.name()))
        print(json.dumps({"name": instance.name(), "running": alive, "pid": record.get("pid"),
                          "adb": runtime.ADB_PORT, "console": runtime.CONSOLE_PORT}, indent=2))


if __name__ == "__main__":
    main()
