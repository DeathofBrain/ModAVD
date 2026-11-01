# Darwin RAM reclamation findings and minimum safe backend

This is an implementation design and bounded native experiment, not a claim
that the Android emulator's RAM advantage has been validated on macOS.

## What the current reporting handler guarantees

`hw/virtio/virtio-balloon.c::virtio_balloon_handle_report()` retains a popped
`VirtQueueElement` until after discard, then pushes it back and notifies the
guest. The reporting protocol keeps those guest free buddy pages isolated until
the element is returned. This lifetime is the opportunity to replace backing.
It does not by itself stop other host threads or vCPUs.

The handler processes each scatter/gather entry independently. It does not
combine four separate 4 KiB reports into a 16 KiB host page. It checks
`ram_offset | size` against `qemu_ram_pagesize(rb)` and skips unaligned entries
or ranges crossing the RAMBlock's used length. On Apple Silicon, ordinary RAM
blocks use `getpagesize()` = 16 KiB, so the current code already avoids rounding
outwards into neighboring live guest pages.

A 4 KiB guest's aligned buddy block of order two or greater is naturally at
least 16 KiB, so suitably aligned reports can pass directly. The chosen Android
kernel's reporting order and actual negotiated reporting must still be read
from the running image; this source repository does not establish them. A set
of separate 4 KiB entries can all be skipped despite referring to free memory.

The minimum implementation should keep the skip rule. A later improvement may
combine ranges only while every contributing entry remains owned in the same
outstanding element, or select only complete host pages contained inside an
entry. It must never round a partially reported host page outwards, or remember
an old free report after its element has been returned to the guest.

## Native observations

On Apple Silicon macOS 26.6.2, a 64 MiB private or shared anonymous mapping was
filled, mapped into HVF, and read by a real single AArch64 vCPU at every 4 KiB
boundary before advice was applied:

| Operation with the HVF mapping retained | Observation |
| --- | --- |
| MADV_DONTNEED | Success; old contents remained; resident size and footprint did not fall immediately. |
| MADV_FREE | Success; old contents remained; resident size and footprint did not fall immediately. Memory pressure behavior was not measured. |
| MADV_FREE_REUSABLE | Footprint fell and reusable accounting rose; resident size stayed. Subsequent guest writes did not restore the accounting. |
| MADV_ZERO | The host and guest read zero, but resident size and footprint did not fall. This advice is also not available on every target kernel. |

`MADV_FREE_REUSABLE` without a corresponding `MADV_FREE_REUSE` at every future
guest reuse is not a correct backend. The host does not observe the guest's
allocator lifecycle. A lower footprint value in this experiment does not prove
physical reclamation.

Explicitly unmapping the HVF range, replacing the same host virtual range with
a fresh anonymous mapping, and mapping it back at the same guest physical
address did reduce resident size and footprint, with `reusable=0`. Guest and
host saw the same zeros and later guest writes. A 2 MiB range starting at a
16 KiB interior offset also preserved every tested 4 KiB boundary outside it.
The probe executes no vCPU during this sequence; it has no Android, DMA,
renderer, snapshot activity, concurrent mappings, or failure injection.

### Bounded multi-vCPU transaction experiment

`experimental-hvf-transaction-probe.c` extends the native investigation to
2, 4, and 8 vCPUs. Each process maps 64 MiB of data, a 16 KiB code page, and
at most 1 MiB of result buffers into one HVF guest. The cases run sequentially.
The vCPUs first read each 4 KiB boundary, then run an ongoing read loop. The
host calls `hv_vcpus_exit()` and waits for every worker's acknowledgement that
`hv_vcpu_run()` has returned before changing any mapping. Every vCPU later
reads the full set of sampled boundaries again. This is an explicit native
handshake, not an implementation or test of QEMU's pause machinery.

Before replacing backing, the experiment allocates fresh anonymous memory and
retains the old backing through a temporary `mach_vm_remap()` alias. It then
unmaps the exact GPA range, replaces the original HVA from the fresh backing,
and maps the same GPA back. The old alias is held until a successful HVF map.
An unaligned unmap and an unaligned remap inject real HVF failures; both return
`0xfae94003` (`HV_BAD_ARGUMENT`). Failed unmap leaves the original mapping;
failed remap restores the HVA from the old alias and maps it back before
resuming. Restoration errors abort the experiment without resuming vCPUs.

