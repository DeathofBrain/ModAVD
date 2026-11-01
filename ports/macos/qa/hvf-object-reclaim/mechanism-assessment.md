# Darwin HVF backing-object assessment

This is a native feasibility result for a private RAM experiment. It is not a
production RAM feature, an Android workload measurement, or authorization to
enable reclamation by default. The portable probe in this directory tests the
mechanism; the exact private allocator/transaction checks below have additional
scope and retained local evidence.

## Minimum mechanism

Keep QEMU's original contiguous, host-page-aligned virtual allocation and its
tail guard. Before publishing the allocation, replace each 2 MiB extent with an
independent anonymous VM object. Use an untouched temporary RW interior bounded
by two PROT_NONE guard pages, remap that interior at the original address, then
release the entire temporary reservation. The guards prevent adjacent anonymous
RW mappings from coalescing before the independent object is materialized.

The first HVF mapping can still cover the complete continuous virtual range.
Reclaim exactly one whole 2 MiB object per transaction: retain the existing
rollback alias, unmap its GPA from HVF, remap untouched fresh independent backing
at the same HVA, map HVF again, then destroy the temporary fresh reservation and
the rollback alias. Initial and subsequent generations must both use independent
objects. A monolithic fresh replacement would reintroduce the retention problem.

Whole-object alignment is relative to the RAMBlock base, not absolute HVA
alignment. A partial tail is separately constructed but is never eligible for a
whole-object transaction. A single large Darwin mmap can contain multiple VM
regions (observed at 128 MiB), so initial construction validates the planned
extents individually. Preserve current/maximum protections and inheritance.

A successful construction receipt records the live allocation's exact base,
size, and monotonically increasing serial. Retire it before unmapping/freeing
the allocation; a later allocation at the same address must have another serial.
An environment switch, an anonymous-looking region, or a `shared` metadata bit
cannot substitute for construction provenance. Kernel object IDs are useful QA
observations and are not persistent authorization tokens.

Existing stopped-vCPU, report ownership, epoch, exposure/no-alias, private
allocation, and effective-HVF-permission gates remain necessary. Exclude shared,
file, user, custom, preallocated, resizable, and read-only backing. Refuse partial
objects and allocations without a live receipt. On construction failure, do not
publish the partially rebuilt range or silently fall back to qualified
monolithic backing. On uncertain HVF unmap or failed restoration, fail-stop. A
cleanup failure invalidates subsequent no-alias eligibility.

## What the native checks establish

On Apple M4/macOS 26.6.2 with 16 KiB host pages, the guarded helper was tested
without touching initial or fresh guest data. Each chunk's first and last page
metadata identified the same object at offsets zero and `length - page_size`;
different chunks had different IDs, one remaining reference, and no residency.
After actual synthetic-vCPU writes and two replacement generations, each
independent old object had exactly one remaining reference: its transaction's
rollback alias. Monolithic controls retained additional object references.

The exact private QEMU allocator, mmap/free, and receipt function bodies were
also extracted verbatim and compiled with small QEMU/GLib support scaffolding.
The actual private transaction header and real HVF/Mach calls were used. Three
rounds of 64/128 MiB targets produced 576 successful positive 2 MiB transactions
across two generations and 288 monolithic control transactions. All positive old
objects were fully resident, nonreusable, non-paged-out, and had reference count
one; replacement mappings remained nonresident and neighboring object identity
and residency were preserved. This does not validate full QEMU registry/lock
integration: the support scaffolding and synthetic guest are explicit limits.

Separate exact-helper checks covered protection/inheritance preservation, an HVA
that is not absolutely 2 MiB aligned, a partial tail, allocation and remap
failure, retained cleanup handles, three rollback paths, uncertain-unmap fatal
handling, two cleanup failures, and partial-length refusal. Synthetic guest
reads verified zero data after commit and original data after rollback. No guest
execution followed the fatal result; retained mappings were recovered only
after destroying that probe's own VM.

## Physical evidence and limits

`mincore == 0` at the new HVA is not physical-release evidence for the old
object. In a monolithic allocation, neighboring mappings can keep old physical
pages alive even when the new address is nonresident. `MADV_FREE` and
`MADV_FREE_REUSABLE` were observed to retain old resident pages; reusable
accounting alone is insufficient.

Use direct `sysctlbyname("vm.page_free_count")` and speculative-page counters
around destruction of the already-required rollback aliases. Make no user
allocation, guest execution, target data read, extra diagnostic alias, madvise,
or pressure operation in that window. `host_statistics64` can return cached
samples because of kernel rate limiting, so its zero delta is not a reliable
negative result.

An earlier quiet bounded batch observed exactly 4,096/8,192 freed host pages for
all 12 independent-object generations (64/128 MiB) and approximately zero for six
monolithic controls. Later packaging and exact-helper runs during concurrent
work observed background noise in these systemwide counters. Preserve those
runs' strict physical-observation failures; do not widen the criterion or claim
that historical exact results describe a later run. In the exact-helper run,
382/576 individual positive windows observed exactly 128 free pages and
276/288 controls observed exactly zero; other windows were noisy. A separate
fault-suite commit observed exactly 128 and its rollback windows zero.

Global counters are observations, not per-object accounting. Object identity,
reference count, resident-page state, controlled positive/negative comparisons,
and direct counters provide complementary evidence. No claim is made about
immediate reclamation under every kernel condition: public XNU termination code
can defer reaping when paging/activity is in progress, and the public source tag
examined is not the exact running kernel build.

## Retained local evidence (2026-11-01)

No reports, generated fixtures, or binaries are vendored here. Immutable local
evidence under `.macos-work/qa/` includes:

- `oldobject-hvf-release-sysctl-native-20261101/`: earlier controlled batch;
  report SHA-256 `01b23a553c8cfee060f784e2326051e69d095c1c40342a2d21ce4847eba85808`.
- `guarded-chunk-exact-native-v3-20261101/`: untouched exact guarded-helper
  checks; earlier failed attempts remain in the unsuffixed and v2 directories.
- `guarded-chunk-fault-native-20261101/`: 86 native checks.
- `exact-allocator-transaction-v6-native-20261101/`: exact source snapshots,
  extraction/scaffolding, raw evidence, and `assessment-v2.json` (4,558 checks).
  The v2 assessment corrects a criterion that mistakenly applied offset-zero
  requirements to deliberately monolithic controls. It preserves the original
  assessment and unchanged raw evidence; strict physical observation is false.
- `exact-transaction-v6-fault-native-20261101/`: eight modes, 305 checks;
  report SHA-256 `3f81947f4777b9dc626467fe9187f9f488deba5f12b5822c7fcb3dd001d10818`.
- `hvf-object-reclaim-export-20261101/`: fresh portable-wrapper execution;
  correctness passes, strict physical observation is false due to noisy global
  counter windows. It does not supersede or relabel the earlier quiet run.

The largest touched native target plus neighbors was 132 MiB. No system pressure,
production guest access, ADB access, or production RAM default change was used.
