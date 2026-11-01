# Guest reporting order: read-only coverage assessment

The existing data supports a guest high-order coverage limitation as a separate
hypothesis. It does not show that lowering the order will release more host
resident memory. No guest, sysfs value, configuration, host granule or safety gate
was changed, and no guest or build was started for this assessment.

## Page size and the two observed settings

The actual v7 memory probes report `guest_page_bytes=4096` in their first JSONL
record. The probe gets this value from `sysconf(_SC_PAGESIZE)`, rather than from
filesystem block size or the host page size. The host uses 16 KiB pages; this is
a different quantity. The seven original, positive, fault and tail probe records
and their hashes are indexed in `REPORTING-ORDER-ASSESSMENT.json`.

The deployed kernel commit resolves to
`7e35917775b8b3e3346a87f294e334e258bf15e6`. Its exact official sources are frozen
under `evidence/20261101-guest-reporting-order-source/`, together with the pinned
virtio driver, Manager and probe source snapshots. In `mm/page_reporting.c:341`,
registration assigns `prdev->order ?: pageblock_order`. The virtio driver's
explicit order 5 override only applies to ARM64 with **64 KiB guest base pages**
(`drivers/virtio/virtio_balloon.c:982`); it does not apply to these 4 KiB guests.

The private v6 sysfs reads explicitly returned order 10, and `/proc/pagetypeinfo`
returned `Page block order: 10`, with 1,024 pages per block. Manager instead sets
its own constant `balloonReportingOrder=9` (`manager/manager.go:62`) after binding,
using `su` to write and read back the parameter (`manager/afterboot.go:254`). The
old Manager PID 54065 read-only capture confirms order 9. These are different
launch paths, not conflicting measurements of one unchanged guest.

No per-launch sysfs order capture was taken in the v7 windows. Their actual logs
contain 4 MiB SGs and the private wrapper does not run Manager setup, consistent
with the private default, but this is not a new v7 sysfs measurement. The v6
snapshots and v7 logs must retain their separate process/boot/time identities.

| Order at 4 KiB guest pages | Smallest eligible buddy block | 256 KiB backing objects in that SG | Cold native attempts allowed for that SG | Extra zone watermark above low |
| --- | ---: | ---: | ---: | ---: |
| 10 | 4 MiB | 16 | 2 | 128 MiB |
| 9 | 2 MiB | 8 | 2 | 64 MiB |
| 8 | 1 MiB | 4 | 2 | 32 MiB |
| 7 | 512 KiB | 2 | 2 | 16 MiB |
| 6 | 256 KiB | 1 | 1 | 8 MiB |

The main RAM GPA base observed in transactions is `0x40000000`, aligned to
256 KiB. Order 6 buddy extents therefore match the existing whole-object
alignment for this RAMBlock. Below order 6, the newly included smallest SGs are
smaller than 256 KiB and fail the current whole-chunk guard. Independent report
lifetimes are never accumulated to authorize one object replacement.

## What changing the parameter would do

The fixed kernel uses `module_param(page_reporting_order, uint, 0644)`
(`mm/page_reporting.c:15`). This exposes a privileged runtime write, subject to
Android permissions and SELinux. Existing private evidence has a denied ordinary
shell read followed by successful root reads; no private v7 write was attempted.
The separate Manager order 9 write/readback is actual successful evidence for its
own guest. A driver rebind resets the value during registration, explaining why
Manager applies its value afterwards.

This is the **minimum order**, not a command to split every free block to that
size. `page_reporting_process_zone` scans from the value through `MAX_ORDER-1`
(`mm/page_reporting.c:252`); each SG retains `PAGE_SIZE << order` (`:121`).
Lowering to 6 adds existing order 6–9 lists while order 10 blocks still arrive as
4 MiB SGs. The previously observed cold first-two/tail-fourteen structure can
therefore persist. This parameter neither compacts guest memory nor changes the
host allocator.

