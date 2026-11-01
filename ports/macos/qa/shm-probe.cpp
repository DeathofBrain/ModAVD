// Development-only POSIX AVD screenshot probe. No Maa Verge runtime dependency.
#include <array>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <iostream>
#include <regex>
#include <stdexcept>
#include <string>
#include <sys/mman.h>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>
#include <vector>

struct VideoInfo {
    uint32_t width;
    uint32_t height;
    uint32_t fps;
    uint32_t frameNumber;
    uint64_t tsUs;
};
static_assert(sizeof(VideoInfo) == 24);
static_assert(offsetof(VideoInfo, tsUs) == 16);

class Region {
public:
    explicit Region(const std::string& name) {
        fd = shm_open(name.c_str(), O_RDONLY, 0);
        if (fd == -1) throw std::runtime_error(std::string("shm_open: ") + strerror(errno));
        struct stat status {};
        if (fstat(fd, &status) != 0 || status.st_size < 24 || status.st_size > 512 * 1024 * 1024) {
            close(fd);
            throw std::runtime_error("invalid mapping size (expected 24 B .. 512 MiB)");
        }
        size = static_cast<size_t>(status.st_size);
        data = mmap(nullptr, size, PROT_READ, MAP_SHARED, fd, 0);
        if (data == MAP_FAILED) {
            close(fd);
            throw std::runtime_error(std::string("mmap: ") + strerror(errno));
        }
    }
    ~Region() { munmap(data, size); close(fd); }
    Region(const Region&) = delete;
    Region& operator=(const Region&) = delete;
    void* data = nullptr;
    size_t size = 0;
    int fd = -1;
};

size_t frame_bytes(const VideoInfo& info, size_t mapping) {
    if (info.width == 0 || info.height == 0 || info.width > 8192 || info.height > 8192 || info.fps == 0 || info.fps > 1000) {
        throw std::runtime_error("invalid frame dimensions or fps");
    }
    if (info.tsUs == 0) throw std::runtime_error("frame has not been published yet (zero timestamp)");
    const auto payload = uint64_t(info.width) * uint64_t(info.height) * 4;
    if (payload > mapping - sizeof(VideoInfo)) throw std::runtime_error("truncated BGRA payload");
    return static_cast<size_t>(payload);
}

uint64_t fingerprint(const std::vector<uint8_t>& data) {
    uint64_t result = 14695981039346656037ULL;
    for (const auto value : data) { result ^= value; result *= 1099511628211ULL; }
    return result;
}

int probe(const std::string& name, int samples, int delay, bool progress) {
    // The name is exactly what the emulator console returned. SHM_ is Windows-only.
    if (!std::regex_match(name, std::regex("/?videmulator[0-9]{1,8}"))) {
        throw std::runtime_error("expected POSIX videmulator<console-port>, without SHM_");
    }
    Region mapping(name);
    uint32_t first = 0, last = 0;
    std::cout << "{\"name\":\"" << name << "\",\"headerBytes\":24,\"pixelFormat\":\"BGRA\",\"mappingBytes\":"
              << mapping.size << ",\"samples\":[";
    for (int index = 0; index < samples; ++index) {
        VideoInfo before {}, after {};
        std::vector<uint8_t> pixels;
        bool consistent = false;
        for (int attempt = 0; attempt < 50; ++attempt) {
            std::memcpy(&before, mapping.data, sizeof(before));
            const auto count = frame_bytes(before, mapping.size);
            pixels.resize(count);
            std::memcpy(pixels.data(), static_cast<const uint8_t*>(mapping.data) + sizeof(VideoInfo), count);
            std::memcpy(&after, mapping.data, sizeof(after));
            if (std::memcmp(&before, &after, sizeof(before)) == 0) { consistent = true; break; }
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        if (!consistent) throw std::runtime_error("header kept changing during copy; retry while display is quieter");
        if (index == 0) first = before.frameNumber;
        last = before.frameNumber;
        if (index) std::cout << ',';
        std::cout << "{\"width\":" << before.width << ",\"height\":" << before.height << ",\"fps\":" << before.fps
                  << ",\"frameNumber\":" << before.frameNumber << ",\"timestampUs\":" << before.tsUs
                  << ",\"payloadBytes\":" << pixels.size() << ",\"fnv1a64\":\"" << std::hex << fingerprint(pixels)
                  << std::dec << "\",\"unchangedHeaderDuringCopy\":true}";
        if (index + 1 != samples) std::this_thread::sleep_for(std::chrono::milliseconds(delay));
    }
    std::cout << "],\"frameCounterChanged\":" << (first != last ? "true" : "false") << "}\n";
    if (progress && first == last) throw std::runtime_error("no frame progress observed (static screens may legitimately stop publishing)");
    return 0;
}

int main(int argc, char** argv) {
    try {
        std::string name;
        int samples = 5, delay = 100;
        bool progress = false;
        for (int index = 1; index < argc; ++index) {
            const std::string key(argv[index]);
            if (key == "--require-progress") { progress = true; continue; }
            if (index + 1 == argc) throw std::runtime_error("missing option value");
            const std::string value(argv[++index]);
            if (key == "--name") name = value;
            else if (key == "--samples") samples = std::stoi(value);
            else if (key == "--interval-ms") delay = std::stoi(value);
            else throw std::runtime_error("unknown option: " + key);
        }
        if (samples < 1 || samples > 100 || delay < 0 || delay > 1000 || name.empty()) {
            throw std::runtime_error("usage: shm-probe --name videmulator20060 [--samples 5] [--interval-ms 100] [--require-progress]");
        }
        return probe(name, samples, delay, progress);
    } catch (const std::exception& error) {
        std::cerr << "SHM probe failed: " << error.what() << '\n';
        return 1;
    }
}
