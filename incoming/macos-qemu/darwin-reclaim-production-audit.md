# Minimum production path after native HVF experiments

This audit is against the pinned QEMU checkout with the 65 original patches and
five Darwin additions. Files under the parent AOSP checkout were read only.
There is no production reclaim patch or new whole-core build here.

## Decision

The current metadata cannot prove that any particular reported range has no
host users. Pausing all vCPUs and inspecting `RAM_SHARED` or `mac_slots` is
insufficient. A range may be mapped by virtio/block I/O, held by Goldfish DMA,
returned by an unpaired HVA lookup, or read through a direct RAM helper outside
the BQL. None of those lifetimes is represented by a per-range counter today.

A bounded implementation can avoid redesigning every renderer lease: allow
only host pages of default private anonymous system RAM that have **never been
exposed to any other host user**. Record a permanent exposure bitmap from RAM
allocation onward, and reserve candidate ranges against new exposures during
replacement. Unsupported or unsized pointer escapes taint the entire RAMBlock.
This is conservative, and can produce zero eligible bytes in a normal GUI
session until a few known callers gain sized access helpers. Its actual useful
coverage must be measured before enabling replacement. Do not infer unexposed
pages merely from the absence of an `address_space_map()` reference.

## Current mapping and host-access surface

Paths in this section are relative to `external/qemu` unless prefixed with
`hardware/google/gfxstream`.

