# Smaller independent backing objects: source assessment

Status: this source assessment led to an authorized private v7 implementation
phase. The latest validated v6 allocator/native replacement still uses 2 MiB.
The read-only diagnostic observes 128/256 KiB subranges; it does not allocate them
or authorize their replacement. Main/default code is unchanged.

## Decision supported by current evidence

The v6 default-order10 guest reports aligned 4 MiB SGs. Of 284 constituent 2 MiB
objects, 262 were replaced while never resident and 22 were rejected for historic
host exposure. Those 22 objects contain 3,801,088 candidate bytes in aggregate.
Even with perfectly favorable placement, `sum(floor(candidates / 262144))` is only
6 whole 256 KiB objects, at most 1.5 MiB of additional candidate address space in
this short run. Placement can make the actual count zero; current logs cannot
reconstruct the bitmap. This bound is specific to this launch and workload, not
a limit on all future Android workloads.

A smaller granule can preserve every safety gate while avoiding exposed neighbors.
Its benefit is unresolved. First observe aligned 128/256 KiB whole-safe subranges
and PRESENT pages using the existing bitmap and kernel metadata, without touching
guest bytes or adding aliases. PRESENT is a resident-candidate upper bound inside
the current larger object. It is not release evidence, a future allocation receipt,
or validation of the future native mapping/alias gates.

The final directed v6 run reproduced the original 256/512 MiB x3 x8s sequence:
5 distinct whole 256 KiB candidate ranges were fully PRESENT (1.25 MiB across
observations); at 128 KiB there were 14 distinct fully PRESENT ranges (1.75 MiB).
The sets overlap. Query errors and budget skips were zero. These are real
report-owned resident-candidate upper bounds inside existing 2 MiB objects,
not released bytes or proof of a future finer receipt/native alias check.
`evidence/20261101-v6-subobject-directed-guest/subobject-assessment.json` preserves
all offsets and metadata. A prior short launch lacked hot reports and remains
explicitly a coverage failure. The bounded v7 cost/safety stage is now justified.

## Minimum future implementation delta

| Area | Concrete change | Constraint |
| --- | --- | --- |
| Shared helper | Change `DCR_CHUNK_SIZE` to 256 KiB in a separate private build; update the 2 MiB comment. Both initial loops and fresh-map maximum already use this constant. | Keep host-page alignment, independent guarded temporary mapping, full construction before publish, failure cleanup and no monolithic fallback. |
| Allocation receipt | Store `chunk_size` in `DarwinRamReceipt`; extend `qemu_anon_ram_allocation_serial(base, size, expected_chunk_size)` so both capture and per-report revalidation require the exact compile-time granule. | Keep serial/base/size matching, retire-before-unmap, saturation failure and receipt mutex lock order. No environment-derived receipt. Existing RAMBlock serial can remain the captured identity. |
| Exec loop | Retain the byte caps, add explicit caps of 2 native attempts and 32 inspected objects per report. | Current caps imply 2 attempts and 32 inspected objects at 2 MiB; byte caps alone silently permit 16 attempts and 256 inspected objects at 256 KiB. Acknowledge unprocessed report pages without replacing them. |
| Native transaction | Keep `length == DCR_CHUNK_SIZE`; the constant automatically changes strict whole-object length. | Preserve all original pause/report/reservation/generation/flags/no-alias/VM-attribute/rollback/fail-stop gates. Never replace a partial old object. |
| Diagnostics | Log receipt granule, attempted/inspected object counts and duration; keep physical metadata observations separate from ownership decisions. | Do not use gross system counters to approve a transaction or claim benefit. |
| Native tests | Replace hardcoded 2 MiB and 128 host pages with the compiled constant and `chunk_size / getpagesize()`. | Execute the exact new headers/allocator bodies, not a rewritten model. |

A fixed 256 KiB build needs no runtime granularity selector. The existing RAMBlock
host virtual base remains continuous and retains the original alignment and tail
guard. Temporary construction guards are only two host pages at a time; they are
removed after each remap, not retained around every object.

For the actual 2.5 GiB RAMBlock, object count rises from 1,280 to 10,240. Other
private anonymous allocations also use the helper, so measure their total receipt
sizes and ceil(size/granule) counts. Receipt storage remains per allocation, not
per object. Persistent kernel VM-object/map metadata and construction calls grow;
there is no measured byte or time estimate yet.

## Bounded validation before another full guest build

1. Compare untouched 64/128 MiB native allocations at 2 MiB and 256 KiB, then a
   single untouched 2.5 GiB construction if the small trials are stable. Record
   elapsed construction/free time, task region count, Mach interval/object IDs,
   task footprint/resident values and source hashes. Do not touch 2.5 GiB or induce
   host pressure. Run variants sequentially and retain all measurements.
2. Assert each object has a unique ID, correct object-relative first/last offset,
   zero initial PRESENT pages, exact current/max permissions and inheritance;
   keep non-granule-aligned HVA and host-page tail cases. Verify complete cleanup.
3. Rerun two-generation touched-object native replacement and real synthetic vCPU
   reads at 256 KiB. Expected PRESENT/free-page quantity is 16 host pages on this
   16 KiB host. Keep monolithic controls and commit/map/unmap/replace rollback,
   uncertain-unmap fatal, both cleanup failures and partial-length rejection.
4. Only if the read-only guest diagnostic exposes meaningful resident candidates
   and native construction cost is acceptable, build one private 256 KiB guest
   with the explicit loop-count caps. Measure startup and per-report pause cost,
   direct old-object metadata and repeated generation behavior under the same
   short workload. Keep the current default reporter order10 and all gates.

At the time of this assessment no granule/receipt/native change had been
validated. The subsequent v7 source work is private and must pass exact native
validation before guest compilation. A failed benefit/cost check leaves the backend private and opt-in;
it does not establish that the overall physical-reclaim goal is unreachable.
