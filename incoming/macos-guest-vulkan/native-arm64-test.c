// Execute the candidate's exact ARM instructions and original inline validators
// in private JIT memory. Replace only external PLT targets with checked callbacks.
// This is an ABI/locking test, not an Android loader or Vulkan protocol test.
#include <libkern/OSCacheControl.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <unwind.h>

#if !defined(__aarch64__)
#error This test requires a native ARM64 host.
#endif

#define REQUIRE(condition) do { if (!(condition)) { \
    fprintf(stderr, "failed: %s at line %d\n", #condition, __LINE__); abort(); \
} } while (0)

typedef struct Node { struct Node* next; uint64_t hash, key; } Node;
typedef struct BindInfo {
    uint32_t type, padding;
    const void* next;
    uint64_t image, memory, offset;
} BindInfo;
typedef struct Case {
    uint64_t device, image, memory, offset;
    BindInfo infos[2];
    int mode, encoder_result;
    unsigned expected_count;
    _Atomic unsigned calls;
} Case;
typedef int (*Bind)(void*, void*, int, uint64_t, uint64_t, uint64_t, uint64_t);
typedef int (*Bind2)(void*, void*, int, uint64_t, unsigned, const BindInfo*);
typedef void (*KHR)(uint64_t, uint64_t, uint64_t, const void*);

static _Alignas(16) unsigned char impl[0x700];
static void* tracker[2] = {NULL, impl};
static pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
static Node memory_node, image_node, memory_previous, image_previous;
static Node* memory_buckets[4];
static Node* image_buckets[4];
static _Thread_local Case* current;
static _Thread_local int depth;
static _Atomic unsigned locks, unlocks, encodes, khr_calls;
static _Atomic unsigned trace_attempts, trace_checks, traced_wrappers;
static uintptr_t candidate_base;
extern void __register_frame(const void* frame);
static Bind bind_call;
static Bind2 bind2_call, bind2khr_call;

typedef struct Trace { unsigned count; int wrapper, caller; } Trace;
static _Unwind_Reason_Code frame_seen(struct _Unwind_Context* context, void* opaque) {
    Trace* trace = opaque;
    uintptr_t pc = _Unwind_GetIP(context);
    if (pc >= candidate_base + 0x3c86cc && pc < candidate_base + 0x3c87e0) {
        trace->wrapper = 1;
        atomic_fetch_or(&traced_wrappers, pc < candidate_base + 0x3c875c ? 1U : 2U);
    } else if (trace->wrapper && pc != 0) {
        trace->caller = 1;
    }
    return ++trace->count >= 32 ? _URC_END_OF_STACK : _URC_NO_REASON;
}

static void check_unwind(void) {
    unsigned attempt = atomic_fetch_add(&trace_attempts, 1);
    if (attempt >= 24 && attempt % 10000 != 0) return;
    Trace trace = {0};
    _Unwind_Backtrace(frame_seen, &trace);
    REQUIRE(trace.wrapper && trace.caller);
    atomic_fetch_add(&trace_checks, 1);
}

static uint32_t read32(const void* address) {
    uint32_t result;
    memcpy(&result, address, 4);
    return result;
}

static void register_wrapper_frame(unsigned char* image) {
    // Darwin's __register_frame takes one FDE. Its PC-relative addresses remain
    // valid because this private image uses the ELF's original VA/file offsets.
    size_t offset = 20;
    while (offset < 441924) {
        unsigned char* record = image + 0x188278 + offset;
        uint32_t length = read32(record);
        if (!length) break;
        REQUIRE(length >= 16 && offset + length + 4 <= 441924);
        uintptr_t pc = (uintptr_t)(record + 8) + (int32_t)read32(record + 8);
        if (pc == (uintptr_t)image + 0x3c86c8) {
            __register_frame(record);
            return;
        }
        offset += length + 4;
    }
    REQUIRE(0 && "wrapper FDE missing");
}

static uint64_t handle_hash(uint64_t handle) {
    const uint64_t multiplier = UINT64_C(0x9ddfea08eb382d69);
    uint64_t a = (uint64_t)((uint32_t)handle << 3) + 8;
    uint64_t b = handle >> 32;
    a = (a ^ b) * multiplier;
    b ^= a >> 47;
    b = (b ^ a) * multiplier;
    return (b ^ (b >> 47)) * multiplier;
}

static void store64(size_t offset, uint64_t value) { memcpy(impl + offset, &value, 8); }
static void clear_maps(void) { store64(0xf0, 0); store64(0x140, 0); }

