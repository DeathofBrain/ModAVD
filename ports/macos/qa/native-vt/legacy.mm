#import <Foundation/Foundation.h>
#include <VideoToolbox/VideoToolbox.h>
#include <sys/mman.h>
#include <unistd.h>
#include <array>
#include <cassert>
#include <cstring>
#include <fstream>
#include <iostream>
#include <list>
#include <map>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>
#include "host-common/H264PingInfoParser.h"
#include "host-common/MediaH264DecoderPlugin.h"
#include "host-common/MediaHostRenderer.h"
// QA observes actual existing private state; no field or behavior substitution.
#define private public
#include "host-common/MediaH264DecoderVideoToolBox.h"
#undef private
using namespace android::emulation;
static uint64_t checks = 0, comparedFrames = 0, byteComparisons = 0;
static uint64_t revokedInputs = 0, nalCalls = 0, hardwareSessions = 0;
static void check(bool value, const char* text) {
    ++checks;
    if (!value) throw std::runtime_error(text);
}
static std::vector<uint8_t> readFile(const std::string& path) {
    std::ifstream stream(path, std::ios::binary);
    check(stream.good(), "fixture read failed");
    return {(std::istreambuf_iterator<char>(stream)), {}};
}
struct Unit { uint64_t pts; std::vector<uint8_t> packet, reference; };
struct Request {
    size_t page = static_cast<size_t>(sysconf(_SC_PAGESIZE));
    uint8_t* mapping;
    uint8_t* base;
    explicit Request(const std::vector<uint8_t>& packet, uint64_t pts) {
        check(packet.size() + 576 < page, "NAL exceeds request page");
        mapping = static_cast<uint8_t*>(mmap(nullptr, 3*page, PROT_NONE, MAP_PRIVATE|MAP_ANON, -1, 0));
        check(mapping != MAP_FAILED, "request mmap failed");
        base = mapping + page;
        check(mprotect(base, page, PROT_READ|PROT_WRITE) == 0, "request mprotect failed");
        memset(base, 0xA5, page);
        *reinterpret_cast<uint64_t*>(base) = 1;
        *reinterpret_cast<uint64_t*>(base+8) = 512;
        *reinterpret_cast<size_t*>(base+16) = packet.size();
        *reinterpret_cast<uint64_t*>(base+24) = pts;
        memcpy(base+512, packet.data(), packet.size());
        memset(base+512+packet.size(), 0, 64);
    }
    void revoke(const std::vector<uint8_t>& packet) {
        check(*reinterpret_cast<size_t*>(base+256) == packet.size(), "legacy consumed wrong NAL size");
        check(*reinterpret_cast<int32_t*>(base+264) == 0, "legacy decode error");
        check(memcmp(base+512, packet.data(), packet.size()) == 0, "legacy mutated NAL");
        check(mprotect(base, page, PROT_NONE) == 0, "request revoke failed");
        ++revokedInputs;
    }
    ~Request() { munmap(mapping, 3*page); }
};
static std::vector<std::vector<uint8_t>> split(const std::vector<uint8_t>& packet) {
    std::vector<size_t> starts;
    for (size_t i=0; i+4<packet.size(); ++i) {
        if (packet[i] == 0 && packet[i+1] == 0 && packet[i+2] == 0 && packet[i+3] == 1) {
            starts.push_back(i); i += 3;
        }
    }
    check(!starts.empty() && starts[0] == 0, "expected four-byte Annex-B starts");
    std::vector<std::vector<uint8_t>> result;
    for (size_t i=0; i<starts.size(); ++i) {
        size_t end = i+1 == starts.size() ? packet.size() : starts[i+1];
        result.emplace_back(packet.begin()+starts[i], packet.begin()+end);
    }
    return result;
}
static void init(MediaH264DecoderVideoToolBox& decoder, bool reset) {
    alignas(8) std::array<uint8_t, 512> request{};
    *reinterpret_cast<uint32_t*>(request.data()) = reset ? 1 : 100;
    for (size_t offset : {8u, 24u}) *reinterpret_cast<uint32_t*>(request.data()+offset) = 128;
    for (size_t offset : {16u, 32u}) *reinterpret_cast<uint32_t*>(request.data()+offset) = 96;
    request[40] = static_cast<uint8_t>(MediaH264Decoder::PixelFormat::YUV420P);
    if (reset) decoder.reset(request.data()); else decoder.initH264Context(request.data());
}
static bool image(MediaH264DecoderVideoToolBox& decoder, const Unit* expected) {
    // Native parser's fixed return header is at256; decoded bytes start512.
    std::vector<uint64_t> memory((512 + 18432 + 128) / 8, UINT64_C(0xA5A5A5A5A5A5A5A5));
    auto* base = reinterpret_cast<uint8_t*>(memory.data());
    *reinterpret_cast<uint64_t*>(base) = 1;
    *reinterpret_cast<uint64_t*>(base+8) = 512;
    decoder.getImage(base);
    int32_t returned = *reinterpret_cast<int32_t*>(base+256);
    if (!expected) { check(returned < 0, "unexpected legacy frame"); return false; }
    check(returned == 18432 && expected->reference.size() == 18432, "legacy frame extent mismatch");
    check(*reinterpret_cast<uint32_t*>(base+264) == 128, "legacy frame width mismatch");
    check(*reinterpret_cast<uint32_t*>(base+272) == 96, "legacy frame height mismatch");
    check(*reinterpret_cast<uint64_t*>(base+280) == expected->pts, "legacy PTS mismatch");
    check(memcmp(base+512, expected->reference.data(), expected->reference.size()) == 0,
          "legacy decoded bytes differ from independent VT reference");
    for (size_t i=512+18432; i<memory.size()*8; ++i) check(base[i] == 0xA5, "legacy output overrun");
    ++comparedFrames;
    byteComparisons += expected->reference.size();
    return true;
}
static void verifyDestroyed(MediaH264DecoderVideoToolBox& decoder) {
    check(decoder.mDecoderSession == nullptr, "legacy session retained after destroy");
    check(decoder.mCmFmtDesc == nullptr, "legacy format retained after destroy");
    check(decoder.mDecodedFrame == nullptr, "legacy pixel retained after destroy");
    image(decoder, nullptr);
}
static void feed(MediaH264DecoderVideoToolBox& decoder, const std::vector<Unit>& units,
    size_t count, bool leaveLast, std::vector<std::unique_ptr<Request>>& requests) {
    for (size_t i=0; i<count; ++i) {
        for (const auto& nal : split(units[i].packet)) {
            auto request = std::make_unique<Request>(nal, units[i].pts);
            decoder.decodeFrame(request->base);
            ++nalCalls;
            request->revoke(nal);
            requests.push_back(std::move(request));
        }
        check(decoder.getState() == MediaH264DecoderVideoToolBox::DecoderState::GOOD_STATE,
              "legacy decoder bad state");
        check(decoder.mDecoderSession && decoder.mCmFmtDesc && decoder.mDecodedFrame,
              "legacy actual VT decode did not produce session and pixel");
        if (i == 0) {
            CFTypeRef value = nullptr;
            check(VTSessionCopyProperty(decoder.mDecoderSession,
                kVTDecompressionPropertyKey_UsingHardwareAcceleratedVideoDecoder,
                kCFAllocatorDefault, &value) == noErr && value, "legacy hardware query failed");
            check(CFEqual(value, kCFBooleanTrue), "legacy VT not hardware accelerated");
            CFRelease(value); ++hardwareSessions;
        }
        check(decoder.mImageReady, "legacy image not ready");
        // Actual callback copied to independent vector; source request pages are now inaccessible.
        check(decoder.mSavedDecodedFrame == units[i].reference, "legacy retained output differs");
        if (!leaveLast || i+1 != count) {
            image(decoder, &units[i]);
            image(decoder, nullptr);
        }
    }
}
int main(int argc, char** argv) {
    @autoreleasepool {
        try {
            check(argc == 2, "pass fixture manifest path");
            NSString* manifestPath = [NSString stringWithUTF8String:argv[1]];
            NSData* data = [NSData dataWithContentsOfFile:manifestPath];
            NSDictionary* manifest = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
            check(manifest != nil, "manifest JSON invalid");
            NSString* fixtureRoot = [[manifestPath stringByDeletingLastPathComponent] stringByAppendingPathComponent:@"fixtures"];
            std::vector<Unit> units;
            for (NSDictionary* codec in manifest[@"codecs"]) {
                if (![codec[@"codec"] isEqualToString:@"h264"]) continue;
                for (NSDictionary* au in codec[@"access_units"]) {
                    units.push_back({[au[@"pts"] unsignedLongLongValue],
                        readFile([[fixtureRoot stringByAppendingPathComponent:au[@"annexb"]] UTF8String]),
                        readFile([[fixtureRoot stringByAppendingPathComponent:au[@"reference_i420"]] UTF8String])});
                }
            }
            check(units.size() == 8, "expected eight H264 units");
            for (int cycle=0; cycle<10; ++cycle) {
                // Requests are retained and PROT_NONE until after decoder destruction.
                std::vector<std::unique_ptr<Request>> requests;
                {
                    MediaH264DecoderVideoToolBox decoder(1, H264PingInfoParser(100));
                    init(decoder, false);
                    image(decoder, nullptr);
                    feed(decoder, units, 8, false, requests);
                    decoder.flush(nullptr);
                    image(decoder, nullptr);
                    init(decoder, true);
                    feed(decoder, units, 8, false, requests);
                    decoder.destroyH264Context();
                    verifyDestroyed(decoder);
                    decoder.destroyH264Context();
                    verifyDestroyed(decoder);
                    init(decoder, false);
                    feed(decoder, units, 2, true, requests);
                    check(decoder.mDecodedFrame && decoder.mImageReady, "legacy leftover teardown setup absent");
                }
                // All source pages remain inaccessible during destructor and release.
                requests.clear();
            }
            std::cout << "VT_LEGACY_JSON={\"pass\":true,\"cycles\":10,\"checks\":" << checks
                      << ",\"compared_frames\":" << comparedFrames << ",\"decoded_frames\":180"
                      << ",\"byte_comparisons\":" << byteComparisons << ",\"revoked_inputs\":" << revokedInputs
                      << ",\"nal_calls\":" << nalCalls << ",\"hardware_sessions\":" << hardwareSessions << "}\n";
        } catch (const std::exception& error) {
            std::cerr << error.what() << "\n";
            return 1;
        }
    }
}
