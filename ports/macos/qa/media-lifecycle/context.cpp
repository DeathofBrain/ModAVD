// The complete production context TU and public headers are compiled separately.
// Codec implementations/factories, allocation and address-space control are shims.
#include "host-common/address_space_host_media.h"
#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <memory>
#include <string>
using namespace android::emulation;

static uint64_t checks = 0, snapshots = 0, nextGpa = 0x100000000ull;
static uint64_t generation = 0, currentGpa = 0, removals = 0, allocations = 0, frees = 0;
static std::array<uint64_t, 3> created{}, destroyed{}, loaded{}, saved{}, live{};
static void require(bool condition, const char* description) {
    ++checks;
    if (!condition) { std::fprintf(stderr, "failure: %s\n", description); std::exit(1); }
}
struct Mapping { void* pointer; uint64_t size, generation; };
static std::map<uint64_t, Mapping> mappings;
static std::map<void*, size_t> allocated;
static std::map<uint64_t, uint64_t> owners;

namespace android {
void* aligned_buf_alloc(size_t alignment, size_t size) {
    void* pointer = nullptr;
    require(posix_memalign(&pointer, alignment, size) == 0, "aligned allocation");
    allocated.emplace(pointer, size); ++allocations;
    *static_cast<uint64_t*>(pointer) = 0xaabbccdd11223344ull;
    return pointer;
}
void aligned_buf_free(void* pointer) {
    require(allocated.erase(pointer) == 1, "free exactly one owned allocation");
    ++frees; std::free(pointer);
}
}
static int addMapping(uint64_t gpa, void* pointer, uint64_t size) {
    require(mappings.find(gpa) == mappings.end(), "no overlapping test mapping");
    require(allocated.at(pointer) == size, "mapping uses actual context allocation size");
    mappings.emplace(gpa, Mapping{pointer, size, ++generation}); currentGpa = gpa;
    return 0;
}
static int removeMapping(uint64_t gpa, void* pointer, uint64_t size) {
    require(owners[gpa] == 0, "mapping removed with live decoders");
    const auto& mapping = mappings.at(gpa);
    require(mapping.pointer == pointer && mapping.size == size, "remove exact owned mapping");
    mappings.erase(gpa); ++removals; return 0;
}
static void* hostPointer(uint64_t gpa) {
    currentGpa = gpa; return mappings.at(gpa).pointer;
}
static address_space_device_control_ops operations() {
    address_space_device_control_ops result{};
    result.add_memory_mapping = addMapping;
    result.remove_memory_mapping = removeMapping;
    result.get_host_ptr = hostPointer;
    return result;
}

template<class Base, size_t Index> class CountedDecoder : public Base {
    uint64_t mGpa, mGeneration, mPayload = 0xc000000000000000ull + Index;
    uint64_t* mRetained;
public:
    CountedDecoder() : mGpa(currentGpa), mGeneration(mappings.at(mGpa).generation),
                       mRetained(static_cast<uint64_t*>(mappings.at(mGpa).pointer)) {
        ++created[Index]; ++live[Index]; ++owners[mGpa];
    }
    ~CountedDecoder() override {
        auto mapping = mappings.find(mGpa);
        require(mapping != mappings.end() && mapping->second.generation == mGeneration,
                "decoder teardown must precede mapping removal/replacement");
        // Actual retained-pointer read remains covered by ASan.
        require(*mRetained == 0xaabbccdd11223344ull, "decoder retained buffer still valid");
        --live[Index]; --owners[mGpa]; ++destroyed[Index];
    }
    void handlePing(MediaCodecType, MediaOperation, void* pointer) override {
        require(pointer == mRetained, "real context dispatch passes owned mapped pointer");
    }
    void save(android::base::Stream* stream) const override {
        ++saved[Index]; stream->putBe64(mPayload);
    }
    bool load(android::base::Stream* stream) override {
        ++loaded[Index]; mPayload = stream->getBe64();
        require(mPayload == 0xc000000000000000ull + Index, "snapshot codec type/payload pairing");
        return true;
    }
};
MediaVpxDecoder* MediaVpxDecoder::create() { return new CountedDecoder<MediaVpxDecoder, 0>; }
MediaH264Decoder* MediaH264Decoder::create() { return new CountedDecoder<MediaH264Decoder, 1>; }
MediaHevcDecoder* MediaHevcDecoder::create() { return new CountedDecoder<MediaHevcDecoder, 2>; }

