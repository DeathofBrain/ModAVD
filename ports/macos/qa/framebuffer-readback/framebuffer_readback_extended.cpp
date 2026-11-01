// Native FrameBuffer format/rejection contract. No guest and no failure injection.
#include "frame_buffer.h"
#include "gfxstream/host/features.h"
#include "render_thread_info.h"
#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>

using namespace gfxstream::host;
constexpr unsigned W = 64, H = 48, Bytes = W * H * 4;
struct Capture {
    std::mutex lock;
    std::condition_variable changed;
    std::vector<unsigned char> pixels;
    unsigned calls = 0, nullCalls = 0;
    bool hasPixels = false;
};
static void require(bool ok, const char* what) {
    if (!ok) { std::fprintf(stderr, "FAIL: %s\n", what); std::exit(1); }
}
static void captured(void* context, uint32_t display, int width, int height,
                     int direction, int format, int type, unsigned char* pixels) {
    require(display == 0 && width == W && height == H && direction == -1 &&
            format == 0x1908 && type == 0x1401, "callback metadata");
    auto* c = static_cast<Capture*>(context);
    std::lock_guard<std::mutex> guard(c->lock);
    c->hasPixels = pixels != nullptr;
    if (pixels) c->pixels.assign(pixels, pixels + Bytes);
    else { c->pixels.clear(); ++c->nullCalls; }
    ++c->calls;
    c->changed.notify_all();
}
static std::vector<unsigned char> rgba(unsigned w, unsigned h, unsigned frame) {
    std::vector<unsigned char> bytes(w * h * 4);
    for (unsigned y = 0; y < h; ++y) for (unsigned x = 0; x < w; ++x) {
        unsigned at = 4 * (y * w + x);
        bytes[at] = (x * 7 + y * 3 + frame * 19) & 255;
        bytes[at + 1] = (x * 5 + y * 11 + frame * 23) & 255;
        bytes[at + 2] = (x * 13 + y * 17 + frame * 29) & 255;
        bytes[at + 3] = (x * 19 + y * 23 + frame * 31) & 255;
    }
    return bytes;
}
static std::vector<unsigned char> latest(FrameBuffer* fb) {
    std::vector<unsigned char> bytes(Bytes, 0xcd);
    fb->getPixels(bytes.data(), bytes.size(), 0);
    return bytes;
}
static unsigned calls(Capture& c) {
    std::lock_guard<std::mutex> guard(c.lock);
    return c.calls;
}
static HandleType upload(FrameBuffer* fb, unsigned w, unsigned h, GfxstreamFormat format,
                         const std::vector<unsigned char>& bytes,
                         std::vector<unsigned char>* rawReadback = nullptr) {
    auto handle = fb->createColorBuffer(w, h, format);
    require(handle != 0, "create selected format/dimensions (no silent skip)");
    // Legacy upload API takes void*. Keep the immutable reference bytes separate.
    auto uploadBytes = bytes;
    require(fb->updateColorBuffer(handle, 0, 0, w, h, format, uploadBytes.data()), "GPU upload");
    std::vector<unsigned char> reference(bytes.size(), 0xcd);
    fb->readColorBuffer(handle, 0, 0, w, h, format, reference.data(), reference.size());
    if (format == GfxstreamFormat::R8G8B8X8_UNORM) {
        for (unsigned i = 0; i < bytes.size(); ++i)
            if (i % 4 != 3) require(reference[i] == bytes[i], "RGBX GPU RGB source bytes");
        // X has no alpha semantics; its raw value may differ between GL and Vulkan.
    } else require(reference == bytes, "GPU source bytes");
    if (rawReadback) *rawReadback = reference;
    return handle;
}
static void postKnown(FrameBuffer* fb, Capture& c, bool noGl, HandleType handle,
                       const std::vector<unsigned char>& expected) {
    unsigned before = calls(c);
    require(fb->post(handle), "post accepted");
    unsigned afterPost = calls(c);
    fb->flushReadPipeline(0);
    {
        std::unique_lock<std::mutex> guard(c.lock);
        require(c.changed.wait_for(guard, std::chrono::seconds(5), [&]{return c.calls > before;}),
                "callback timeout");
        if (noGl) {
            require(afterPost == before + 1 && c.calls == afterPost,
                    "one synchronous callback; no flush callback");
            require(c.hasPixels && c.pixels == expected, "direct noGL callback bytes");
        }
    }
    auto observed = latest(fb);
    if (observed != expected) {
        auto mismatch = std::mismatch(observed.begin(), observed.end(), expected.begin());
        auto at = static_cast<unsigned>(mismatch.first - observed.begin());
        std::fprintf(stderr, "MISMATCH: byte=%u observed=%u expected=%u callbacks=%u\n",
                     at, observed[at], expected[at], calls(c));
    }
    require(observed == expected, "latest GetPixels bytes");
}
int main(int argc, char** argv) {
    require(argc == 3 && (!std::strcmp(argv[1], "vulkan-only") || !std::strcmp(argv[1], "gl")),
            "mode/profile args");
    bool noGl = !std::strcmp(argv[1], "vulkan-only");
    bool initialZero = !std::strcmp(argv[2], "candidate");
    require(initialZero || !std::strcmp(argv[2], "sealed"), "profile");
    FeatureSet features{};
    features.Vulkan.setEnabled(true); features.GuestVulkanOnly.setEnabled(noGl);
    require(FrameBuffer::initialize(W, H, features, false), "initialize");
    auto* fb = FrameBuffer::getFB(); require(fb != nullptr, "framebuffer");
    require((fb->getEmulationGl() == nullptr) == noGl, "actual GL state");
    auto info = std::make_unique<RenderThreadInfo>();
    if (!noGl) info->initGl(fb->getEmulationGl());
    Capture c; unsigned frames = 0; std::vector<std::string> cases;
    if (noGl && initialZero) {
        fb->setPostCallback(captured, &c, 0, false);
        require(latest(fb) == std::vector<unsigned char>(Bytes, 0), "initial noGL empty cache");
        fb->flushReadPipeline(0); require(calls(c) == 0, "empty cache is not a valid callback frame");
        fb->setPostCallback(nullptr, nullptr, 0, false);
        cases.emplace_back("initial_zero_no_callback");
    }
    struct Source { GfxstreamFormat format; const char* id; unsigned frames; };
    const Source sources[] = {
        {GfxstreamFormat::R8G8B8A8_UNORM, "rgba", 8},
        {GfxstreamFormat::R8G8B8X8_UNORM, "rgbx", 2},
        {GfxstreamFormat::B8G8R8A8_UNORM, "bgra", 2},
    };
    for (const auto& source : sources) for (bool targetBgra : {false, true}) {
        fb->setPostCallback(captured, &c, 0, targetBgra);
        for (unsigned frame = 0; frame < source.frames; ++frame) {
            std::fprintf(stderr, "CASE: mode=%s source=%s target=%s frame=%u\n",
                         argv[1], source.id, targetBgra ? "bgra" : "rgba", frame);
            auto expected = rgba(W, H, frame + 1);
            auto encoded = expected;
            if (source.format == GfxstreamFormat::B8G8R8A8_UNORM)
                for (unsigned i = 0; i < Bytes; i += 4) std::swap(encoded[i], encoded[i + 2]);
            for (unsigned i = 0; i < Bytes; i += 4) {
                if (targetBgra) std::swap(expected[i], expected[i + 2]);
                if (source.format == GfxstreamFormat::R8G8B8X8_UNORM && noGl) expected[i + 3] = 255;
            }
            std::vector<unsigned char> rawReadback;
            auto handle = upload(fb, W, H, source.format, encoded, &rawReadback);
            if (source.format == GfxstreamFormat::R8G8B8X8_UNORM && !noGl) {
                // GL stores RGBX as RGBA8 and reads RGBA/BGRA without opaque-alpha
                // normalization. Assert every X byte against this GPU source's raw
                // readback; RGB still uses the independent known pattern above.
                for (unsigned i = 0; i < Bytes; i += 4) expected[i + 3] = rawReadback[i + 3];
            }
            postKnown(fb, c, noGl, handle, expected); fb->closeColorBuffer(handle); ++frames;
        }
        fb->setPostCallback(nullptr, nullptr, 0, targetBgra);
        cases.emplace_back(std::string(source.id) + (targetBgra ? "_bgra" : "_rgba"));
    }
    if (noGl) {
        fb->setPostCallback(captured, &c, 0, false);
        struct Rejected { const char* id; unsigned w, h; GfxstreamFormat format; };
        const Rejected rejected[] = {
            {"unsupported_preserve_recover", W, H, GfxstreamFormat::R8_UNORM},
            {"width_mismatch_preserve_recover", W - 1, H, GfxstreamFormat::R8G8B8A8_UNORM},
            {"height_mismatch_preserve_recover", W, H - 1, GfxstreamFormat::R8G8B8A8_UNORM},
        };
        unsigned generation = 40;
        for (const auto& reject : rejected) {
            auto seed = rgba(W, H, ++generation);
            auto good = upload(fb, W, H, GfxstreamFormat::R8G8B8A8_UNORM, seed);
            postKnown(fb, c, true, good, seed); fb->closeColorBuffer(good);
            auto bytes = rgba(reject.w, reject.h, ++generation);
            if (reject.format == GfxstreamFormat::R8_UNORM) bytes.resize(reject.w * reject.h);
            auto bad = upload(fb, reject.w, reject.h, reject.format, bytes);
            unsigned before = calls(c);
            require(fb->post(bad), "post acceptance differs from callback publication");
            fb->flushReadPipeline(0);
            require(calls(c) == before, "rejected source must not publish callback");
            require(latest(fb) == seed, "rejected source preserves last successful cache");
            fb->closeColorBuffer(bad);
            auto recovered = rgba(W, H, ++generation);
            good = upload(fb, W, H, GfxstreamFormat::R8G8B8A8_UNORM, recovered);
            postKnown(fb, c, true, good, recovered); fb->closeColorBuffer(good);
            cases.emplace_back(reject.id);
        }
        auto expected = latest(fb);
        for (unsigned count : {0u, 17u, Bytes + 32u}) {
            std::vector<unsigned char> guarded(Bytes + 64, 0xcd);
            fb->getPixels(guarded.data() + 16, count, 0);
            unsigned copied = std::min(count, Bytes);
            require(std::equal(expected.begin(), expected.begin() + copied, guarded.begin() + 16),
                    "GetPixels bounded prefix");
            require(std::all_of(guarded.begin(), guarded.begin() + 16, [](auto b){return b == 0xcd;}) &&
                    std::all_of(guarded.begin() + 16 + copied, guarded.end(), [](auto b){return b == 0xcd;}),
                    "GetPixels bounds guards");
        }
        cases.emplace_back("getpixels_copy_bounds");
        fb->setPostCallback(nullptr, nullptr, 0, false);
    }
    FrameBuffer::finalize(); info.reset();
    std::printf("{\"pass\":true,\"mode\":\"%s\",\"profile\":\"%s\",\"frames\":%u,\"cases\":[", argv[1], argv[2], frames);
    for (unsigned i = 0; i < cases.size(); ++i)
        std::printf("%s{\"id\":\"%s\",\"pass\":true}", i ? "," : "", cases[i].c_str());
    std::printf("],\"direct_callback_compared\":%s,\"null_callbacks\":%u,\"gpu_failure_injected\":false,\"rgbx_expected_alpha\":\"%s\"}\n",
                noGl ? "true" : "false", c.nullCalls,
                noGl ? "explicit_opaque_255" : "exact_GPU_raw_X");
}
