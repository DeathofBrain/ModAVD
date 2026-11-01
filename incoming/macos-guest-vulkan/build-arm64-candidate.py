#!/usr/bin/env python3
"""Build an offline candidate only; never push, mount, restart or edit a guest."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
from arm64_unwind import rebuild_unwind

ORIGINAL_SHA256 = "d6188011ed2ed58a1d69adfb04246abaa2595c0f15565a22da83f7751a3d6828"
CANDIDATE_SHA256 = "73ddfe0935826feef1f1a78d5a70af3ee7fa83584d2efaf5bad6ee91f36eb613"
CAVE = 0x3C86CC
CAVE_END = 0x3C87F0
ADDRESSES = {"mutex_lock": 0x3FA510, "mutex_unlock": 0x3FA540,
             "impl_bind": 0x213D70, "impl_bind2": 0x213F48,
             "encoder_bind": 0x3FAF00, "encoder_bind2": 0x3FAF20}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def word(*words):
    return b"".join(struct.pack("<I", value) for value in words)


def branch(source, target):
    delta = target - source
    if delta % 4 or not -(1 << 27) <= delta < (1 << 27):
        raise ValueError("unaligned/out-of-range AArch64 branch")
    return word(0x14000000 | ((delta // 4) & 0x3FFFFFF))


def build_wrappers(toolchain, here):
    with tempfile.TemporaryDirectory(prefix="modavd-arm-vulkan-") as temporary:
        obj = Path(temporary) / "wrappers.o"
        linked = Path(temporary) / "wrappers.elf"
        raw = Path(temporary) / "wrappers.bin"
        subprocess.run([str(toolchain / "clang"), "--target=aarch64-linux-android34", "-c",
                        str(here / "arm64-bindlock.S"), "-o", str(obj)], check=True)
        subprocess.run([str(toolchain / "ld.lld"), "-m", "aarch64elf", "-Ttext=" + hex(CAVE),
                        "-e", "wrapper_bind", *["--defsym=" + name + "=" + hex(address)
                        for name, address in ADDRESSES.items()], str(obj), "-o", str(linked)],
                       check=True)
        subprocess.run([str(toolchain / "llvm-objcopy"), "--dump-section", ".text=" + str(raw),
                        str(linked)], check=True)
        symbols = subprocess.check_output([str(toolchain / "llvm-nm"), "--defined-only", str(linked)],
                                          text=True)
        positions = {line.split()[2]: int(line.split()[0], 16) for line in symbols.splitlines()
                     if len(line.split()) == 3}
        return raw.read_bytes(), positions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path,
                        default=Path(".macos-work/aosp/prebuilts/clang/host/darwin-x86/clang-r596125/bin"))
    args = parser.parse_args()
    original = args.original.read_bytes()
    if digest(original) != ORIGINAL_SHA256:
        raise ValueError("unsupported guest library SHA-256; no output written")
    if original[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", original, 18)[0] != 183:
        raise ValueError("expected ELF64 little-endian AArch64")
    wrappers, positions = build_wrappers(args.toolchain.resolve(), Path(__file__).resolve().parent)
    if len(wrappers) > CAVE_END - CAVE or positions["wrapper_bind"] != CAVE:
        raise ValueError("wrapper code does not fit the retired dynCheck body")
    data = bytearray(original)
    edits = []

    def put(offset, blob, expected, name):
        if original[offset:offset + len(expected)] != expected:
            raise ValueError("original bytes mismatch: " + name)
        data[offset:offset + len(blob)] = blob
        edits.append({"name": name, "offset": hex(offset), "bytes": len(blob),
                      "original_sha256": digest(original[offset:offset + len(blob)]),
                      "replacement_hex": blob.hex()})

    put(0x3EBD10, branch(0x3EBD10, 0x3EA5A0), word(0xD10143FF), "KHR entry to core entry")
    put(0x3C86C8, branch(0x3C86C8, 0x3EA5A0), word(0xD10183FF), "KHR dynCheck to core entry")
    cave_original = original[CAVE:CAVE_END]
    if digest(cave_original) != "9ae7f5989678fbf36a36fe545bec25a3463add4c714b2b5a2a2f7cb615d96ab9":
        raise ValueError("retired dynCheck bytes mismatch")
    put(CAVE, wrappers + word(0xD503201F) * ((CAVE_END - CAVE - len(wrappers)) // 4),
        cave_original, "bind wrappers in retired dynCheck")
    # The original validators have only one error result (-2) and one tail call
    # each. Turn the success tail call into zero + return; retain error paths.
    put(0x213F24, word(0x52800000, 0xD65F03C0) + word(0xD503201F) * 5,
        bytes.fromhex("e00301aae10303aae20304aae30305aae40306aa25008052f19b0714"),
        "bind validator success, no host call while locked")
    put(0x214118, word(0xA9414FF4, 0x52800000, 0xA8C257F6, 0xD65F03C0) + word(0xD503201F) * 4,
        bytes.fromhex("f44f41a9e00301aae10303aae203042ae30305aa24008052f657c2a87b9b0714"),
        "bind2 validator success, restored original stack")
    for address, target, expected, name in (
            (0x213D68, positions["wrapper_bind"], word(0xF9400400, 0x14000001), "bind forwarder"),
            (0x213F40, positions["wrapper_bind2"], word(0xF9400400, 0x14000001), "bind2 forwarder"),
            (0x214148, positions["wrapper_bind2"], word(0xF9400400, 0x17FFFF7F), "bind2KHR forwarder")):
        put(address, branch(address, target) + word(0xD503201F), expected, name)
    if len(data) != len(original):
        raise ValueError("candidate changed ELF size")
    unwind = rebuild_unwind(original, data)
    if digest(data) != CANDIDATE_SHA256:
        raise ValueError("candidate result hash mismatch; no output written")
    report = {"scope": "offline experimental ARM candidate; not installed or enabled in Manager",
              "original_sha256": ORIGINAL_SHA256, "candidate_sha256": digest(data),
              "file_bytes": len(data), "elf_machine": "EM_AARCH64", "file_text_delta": 0,
              "wrappers_bytes": len(wrappers), "wrapper_addresses": positions,
              "wrappers_sha256": digest(wrappers), "edits": edits, "unwind": unwind,
              "limits": ["No live guest or full Vulkan correctness/stress test has passed yet.",
                         "Native instruction/ABI checks do not establish Android loader or Vulkan protocol compatibility.",
                         "Rebuilt unwind tables require independent CFI and real Android unwinder verification before production use."]}
    with args.output.open("xb") as output:
        output.write(data)
    with args.report.open("x") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
