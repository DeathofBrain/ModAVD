#import <Foundation/Foundation.h>
#include <VideoToolbox/VideoToolbox.h>
#include <sys/mman.h>
#include <unistd.h>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <vector>
#include "host-common/H264NaluParser.h"
#include "android/emulation/HevcNaluParser.h"
#include "host-common/MediaFfmpegVideoHelper.h"
#include "host-common/MediaTexturePool.h"
#include "host-common/YuvConverter.h"
// Test-only access to state in the actual, unchanged helper class. No fields,
// virtual methods or source functions are replaced or added to its layout.
#define private public
#include "host-common/MediaVideoToolBoxVideoHelper.h"
#include "android/emulation/MediaVideoToolBoxHevcVideoHelper.h"
#undef private

using namespace android::emulation;
static uint64_t checks = 0, frames = 0, byteComparisons = 0, protectedInputs = 0;
static void check(bool value, const char* text) {
    ++checks;
    if (!value) throw std::runtime_error(text);
}
static std::vector<uint8_t> readFile(const std::string& path) {
    std::ifstream file(path, std::ios::binary);
    check(file.good(), "fixture file open failed");
    return {(std::istreambuf_iterator<char>(file)), {}};
}
struct AccessUnit {
    uint64_t pts;
    std::vector<uint8_t> packet, reference;
};
struct GuardedInput {
    size_t page = static_cast<size_t>(sysconf(_SC_PAGESIZE));
    uint8_t* base;
    uint8_t* data;
    size_t length;
    explicit GuardedInput(const std::vector<uint8_t>& bytes) : length(bytes.size()) {
        check(length + 64 < page, "fixture packet too large for one page");
        base = static_cast<uint8_t*>(mmap(nullptr, 3*page, PROT_NONE,
                                         MAP_PRIVATE|MAP_ANON, -1, 0));
        check(base != MAP_FAILED, "mmap failed");
        check(mprotect(base+page, page, PROT_READ|PROT_WRITE) == 0,
              "input page enable failed");
        data = base+2*page-length-64;
        memset(base+page, 0xA5, page);
        memcpy(data, bytes.data(), length);
        memset(data+length, 0, 64); // FFmpeg's required readable zero padding.
    }
    void verifyAndRevoke(const std::vector<uint8_t>& original) {
        check(memcmp(data, original.data(), length) == 0, "input packet mutated");
        for (size_t i=0; i<64; ++i) check(data[length+i] == 0, "input padding mutated");
        for (auto p=base+page; p<data; ++p) check(*p == 0xA5, "input prefix mutated");
        check(mprotect(base+page, page, PROT_NONE) == 0, "input revoke failed");
        ++protectedInputs;
    }
    ~GuardedInput() { munmap(base, 3*page); }
};
template<class T> struct Inspector : T {
    using T::T;
    size_t pendingFrames() const { return this->mSavedDecodedFrames.size(); }
};
template<class T>
static void verifyCleared(Inspector<T>& h) {
    check(h.mDecoderSession == nullptr, "session remains after deInit");
    check(h.mCmFmtDesc == nullptr, "format remains after deInit");
    check(h.mDecodedFrame == nullptr, "pixel buffer remains after deInit");
    check(!h.mFfmpegVideoHelper, "FFmpeg probe remains after deInit");
    check(h.pendingFrames() == 0, "saved output remains after deInit");
    check(h.mVtbBufferMap.empty(), "reorder output remains after deInit");
    check(h.mSavedDecodedFrame.empty(), "scratch pixels remain after deInit");
    check(h.mInputFrames.empty(), "parsed input remains after deInit");
    check(!h.mVtbReady, "session ready remains after deInit");
    MediaSnapshotState::FrameInfo f{};
    check(!h.receiveFrame(&f), "output available after deInit");
}
template<class T>
static bool sessionHardware(Inspector<T>& h) {
    CFTypeRef value = nullptr;
    OSStatus status = VTSessionCopyProperty(h.mDecoderSession,
        kVTDecompressionPropertyKey_UsingHardwareAcceleratedVideoDecoder,
        kCFAllocatorDefault, &value);
    check(status == noErr && value, "hardware property query failed");
    bool hardware = CFEqual(value, kCFBooleanTrue);
    CFRelease(value);
    return hardware;
}
template<class T>
static void feed(Inspector<T>& h, const std::vector<AccessUnit>& units,
                 std::vector<std::unique_ptr<GuardedInput>>& retained,
                 uint64_t& hardwareSessions) {
    check(h.init(), "helper init failed");
    for (size_t i=0; i<units.size(); ++i) {
        auto input = std::make_unique<GuardedInput>(units[i].packet);
        h.decode(input->data, input->length, units[i].pts);
        check(h.good(), "helper marked codec bad");
        check(h.mDecoderSession && h.mCmFmtDesc && h.mVtbReady,
              "actual VideoToolbox session not ready");
        check(h.mDecodedFrame == nullptr, "callback retained a pixel buffer");
        check(h.mInputFrames.empty(), "decoder retained parsed packet pointers");
        if (i == 0) {
            hardwareSessions += sessionHardware(h);
            check(h.mVtbBufferSize == 0, "fixture unexpectedly needs frame reordering");
        }
        input->verifyAndRevoke(units[i].packet);
        retained.push_back(std::move(input));
        ++frames;
    }
}
template<class T>
static void drain(Inspector<T>& h, const std::vector<AccessUnit>& units) {
    h.flush();
    check(h.mVtbBufferMap.empty(), "flush left a reorder frame");
    MediaSnapshotState::FrameInfo frame{};
    size_t index=0;
    while (h.receiveFrame(&frame)) {
        check(index < units.size(), "extra decoded output");
        check(frame.width == 128 && frame.height == 96, "output dimensions mismatch");
        check(frame.data.size() == 18432 && frame.texture.empty(), "output I420 layout mismatch");
        check(frame.pts == units[index].pts, "output PTS mismatch");
        check(frame.data == units[index].reference, "actual codec output differs from VT reference");
        byteComparisons += frame.data.size();
        frame = {};
        ++index;
    }
    check(index == units.size(), "missing decoded frame");
    h.flush();
    check(!h.receiveFrame(&frame), "repeat flush duplicated output");
}
template<class T>
static NSDictionary* runCodec(NSString* name, const std::vector<AccessUnit>& units) {
    uint64_t hardwareSessions=0, sessions=0;
    // Fresh helpers: complete decode/flush/drain, repeated explicit deInit.
    for (int cycle=0; cycle<12; ++cycle) {
        std::vector<std::unique_ptr<GuardedInput>> inputs;
        Inspector<T> h(128,96,T::OutputTreatmentMode::SAVE_RESULT,
                       T::FrameStorageMode::USE_BYTE_BUFFER);
        feed(h,units,inputs,hardwareSessions); ++sessions;
        drain(h,units);
        h.deInit(); verifyCleared(h);
        h.deInit(); verifyCleared(h);
    }
    // Same object reused after a completed teardown, with all old input pages
    // inaccessible during both the new decode and its teardown.
    {
        std::vector<std::unique_ptr<GuardedInput>> inputs;
        Inspector<T> h(128,96,T::OutputTreatmentMode::SAVE_RESULT,
                       T::FrameStorageMode::USE_BYTE_BUFFER);
        for (int cycle=0; cycle<8; ++cycle) {
            feed(h,units,inputs,hardwareSessions); ++sessions;
            drain(h,units);
            h.deInit(); verifyCleared(h);
        }
    }
    // Teardown without consuming output, including destructor-only teardown.
    for (int cycle=0; cycle<8; ++cycle) {
        std::vector<std::unique_ptr<GuardedInput>> inputs;
        auto h = std::make_unique<Inspector<T>>(128,96,T::OutputTreatmentMode::SAVE_RESULT,
                                               T::FrameStorageMode::USE_BYTE_BUFFER);
        feed(*h,units,inputs,hardwareSessions); ++sessions;
        check(h->pendingFrames() == units.size(), "pending-output fixture did not accumulate");
        if (cycle % 2 == 0) { h->deInit(); verifyCleared(*h); }
        h.reset(); // Actual helper destructor runs before revoked pages unmap.
    }
    return @{ @"codec":name, @"sessions":@(sessions), @"hardware_sessions":@(hardwareSessions),
              @"fresh_decode_flush_teardowns":@12, @"same_object_reinit_teardowns":@8,
              @"pending_output_teardowns":@8, @"destructor_only_teardowns":@4,
              @"frames_per_session":@(units.size()), @"pass":@YES };
}
int main(int argc,char** argv) { @autoreleasepool {
    NSMutableDictionary* result=[NSMutableDictionary dictionary];
    try {
        check(argc == 2, "expected fixture manifest");
        NSString* path=[NSString stringWithUTF8String:argv[1]];
        NSData* data=[NSData dataWithContentsOfFile:path];
        NSDictionary* manifest=[NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
        check(manifest != nil, "fixture manifest parse failed");
        std::string root=std::string([[path stringByDeletingLastPathComponent] UTF8String])+"/fixtures/";
        NSMutableArray* codecResults=[NSMutableArray array];
        for (NSDictionary* codec in manifest[@"codecs"]) {
            std::vector<AccessUnit> units;
            for (NSDictionary* au in codec[@"access_units"]) {
                units.push_back({[au[@"pts"] unsignedLongLongValue],
                    readFile(root+std::string([au[@"annexb"] UTF8String])),
                    readFile(root+std::string([au[@"reference_i420"] UTF8String]))});
            }
            if ([codec[@"codec"] isEqual:@"h264"])
                [codecResults addObject:runCodec<MediaVideoToolBoxVideoHelper>(@"h264",units)];
            else
                [codecResults addObject:runCodec<MediaVideoToolBoxHevcVideoHelper>(@"hevc",units)];
        }
        result[@"codecs"]=codecResults;
        result[@"pass"]=@YES;
    } catch(const std::exception& e) {
        result[@"pass"]=@NO;
        result[@"failure"]=[NSString stringWithUTF8String:e.what()];
    }
    result[@"checks"]=@(checks); result[@"decoded_frames"]=@(frames);
    result[@"byte_comparisons"]=@(byteComparisons);
    result[@"inputs_revoked_after_decode"]=@(protectedInputs);
    NSData* json=[NSJSONSerialization dataWithJSONObject:result options:NSJSONWritingSortedKeys error:nil];
    std::cout << "VT_RESULT_JSON=" << std::string(static_cast<const char*>(json.bytes),json.length) << std::endl;
    return [result[@"pass"] boolValue] ? 0 : 1;
} }
