#!/usr/bin/env python3
"""Run the actual Media dispatchers with counted codec shims under ASan/UBSan.

The real parsers and public dispatcher/plugin declarations are used. Heavy codec
implementations, logging and snapshot Stream I/O are shims. This verifies plugin
ownership at dispatch/teardown; it does not claim a native VideoToolbox exercise.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[4]
MEDIA = Path("android/emu/media/src/android/emulation")
PRIVATE = ROOT / ".macos-work/qemu-patch-dev"
ORIGINAL = ROOT / ".macos-work/aosp/external/qemu"
PUBLIC = ROOT / ".macos-work/aosp/hardware/google/aemu/host-common/include"
EXPERIMENT = Path(__file__).resolve().parent
DISPATCHERS = ["MediaVpxDecoder.cpp", "MediaH264DecoderDefault.cpp", "MediaHevcDecoderDefault.cpp"]
PARSERS = ["VpxPingInfoParser.cpp", "H264PingInfoParser.cpp", "HevcPingInfoParser.cpp"]


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a fresh evidence path")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    build = args.output.parent / (args.output.stem + "-build")
    build.mkdir(exist_ok=False)
    shim = build / "shim"
    write(shim, "test-counters.h", """#pragma once
#include <array>
#include <functional>
namespace tracked {
inline std::array<int,3> live{}, created{}, destroyed{};
inline std::function<void()> onDelete;
inline void born(int i) { ++live[i]; ++created[i]; }
inline void gone(int i) { --live[i]; ++destroyed[i]; if(onDelete) onDelete(); }
}
""")
    write(shim, "aemu/base/logging/Log.h", "#pragma once\n#define dfatal(...) ((void)0)\n")
    write(shim, "android/base/system/System.h", "#pragma once\n#include <cstdlib>\n")
    write(shim, "aemu/base/files/Stream.h", """#pragma once
#include <cstdint>
#include <cstdlib>
#include <vector>
namespace android { namespace base {
struct Stream {
    std::vector<uint64_t> words; size_t pos=0;
    void putBe32(uint32_t n) { words.push_back(n); }
    void putBe64(uint64_t n) { words.push_back(n); }
    size_t write(const void*, size_t) { std::abort(); }
    size_t read(void*, size_t) { std::abort(); }
    uint32_t getBe32() { return static_cast<uint32_t>(getBe64()); }
    uint64_t getBe64() { if(pos>=words.size()) std::abort(); return words[pos++]; }
};
}}
""")
    write(shim, "host-common/MediaVpxDecoderGeneric.h", """#pragma once
#include "test-counters.h"
#include "host-common/MediaVpxDecoderPlugin.h"
namespace android { namespace emulation {
class MediaVpxDecoderGeneric : public MediaVpxDecoderPlugin {
public:
    MediaVpxDecoderGeneric(VpxPingInfoParser, MediaCodecType) { tracked::born(0); }
    ~MediaVpxDecoderGeneric() override { tracked::gone(0); }
    void initVpxContext(void*) override {}
    void destroyVpxContext(void*) override {}
    void decodeFrame(void*) override {}
    void flush(void*) override {}
    void getImage(void*) override {}
    void sendMetadata(void*) override {}
    int type() const override { return PLUGIN_TYPE_GENERIC; }
};
}}
""")
    for codec, index, prefix in [("H264", 1, "host-common"), ("Hevc", 2, "android/emulation")]:
        write(shim, f"{prefix}/Media{codec}DecoderGeneric.h", f"""#pragma once
