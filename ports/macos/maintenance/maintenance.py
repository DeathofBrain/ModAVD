#!/usr/bin/env python3
"""ModAVD macOS user maintenance entry point (Python 3.9+ standard library).

SPDX-License-Identifier: AGPL-3.0-or-later
"""
import sys
sys.dont_write_bytecode = True

import argparse
import json
from pathlib import Path
import subprocess

from components import Components
from diagnostics import Diagnostics, copy_to_clipboard


def parser():
    result = argparse.ArgumentParser(description="ModAVD macOS 组件修复与脱敏诊断（不会启动ADB或模拟器）")
    result.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2], help="ModAVD 包根目录")
    commands = result.add_subparsers(dest="command", required=True)
    licenses = commands.add_parser("licenses", help="列出许可证，或显示指定许可全文")
    licenses.add_argument("--id")
    accept = commands.add_parser("accept-license", help="仅供读完并明确接受条款的人执行")
    accept.add_argument("--id", required=True)
    accept.add_argument("--confirm", required=True, help="licenses 命令显示的完整许可 SHA-256")
    accept.add_argument("--i-have-read-and-accept", action="store_true")
    components = commands.add_parser("components", help="检查或修复锁定Google组件")
    components.add_argument("action", choices=("status", "repair"))
    components.add_argument("--component", choices=("platform-tools", "system-image"), action="append")
    collect = commands.add_parser("collect", help="在本机生成脱敏问题ZIP；不会上传")
    collect.add_argument("--output", type=Path, required=True)
    collect.add_argument("--live", action="store_true", help="通过已存在且验证归属的私有ADB只读查询")
    collect.add_argument("--screenshot", action="store_true", help="显式收集完整屏幕；像素未经脱敏，可能含个人信息，要求 --live")
    report = commands.add_parser("report", help="输出脱敏硬件/实例/游戏报告")
    report.add_argument("--live", action="store_true")
    report.add_argument("--clipboard", action="store_true", help="同时复制报告到macOS剪贴板")
    return result


def main(argv=None):
    arguments = parser().parse_args(argv)
    if arguments.command in ("licenses", "accept-license", "components"):
        components = Components(arguments.root)
        if arguments.command == "licenses":
            if arguments.id:
                text, checksum = components.license(arguments.id)
                print(text)
                print("\n许可ID：%s\nSHA-256：%s\n已接受的同哈希收据：%s" % (arguments.id, checksum, components.accepted(arguments.id)))
                print("只有已阅读全文并明确接受后，才可执行 accept-license --id <ID> --confirm <SHA-256> --i-have-read-and-accept")
            else:
                for identifier in sorted({c["license"] for c in components.lock["components"].values()}):
                    _, checksum = components.license(identifier)
                    print(json.dumps({"id": identifier, "sha256": checksum, "accepted": components.accepted(identifier)}, ensure_ascii=False))
                print("运行 licenses --id <ID> 显示完整条款；不会自动接受。")
        elif arguments.command == "accept-license":
            components.accept(arguments.id, arguments.confirm, arguments.i_have_read_and_accept)
            print("已记录你对该完整许可文本的明确接受。")
        else:
            names = arguments.component or list(components.lock["components"])
            states = components.repair(names) if arguments.action == "repair" else [components.status(n) for n in names]
            print(json.dumps(states, ensure_ascii=False, indent=2))
    else:
        diagnostics = Diagnostics(arguments.root)
        if arguments.command == "collect":
            if arguments.screenshot:
                print("截图将保留完整画面，可能含账号等个人信息；分享前请查看 live/*.png。", file=sys.stderr)
            print(json.dumps(diagnostics.collect(arguments.output, arguments.live, arguments.screenshot), ensure_ascii=False, indent=2))
        else:
            text = diagnostics.report(arguments.live)
            print(text, end="")
            if arguments.clipboard:
                copy_to_clipboard(text)
                print("已复制到剪贴板。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        # Library exceptions never dump arbitrary raw guest/server output.
        print("维护操作失败：" + str(error), file=sys.stderr)
        sys.exit(1)
