#include "gfxstream/host/ring_buffer.h"
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <random>

static void canary(const uint8_t* p,size_t n) { for(size_t i=0;i<n;++i) assert(p[i]==0xa5); }
int main() {
    struct { std::array<uint8_t,128> before; ring_buffer ring; std::array<uint8_t,128> after; } owned{};
    std::array<uint8_t,257+256> storage{};
    std::array<uint8_t,RING_BUFFER_SIZE+256> output{};
    owned.before.fill(0xa5); owned.after.fill(0xa5); storage.fill(0xa5);
    ring_buffer_view view{};
    ring_buffer_view_init(&owned.ring,&view,storage.data()+128,257);
    assert(view.size==256 && view.mask==255 && view.buf==storage.data()+128);
    std::mt19937 random(0x32d4a);
    unsigned reads=0,writes=0,rejected=0;
    for(unsigned test=0;test<10000;++test) {
        const uint32_t read=random(), avail=random()%view.size;
        __atomic_store_n(&owned.ring.read_pos,read,__ATOMIC_SEQ_CST);
        __atomic_store_n(&owned.ring.write_pos,read+avail,__ATOMIC_SEQ_CST);
        output.fill(0xa5);
        const uint32_t wanted=avail ? 1+random()%avail:0;
        assert(ring_buffer_available_read(&owned.ring,&view)==avail);
        assert(ring_buffer_copy_contents(&owned.ring,&view,wanted,output.data()+128)==0);
        ++reads;
        canary(output.data(),128); canary(output.data()+128+wanted,output.size()-128-wanted);
        assert(ring_buffer_copy_contents(&owned.ring,&view,view.size,output.data()+128)==-1); ++rejected;
        const uint32_t free=ring_buffer_available_write(&owned.ring,&view);
        std::array<uint8_t,256> input{}; const uint32_t write=free ? 1+random()%free:0;
        assert(ring_buffer_view_write(&owned.ring,&view,input.data(),write,1)==1); ++writes;
        canary(storage.data(),128); canary(storage.data()+128+view.size,storage.size()-128-view.size);
        // The descriptor ring is a fixed array; arbitrary guest positions are
        // masked independently of guest-visible configuration bytes.
        __atomic_store_n(&owned.ring.read_pos,read,__ATOMIC_SEQ_CST);
        const uint32_t smallAvail=random()%(RING_BUFFER_SIZE);
        __atomic_store_n(&owned.ring.write_pos,read+smallAvail,__ATOMIC_SEQ_CST);
        output.fill(0xa5);
        assert(ring_buffer_copy_contents(&owned.ring,nullptr,smallAvail,output.data()+128)==0); ++reads;
        canary(output.data(),128); canary(output.data()+128+smallAvail,output.size()-128-smallAvail);
        assert(ring_buffer_copy_contents(&owned.ring,nullptr,RING_BUFFER_SIZE,output.data()+128)==-1); ++rejected;
        canary(owned.before.data(),owned.before.size()); canary(owned.after.data(),owned.after.size());
    }
    printf("{\"ok\":true,\"randomized_positions\":10000,\"actual_ring_reads\":%u,\"actual_ring_writes\":%u,\"oversized_reads_rejected\":%u,\"host_view_size_rounded_down\":256,\"all_canaries_intact\":true}\n",reads,writes,rejected);
}