The ordinary uint setter does not schedule reporting. The free hot path only
notifies when the merged free order is at least the current setting
(`mm/page_reporting.h:33`, `mm/page_alloc.c:1205`). An existing worker or a later
qualifying free drives processing. The worker has a two-second delayed schedule,
32-entry SG capacity, per-list work allowance, zone and isolation watermarks,
and skips `PageReported` blocks and the Isolate migration list. A value write
does not supply a synchronized snapshot of an already active worker, an immediate
flush, or a guarantee that every smaller free block will be reported.

## Existing buddy distribution

The following arithmetic uses only the indicated historical v6 snapshots and
4 KiB base pages. Each cell sums `count[order] * 2^order * 4096` for the given
minimum, across non-Isolate migration types. It does not count smaller blocks
as if they had already coalesced.

| Frozen private snapshot, UTC | Order ≥10 | Order ≥9 | Order ≥6 | Added order 6–9 vs order 10 |
| --- | ---: | ---: | ---: | ---: |
| PID 21579, 20:25:39 | 56 MiB | 80 MiB | 203.25 MiB | 147.25 MiB |
| PID 52205, 20:57:08, during 512 MiB probe | 76 MiB | 90 MiB | 132.25 MiB | 56.25 MiB |
| PID 52205, 20:58:34, final | 92 MiB | 108 MiB | 148.5 MiB | 56.5 MiB |

The files are `evidence/20261101-v6-reporter-state.json` and
`evidence/20261101-v6-subobject-directed-guest/reporter-state-{during512,final}.json`.
The machine assessment preserves every migration-type count and all intermediate
order 7/8 values. The first snapshot also has a matching `/proc/buddyinfo` row.

There are substantial free blocks below order 10 but at least order 6 in all
three samples. This establishes that a higher minimum excludes structurally
available smaller free blocks. It does not establish their host residency,
exposure, native permissions, alias state, or previously reported status. CMA
blocks are included, as they are in the scanned migration types; actual zone
and isolation checks can still reject them. PCP pages are not characterized by
these lists. None of these byte totals belongs to the later v7 PIDs or proves
how the specific churn allocation was physically fragmented.

The cold-tail diagnostic rules out resident candidates only inside its exact
covered order 10 tails. These historical buddy snapshots do not rule out useful
smaller guest extents elsewhere, nor prove they exist with usable host backing.
Separating guest high-order fragmentation from host exposure/residency requires
same-boot, phase-correlated order/buddy/pagetype/zone information and per-SG
physical addresses with host candidate/PRESENT observations.

## Expected coverage and work boundary

With fixed 256 KiB backing, a cold order 10 SG attempts at most 2/16 objects,
order 9 at most 2/8, order 7 at most 2/2, and order 6 at most 1/1. Lowering the
minimum admits other SGs; it does not improve that ratio inside existing larger
SGs. All exposure, receipt, permission, pause, snapshot and ownership gates stay
necessary for every transaction.

For equal bytes represented entirely by minimum-sized blocks, order 6 needs
16 times as many SG descriptors as order 10, or eight times as many as order 9.
This is a size ratio, not measured traffic: the kernel does not split the
existing larger blocks. The additional zone watermark falls to 8 MiB from
128/64 MiB, potentially admitting work at lower guest free-memory levels. More
free notifications, scanned lists, reports and paused BHs can increase total
work even when each SG is smaller. The 32-descriptor guest capacity and two-second
worker delay do not establish a host latency bound. Existing host limits still
reset per SG, with no shared paused-BH work budget.

Any future comparison should keep one frozen binary and host policy, explicitly
write/read the desired order only after verifying each guest's boot identity,
and separate pre-write boot reports from the comparison window. Matching the
Manager's order 9 alone does not reproduce the complete Manager environment.
The two-run preparation is recorded separately in `REPORTING-ORDER-COMPARISON-PLAN.md`.