All six 2/4/8-vCPU cases passed for a full 64 MiB range and for a 2 MiB interior
range at offset 16 KiB. The sampled words at every 4 KiB boundary retained their
old values after rollback; committed pages read zero; neighbors retained old
data; subsequent guest writes were visible to all vCPUs and the host.
After full-range commit and alias release, resident size fell from roughly
141–143 MB to 7–9 MB, with reusable accounting zero. For the interior range it
fell by about 4.2 MB. Reading the discarded guest pages faulted them back in and
returned resident size to the baseline; they were charged again on reuse.

The same apparent resident decrease also occurs immediately after restoring
the old backing, although its data still exists and is read back correctly.
This is a useful accounting limitation: an instantaneous per-process RSS or
footprint decrease alone does not establish how much physical memory became
available to other processes. System available/compressed memory and pressure
behavior still need measurement. The experiment does not validate every Mach
VM attribute, aliases outside its control, QEMU locks, devices, or Android.

## Default Android RAM allocation is private anonymous

The current macOS runtime passes `-no-snapshot` and explicitly disables
`QuickbootFileBacked`; it does not pass `-mem-path`. The resulting source path
is:

1. `android-qemu2-glue/main.cpp` adds `-mem-path` only when the Quick Boot RAM
   file feature is enabled. Apple hosts also disable it by default unless
   overridden. `-no-snapshot` alone is not the ownership test.
2. `hw/arm/virt.c::machvirt_init()` allocates `mach-virt.ram` through
   `memory_region_allocate_system_memory()`.
3. `numa.c::allocate_system_memory_nonnuma()` without `mem_path` calls
   `memory_region_init_ram_nomigrate()`.
4. `memory.c` forwards to `memory_region_init_ram_shared_nomigrate(..., false)`
   and then `qemu_ram_alloc(..., share=false)`.
5. `exec.c::ram_block_add()` calls the default `qemu_anon_ram_alloc()`;
   `util/oslib-posix.c` forwards `shared=false` to `qemu_ram_mmap()`;
   `util/mmap-alloc.c` uses `MAP_PRIVATE | MAP_ANONYMOUS` for the final mapping.

Thus ordinary default Android system RAM is a candidate for a private-anonymous
backend. `memory-backend-ram` separately forwards its configurable `share`
property, and file-backed Quick Boot RAM is a different backing class. A
repository search found the only custom `phys_mem_set_alloc()` caller in the
s390x KVM path, so native ARM HVF uses the default allocator. These observations
identify the default path; they do not replace per-block ownership tracking.

## Required ownership and mapping metadata

Initially allow only ordinary private anonymous guest RAM allocated by the
default QEMU allocator. Skip at least:

- File backing (`fd != -1`, Quick Boot `ram.img`, or `RAM_MAPPED`).
- Externally managed or preallocated RAM (`RAM_USER_BACKED`, `RAM_PREALLOC`).
- Shared anonymous mappings and any host alias or exported mapping.
- Resizable blocks and custom physical allocators until their lifecycle is supported.
- Read-only or partially protected mappings, unknown permissions, or multiple
  guest-physical aliases of the host range.
- Balloon inhibition, nonzero poison values, snapshot/migration transitions,
  invalid, overflowing, or unaligned ranges.

There is a source-level prerequisite: `qemu_ram_alloc_internal(..., share, ...)`
passes `share` through to allocation but does not set `RAM_SHARED` on the new
block. `ram_block_remap_backing(..., NULL, shared)` clears the flags during
teardown and likewise does not restore this bit. Checking `rb->flags` alone
therefore cannot prove that anonymous backing is private. Record the actual
allocation/backing class explicitly and update it on all remap paths before
enabling replacement.

A minimal metadata prerequisite would classify backing as default-private-anon,
default-shared-anon, file, external, custom, or unknown, and retain a generation
that changes whenever the backing, length, address, or relevant mapping changes.
Classification must be set by the successful allocation/remap path, not inferred
from the absence of a flag or from the command line. Unknown remains ineligible.

`target/arm/hvf.c::mac_slot` does not store mapping permissions. Existing
`hvf_protect_safe()` can also change permissions of a subrange. A new backend
must track effective permissions or reject any modified slot. It must not
restore arbitrary memory as read/write/execute by assumption.

## Minimum transaction

1. While holding the device's element, validate and collect eligible full host
   pages. Keep ordinary VirtQueue completion until the transaction finishes.
   Allocate bookkeeping and a replacement/rollback backing strategy first.
