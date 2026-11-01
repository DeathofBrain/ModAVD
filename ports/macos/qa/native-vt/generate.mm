#include <VideoToolbox/VideoToolbox.h>
#include <CoreMedia/CoreMedia.h>
#include <CoreVideo/CoreVideo.h>
#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <mutex>
#include <string>
#include <vector>

static constexpr int W = 128, H = 96, N = 8;
static constexpr size_t FrameBytes = W * H * 3 / 2;
static void status(OSStatus value, const char* operation) {
    if (value) { std::cerr << operation << ": " << value << "\n"; std::abort(); }
}
static void save(const std::filesystem::path& name, const std::vector<uint8_t>& data) {
    assert(!std::filesystem::exists(name));
    std::ofstream out(name, std::ios::binary);
    out.write(reinterpret_cast<const char*>(data.data()), data.size());
    assert(out.good());
}
static std::string indexName(int index) {
    return std::string(index < 10 ? "00" : "0") + std::to_string(index);
}
static std::vector<uint8_t> sourceFrame(int index) {
    std::vector<uint8_t> result(FrameBytes);
    for (int y = 0; y < H; ++y)
        for (int x = 0; x < W; ++x)
            result[y * W + x] = 32 + ((x >= W / 2 ? 48 : 0) +
                                      (y >= H / 2 ? 64 : 0) + index * 9) % 176;
    for (int y = 0; y < H / 2; ++y)
        for (int x = 0; x < W / 2; ++x) {
            result[W * H + y * W / 2 + x] = 96 + index * 3 + (x >= W / 4 ? 16 : 0);
            result[W * H * 5 / 4 + y * W / 2 + x] = 160 - index * 3 - (y >= H / 4 ? 16 : 0);
        }
    return result;
}
static CVPixelBufferRef pixelBuffer(int index) {
    const void* keys[] = {kCVPixelBufferIOSurfacePropertiesKey};
    CFDictionaryRef empty = CFDictionaryCreate(kCFAllocatorDefault, nullptr, nullptr, 0,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    const void* values[] = {empty};
    CFDictionaryRef attrs = CFDictionaryCreate(kCFAllocatorDefault, keys, values, 1,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CVPixelBufferRef pixel = nullptr;
    status(CVPixelBufferCreate(kCFAllocatorDefault, W, H,
        kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange, attrs, &pixel), "CVPixelBufferCreate");
    CFRelease(attrs); CFRelease(empty);
    auto input = sourceFrame(index);
    status(CVPixelBufferLockBaseAddress(pixel, 0), "lock input");
    auto* yPlane = static_cast<uint8_t*>(CVPixelBufferGetBaseAddressOfPlane(pixel, 0));
    auto* uvPlane = static_cast<uint8_t*>(CVPixelBufferGetBaseAddressOfPlane(pixel, 1));
    size_t yStride = CVPixelBufferGetBytesPerRowOfPlane(pixel, 0);
    size_t uvStride = CVPixelBufferGetBytesPerRowOfPlane(pixel, 1);
    for (int y = 0; y < H; ++y) std::memcpy(yPlane + y * yStride, input.data() + y * W, W);
    for (int y = 0; y < H / 2; ++y)
        for (int x = 0; x < W / 2; ++x) {
            uvPlane[y * uvStride + 2*x] = input[W*H + y*W/2 + x];
            uvPlane[y * uvStride + 2*x + 1] = input[W*H*5/4 + y*W/2 + x];
        }
    status(CVPixelBufferUnlockBaseAddress(pixel, 0), "unlock input");
    return pixel;
}
struct Encoded {
    int index;
    CMSampleBufferRef sample;
};
struct EncoderOutput { std::mutex lock; std::vector<Encoded> samples; };
static void encodeCallback(void* opaque, void* frame, OSStatus result,
                           VTEncodeInfoFlags flags, CMSampleBufferRef sample) {
    status(result, "compression callback");
    assert(sample && !(flags & kVTEncodeInfo_FrameDropped));
    auto& output = *static_cast<EncoderOutput*>(opaque);
    std::lock_guard<std::mutex> guard(output.lock);
    CFRetain(sample);
    output.samples.push_back({static_cast<int>(reinterpret_cast<intptr_t>(frame) - 1), sample});
}
static bool property(VTSessionRef session, CFStringRef name) {
    CFTypeRef result = nullptr;
    status(VTSessionCopyProperty(session, name, kCFAllocatorDefault, &result), "copy VT property");
    bool value = result && CFGetTypeID(result) == CFBooleanGetTypeID() &&
        CFBooleanGetValue(static_cast<CFBooleanRef>(result));
    if (result) CFRelease(result);
    return value;
}
static void numberProperty(VTSessionRef session, CFStringRef name, int value) {
    CFNumberRef number = CFNumberCreate(kCFAllocatorDefault, kCFNumberIntType, &value);
    status(VTSessionSetProperty(session, name, number), "set numeric property");
    CFRelease(number);
}
static std::vector<Encoded> encode(bool hevc, bool& hardware) {
    EncoderOutput output;
    VTCompressionSessionRef encoder = nullptr;
    const void* specKey[] = {kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder};
    const void* specValue[] = {kCFBooleanTrue};
    CFDictionaryRef spec = CFDictionaryCreate(kCFAllocatorDefault, specKey, specValue, 1,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    status(VTCompressionSessionCreate(kCFAllocatorDefault, W, H,
        hevc ? kCMVideoCodecType_HEVC : kCMVideoCodecType_H264, spec, nullptr, nullptr,
        encodeCallback, &output, &encoder), "VTCompressionSessionCreate");
    CFRelease(spec);
    status(VTSessionSetProperty(encoder, kVTCompressionPropertyKey_RealTime, kCFBooleanTrue), "set realtime");
    status(VTSessionSetProperty(encoder, kVTCompressionPropertyKey_AllowFrameReordering,
        kCFBooleanFalse), "disable B frames");
    status(VTSessionSetProperty(encoder, kVTCompressionPropertyKey_ProfileLevel,
        hevc ? kVTProfileLevel_HEVC_Main_AutoLevel : kVTProfileLevel_H264_Baseline_AutoLevel), "set profile");
    numberProperty(encoder, kVTCompressionPropertyKey_MaxKeyFrameInterval, N);
    numberProperty(encoder, kVTCompressionPropertyKey_ExpectedFrameRate, 30);
    numberProperty(encoder, kVTCompressionPropertyKey_AverageBitRate, 2000000);
    status(VTCompressionSessionPrepareToEncodeFrames(encoder), "prepare compression");
    hardware = property(encoder, kVTCompressionPropertyKey_UsingHardwareAcceleratedVideoEncoder);
    for (int i = 0; i < N; ++i) {
        CVPixelBufferRef pixel = pixelBuffer(i);
        VTEncodeInfoFlags info = 0;
        status(VTCompressionSessionEncodeFrame(encoder, pixel, CMTimeMake(1000 + 33*i, 1000),
            CMTimeMake(33, 1000), nullptr, reinterpret_cast<void*>(static_cast<intptr_t>(i + 1)),
            &info), "VTCompressionSessionEncodeFrame");
        CVPixelBufferRelease(pixel);
    }
    status(VTCompressionSessionCompleteFrames(encoder, kCMTimeInvalid), "complete compression");
    VTCompressionSessionInvalidate(encoder);
    CFRelease(encoder);
    assert(output.samples.size() == N && hardware);
    std::sort(output.samples.begin(), output.samples.end(),
              [](const Encoded& a, const Encoded& b) { return a.index < b.index; });
    return output.samples;
}
struct Decoded {
    int index;
    CMTime pts;
    OSType pixelFormat;
    size_t width, height;
    std::vector<uint8_t> i420;
};
struct DecoderOutput { std::mutex lock; std::vector<Decoded> frames; };
static void decodeCallback(void* opaque, void* frame, OSStatus result,
    VTDecodeInfoFlags flags, CVImageBufferRef image, CMTime pts, CMTime) {
    status(result, "decompression callback");
    assert(image && !(flags & kVTDecodeInfo_FrameDropped));
    Decoded decoded;
    decoded.index = static_cast<int>(reinterpret_cast<intptr_t>(frame) - 1);
    decoded.pts = pts;
    decoded.pixelFormat = CVPixelBufferGetPixelFormatType(image);
    decoded.width = CVPixelBufferGetWidth(image);
    decoded.height = CVPixelBufferGetHeight(image);
    assert(decoded.width == W && decoded.height == H);
    assert(decoded.pixelFormat == kCVPixelFormatType_420YpCbCr8Planar &&
           CVPixelBufferGetPlaneCount(image) == 3);
    decoded.i420.reserve(FrameBytes);
    status(CVPixelBufferLockBaseAddress(image, kCVPixelBufferLock_ReadOnly), "lock decoded");
    for (int plane = 0; plane < 3; ++plane) {
        int width = plane == 0 ? W : W/2, height = plane == 0 ? H : H/2;
        assert(CVPixelBufferGetWidthOfPlane(image, plane) == width &&
               CVPixelBufferGetHeightOfPlane(image, plane) == height);
        auto* base = static_cast<const uint8_t*>(CVPixelBufferGetBaseAddressOfPlane(image, plane));
        size_t stride = CVPixelBufferGetBytesPerRowOfPlane(image, plane);
        for (int y = 0; y < height; ++y)
            decoded.i420.insert(decoded.i420.end(), base + y*stride, base + y*stride + width);
    }
    status(CVPixelBufferUnlockBaseAddress(image, kCVPixelBufferLock_ReadOnly), "unlock decoded");
    auto& output = *static_cast<DecoderOutput*>(opaque);
    std::lock_guard<std::mutex> guard(output.lock);
    output.frames.push_back(std::move(decoded));
}
static std::vector<Decoded> reference(const std::vector<Encoded>& samples,
                                    bool& hardware, bool& synchronous) {
    DecoderOutput output;
    VTDecompressionSessionRef decoder = nullptr;
    VTDecompressionOutputCallbackRecord callback = {decodeCallback, &output};
    int pixelFormat = kCVPixelFormatType_420YpCbCr8Planar;
    CFNumberRef pixelNumber = CFNumberCreate(kCFAllocatorDefault, kCFNumberIntType, &pixelFormat);
    CFDictionaryRef empty = CFDictionaryCreate(kCFAllocatorDefault, nullptr, nullptr, 0,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    const void* keys[] = {kCVPixelBufferPixelFormatTypeKey, kCVPixelBufferIOSurfacePropertiesKey};
    const void* values[] = {pixelNumber, empty};
    CFDictionaryRef attrs = CFDictionaryCreate(kCFAllocatorDefault, keys, values, 2,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    const void* specKeys[] = {kVTVideoDecoderSpecification_RequireHardwareAcceleratedVideoDecoder};
    const void* specValues[] = {kCFBooleanTrue};
    CFDictionaryRef spec = CFDictionaryCreate(kCFAllocatorDefault, specKeys, specValues, 1,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    status(VTDecompressionSessionCreate(kCFAllocatorDefault,
        CMSampleBufferGetFormatDescription(samples[0].sample), spec, attrs, &callback, &decoder),
        "VTDecompressionSessionCreate");
    CFRelease(spec); CFRelease(attrs); CFRelease(empty); CFRelease(pixelNumber);
    synchronous = true;
    for (const auto& sample : samples) {
        const auto before = output.frames.size();
        VTDecodeInfoFlags flags = 0;
        status(VTDecompressionSessionDecodeFrame(decoder, sample.sample, 0,
            reinterpret_cast<void*>(static_cast<intptr_t>(sample.index + 1)), &flags),
            "VTDecompressionSessionDecodeFrame");
        synchronous = synchronous && output.frames.size() == before + 1;
    }
    status(VTDecompressionSessionFinishDelayedFrames(decoder), "finish delayed");
    status(VTDecompressionSessionWaitForAsynchronousFrames(decoder), "wait decoder");
    hardware = property(decoder, kVTDecompressionPropertyKey_UsingHardwareAcceleratedVideoDecoder);
    VTDecompressionSessionInvalidate(decoder);
    CFRelease(decoder);
    assert(output.frames.size() == N && hardware && synchronous);
    std::sort(output.frames.begin(), output.frames.end(),
              [](const Decoded& a, const Decoded& b) { return a.index < b.index; });
    return output.frames;
}
static void appendNal(std::vector<uint8_t>& result, const uint8_t* nal, size_t size) {
    static const uint8_t start[] = {0, 0, 0, 1};
    result.insert(result.end(), start, start + 4);
    result.insert(result.end(), nal, nal + size);
}
static std::vector<uint8_t> annexB(CMSampleBufferRef sample, bool hevc, bool includeParameters,
                                  std::vector<unsigned>& types) {
    std::vector<uint8_t> result;
    auto format = CMSampleBufferGetFormatDescription(sample);
    if (includeParameters) {
        const uint8_t* parameter = nullptr;
        size_t size = 0, count = 0;
        int header = 0;
        if (hevc) status(CMVideoFormatDescriptionGetHEVCParameterSetAtIndex(format, 0,
            &parameter, &size, &count, &header), "HEVC parameter count");
        else status(CMVideoFormatDescriptionGetH264ParameterSetAtIndex(format, 0,
            &parameter, &size, &count, &header), "H264 parameter count");
        assert(header == 4 && count == (hevc ? 3 : 2));
        for (size_t i = 0; i < count; ++i) {
            if (hevc) status(CMVideoFormatDescriptionGetHEVCParameterSetAtIndex(format, i,
                &parameter, &size, nullptr, nullptr), "HEVC parameter");
            else status(CMVideoFormatDescriptionGetH264ParameterSetAtIndex(format, i,
                &parameter, &size, nullptr, nullptr), "H264 parameter");
            types.push_back(hevc ? ((parameter[0] >> 1) & 63) : (parameter[0] & 31));
            appendNal(result, parameter, size);
        }
    }
    CMBlockBufferRef buffer = CMSampleBufferGetDataBuffer(sample);
    size_t length = CMBlockBufferGetDataLength(buffer);
    std::vector<uint8_t> encoded(length);
    status(CMBlockBufferCopyDataBytes(buffer, 0, length, encoded.data()), "copy compressed sample");
    for (size_t offset = 0; offset < length;) {
        assert(length - offset >= 4);
        uint32_t size = (uint32_t(encoded[offset]) << 24) | (uint32_t(encoded[offset+1]) << 16) |
                        (uint32_t(encoded[offset+2]) << 8) | uint32_t(encoded[offset+3]);
        offset += 4;
        assert(size > 0 && size <= length - offset);
        types.push_back(hevc ? ((encoded[offset] >> 1) & 63) : (encoded[offset] & 31));
        appendNal(result, encoded.data() + offset, size);
        offset += size;
    }
    return result;
}
int main(int argc, char** argv) {
    assert(argc == 2);
    std::filesystem::path root(argv[1]);
    assert(std::filesystem::is_directory(root));
    std::filesystem::create_directory(root / "source");
    for (int i = 0; i < N; ++i) save(root / "source" / ("frame-" + indexName(i) + ".i420"), sourceFrame(i));
    std::cout << "{\"width\":" << W << ",\"height\":" << H << ",\"frame_count\":" << N
              << ",\"pixel_format\":\"I420\",\"codecs\":[";
    for (int codec = 0; codec < 2; ++codec) {
        bool hevc = codec == 1, encHardware = false, decHardware = false, synchronous = false;
        std::string name = hevc ? "hevc" : "h264";
        std::filesystem::create_directory(root / name);
        auto samples = encode(hevc, encHardware);
        auto frames = reference(samples, decHardware, synchronous);
        std::vector<uint8_t> stream;
        if (codec) std::cout << ",";
        std::cout << "{\"codec\":\"" << name << "\",\"encoder_hardware\":" << (encHardware ? "true" : "false")
                  << ",\"reference_decoder_hardware\":" << (decHardware ? "true" : "false")
                  << ",\"reference_callbacks_synchronous\":" << (synchronous ? "true" : "false")
                  << ",\"access_units\":[";
        for (int i = 0; i < N; ++i) {
            assert(samples[i].index == i && frames[i].index == i && frames[i].i420.size() == FrameBytes);
            assert(CMTimeCompare(frames[i].pts, CMTimeMake(1000+33*i, 1000)) == 0);
            std::vector<unsigned> types;
            auto au = annexB(samples[i].sample, hevc, i == 0, types);
            std::string auPath = name + "/au-" + indexName(i) + ".annexb";
            std::string referencePath = name + "/reference-" + indexName(i) + ".i420";
            std::string sourcePath = "source/frame-" + indexName(i) + ".i420";
            save(root / auPath, au);
            save(root / referencePath, frames[i].i420);
            stream.insert(stream.end(), au.begin(), au.end());
            auto original = sourceFrame(i);
            int maximum = 0;
            uint64_t sum = 0;
            for (size_t j = 0; j < FrameBytes; ++j) {
                int difference = std::abs(int(original[j]) - int(frames[i].i420[j]));
                maximum = std::max(maximum, difference); sum += difference;
            }
            double mean = double(sum) / FrameBytes;
            assert(maximum <= 24 && mean < 6.0);
            if (i) std::cout << ",";
            std::cout << "{\"index\":" << i << ",\"pts\":" << 1000+33*i
                      << ",\"annexb\":\"" << auPath << "\",\"bytes\":" << au.size()
                      << ",\"reference_i420\":\"" << referencePath << "\",\"source_i420\":\"" << sourcePath
                      << "\",\"max_source_error\":" << maximum << ",\"mean_source_error\":" << mean
                      << ",\"nal_types\":[";
            for (size_t j = 0; j < types.size(); ++j) std::cout << (j ? "," : "") << types[j];
            std::cout << "]}";
        }
        save(root / name / ("stream." + (hevc ? std::string("hevc") : std::string("h264"))), stream);
        for (const auto& sample : samples) CFRelease(sample.sample);
        std::cout << "]}";
    }
    std::cout << "]}\n";
}