| Entry/path | What it actually retains or exposes | Required handling |
| --- | --- | --- |
| `target/arm/hvf.c:497`, `hvf_set_phys_mem()` → `__hvf_set_memory_with_flags_locked()` | Maps a MemoryRegion's HVA into HVF and keeps one logical slot. Normal mappings use RWX; `mac_slot` has no permission/generation field. | Treat the initial accelerator mapping as an explicitly tracked internal use. Record the successful effective flags and one-to-one HVA/GPA coverage. |
| `target/arm/hvf.c:326/386`, `hvf_map_safe()` / `hvf_unmap_safe()` | Adds/removes exact logical slots, including user-backed graphics memory. Partial overlap aborts. Same-HVA/same-GPA early return does not apply newly requested flags. | Preserve existing behavior; update metadata only for actual successful mutations. Reject normal-RAM aliases and user-backed/external blocks from reclaim. |
| `target/arm/hvf.c:418/425`, `hvf_protect_safe()` / `hvf_remap_safe()` | Changes actual HVF permissions/backing directly without updating `mac_slots`. Called through snapshot MemoryWatch callbacks. | Minimal cold-boot implementation disables reclaim once either path is used. A later implementation needs actual permission intervals and synchronized remap records. |
| `target/arm/hvf.c:249/268`, `hvf_gpa2hva()` / `hvf_hva2gpa()` | Scans slot arrays without taking `mem_lock`. The former returns a raw HVA, without length or release. | Add a synchronized lookup; a raw escaping lookup must permanently exclude the whole block unless the caller supplies a trustworthy extent. |
| `exec.c:3991/4048`, `address_space_map()` / `address_space_unmap()`; `include/sysemu/dma.h:130` | A direct mapping refs/unrefs the **MemoryRegion**, not a byte-range lease. Generic map/unmap covers virtio SGs, Goldfish pipe, DmaMap, and block DMA. | Introduce range exposure/reservation tracking here. The reporting element's own mappings need an explicit internal owner/classification. |
| `dma-helpers.c:104/147`, DMA block request SGs | Holds raw mapped iovecs until asynchronous block completion. vCPU pause does not drain those requests. | Permanently exclude exposed pages initially, or later maintain leases until `dma_blk_unmap()`. Do not wait for I/O completion while holding locks it needs. |
| `hw/virtio/virtio.c:734/852/399`, descriptor map/pop/unmap | Maps all element SGs through the generic DMA API. Inflight virtio-net/block/vsock requests may outlive their notification callback. | Initial permanent exposure policy excludes all non-report mappings; reporting mappings get a narrowly scoped explicit class. |
| `hw/misc/goldfish_pipe.c:629` and `:838/:1226/:1240` | `map_guest_buffer()` maps temporary data plus persistent command/open/signalled buffers. Reset/save/load manage those pointers separately. | Generic DMA tracking catches the mapping range. Permanent exposure avoids relying on a reset or pipe-close event as proof that renderer use has ended. |
| `android-qemu2-glue/emulation/DmaMap.cpp:34/50`; `android/android-emu/android/emulation/DmaMap.cpp:58–137` | Caches generic physical-memory mappings. `getHostAddr()` returns the pointer after releasing its lock; `removeBuffer()` / invalidation can unmap separately. | Keep DMA-exposed pages excluded for the entire backing generation. The DmaMap lock itself is not a renderer read lease. |
| `android/android-emu/android/emulation/GoldfishDma.cpp:21/36` | Host lookup first tries address-space-device host allocations, then DmaMap. `unlock()` only signals `PIPE_WAKE_UNLOCK_DMA`; it does not end a counted host lease. | Do not equate this unlock with safe reclamation of main RAM. Address-space host allocations are external/user-backed and excluded separately. |
| `android-qemu2-glue/qemu-vm-operations-impl.cpp:1117` → `hvf_gpa2hva()` | `physicalMemoryGetAddr()` returns an unpaired raw pointer. `address_space_device.cpp:98/316` stores it as persistent `pingInfo`. | Unsized fallback must exclude the entire source RAMBlock. A sized permanent-borrow API for `sizeof(AddressSpaceDevicePingInfo)` can recover coverage later. |
| `android/android-emu/android/opengles.cpp:550` → gfxstream VM ops | Registers map/unmap user memory plus an unsized lookup callback with the renderer. Direct GL/Vulkan mappings are normally externally allocated host memory, not default system RAM. | Reject those blocks and any HVA alias into normal RAM. The current gfxstream search found lookup callback declarations/registration but no consumer call; still instrument its QEMU boundary. |
| `hardware/google/gfxstream/host/gl/gles2_dec/gles_v2_decoder.cpp:409/432`, `host/channel_stream.cpp:85`, `host/ring_stream.cpp:415` | Renderer threads use Goldfish DMA pointers for copies/stream reads. Their execution is not stopped by `pause_all_vcpus()`. | Permanent exclusion of the original DMA ranges avoids adding renderer-wide fences in the first backend. |
| `exec.c:2730/2758`, `qemu_map_ram_ptr()` / `qemu_ram_ptr_length()`; `memory.c:2093` | Raw access helpers return pointers. Some are transient accesses, some escape to devices. `qemu_map_ram_ptr()` / `memory_region_get_ram_ptr()` do not know a caller's access length. | Audit/sized internal helpers for known transient callers. Every remaining unsized escape fails closed by excluding the whole block. An MR ref or RCU read section does not serialize backing replacement. |
| `exec.c:3573/3680`, direct flatview copies; `include/exec/memory.h:2004`; `memory_ldst.inc.c` | RAM loads/stores can execute under RCU without the BQL; only MMIO branches necessarily acquire it. Virtqueue cached accesses use these helpers too. | Expose/guard the exact known byte range before returning or touching the pointer. A BQL-only reclaim lock cannot exclude these accesses. |
| `hw/display/framebuffer.c:88` → `hw/display/goldfish_fb.c:354` | Obtains the RAMBlock base then adds the framebuffer offset; framebuffer MR references are coarse. | Use a sized range helper for `offset + rows * src_width`; an unsized fallback would conservatively disable the full system RAMBlock after the first display update. |
| `exec.c:4090`, `cpu_physical_memory_get_addr()` | Maps 4 KiB, immediately unmaps, then returns the same raw pointer. TCG lookup fallback uses it. | Not an adequate lifetime primitive. Native HVF reclaim excludes TCG entirely; preserve a permanent-exposure fallback for any other consumer. |
| `exec.c:4225/4243`, RAM iteration; `qemu-vm-operations-impl.cpp:816/885` | Passes raw full-block `hostPtr` to snapshot saver/loader. The loader/saver stores it and uses worker threads. | Initial backend requires snapshots disabled and permanently inhibits reclaim after any snapshot registration/transition. No attempt to infer worker completion from run state alone. |
| `numa.c:612/624`, RAMBlockNotifier; `hw/virtio/vhost.c` | Can expose whole RAM mappings to outside backends. Current notifier consumers include VFIO and x86 SEV, not the ordinary native ARM setup. | Inhibit reclaim with any unsupported RAM notifier/vhost/VFIO backend; do not silently whitelist future registrations. |