static int checked_lock(void* address) {
    REQUIRE(address == impl + 0x660 && depth == 0 && current != NULL);
    REQUIRE(pthread_mutex_lock(&mutex) == 0);
    depth = 1;
    check_unwind();
    // Handles are visible only inside the real mutex interval. An unguarded
    // validator deterministically rejects a valid handle (negative control).
    memory_node = (Node){NULL, handle_hash(current->memory), current->memory};
    image_node = (Node){NULL, handle_hash(current->image), current->image};
    memory_previous.next = &memory_node;
    image_previous.next = &image_node;
    memory_buckets[0] = &memory_previous;
    image_buckets[0] = &image_previous;
    store64(0xe8, (uintptr_t)memory_buckets);
    store64(0x138, (uintptr_t)image_buckets);
    store64(0xf0, current->mode == 1 ? 0 : 1);
    store64(0x140, current->mode == 2 ? 0 : 1);
    atomic_fetch_add(&locks, 1);
    return 0;
}

static int checked_unlock(void* address) {
    REQUIRE(address == impl + 0x660 && depth == 1);
    clear_maps();
    depth = 0;
    atomic_fetch_add(&unlocks, 1);
    REQUIRE(pthread_mutex_unlock(&mutex) == 0);
    return 0;
}

static int encoder_bind(void* context, uint64_t device, uint64_t image,
                        uint64_t memory, uint64_t offset, unsigned do_lock) {
    Case* c = context;
    REQUIRE(c == current && depth == 0 && do_lock == 1);
    check_unwind();
    REQUIRE(device == c->device && image == c->image && memory == c->memory && offset == c->offset);
    atomic_fetch_add(&encodes, 1);
    atomic_fetch_add(&c->calls, 1);
    return c->encoder_result;
}

static int encoder_bind2(void* context, uint64_t device, unsigned count,
                         const BindInfo* infos, unsigned do_lock) {
    Case* c = context;
    REQUIRE(c == current && depth == 0 && do_lock == 1);
    check_unwind();
    REQUIRE(device == c->device && count == c->expected_count && infos == c->infos);
    atomic_fetch_add(&encodes, 1);
    atomic_fetch_add(&c->calls, 1);
    return c->encoder_result;
}

static void core_entry(uint64_t device, uint64_t descriptor, uint64_t update_template, const void* data) {
    REQUIRE(current != NULL && device == current->device && descriptor == current->image);
    REQUIRE(update_template == current->memory && data == current->infos);
    atomic_fetch_add(&khr_calls, 1);
}

static void jump_to(unsigned char* image, size_t address, void* callback) {
    uint32_t code[2] = {0x58000050, 0xd61f0200}; // ldr x16, .+8; br x16
    memcpy(image + address, code, 8);
    memcpy(image + address + 8, &callback, 8);
}

static unsigned char* load_code(const char* path) {
    FILE* file = fopen(path, "rb");
    REQUIRE(file != NULL && fseek(file, 0, SEEK_END) == 0);
    long size = ftell(file);
    REQUIRE(size == 4409384 && fseek(file, 0, SEEK_SET) == 0);
    unsigned char* source = malloc((size_t)size);
    REQUIRE(source != NULL && fread(source, 1, (size_t)size, file) == (size_t)size);
    REQUIRE(fclose(file) == 0);
    size_t length = ((size_t)size + getpagesize() - 1) & ~(size_t)(getpagesize() - 1);
    unsigned char* image = mmap(NULL, length, PROT_READ | PROT_WRITE | PROT_EXEC,
                                MAP_PRIVATE | MAP_ANON | MAP_JIT, -1, 0);
    REQUIRE(image != MAP_FAILED);
    pthread_jit_write_protect_np(0);
    memcpy(image, source, (size_t)size);
    free(source);
    jump_to(image, 0x3fa510, checked_lock);
    jump_to(image, 0x3fa540, checked_unlock);
    jump_to(image, 0x3faf00, encoder_bind);
    jump_to(image, 0x3faf20, encoder_bind2);
    // Route-only check: bypass Android TLS/tracing and Vulkan protocol internals.
    jump_to(image, 0x3ea5a0, core_entry);
    sys_icache_invalidate(image, length);
    pthread_jit_write_protect_np(1);
    return image;
}

static void setup_case(Case* c, unsigned id) {
    memset(c, 0, sizeof(*c));
    c->device = UINT64_C(0x1100200030004000) + id;
    c->image = UINT64_C(0x2200300040005000) + id;
    c->memory = UINT64_C(0x3300400050006000) + id;
    c->offset = UINT64_C(0x4400500060007000) + id;
    c->encoder_result = (int)(id + 7);
    c->expected_count = 1;
    c->infos[0] = (BindInfo){1000157001, 0, NULL, c->image, c->memory, c->offset};
    c->infos[1] = c->infos[0];
}

