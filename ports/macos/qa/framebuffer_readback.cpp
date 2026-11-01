// Native production FrameBuffer readback check. No guest or replacement codecs.
#include "frame_buffer.h"
#include "gfxstream/host/features.h"
#include "render_thread_info.h"
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <vector>

using namespace gfxstream::host;
struct Capture {
    std::mutex lock;
    std::condition_variable changed;
    std::vector<unsigned char> pixels;
    unsigned calls = 0;
    unsigned nullCalls = 0;
    bool hasPixels = false;
    int width = 0, height = 0, direction = 0, format = 0, type = 0;
};
static void captured(void* context, uint32_t display, int width, int height,
                     int direction, int format, int type, unsigned char* pixels) {
    auto* capture = static_cast<Capture*>(context);
    std::lock_guard<std::mutex> guard(capture->lock);
    if (display != 0 || width != 64 || height != 48) std::abort();
    capture->hasPixels = pixels != nullptr;
    if (pixels) capture->pixels.assign(pixels, pixels + width * height * 4);
    else { capture->pixels.clear(); ++capture->nullCalls; }
    capture->width = width; capture->height = height; capture->direction = direction;
    capture->format = format; capture->type = type; ++capture->calls;
    capture->changed.notify_all();
}
static void require(bool passed, const char* what) {
    if (!passed) { std::fprintf(stderr, "FAIL: %s\n", what); std::exit(1); }
}
int main(int argc, char** argv) {
    require(argc == 2 && (!std::strcmp(argv[1], "vulkan-only") || !std::strcmp(argv[1], "gl")), "mode");
    const bool noGl = !std::strcmp(argv[1], "vulkan-only");
    FeatureSet features{};
    features.Vulkan.setEnabled(true);
    features.GuestVulkanOnly.setEnabled(noGl);
    require(FrameBuffer::initialize(64, 48, features, false), "initialize");
    auto* fb = FrameBuffer::getFB();
    require(fb != nullptr, "framebuffer");
    require((fb->getEmulationGl() == nullptr) == noGl, "actual GL emulation state");
    auto info = std::make_unique<RenderThreadInfo>();
    if (!noGl) info->initGl(fb->getEmulationGl());
    Capture capture;
    unsigned successfulFrames = 0;
    for (bool bgra : {false, true}) {
        fb->setPostCallback(captured, &capture, 0, bgra);
        for (unsigned frame = 0; frame < 8; ++frame) {
            std::vector<unsigned char> source(64 * 48 * 4), expected(source.size());
            for (int y = 0; y < 48; ++y) for (int x = 0; x < 64; ++x) {
                const auto at = 4 * (y * 64 + x);
                source[at] = (x * 7 + y * 3 + frame * 19) & 255;
                source[at + 1] = (x * 5 + y * 11 + frame * 23) & 255;
                source[at + 2] = (x * 13 + y * 17 + frame * 29) & 255;
                source[at + 3] = (x * 19 + y * 23 + frame * 31) & 255;
                expected[at] = source[at + (bgra ? 2 : 0)];
                expected[at + 1] = source[at + 1];
                expected[at + 2] = source[at + (bgra ? 0 : 2)];
                expected[at + 3] = source[at + 3];
            }
            auto handle = fb->createColorBuffer(64, 48, GfxstreamFormat::R8G8B8A8_UNORM);
            require(handle != 0, "createColorBuffer");
            require(fb->updateColorBuffer(handle, 0, 0, 64, 48, GfxstreamFormat::R8G8B8A8_UNORM, source.data()), "updateColorBuffer");
            std::vector<unsigned char> reference(source.size());
            fb->readColorBuffer(handle, 0, 0, 64, 48, GfxstreamFormat::R8G8B8A8_UNORM, reference.data(), reference.size());
            require(reference == source, "GPU source readback");
            unsigned before;
            { std::lock_guard<std::mutex> guard(capture.lock); before = capture.calls; }
            require(fb->post(handle), "post");
            // GL readback may publish a prior frame until its pipeline drains.
            fb->flushReadPipeline(0);
            {
                std::unique_lock<std::mutex> guard(capture.lock);
                require(capture.changed.wait_for(guard, std::chrono::seconds(5), [&]{ return capture.calls > before; }), "post callback timeout");
                // The GL pipeline legally notifies with a null pointer or a prior frame.
                // Vulkan-only readback publishes the current CPU buffer directly.
                if (noGl) require(capture.hasPixels && capture.pixels == expected,
                                  "callback bytes/channel order/alpha/row order");
            }
            std::vector<unsigned char> latest(source.size());
            fb->getPixels(latest.data(), latest.size(), 0);
            require(latest == expected, "GetPixels latest frame");
            fb->closeColorBuffer(handle);
            ++successfulFrames;
        }
        fb->setPostCallback(nullptr, nullptr, 0, bgra);
    }
    FrameBuffer::finalize();
    info.reset();
    std::printf("{\"pass\":true,\"mode\":\"%s\",\"frames\":%u,\"width\":64,\"height\":48,\"RGBA_and_BGRA\":true,\"varying_alpha\":true,\"direct_callback_compared\":%s,\"null_callbacks\":%u,\"byte_comparisons\":%u}\n", argv[1], successfulFrames, noGl ? "true" : "false", capture.nullCalls, successfulFrames * 64 * 48 * 4 * (noGl ? 3 : 2));
}