The current `qemu_balloon_is_inhibited()` flag is only a global boolean. Its
in-tree writers are the postcopy migration path. It is not a count or proof
that Goldfish DMA, renderer users, snapshots, and external memory are absent.

## A simpler report/reset lifetime boundary

The current handler pops the element before doing work. `virtqueue_pop()` maps
its SGs and increments `vq->inuse`; `virtqueue_push()` unmaps SGs, writes the
used ring, and decrements it (`virtio.c:399/525/852`). `virtio_reset()`
(`:1197`) calls the device reset and then clears queue addresses/caches and
sets `inuse=0`. The balloon reset (`virtio-balloon.c:606`) handles only its
stats element; it has no tracking for a reporting element held over a wait.

Avoid holding any popped report across a BQL-releasing pause:

1. From the balloon queue callback, schedule a serialized main-loop/BQL bottom
   half and return without popping. This also avoids carrying an outer MMIO/
   virtqueue RCU read section or vCPU callback stack into a CPU-stop wait.
   Record a device reference, queue index, device generation, VM
   run generation, and individual CPU stop states. Do not retain an SG/element.
2. Pause vCPUs **before** the first `virtqueue_pop()`, with no `mem_lock` or
   exposure/reservation lock held. The wait releases the BQL.
3. After the wait, revalidate realization, device/queue generation, DRIVER_OK,
   negotiated REPORTING/PAGE_POISON, poison zero, and VM transition state.
   Reacquire the queue by index after checking realization. On invalidation,
   cancel without touching the old queue or popping anything.
4. With CPUs stopped and the BQL continuously held, pop a bounded batch,
   validate/reserve ranges, perform transactions, and complete/free elements.
   No callback into UI/renderer or other BQL-releasing operation is permitted
   between pop and push. A syscalls-only transaction can follow this boundary.
5. Restore only CPUs whose preexisting stop/run state permits it, provided no
   intervening shutdown/reset/run generation invalidated the resume decision.

This keeps the report element within the existing synchronous virtqueue
lifetime. Add a balloon device generation increment on reset/unrealize and any
feature/status change relevant to reporting. There is no need for a deferred
report-element object in the minimum design. If later batching yields the BQL
after pop, explicit inflight ownership and reset cancellation become required.

## Minimum metadata and API surface

Names below describe proposed APIs; none is implemented by these experiments.

| Record | Minimum fields/invariant | Files affected |
| --- | --- | --- |
| RAM backing | Backing kind, allocator identity, base/length, backing generation, permanent unsupported/exported state. Set only after successful allocation/remap. No flags-only inference. Preserve QEMU's actual VM protections/inheritance: allocation also applies `QEMU_MADV_DONTFORK` (`exec.c:2215`). | `include/exec/ram_addr.h`, `exec.c` allocation/from-fd/from-ptr/user-backed, resize/free/remap/teardown paths |
| Host exposure | One permanent bit per 16 KiB host page; unknown extent taints entire block. One transaction reservation per range, protected by a small registry lock. New pointer escapes must serialize with reservation before they can return/touch memory. Never clear exposure at guest reset. | `exec.c`, `memory.c`, `include/exec/memory.h`, `memory_ldst.inc.c`; sized helper for `hw/display/framebuffer.c`; QEMU VM-ops raw lookup boundary |
| Reporting ownership | An explicit internal report owner/class for SG map calls; only actual reporting data SGs are exempt from permanent exposure. Descriptor/used/available ring accesses and every non-report SG remain normal exposures. Reject malformed/outbound SGs and overlapping ambiguous ownership initially. | `hw/virtio/virtio.c`, `include/hw/virtio/virtio.h`, `hw/virtio/virtio-balloon.c` |
| HVF mapping | Successful effective flags, generation, HVA/GPA range, backing identity/generation; complete one-to-one coverage. Global unsupported-state bit for any untracked snapshot protection/remap. Scan all slots under `mem_lock` for HVA aliases. | `target/arm/hvf.c`, `include/sysemu/hvf.h` and snapshot mapping callbacks |
| VM/device lifecycle | Run/reset/snapshot generation, balloon realization/queue generation, active transaction state; previous per-CPU stop state. No snapshot saver/loader may hold raw main-RAM pointers in an eligible generation. | `hw/virtio/virtio-balloon.c`, its device header, `qemu-vm-operations-impl.cpp`; existing CPU pause APIs |