2. Run the transaction in the appropriate serialized QEMU context with the BQL.
   Pause all vCPUs using the existing `pause_all_vcpus()` machinery, preserving
   the prior run state. That routine may release the BQL while waiting, so
   revalidate the element, run state, backing identity, and topology afterwards.
   Do not hold `mem_lock` while waiting for vCPUs to stop.
3. With all vCPUs stopped, acquire the HVF memory lock, exclude concurrent
   topology/protection changes, and verify that each host range has one fully
   understood GPA mapping with unchanged permissions. Host renderer/device and
   snapshot access must also be excluded or proven outside the range.
4. Unmap the exact 16 KiB-aligned GPA range directly through the HVF transaction
   helper. The existing `hvf_unmap_safe()` rejects a partial overlap with a
   large slot and cannot be reused unchanged for ordinary reports.
5. Replace only the eligible host range with fresh zero backing, retain its
   virtual address, restore the GPA mapping with its exact original permissions,
   and update any necessary generation/dirty tracking. Keep rollback resources
   until every mapping has been restored. A failed unmap must leave the old
   backing intact; a later failure must restore the old mapping/backing before
   any CPU resumes. If restoration itself fails, stop the VM with a diagnostic.
6. Release the mapping lock, restore the previous CPU run state, and return the
   element only after all ranges have a valid guest mapping. Count actual
   committed replacements separately from attempted/reported bytes.

The native Mach alias experiment supplies one implementable rollback primitive:
retain the old VM object mapping, preallocate new zero backing, overwrite the
same HVA from the new backing, and retain the old object until every HVF mapping
has succeeded. On map failure, overwrite the HVA from the retained old object
and restore HVF with the old permissions. A production version must preserve
the original effective/current/maximum protections and inheritance, account for
the temporary aliases explicitly, and prove that external mappings or pins do
not exist. The experiment uses a known private mapping and `VM_INHERIT_COPY`
for the replaced target; it does not prove all RAMBlock mapping attributes.

For 2, 4, or 8 vCPUs, the safety rule is identical: every affected CPU must have
acknowledged its stop before any mapping changes, and none resumes until every
range is committed or restored. Preserve individual preexisting CPU stop state
as well as the VM run state; an unconditional `resume_all_vcpus()` could restart
a CPU that was already stopped. Since QEMU's wait releases the BQL, a reset,
shutdown, snapshot transition, mapping generation change, or device-generation
change during the wait must cause revalidation or cancellation before unmap.
The report element's lifetime and queue generation must remain protected.

Batch only currently held reports, with a bounded range/byte budget. Costs
include a VM-wide stop and acknowledgement from all vCPUs, VM/HVF lock traffic,
old-object alias and replacement VM entries, at least an HVF unmap and map per
disjoint range, stage-two/TLB work, and host/stage-two faults when Android reuses
the pages. More vCPUs increase kick/wait work and the chance that the slowest
CPU dominates the pause; many fragmented 16 KiB ranges increase syscall cost.
The native experiment uses a deliberate 2 ms run interval and cannot provide
production pause-latency or throughput numbers. Budget selection and batching
must be measured with the actual Android allocation/free workload.

Simply putting a `MAP_FIXED` call inside `ram_block_discard_range()` does not
satisfy these steps. An allocator-backed rollback design and synchronization
with aliases must precede production code. Batch work per reporting element
instead of pausing the machine for every scatter/gather entry.

## Release evidence still needed

Use the selected 4 KiB Android guest with negotiated REPORTING and PAGE_POISON,
then test 1/2/4/8 vCPUs while the guest repeatedly allocates, dirties, frees, and
reuses memory. Confirm the actual report size distribution and discard skip
reasons. Exercise renderer/device access, rapid window/IME automation, suspend,
shutdown, snapshot rejection or synchronization, topology changes, and injected
unmap/replacement/remap failures. Check neighboring guest pages and data checksums
alongside host resident/compressed memory and system available memory. Establish
that memory really becomes available and is correctly charged on reuse; compare
latency and throughput against the baseline. Native helper checks and the
single-vCPU probe provide no substitute for this whole-machine evidence.

No production reclamation patch is exported. The allocation class/generation,
effective HVF permission tracking, report/reset lifetime guard, and exclusion of
untracked host users are still prerequisites. The original Darwin advice path
remains unchanged while these conditions are unresolved.
