# Native Darwin/HVF private backend ingress review

This review covers the v6 private candidate against QEMU HEAD
`568f0a3e755118d5dddd13bbdd821146d75f5711`. It authorizes bounded validation in
one disposable Android guest. It does not certify other accelerators,
architectures, hostile protocol correctness, or physical-memory benefit.

## RAMBlock pointer consumers

A directed `exec/ram_addr.h` include search yields 15 implementation files.
The native ARM/HVF candidates are `exec.c`, `memory.c`, `migration/ram.c`,
`target/arm/hvf.c`, TCG `cputlb.c`, and Goldfish tty/pic. The other consumers
are KVM/PPC/s390 or x86 HVF/AEHD/WHPX and do not execute in this native ARM
accelerator configuration. Goldfish tty/pic do not directly read `RAMBlock.host`.

- `exec.c` ordinary scalar, flatview, address-space, map and returned RAM pointer
  paths permanently expose complete actual host pages before returning a pointer.
  Unsized legacy exports exclude their whole intersecting block. Generic RAM
  iterators exclude blocks before invoking callbacks. User-backed and external
  allocations expose every intersection with existing RAM; overflowing addresses
  inhibit globally. The report-only map owner is selected by the balloon queue,
  separate from ordinary pointer exports.
- `ramblock_ptr` has only four call sites, all in `exec.c`: dirty-code invalidation,
  backing remap, and the audited pointer helpers. Dirty-code invalidation is
  metadata/TCG bookkeeping; remap invalidates the block before replacing backing.
- `memory.c` direct host loads/stores are `memory_region_ram_device_read/write`.
  Those MemoryRegions are created by `memory_region_init_ram_device_ptr` through
  `memory_region_init_ram_ptr`/`qemu_ram_alloc_from_ptr`, whose backing is external,
  never private-transaction eligible. Registration audits aliases of known RAM.
- Migration direct-host consumers are globally inhibited at save/load setup,
  before worker work; snapshot RAM registration and HVF protection/remap also
  inhibit the entire process, including future blocks. A callback installation
  alone does not inhibit an otherwise ordinary guest.
- Native ARM HVF uses the explicit internal registration helper for mandatory
  guest mappings. Exported GPA-to-HVA lookup is sized or conservatively unsized.
  The effective mapping registry records successful syscalls only, rejects gaps,
  aliases and saturated/incomplete generations, and supplies actual flags to the
  transaction. No coarse fallback can authorize replacement.

## Renderer and asynchronous consumers

Checked DMA borrows validate full registered extents while the owning mapping
lock is held, then mark host pages permanently before escape. Common RGBA8 RC
commands hold the actual ColorBuffer and FrameBuffer lock, validate exact byte
sizes, and establish/restore GL pixel state in the actual context. Unknown
formats keep legacy functionality and conservatively exclude the affected block.
Normal channel buffers are local vectors; checked ASG rings use the host-owned
buffer size. Type-2 raw getPtr is fatal/unreachable and remains disabled.

Media guest-controlled slot/parser/output arithmetic has no complete bounded
ownership proof. The Media context enters a balanced global lifetime inhibit
before allocation or pointer publication. Its three dispatchers now delete all
plugins; decoder teardown completes before alias deallocation and leave.

External ASG raw HostMemInfo mappings also lack retained allocation extent and
owner proof. Only a single-use synchronous producer certificate for the actual
retained RingBlob's exact map and actual size bypasses a context lifetime guard.
Unknown/raw/missing capability and snapshot contexts enter before allocation or
worker publication. Consumer destruction joins the renderer, storage is freed,
then the guard leaves. A guest-declared blob size cannot certify ownership.

## Synchronization and report ownership

The balloon callback schedules a referenced BH without retaining a popped SG.
CPU pause owns latest resume intent while BQL is temporarily released. The BH
reacquires the current queue and validates VM/queue/device generations, complete
features, poison zero, DRIVER_OK, realization and unchanged stopped CPU topology
before popping. The bounded drain keeps BQL until push. A mutation additionally
requires the active pause owner, native HVF, private actual backing, aligned
complete host pages, no external lifetime or permanent inhibit, a metadata
reservation, and one exact effective HVF interval without a host alias.

Transactions hold BQL -> audit/reservation mutex -> HVF registry lock. Pointer
exports audit before escape. External lifetime registry enters audit while
holding its own mutex; transactions never acquire that registry, contexts, or
renderer locks. Sized GPA lookup releases HVF before audit and revalidates its
interval generation, preventing a reverse lock edge.

Virtio 1.2 section 5.5.6.7 and the exact deployed Android kernel
`7e35917775b8` permit zeroing reported pages when PAGE_POISON is absent. With
PAGE_POISON present only configured poison zero is accepted. The kernel isolates
free buddy pages until the used-ring acknowledgement. Full feature changes still
cancel the scheduled report. See the retained protocol source receipts below.

## Independent evidence

- `.macos-work/qa/dma-renderer-ingress-review-rgba8-20261101.json`
- `.macos-work/qa/renderer-ram-pointer-audit-20261101.md`
- `.macos-work/qa/dma-native-pixel-store-rgba8-20261101.json`
- `.macos-work/qa/renderer-ring-extent-native-20261101.json`
- `.macos-work/qa/media-dispatcher-cleanup-final-20261101.json`
- `.macos-work/qa/dma-external-lifetime-fair-20261101.json`
- `.macos-work/qa/asg-ownership-lifecycle-20261101.json`
- `.macos-work/qa/page-reporting-protocol-review-20261101.json`
- `.macos-work/qa/page-reporting-kernel-source-20261101.json`
- `evidence/20261101-production-transaction-native-r2/capture.json`
- `evidence/20261101-pause-owner-native/capture.json`

These tests combine exact production components, native HVF/GL execution,
sanitisers, directed source review and real guest runs. Shim tests explicitly
identify the boundary they do not execute. A capability flag is a runtime
installation check, not a substitute for this reviewed source boundary.

## v6 allocation and physical-observation boundary

Private Darwin allocation construction now materializes independent2MiB objects
before publishing an exact base/size/serial receipt. The original continuous HVA,
alignment and tail guard are preserved. Allocation failure destroys the complete
original reservation; receipts retire before native unmap and a mismatched free
cannot retain a certificate. The receipt mutex never enters audit/HVF registries.
RAMBlock qualification rechecks the exact live receipt for every report.
Transactions require whole unexposed objects and never combine fragments from
separate report lifetimes. A4MiB SG can contain two independent2MiB transactions.

With residency telemetry enabled, `mach_vm_page_info` inspects the already
required rollback alias without reading bytes or adding a diagnostic alias.
Direct free/speculative counter samples bracket only destruction of that alias.
These fields are observations and never authorize replacement. Real guest v6
commits all had old PRESENT0: they demonstrate no physical release. The frozen
v6 diagnostic shows22 exposure-fragmented objects and no size/alignment problem.
Any smaller-granule proposal must preserve the same gates and independently
measure construction cost and old-object release. The read-only subobject probe
only estimates candidate PRESENT pages inside current2MiB objects; it cannot
certify future native mapping/alias gates or a finer allocation receipt.

Latest exact-component index:
`.macos-work/qa/exact-v6-native-suite-v2-20261101.json`. Its correctness checks pass;
its global physical windows remain explicitly noisy and are not promoted to a
strict all-window physical pass. Guest evidence and limits are listed in README.