static std::unique_ptr<AddressSpaceHostMediaContext> make(
        const address_space_device_control_ops* ops, uint64_t gpa, bool snapshot = false) {
    AddressSpaceCreateInfo info{}; info.physAddr = gpa; info.fromSnapshot = snapshot;
    auto result = std::make_unique<AddressSpaceHostMediaContext>(info, ops);
    require(result->getDeviceType() == AddressSpaceDeviceType::Media, "real media device type");
    return result;
}
static void activate(AddressSpaceHostMediaContext& context, uint64_t gpa, unsigned mask) {
    const std::array<MediaCodecType, 3> codecs = {
        MediaCodecType::VP9Codec, MediaCodecType::H264Codec, MediaCodecType::HevcCodec};
    for (unsigned i = 0; i < 3; ++i) if (mask & (1u << i)) {
        AddressSpaceDevicePingInfo ping{}; ping.phys_addr = gpa;
        ping.metadata = (static_cast<uint64_t>(codecs[i]) << 56) |
                        static_cast<uint64_t>(MediaOperation::InitContext);
        context.perform(&ping);
    }
}
static void destructorCase(const address_space_device_control_ops* ops) {
    uint64_t gpa = nextGpa; nextGpa += 0x4000000ull;
    auto context = make(ops, gpa); activate(*context, gpa, 7); context.reset();
    require(mappings.empty() && allocated.empty(), "destructor releases context buffer");
}
static void loadCase(const address_space_device_control_ops* ops) {
    uint64_t gpa = nextGpa; nextGpa += 0x4000000ull;
    auto context = make(ops, gpa); activate(*context, gpa, 7);
    android::base::Stream empty; empty.putBe64(gpa); empty.putBe32(0);
    require(context->load(&empty), "load empty snapshot into populated context");
    require(live[0] + live[1] + live[2] == 0, "load clears codecs absent from incoming snapshot");
    require(empty.pos == empty.bytes.size(), "empty snapshot fully consumed");
    context.reset();
    require(mappings.empty() && allocated.empty(), "load/destructor release all mappings");
}
static void snapshotCase(const address_space_device_control_ops* ops, unsigned mask) {
    constexpr uint64_t sentinel = 0x6ac47e3208fa59bdull;
    uint64_t gpa = nextGpa; nextGpa += 0x4000000ull;
    auto context = make(ops, gpa); activate(*context, gpa, mask);
    android::base::Stream snapshot; context->save(&snapshot);
    auto inspection = snapshot;
    require(inspection.getBe64() == gpa, "snapshot retains guest address");
    unsigned count = 0; for (unsigned i = 0; i < 3; ++i) count += !!(mask & (1u << i));
    require(inspection.getBe32() == count, "snapshot active count must include HEVC");
    auto originalBytes = snapshot.bytes; snapshot.putBe64(sentinel);
    context.reset();
    auto restored = make(ops, gpa, true);
    require(restored->load(&snapshot), "restore real context snapshot");
    require(snapshot.getBe64() == sentinel && snapshot.pos == snapshot.bytes.size(),
            "snapshot consumes exact payload and preserves next-record boundary");
    android::base::Stream repeated; restored->save(&repeated);
    require(repeated.bytes == originalBytes, "VPx/H264/HEVC snapshot byte roundtrip");
    for (unsigned i = 0; i < 3; ++i)
        require(live[i] == !!(mask & (1u << i)), "restore exactly the requested codec set");
    restored.reset(); ++snapshots;
    require(mappings.empty() && allocated.empty(), "roundtrip releases mapping and buffer");
}
int main(int argc, char** argv) {
    std::string scenario = argc == 2 ? argv[1] : "all";
    auto ops = operations();
    if (scenario == "all") {
        for (int i = 0; i < 20; ++i) { destructorCase(&ops); loadCase(&ops); }
        for (int i = 0; i < 10; ++i) for (unsigned mask = 0; mask < 8; ++mask) snapshotCase(&ops, mask);
    } else if (scenario == "destructor") destructorCase(&ops);
    else if (scenario == "load") loadCase(&ops);
    else if (scenario == "snapshot") snapshotCase(&ops, 7);
    else return 2;
    for (size_t i = 0; i < 3; ++i)
        require(live[i] == 0 && created[i] == destroyed[i], "all counted decoders released");
    require(allocations == frees && allocations == removals, "every allocation mapping removed then freed");
    std::printf("{\"pass\":true,\"checks\":%llu,\"snapshots\":%llu,\"created\":[%llu,%llu,%llu],"
                "\"destroyed\":[%llu,%llu,%llu],\"loaded\":[%llu,%llu,%llu],\"live\":[%llu,%llu,%llu],"
                "\"allocations\":%llu,\"removals\":%llu,\"frees\":%llu}\n",
        (unsigned long long)checks, (unsigned long long)snapshots,
        (unsigned long long)created[0], (unsigned long long)created[1], (unsigned long long)created[2],
        (unsigned long long)destroyed[0], (unsigned long long)destroyed[1], (unsigned long long)destroyed[2],
        (unsigned long long)loaded[0], (unsigned long long)loaded[1], (unsigned long long)loaded[2],
        (unsigned long long)live[0], (unsigned long long)live[1], (unsigned long long)live[2],
        (unsigned long long)allocations, (unsigned long long)removals, (unsigned long long)frees);
}