static void* stress(void* opaque) {
    Case* c = opaque;
    current = c;
    for (unsigned i = 0; i < 5000; ++i) {
        REQUIRE(bind_call(tracker, c, 0, c->device, c->image, c->memory, c->offset) == c->encoder_result);
        REQUIRE(bind2_call(tracker, c, 0, c->device, 1, c->infos) == c->encoder_result);
        REQUIRE(bind2khr_call(tracker, c, 0, c->device, 1, c->infos) == c->encoder_result);
    }
    return NULL;
}

int main(int argc, char** argv) {
    REQUIRE(argc == 3 && sizeof(BindInfo) == 40);
    unsigned char* original = load_code(argv[1]);
    unsigned char* candidate = load_code(argv[2]);
    candidate_base = (uintptr_t)candidate;
    register_wrapper_frame(candidate);
    bind_call = (Bind)(candidate + 0x213d68);
    bind2_call = (Bind2)(candidate + 0x213f40);
    bind2khr_call = (Bind2)(candidate + 0x214148);
    Case c;
    setup_case(&c, 1);
    current = &c;
    clear_maps();
    REQUIRE(((Bind)(original + 0x213d68))(tracker, &c, 0, c.device, c.image, c.memory, c.offset) == -2);
    REQUIRE(atomic_load(&locks) == 0 && atomic_load(&c.calls) == 0);
    REQUIRE(bind_call(tracker, &c, 0, c.device, c.image, c.memory, c.offset) == c.encoder_result);
    REQUIRE(atomic_load(&c.calls) == 1 && atomic_load(&locks) == 1);
    for (int result = -123456; result <= 123456; result += 123456) {
        c.encoder_result = result;
        REQUIRE(bind_call(tracker, &c, 99, c.device, c.image, c.memory, c.offset) == result);
        REQUIRE(bind2_call(tracker, &c, 99, c.device, 1, c.infos) == result);
        REQUIRE(bind2khr_call(tracker, &c, 99, c.device, 1, c.infos) == result);
    }
    unsigned before = atomic_load(&c.calls);
    for (int mode = 1; mode <= 2; ++mode) {
        c.mode = mode;
        REQUIRE(bind_call(tracker, &c, 0, c.device, c.image, c.memory, c.offset) == -2);
        REQUIRE(bind2_call(tracker, &c, 0, c.device, 1, c.infos) == -2);
        REQUIRE(bind2khr_call(tracker, &c, 0, c.device, 1, c.infos) == -2);
    }
    c.mode = 0;
    REQUIRE(bind2_call(tracker, &c, 0, c.device, 0, c.infos) == -2);
    REQUIRE(bind2_call(tracker, &c, 0, c.device, 1, NULL) == -2);
    c.infos[1].image ^= 0x8000;
    REQUIRE(bind2_call(tracker, &c, 0, c.device, 2, c.infos) == -2);
    REQUIRE(atomic_load(&c.calls) == before);
    // A null VkDeviceMemory is permitted by the original bind2 validator.
    c.infos[0].memory = 0;
    c.mode = 1;
    REQUIRE(bind2_call(tracker, &c, 0, c.device, 1, c.infos) == c.encoder_result);
    REQUIRE(bind2khr_call(tracker, &c, 0, c.device, 1, c.infos) == c.encoder_result);
    ((KHR)(candidate + 0x3ebd10))(c.device, c.image, c.memory, c.infos);
    ((KHR)(candidate + 0x3c86c8))(c.device, c.image, c.memory, c.infos);
    REQUIRE(atomic_load(&khr_calls) == 2 && depth == 0);

    Case cases[4];
    pthread_t workers[4];
    for (unsigned i = 0; i < 4; ++i) {
        setup_case(&cases[i], i + 10);
        REQUIRE(pthread_create(&workers[i], NULL, stress, &cases[i]) == 0);
    }
    for (unsigned i = 0; i < 4; ++i) REQUIRE(pthread_join(workers[i], NULL) == 0);
    REQUIRE(atomic_load(&locks) == atomic_load(&unlocks));
    REQUIRE(atomic_load(&encodes) == 60012);
    REQUIRE(atomic_load(&trace_checks) >= 24 && atomic_load(&traced_wrappers) == 3);
    printf("{\"ok\":true,\"actual_arm64_instructions\":true,"
           "\"original_validator_bytes\":true,\"negative_control\":true,"
           "\"threads\":4,\"stress_calls\":60000,\"encoder_calls\":%u,"
           "\"lock_calls\":%u,\"unlock_calls\":%u,\"khr_route_calls\":%u,"
           "\"host_call_outside_lock\":true,\"actual_unwind_checks\":%u,"
           "\"both_wrapper_callers_recovered\":true,\"scope\":\"private native ABI/locking/unwind only\"}\n",
           atomic_load(&encodes), atomic_load(&locks), atomic_load(&unlocks), atomic_load(&khr_calls),
           atomic_load(&trace_checks));
    return 0;
}
