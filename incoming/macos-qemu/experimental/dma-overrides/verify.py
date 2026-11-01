#!/usr/bin/env python3
"""Run exact modified DMA consumer functions/cases against checked interface shims."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


def block(source, start):
    first = source.index(start)
    brace = source.index("{", first)
    level = 0
    for pos in range(brace, len(source)):
        if source[pos] == "{":
            level += 1
        elif source[pos] == "}":
            level -= 1
            if not level:
                return source[first:pos+1]
    raise ValueError("unterminated source block")


STUB = r'''#pragma once
#include <memory>
#include <cstdint>
#include <cassert>
namespace gfxstream { namespace host {
enum class GfxstreamFormat { R8G8B8A8_UNORM, YUV, RGBA16F };
struct IColorBuffer { virtual ~IColorBuffer() = default; };
struct ColorBuffer : IColorBuffer {
    GfxstreamFormat format = GfxstreamFormat::R8G8B8A8_UNORM;
    int updates=0, reads=0; int* depth=nullptr;
    uint32_t getWidth() const { return 64; }
    uint32_t getHeight() const { return 64; }
    GfxstreamFormat getFormat() const { return format; }
    bool updateFromBytes(int,int,int,int,GfxstreamFormat,void* p) {
        assert(p && depth && *depth==1 && modavd_dma_precise_pixel_transfer_active()); ++updates; return true;
    }
    void readToBytes(int,int,int,int,GfxstreamFormat,void* p,uint64_t n) {
        assert(p && depth && *depth==1 && n==64 && modavd_dma_precise_pixel_transfer_active()); ++reads;
    }
};
struct FrameBuffer {
    std::shared_ptr<ColorBuffer> cb=std::make_shared<ColorBuffer>(); int depth=0;
    FrameBuffer() { cb->depth=&depth; }
    void lock() { assert(depth==0); ++depth; }
    void unlock() { assert(depth==1); --depth; }
    static FrameBuffer* getFB() { static FrameBuffer instance; return &instance; }
    std::shared_ptr<IColorBuffer> findColorBuffer(uint32_t h) { return h==1?cb:nullptr; }
    bool updateColorBufferDeprecated(uint32_t,int,int,int,int,uint32_t,uint32_t,void* p) {
        assert(p && depth==0); ++cb->updates; return true;
    }
    void readColorBufferDeprecated(uint32_t,int,int,int,int,uint32_t,uint32_t,void* p,uint32_t) {
        assert(p && depth==0); ++cb->reads;
    }
};
} }
''' 


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    manifest = json.loads((here / "manifest.json").read_text())
    for item in manifest["overrides"]:
        if hashlib.sha256(Path(item["original"]).read_bytes()).hexdigest() != item["original_sha256"]:
            raise ValueError("original source changed: " + item["original"])
    gl = (here / "host/gl/gles2_dec/gles_v2_decoder.cpp").read_text()
    rc = (here / "host/render_control.cpp").read_text()
    decoder = (here / "host/renderControl_dec/renderControl_dec.cpp").read_text()
    harness = (here / "native-test.cpp.in").read_text()
    replacements = {
        "@@GL_FUNCTIONS@@": "\n".join(block(gl, "void GLESv2Decoder::" + name + "(") for name in (
            "s_glMapBufferRangeDMA", "s_glUnmapBufferDMA")),
        "@@RC_FUNCTIONS@@": "\n".join(block(rc, "static int " + name + "(") for name in (
            "rcUpdateColorBufferDMA", "rcReadColorBufferDMA")) + "\n" +
            block(rc, 'extern "C" int modavd_rc_color_buffer_dma_v1('),
        "@@RC_CASES@@": "\n".join(block(decoder, "case OP_" + name + ":") for name in (
            "rcUpdateColorBufferDMA", "rcReadColorBufferDMA")),
    }
    for marker, content in replacements.items():
        harness = harness.replace(marker, content)
    source_root = Path(manifest["overrides"][0]["original"]).parents[2]
    with tempfile.TemporaryDirectory(prefix="modavd-dma-sized-") as temporary:
        temp = Path(temporary)
        (temp / "host").mkdir()
        (temp / "host/frame_buffer.h").write_text(STUB)
        (temp / "host/color_buffer.h").write_text('#include "frame_buffer.h"\n')
        (temp / "test.cpp").write_text(harness)
        binary = temp / "verify"
        command = ["xcrun", "--sdk", "macosx", "clang++", "-std=c++17", "-O1", "-g",
                   "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fvisibility=hidden",
                   "-I"+str(temp), "-I"+str(here / "include"),
                   "-I"+str(source_root / "host/common/include"),
                   "-I"+str(source_root / "host/include"),
                   str(temp / "test.cpp"), str(here / "host/common/dma_device.cpp"), "-o", str(binary)]
        compiled = subprocess.run(command, text=True, capture_output=True, timeout=60)
        if compiled.returncode:
            raise RuntimeError(compiled.stderr)
        result = subprocess.run([binary], text=True, capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError(result.stdout + "\n" + result.stderr)
    checks = json.loads(result.stdout)
    report = {"utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "checks": checks, "asan_ubsan": True, "compile_stderr": compiled.stderr,
              "run_stderr": result.stderr,
              "sha256": {str(p.relative_to(here)): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in here.rglob("*") if p.is_file() and "__pycache__" not in str(p)},
              "limits": ["Executes exact production consumer functions and decoder case bodies with GL/resource/stream interface shims.",
                         "Does not establish a real GL/Vulkan renderer, QEMU callback, loader handshake, GPU output or ingress completeness.",
                         "Only exact target/request RGBA8 uses a precise pixel borrow; all other formats retain the original unsized export.",
                         "No original source, package, emulator, guest or ADB service modified."]}
    with args.output.open("x") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
