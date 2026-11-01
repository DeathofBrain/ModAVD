#!/usr/bin/env python3
"""Inspect official metadata or a user-provided local Arknights Bilibili APK."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import shutil
import stat
import struct
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

REPO = Path(__file__).resolve().parents[3]
GAME_ID = 101772
PACKAGE = "com.hypergryph.arknights.bilibili"
PAGE = f"https://www.biligame.com/detail/?id={GAME_ID}"
API = "https://line1-h5-pc-api.biligame.com"
CDNS = {"pkg.biligame.com", "pkgdl.biligame.net"}
RESERVE = 20 * 1024**3
LIMIT = 2 * 1024**2


def space(path: Path, additional: int = 0) -> None:
    probe = path
    while not probe.exists():
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if free - additional < RESERVE:
        raise RuntimeError(f"空间停线：须保留 20 GiB；当前可用 {free / 1024**3:.1f} GiB")


def write_json(path: Path, value: object) -> None:
    body = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    space(path.parent, len(body))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(body)
    tmp.replace(path)


def request(url: str, *, head: bool = False) -> dict:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.hostname not in CDNS | {
        "line1-h5-pc-api.biligame.com", "www.biligame.com"
    }:
        raise RuntimeError(f"拒绝非锁定官方地址：{url}")
    req = urllib.request.Request(url, method="HEAD" if head else "GET",
                                 headers={"User-Agent": "ModAVD-official-metadata/1"})
    # No CookieJar, login tokens, browser profile, or credentials are used.
    try:
        response = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        final = urllib.parse.urlsplit(response.url)
        if final.scheme != "https" or final.hostname != parsed.hostname:
            raise RuntimeError("官方请求被重定向到另一个主机；须先人工审查来源")
        body = b"" if head else response.read(LIMIT + 1)
        if len(body) > LIMIT:
            raise RuntimeError("元数据响应超过 2 MiB，已停止")
        record = {
            "url": url, "finalUrl": response.url, "method": "HEAD" if head else "GET",
            "status": response.status,
            "headers": {key: value for key, value in response.headers.items() if key.lower() in {
                "content-type", "content-length", "content-md5", "etag", "last-modified",
                "accept-ranges", "x-error-info", "date"
            }},
            "bodySha256": hashlib.sha256(body).hexdigest(),
        }
        if not head:
            try:
                record["json"] = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                record["text"] = body.decode(errors="replace")
        return record


def inspect(root: Path) -> dict:
    root = root.expanduser().resolve()
    space(root)
    marker = root / ".modavd-game-cache.json"
    if not marker.exists():
        if root.exists() and any(root.iterdir()):
            raise RuntimeError("缓存目录非空且没有本工具标记；保留现有内容")
        root.mkdir(parents=True, exist_ok=True)
        write_json(marker, {"schema": 1, "gameId": GAME_ID, "channel": "bilibili"})
    if json.loads(marker.read_text()) != {"schema": 1, "gameId": GAME_ID, "channel": "bilibili"}:
        raise RuntimeError("缓存目录标记不符")
    game = request(f"{API}/game/detail/gameinfo?game_base_id={GAME_ID}")
    content = request(f"{API}/game/detail/content?game_base_id={GAME_ID}")
    if game.get("json", {}).get("code") != 0 or content.get("json", {}).get("code") != 0:
        raise RuntimeError("官方匿名元数据接口未返回成功；不开始下载")
    data = game["json"]["data"]
    detail = content["json"]["data"]
    if data.get("android_pkg_name") != PACKAGE or data.get("title") != "明日方舟":
        raise RuntimeError("官方条目身份改变，须重新审查")
    links = [data.get(key, "") for key in ("android_download_link", "android_download_link2")]
    for url in links:
        if urllib.parse.urlsplit(url).hostname not in CDNS:
            raise RuntimeError("官方元数据返回未审查的 CDN 地址")
    checks = [request(url, head=True) for url in links]
    crypto = request(f"{API}/game/download/get_crypto_url?game_base_id={GAME_ID}")
    snapshot = {
        "schema": 1, "checkedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "channel": "bilibili", "gameId": GAME_ID, "sourcePage": PAGE,
        "title": data["title"], "packageReportedByOfficialApi": PACKAGE,
        "versionReportedByOfficialApi": detail.get("android_version"),
        "androidPkgVerReportedByOfficialApi": data.get("android_pkg_ver"),
        "sizeBytesReportedByOfficialApi": data.get("android_pkg_size"),
        "updatedAtReportedByOfficialApi": detail.get("android_update_time"),
        "privacyPolicy": data.get("privacy_policy_link"), "officialDownloadUrls": links,
        "downloadAccess": "requires_official_user_action" if any(c["status"] != 200 for c in checks)
                          else "head_available_apk_not_downloaded",
        "apkPresent": False, "apkManifestAndArm64": "unverified_without_apk",
        "evidence": {"gameinfo": game, "content": content, "cdnHeads": checks, "crypto": crypto},
    }
    directory = root / "metadata"
    directory.mkdir(exist_ok=True)
    name = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8] + ".json"
    write_json(directory / name, snapshot)
    write_json(root / "source.json", snapshot)
    return {"metadata": str(root / "source.json"), "version": snapshot["versionReportedByOfficialApi"],
            "sizeBytes": snapshot["sizeBytesReportedByOfficialApi"], "packageReported": PACKAGE,
            "cdnStatuses": [item["status"] for item in checks],
            "cryptoCode": crypto.get("json", {}).get("code"),
            "cryptoMessage": crypto.get("json", {}).get("message"),
            "apkPresent": False, "arm64Verified": False}


def local_manifest(apk: Path, manager: Path, work: Path) -> dict:
    """Reuse a temporary copy of Manager's bounded binary-XML parser.

    The copied parser additionally reads typed android:versionName. Neither the
    Manager source, exported patch, binaries nor build receipts are modified.
    """
    inputs = {"internal/apk/apk.go": manager / "internal/apk/apk.go",
              "internal/errs/errs.go": manager / "internal/errs/errs.go"}
    original = inputs["internal/apk/apk.go"].read_text()
    fields = "VersionCode int64"
    branch = '} else if strs.get(nameIndex) == "package" {'
    if original.count(fields) != 1 or original.count(branch) != 1:
        raise RuntimeError("Manager APK parser changed; review the versionName helper before use")
    extended = original.replace(fields, fields + "\n\tVersionName string", 1).replace(
        branch,
        '} else if resID == 0x0101021C || strs.get(nameIndex) == "versionName" {\n'
        '\t\t\tif dataType != typeString { return nil, fmt.Errorf("versionName is not a string") }\n'
        '\t\t\tm.VersionName = strs.get(value)\n\t\t' + branch, 1)
    source = '''package main
import ("encoding/json"; "fmt"; "os"; "modavd/internal/apk")
func main() {
    result, err := apk.ReadManifest(os.Args[1])
    if err != nil { fmt.Fprintln(os.Stderr, err); os.Exit(1) }
    if err := json.NewEncoder(os.Stdout).Encode(result); err != nil { panic(err) }
}
'''
    with tempfile.TemporaryDirectory(prefix=".apk-parser-", dir=work) as temporary:
        helper = Path(temporary)
        for name, path in inputs.items():
            target = helper / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(extended if name == "internal/apk/apk.go" else path.read_text())
        (helper / "go.mod").write_text("module modavd\n\ngo 1.26\n")
        (helper / "main.go").write_text(source)
        env = os.environ.copy()
        env["GOTOOLCHAIN"] = "go1.26.5"  # Same pinned toolchain as the Manager build.
        completed = subprocess.run(["go", "run", ".", str(apk)], cwd=helper, env=env,
                                   capture_output=True, text=True, timeout=60)
        if completed.returncode:
            raise RuntimeError("temporary Manager manifest parser failed: " + completed.stderr.strip())
        result = json.loads(completed.stdout)
    return {"package": result["Package"], "versionCode": result["VersionCode"],
            "versionName": result["VersionName"],
            "parser": {"source": str(inputs["internal/apk/apk.go"]),
                       "sourceSha256": hashlib.sha256(original.encode()).hexdigest(),
                       "temporaryExtendedParserSha256": hashlib.sha256(extended.encode()).hexdigest(),
                       "extension": "temporary copy reads typed android:versionName (0x0101021c); Manager source unchanged"}}


def inspect_local(root: Path, apk: Path, manager: Path) -> dict:
    root = root.expanduser().resolve()
    marker = root / ".modavd-game-cache.json"
    if not marker.is_file() or json.loads(marker.read_text()) != {"schema": 1, "gameId": GAME_ID, "channel": "bilibili"}:
        raise RuntimeError("local inspection requires this tool's existing owned game cache")
    apk = apk.expanduser().resolve(strict=True)
    before = apk.stat()
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeError("local APK is not a regular file")
    space(root, 1024**2)
    with apk.open("rb") as source:
        checksum = hashlib.file_digest(source, "sha256").hexdigest()
    manifest = local_manifest(apk, manager.expanduser().resolve(strict=True), root)
    libraries = []
    seen, duplicates, unsafe = set(), [], []
    expected = {"arm64-v8a": (64, 183), "armeabi-v7a": (32, 40), "armeabi": (32, 40),
                "x86": (32, 3), "x86_64": (64, 62)}
    with zipfile.ZipFile(apk) as bundle:
        members = bundle.infolist()
        expanded = sum(member.file_size for member in members)
        if len(members) > 100000 or expanded > 8 * 1024**3:
            raise RuntimeError("APK exceeds the bounded ZIP inspection budget")
        for member in members:
            name = member.filename
            path = PurePosixPath(name)
            if name in seen:
                duplicates.append(name)
            seen.add(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name or stat.S_ISLNK(member.external_attr >> 16):
                unsafe.append(name)
            if member.is_dir():
                continue
            # Consume every entry to EOF: zipfile verifies each entry's CRC.
            # No archive member, and no copy of the 1.8 GiB APK, is extracted.
            with bundle.open(member) as source:
                first = source.read(1024**2)
                native = name.endswith(".so") or first.startswith(b"\x7fELF")
                digest = hashlib.sha256() if native else None
                count = len(first)
                if digest:
                    digest.update(first)
                while data := source.read(1024**2):
                    count += len(data)
                    if count > member.file_size:
                        raise RuntimeError(f"ZIP member exceeds declared size: {name}")
                    if digest:
                        digest.update(data)
            if count != member.file_size:
                raise RuntimeError(f"ZIP member length mismatch: {name}")
            if native:
                header = first[:64]
                valid = len(header) >= 52 and header[:4] == b"\x7fELF" and header[4] in (1, 2) and header[5] in (1, 2)
                bits = header[4] * 32 if valid else None
                endian = ("little" if header[5] == 1 else "big") if valid else None
                machine = int.from_bytes(header[18:20], endian) if valid else None
                elf_type = int.from_bytes(header[16:18], endian) if valid else None
                abi = path.parts[1] if len(path.parts) >= 3 and path.parts[0] == "lib" else None
                matches = valid and (bits, machine) == expected.get(abi) and endian == "little" and elf_type == 3
                libraries.append({"path": name, "abiDirectory": abi, "bytes": count,
                                  "sha256": digest.hexdigest(), "zipCrc32": f"{member.CRC:08x}",
                                  "elfHeaderHex": header.hex(), "validElfHeader": valid,
                                  "bits": bits, "byteOrder": endian, "machine": machine,
                                  "elfType": elf_type, "matchesAbiDirectory": matches})
    after = apk.stat()
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    if identity(before) != identity(after):
        raise RuntimeError("APK changed during inspection; no success report written")
    official = json.loads((root / "source.json").read_text())
    arm64 = [item for item in libraries if item["abiDirectory"] == "arm64-v8a"]
    abi_counts = {abi: sum(item["abiDirectory"] == abi for item in libraries)
                  for abi in sorted({item["abiDirectory"] for item in libraries if item["abiDirectory"]})}
    report = {"schema": 1, "inspectedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
              "mode": "local_read_only_static_inspection", "apkPath": str(apk),
              "apkBytes": before.st_size, "apkSha256": checksum, "inputUnchangedDuringInspection": True,
              "manifest": manifest,
              "zip": {"allEntriesReadAndCrcVerified": True, "entries": len(members),
                      "expandedBytes": expanded, "duplicateNames": duplicates, "unsafeNames": unsafe},
              "nativeLibraries": libraries, "nativeLibraryCountsByAbi": abi_counts,
              "arm64NativeLibrariesVerified": bool(arm64) and all(item["matchesAbiDirectory"] for item in arm64),
              "allNativeLibrariesMatchAbiDirectory": all(item["matchesAbiDirectory"] for item in libraries),
              "officialMetadataComparison": {"source": str(root / "source.json"),
                  "sizeMatches": before.st_size == official["sizeBytesReportedByOfficialApi"],
                  "packageMatches": manifest["package"] == official["packageReportedByOfficialApi"],
                  "versionNameMatches": manifest["versionName"] == official["versionReportedByOfficialApi"],
                  "filenameMatches": apk.name in [Path(urllib.parse.urlsplit(url).path).name for url in official["officialDownloadUrls"]],
                  "officialApkSha256PublishedInCachedMetadata": False},
              "apkCryptographicSignatureVerification": "not_performed_by_this_static_inspector",
              "guestInstallation": "not_performed", "gameLaunchAndAccountLogin": "not_performed"}
    write_json(root / "apk-inspection.json", report)
    return {"report": str(root / "apk-inspection.json"), "apkPath": str(apk),
            "bytes": before.st_size, "sha256": checksum, "manifest": manifest,
            "nativeLibraryCountsByAbi": abi_counts, "arm64Verified": report["arm64NativeLibrariesVerified"],
            "zipCrcVerified": True, "officialMetadataComparison": report["officialMetadataComparison"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("official", "local"), nargs="?", default="official")
    parser.add_argument("--output", type=Path, default=REPO / ".macos-work/game")
    parser.add_argument("--apk", type=Path, help="local APK to inspect without copying or installation")
    parser.add_argument("--manager-source", type=Path, default=REPO / ".macos-work/manager")
    args = parser.parse_args()
    if args.command == "local" and args.apk is None:
        parser.error("local inspection requires --apk")
    result = inspect_local(args.output, args.apk, args.manager_source) if args.command == "local" else inspect(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, zipfile.BadZipFile, subprocess.SubprocessError) as error:
        raise SystemExit(f"error: {error}")