At 4 GiB of RAM, one 16 KiB exposure bitmap costs 32 KiB; a separate reservation
bitmap of the same granularity costs another 32 KiB. Range bookkeeping must
check overflow and cover partial host accesses conservatively. Reclaim itself
still accepts only full aligned host pages, never outward-rounded free reports.

Useful narrow APIs are:

```c
/* Fail closed on unknown backing or unknown extent. */
void qemu_ram_note_host_exposure(RAMBlock *, uint64_t offset, uint64_t length);
void *qemu_ram_borrow_sized(MemoryRegion *, uint64_t offset, uint64_t length);

/* Caller owns the current report element and holds the BQL; CPUs are stopped. */
bool qemu_ram_reserve_reported_range(RAMBlock *, uint64_t offset, uint64_t length,
                                   uint64_t expected_backing_generation,
                                   const ReportOwner *, ReclaimReservation *);
ReclaimResult hvf_replace_reserved_private_pages(ReclaimReservation *);
void qemu_ram_release_reclaim_reservation(ReclaimReservation *);
```

The fresh/old Mach aliases are internal transaction resources, not host
exposures. Preserve old backing until every map succeeds; rollback before any
CPU or element resumes. Failure to restore leaves the VM stopped. A narrow
report transaction API carries the guest-isolation evidence; a generic
`ram_block_discard_range()` caller does not, so do not hide replacement there.

## Lock ordering and scope

Use this order for the mutating phase: BQL → exposure/reservation registry →
HVF `mem_lock`. Keep RAMBlock/MemoryRegion lifetime references; capture and
revalidate backing/topology generations rather than holding a RAM-list lock or
RCU critical section across CPU-stop waits. Never take the reservation or HVF
lock before `pause_all_vcpus()`.

New host exposures briefly acquire the registry lock to taint/check a range
before returning a pointer. Reclaim takes the reservation only if no exposure
exists and holds it through commit/restore. Since older raw pointers to an
eligible page cannot exist under this policy, other host-user locks need not be
acquired by the reclaimer. Do not call `DmaMap::invalidateHostMappings()`, drain
I/O, stop renderer threads, or take renderer/address-space context locks while
holding the registry/HVF locks. Existing order includes DmaMap/context locks →
QEMU mapping or BQL → HVF; acquiring those locks in reverse can deadlock.

No-BQL direct memory helper paths must enter the same exposure gate. RCU alone
only protects object lifetime. HVF map/protect/remap/exit changes need one
consistent mapping epoch under `mem_lock`; no helper may quietly bypass it.

## Actionable next implementation

1. Add metadata and **audit-only** exposure hooks in the independent clone.
   All existing discard behavior remains unchanged. Unit-check overflow,
   4 KiB exposure covering the containing 16 KiB page, alias rejection,
   generation invalidation, reservation versus concurrent first exposure, and
   fail-closed unknown/unsized access. Trace every fallback that excludes a
   complete block; these logs decide which sized callers are worth adding.
2. In the actual Android guest, log negotiated reporting features, report-size
   distribution, private-block identity, and eligible/skipped bytes by concrete
   reason. Check whether normal GUI/DMA activity leaves useful guest-only
   pages. If an unsized HVA escape taints all RAM, keep replacement disabled.
3. Implement the pause-before-pop lifecycle and the HVF transaction helper only
   after all ingress hooks are complete. Add reset/shutdown/snapshot/generation
   cancellation tests and fault-injected restore tests at the QEMU interface.
4. Enable an explicit development mode for whole-machine 2/4/8-vCPU allocation,
   graphics, I/O, and reuse tests, then measure available/compressed memory and
   frame/latency effects. Keep release default off until this evidence exists.

If audit coverage is poor, the next mechanism is sized lifetime leases for
raw HVA users plus renderer DMA read leases/fences, replacing permanent
exclusion. That is a larger follow-up. It is not necessary for the conservative
guest-only subset, but the subset is not yet proved or implemented in this
checkout.
