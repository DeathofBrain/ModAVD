#!/usr/bin/env python3
"""Prepare verbatim production-function contracts with explicit reader doubles.
This generator does not compile, run a helper, initialize a GPU, or operate a guest.
"""
import argparse
import hashlib
import json
from pathlib import Path


def definition(text, signature):
    start = text.index(signature)
    opened = text.index('{', start)
    depth = 1
    end = opened + 1
    while depth:
        if text[end] == '{':
            depth += 1
        elif text[end] == '}':
            depth -= 1
        end += 1
    return text[start:end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True, help='gfxstream tree, not AOSP parent')
    parser.add_argument('--output', type=Path, required=True, help='New output directory')
    args = parser.parse_args()
    source = args.source_root.resolve()
    frame = (source / 'host/frame_buffer.cpp').read_text()
    color = (source / 'host/color_buffer.cpp').read_text()
    checked = 'bool ColorBuffer::Impl::readToBytesChecked(' in color
    impl = definition(color, ('bool ColorBuffer::Impl::readToBytesChecked(' if checked
                              else 'void ColorBuffer::Impl::readToBytes('))
    legacy = definition(color, 'void ColorBuffer::readToBytes(')
    checked_public = definition(color, 'bool ColorBuffer::readToBytesChecked(') if checked else ''
    helper = definition(frame, 'static bool readbackColorBufferWithoutWorker(')
    start = frame.index('                    bool updated = false;')
    terminal = '                        continue;\n                    }'
    end = frame.index(terminal, start) + len(terminal)
    publish = frame[start:end]
    # The real postImpl rejection branch and callback call are copied into a
    # single-iteration loop; renderer/device/locks remain explicit doubles.
    callback = '                doPostCallback(iter.second.img, iter.first);'
    assert callback in frame[end:end + 200]
    def declaration(body, prefix):
        return body.split('{', 1)[0].replace(prefix, '').strip() + ';'
    preamble = r'''
#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <memory>
#include <string>
#include <utility>
#include <vector>
#ifndef GFXSTREAM_ENABLE_HOST_GLES
#define GFXSTREAM_ENABLE_HOST_GLES 1
#endif
#define GFXSTREAM_FATAL(...) ((void)0)
#define GFXSTREAM_ERROR(...) ((void)0)
enum class GfxstreamFormat { R8G8B8A8_UNORM, R8G8B8X8_UNORM, B8G8R8A8_UNORM, R8_UNORM };
static std::string ToString(GfxstreamFormat) { return "reader-double"; }
struct Reader {
    bool result = true, partial = false;
    unsigned calls = 0;
    std::vector<unsigned char> payload = std::vector<unsigned char>(16);
    bool read(void* out, uint64_t bytes) {
        ++calls;
        uint64_t count = result ? bytes : partial ? std::min<uint64_t>(bytes, 7) : 0;
        std::memcpy(out, payload.data(), count);
        return result;
    }
    bool readPixels(int, int, int, int, GfxstreamFormat, void* out, uint64_t bytes) { return read(out, bytes); }
    bool readToBytes(int, int, int, int, void* out, uint64_t bytes) { return read(out, bytes); }
};
class ColorBuffer {
public:
    class Impl {
    public:
        Reader* mColorBufferGl = nullptr;
        Reader* mColorBufferVk = nullptr;
        void touch() {}
        IMPL_DECL
    };
    Impl* mImpl;
    unsigned width = 2, height = 2;
    GfxstreamFormat format = GfxstreamFormat::R8G8B8A8_UNORM;
    unsigned getWidth() const { return width; }
    unsigned getHeight() const { return height; }
    GfxstreamFormat getFormat() const { return format; }
    LEGACY_DECL
    CHECKED_DECL
};
'''
    preamble = preamble.replace('IMPL_DECL', declaration(impl, 'ColorBuffer::Impl::'))
    preamble = preamble.replace('LEGACY_DECL', declaration(legacy, 'ColorBuffer::'))
    preamble = preamble.replace('CHECKED_DECL', declaration(checked_public, 'ColorBuffer::') if checked else '')
    glue = r'''
struct OnPost {
    unsigned char* img;
    std::vector<unsigned char> readbackScratch = std::vector<unsigned char>(16);
    unsigned width = 2, height = 2;
    bool readBgra = false;
};
static int m_readbackWithoutWorkerLock;
struct AutoLock { explicit AutoLock(int&) {} }; // lock behavior is not tested here
static unsigned callbacks;
static void doPostCallback(void*, uint32_t) { ++callbacks; }
static void publish(ColorBuffer* buffer, OnPost& state) {
    for (unsigned once = 0; once < 1; ++once) {
        std::pair<uint32_t, OnPost&> iter(0, state);
        std::shared_ptr<ColorBuffer> cb(buffer, [](ColorBuffer*){});
PUBLISH_BLOCK
        doPostCallback(iter.second.img, iter.first);
    }
}
static unsigned failures;
static void check(bool passed, const char* id) {
    if (!passed) { ++failures; std::fprintf(stderr, "FAIL: %s\n", id); }
}
int main() {
    Reader gl, vk;
    for (unsigned i = 0; i < 16; ++i) gl.payload[i] = vk.payload[i] = (i * 17 + 3) & 255;
    ColorBuffer::Impl impl; impl.mColorBufferVk = &vk;
    ColorBuffer cb; cb.mImpl = &impl;
    std::vector<unsigned char> cache(16, 0xa5);
    OnPost state{cache.data()};
    for (bool partial : {false, true}) {
        vk.result = false; vk.partial = partial; vk.calls = 0;
        std::fill(cache.begin(), cache.end(), 0xa5); callbacks = 0;
        publish(&cb, state);
        check(vk.calls == 1, "VK failure reader reached");
        check(callbacks == 0, partial ? "VK partial failure no callback" : "VK failure no callback");
        check(std::all_of(cache.begin(), cache.end(), [](auto b){return b == 0xa5;}),
              partial ? "VK partial failure preserves cache" : "VK failure preserves cache");
    }
#if GFXSTREAM_ENABLE_HOST_GLES
    impl.mColorBufferGl = &gl;
    for (bool partial : {false, true}) {
        gl.result = false; gl.partial = partial; gl.calls = 0; vk.calls = 0;
        std::fill(cache.begin(), cache.end(), 0xa5); callbacks = 0;
        publish(&cb, state);
        check(gl.calls == 1 && vk.calls == 0, "GL failure selected instead of VK");
        check(callbacks == 0, partial ? "GL partial failure no callback" : "GL failure no callback");
        check(std::all_of(cache.begin(), cache.end(), [](auto b){return b == 0xa5;}),
              partial ? "GL partial failure preserves cache" : "GL failure preserves cache");
    }
    impl.mColorBufferGl = nullptr;
#endif
    vk.result = true; vk.partial = false;
    for (auto format : {GfxstreamFormat::R8G8B8A8_UNORM, GfxstreamFormat::R8G8B8X8_UNORM,
                         GfxstreamFormat::B8G8R8A8_UNORM}) for (bool bgra : {false, true}) {
        cb.format = format; state.readBgra = bgra; callbacks = 0;
        auto expected = vk.payload;
        for (unsigned i = 0; i < 16; i += 4) {
            if ((format == GfxstreamFormat::B8G8R8A8_UNORM) != bgra) std::swap(expected[i], expected[i + 2]);
            if (format == GfxstreamFormat::R8G8B8X8_UNORM) expected[i + 3] = 255;
        }
        publish(&cb, state);
        check(callbacks == 1 && cache == expected, "supported source format publishes exact converted bytes");
    }
    for (unsigned rejected = 0; rejected < 3; ++rejected) {
        cb.format = rejected == 0 ? GfxstreamFormat::R8_UNORM : GfxstreamFormat::R8G8B8A8_UNORM;
        cb.width = rejected == 1 ? 1 : 2; cb.height = rejected == 2 ? 1 : 2;
        callbacks = vk.calls = 0; auto before = cache;
        publish(&cb, state);
        check(callbacks == 0 && vk.calls == 0 && cache == before, "unsupported/dimension rejection before reader");
    }
    std::printf("{\"pass\":%s,\"failures\":%u,\"functions_verbatim\":true,\"renderer_and_readers_are_doubles\":true,\"gpu_initialized\":false}\n",
                failures ? "false" : "true", failures);
    return failures ? 1 : 0;
}
'''
    glue = glue.replace('PUBLISH_BLOCK', publish)
    program = preamble + '\n' + impl + '\n' + legacy + '\n' + checked_public + '\n' + helper + '\n' + glue
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'contract.cpp').write_text(program)
    sha = lambda text: hashlib.sha256(text.encode()).hexdigest()
    manifest = {'source_root': str(source), 'checked_api_present': checked,
                'production_function_sha256': {name: sha(body) for name, body in
                    [('impl', impl), ('legacy', legacy), ('checked_public', checked_public),
                     ('readback_helper', helper), ('post_publish_block', publish)]},
                'contract_cpp_sha256': sha(program), 'compiled': False, 'run': False,
                'limits': 'Verbatim function bodies with Reader/ColorBuffer/device/lock doubles; no GPU, renderer, ABI, threads, driver fault or full FrameBuffer coverage.',
                'future_commands': [
                    ['clang++', '-std=c++17', '-DGFXSTREAM_ENABLE_HOST_GLES=1', 'contract.cpp', '-o', 'contract-gles'],
                    ['clang++', '-std=c++17', '-DGFXSTREAM_ENABLE_HOST_GLES=0', 'contract.cpp', '-o', 'contract-nogles']],
                'negative_control': 'Baseline must compile and fail named no-callback/cache-preservation checks under explicit false/partial reader outcomes; compile failure is not a valid negative control.'}
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'output': str(args.output), 'prepared_only': True, 'checked_api_present': checked}))


if __name__ == '__main__':
    main()