#include "test-counters.h"
#include "{prefix}/{codec}PingInfoParser.h"
#include "{prefix}/Media{codec}DecoderPlugin.h"
namespace android {{ namespace emulation {{
class Media{codec}DecoderGeneric : public Media{codec}DecoderPlugin {{
    uint64_t mId; {codec}PingInfoParser mParser;
public:
    Media{codec}DecoderGeneric(uint64_t id, {codec}PingInfoParser parser):mId(id),mParser(parser) {{ tracked::born({index}); }}
    ~Media{codec}DecoderGeneric() override {{ tracked::gone({index}); }}
    void init{codec}Context(void*) override {{}}
    void reset(void*) override {{}}
    Media{codec}DecoderPlugin* clone() override {{ return new Media{codec}DecoderGeneric(mId,mParser); }}
    void destroy{codec}Context() override {{}}
    void decodeFrame(void*) override {{}}
    void flush(void*) override {{}}
    void getImage(void*) override {{}}
    void sendMetadata(void*) override {{}}
    int type() const override {{ return PLUGIN_TYPE_GENERIC; }}
}};
}}}}
""")
    harness = build / "cleanup.cpp"
    harness.write_text(r'''#include "test-counters.h"
#include "host-common/MediaVpxDecoder.h"
#include "host-common/MediaH264DecoderDefault.h"
#include "android/emulation/MediaHevcDecoderDefault.h"
#include <cstdio>
#include <cstdlib>
#include <memory>
using namespace android::emulation;
#define CHECK(x) do { if(!(x)){std::fprintf(stderr,"failed line %d: %s\n",__LINE__,#x);return 1;} }while(0)
int main() {
    for(int codec=0;codec<3;++codec) for(int iteration=0;iteration<40;++iteration) {
        auto type=codec==0?MediaCodecType::VP9Codec:(codec==1?MediaCodecType::H264Codec:MediaCodecType::HevcCodec);
        auto make=[&]() -> std::unique_ptr<MediaCodec> {
            if(codec==0) return std::unique_ptr<MediaCodec>(MediaVpxDecoder::create());
            if(codec==1) return std::unique_ptr<MediaCodec>(new MediaH264DecoderDefault);
            return std::unique_ptr<MediaCodec>(new MediaHevcDecoderDefault);
        };
        auto decoder=make();
        alignas(8) std::array<uint64_t,64> request{};
        std::array<uint64_t,3> ids{};
        for(int i=0;i<3;++i) {
            request.fill(0); request[0]=codec==0?uint64_t(i+1):100;
            request[1]=codec==0?200:640;
            decoder->handlePing(type,MediaOperation::InitContext,request.data());
            ids[i]=codec==0?uint64_t(i+1):request[32];
        }
        CHECK(tracked::live[codec]==3);
        alignas(8) std::array<uint64_t,64> probe{};probe[0]=~uint64_t(0);
        auto* raw=decoder.get();
        // Reentry takes mMapLock: destruction while still holding it deadlocks.
        tracked::onDelete=[&]{raw->handlePing(type,MediaOperation::Flush,probe.data());};
        request[0]=ids[0];decoder->handlePing(type,MediaOperation::DestroyContext,request.data());
        CHECK(tracked::live[codec]==2);
        if(codec!=0) {
            request[0]=ids[1];decoder->handlePing(type,MediaOperation::Reset,request.data());
            CHECK(tracked::live[codec]==2);
        }
        android::base::Stream saved;decoder->save(&saved);
        decoder.reset(); tracked::onDelete={};
        CHECK(tracked::live[codec]==0);
        decoder=make();CHECK(decoder->load(&saved));CHECK(tracked::live[codec]==2);
        raw=decoder.get();tracked::onDelete=[&]{raw->handlePing(type,MediaOperation::Flush,probe.data());};
        decoder.reset();tracked::onDelete={};CHECK(tracked::live[codec]==0);
        decoder=make();decoder.reset();CHECK(tracked::live[codec]==0);
    }
    for(int i=0;i<3;++i) CHECK(tracked::created[i]==tracked::destroyed[i]);
    std::printf("{\"cycles\":120,\"created\":[%d,%d,%d],\"destroyed\":[%d,%d,%d],\"live\":[%d,%d,%d]}\n",
        tracked::created[0],tracked::created[1],tracked::created[2],
        tracked::destroyed[0],tracked::destroyed[1],tracked::destroyed[2],
        tracked::live[0],tracked::live[1],tracked::live[2]);
}
''')
    clang = subprocess.check_output(["xcrun", "-f", "clang++"], text=True).strip()
    sdk = subprocess.check_output(["xcrun", "--show-sdk-path"], text=True).strip()
    common = [clang, "-std=c++17", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
              "-isysroot", sdk, "-include", "cinttypes", "-I" + str(shim)]
    results = []
    for variant, source in [("patched", PRIVATE), ("original-negative-control", ORIGINAL)]:
        includes = [EXPERIMENT / "include", source / "android/emu/media/src", PUBLIC]
        if variant != "patched":
            # Ensure the original defaulted declarations pair with original code.
            original_headers = build / "original-headers"
            for rel, original in [
                ("host-common/MediaH264DecoderDefault.h", PUBLIC / "host-common/MediaH264DecoderDefault.h"),
                ("android/emulation/MediaHevcDecoderDefault.h", ORIGINAL / MEDIA / "MediaHevcDecoderDefault.h"),
            ]:
                target = original_headers / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(original.read_bytes())
            includes.insert(0, original_headers)
        binary = build / variant
        command = common + ["-I" + str(p) for p in includes]
        command += [str(source / MEDIA / name) for name in DISPATCHERS + PARSERS]
        command += [str(harness), "-o", str(binary)]
        compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
        (build / (variant + ".compile.log")).write_text(compiled.stdout + compiled.stderr)
        if compiled.returncode:
            raise RuntimeError(f"{variant} compile failed; see build log")
        env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0", UBSAN_OPTIONS="halt_on_error=1")
        executed = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30, env=env)
        result = {"variant": variant, "returncode": executed.returncode,
                  "stdout": executed.stdout, "stderr": executed.stderr,
                  "command": command}
        if variant == "patched":
            result["counts"] = json.loads(executed.stdout) if executed.returncode == 0 else None
        results.append(result)
    passed = results[0]["returncode"] == 0 and results[1]["returncode"] != 0
    report = {"pass": passed, "scope": "actual dispatchers and parsers; counted codec shims",
              "results": results, "sources": {
                  str(PRIVATE / MEDIA / name): hashlib.sha256((PRIVATE / MEDIA / name).read_bytes()).hexdigest()
                  for name in DISPATCHERS}}
    contract = Path(sdk) / "System/Library/Frameworks/VideoToolbox.framework/Headers/VTDecompressionSession.h"
    report["sdk_contract"] = {"path": str(contract),
                              "sha256": hashlib.sha256(contract.read_bytes()).hexdigest(),
                              "decode_flags": 0, "helper_override_required": False}
    for name in ["manifest.json", "dispatcher-cleanup.patch"]:
        path = EXPERIMENT / name
        report[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"pass": passed, "counts": results[0].get("counts"),
                      "negative_control_returncode": results[1]["returncode"]}))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
