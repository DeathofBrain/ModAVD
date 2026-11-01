# Private Darwin/HVF RAM replacement candidate

This directory contains a separate opt-in experimental backend. The main macOS
package and bootstrap do not load it. The bootstrap only loads
`incoming/macos-qemu/*.patch`, not this subdirectory; the original 89 ModAVD
patches remain in the main port.

The historical validated v6 complete QEMU diff is
`0005-darwin-hvf-subobject-diagnostic-complete.patch`, based on private QEMU HEAD
`568f0a3e755118d5dddd13bbdd821146d75f5711`. Apply it directly to that base, **not**
after `0001-darwin-host-exposure-audit-only.patch`. The latter is retained as the
historical audit-only prototype. `complete-v6-subobject-diagnostic-patch.json` records the complete diff
hash. Earlier complete patches are preserved alternatives against the same base,
not a sequential patch series. Renderer/AEMU public-header and media overrides are separate manifests.
The manifests currently contain this workspace's absolute paths.
`ram-only-subobject-diagnostic-after-d8.patch` is the remaining RAM/guard/telemetry delta
against the recorded historical main tree after production Q30/Qt/VT/D8 fixes. Its receipt
records every original/target file hash and a successful read-only apply check;
it does not repeat the VT array-owner, UI/SIMD or D8 dispatcher fixes.

The implemented v6 uses independent 2 MiB objects and exact allocation receipts.
Real Android runs have demonstrated safe replacement and rollback, but **no
actual physical-memory release**: all 262 committed old objects were never
resident. The default guest reporter produces aligned 4 MiB SGs; postboot
candidates were fragmented by permanent host exposure. `256K-SOURCE-ASSESSMENT.md`
records the smaller-object proposal and its unresolved benefit/cost boundary.
The read-only 128/256 KiB subobject diagnostic has real report coverage without
changing the v6 allocator/transaction granule.

The private v7 fixed 256 KiB variant is built and installed on the D9 source
layer, with all four distribution signatures strictly verified. Its complete
alternative patch is `0006-darwin-hvf-256k-v7-complete.patch`; the RAM-only delta
against main D9 is `ram-only-256k-v7-after-d9.patch`. Apply the complete patch
directly to the base above, or the RAM-only delta to its recorded D9 base.
`complete-v7-patch.json` and `v7-handoff.json` index source/build/installed receipts.

V7 receipts carry the exact backing granule, and every replacement validates it.
The original byte caps remain, with additional two-attempt/32-inspected-object
caps per `qemu_ram_reclaim_report` SG/range call: at 256 KiB, at most 512 KiB
attempted and 8 MiB inspected per call. The caller can process multiple SGs in
each element and up to 32 elements in one paused BH; these are not total BH caps.
The independent native suite passed 172 external correctness checks, 4,608
two-generation positive transactions, 2,304 monolithic controls, helper and
eight-mode fault validation. Construction of untouched 2.5 GiB used 10,240
objects/regions, locally observed at 40.631416 ms construction / 1.189625 ms free;
these single-run large cases are not bounds or total kernel-metadata estimates.
Native global physical-counter windows remain noisy. The first v7 Android run
passed bounded live/zero/reuse, concurrent network, SIMD and stop/snapshot
checks, then stopped normally. All 262 actual commits replaced never-resident
objects; no resident-object release or same-offset second generation was shown.
`evidence/20261101-v7-reclaim-guest/capture.json` records that launch separately.
The separate telemetry-only version subsequently demonstrated four resident
object replacements: each old object had refcount 1 and 16 PRESENT host pages,
with cleanup-complete free-counter +16 and speculative-counter 0. Three distinct
256 KiB address ranges were observed (768 KiB); one range was populated and
replaced a second time with a different backing ID. The cumulative four windows
cover 1 MiB, not net memory saving. Its same-binary map-fault launch completed
271 rollbacks, including nine resident windows with unchanged free/spec counters.
Live/zero/reuse, concurrent network and SIMD/stop/snapshot checks passed.
`V7-VALIDATION-DECISION.md` and `v7-handoff.json` distinguish all launch identities.

BH telemetry observed up to 64 native attempts or 498 object inspections in a
single BH. The largest pause-request-to-resume-return interval was 14.068 ms;
the instrumented BH span was 15.144 ms. These are local observations, not bounds.
The latter excludes initial snapshot/GArray setup and the final timing log, and
neither interval measures each vCPU's exact off-run time. The full positive run
retained 10/262 cold windows with free/spec counter noise, so its global exact
counter flag remains false despite the four exact resident windows.

The last independent diagnostic version only queries the tail skipped after two
attempts. Across 131 cold 4 MiB SGs, all 1,834 tail objects (458.5 MiB) were whole
unexposed, completely queried and never resident. It found no resident candidate
hidden behind the cold prefix. Its 128/256 KiB groups cover the same pages and
must not be summed. The fully queried cold tails also imply zero resident bytes
for any 64/16 KiB subdivisions there; these smaller bitmap groups were not
separately instrumented, and the result does not cover rejected hot SGs.

`0007-darwin-hvf-v7-pause-telemetry-complete.patch` and
`0008-darwin-hvf-v7-tail-diagnostic-complete.patch` are later alternative full
diffs against the same base, not a sequential patch series. Their manifests
record incremental review deltas and exact installed/runtime identities.
The current private distribution is the stopped diagnostic version; all guest
windows have ended and the quota was released. Native transactions, ownership,
receipt/exposure/permission gates and the original per-SG budgets were preserved.
`evidence/20261101-v7-installed` records the original signed private binaries.
Production defaults remain unmodified; kernel metadata cost and net saving are
unmeasured, and this result does not support default activation or promotion to
the production path. The private source layer remains D9; later production
changes require a separate rebase and validation before any future promotion.
The final independent frozen-evidence reviews passed 67 positive/map-fault and
159 cold-tail checks. `V7-VALIDATION-DECISION.md` also records the tail source
receipt's stage-metadata update and the separate later build/install/launch
times, preserving the historical records.
`REPORTING-ORDER-ASSESSMENT.md` separately audits the 4 KiB guest page size,
Manager order 9, historical private order 10, and lower-order buddy coverage.
`REPORTING-ORDER-COMPARISON-PLAN.md` preserves the earlier preparation;
`REPORTING-ORDER-EXECUTION.md` records the selected frozen f022 instrumentation.
The completed fresh-guest A9/B6 pair is frozen in `REPORTING-ORDER-RESULTS.md`
and `.json`. `REPORTING-ORDER-RESULTS-ERRATA.md` and `.json` correct only the
frozen JSON's narrative label `A6` to `B6`; numbers, order fields and original
JSON/Markdown/107-entry manifest hashes are unchanged.
The strict windows observed 4/83 BHs, 42/2,298 SGs and 6/173
resident commits. The B6 exact subset contains 169 full-resident object windows,
27.75 MiB of address union and 42.25 MiB cumulative target/PRESENT observations;
four other resident windows and one cold global-counter window remain nonexact.
Pause-API sums increased from 14.068 to 136.264 ms despite a smaller B6 single
maximum. Both guests stopped normally; the initial parameter-write failure and
the A9 immediate freezer rejection/retry remain preserved. This single pair
does not establish net saving or general performance; production order stays 9.
The final independent reviews passed
[497 raw-log/BH/pairing/statistics checks](../../../.macos-work/qa/reporting-order-comparison-independent-review-20261101/assessment.json)
and [408 final-table/manifest/exit/document checks](../../../.macos-work/qa/reporting-order-comparison-independent-review-20261101/final-table-review.json).

## Implemented behavior

- Actual allocator provenance, permanent host-page exposure metadata, checked
  sized framebuffer/pingInfo/DMA exports, conservative legacy unsized exclusion,
  alias intersection tracking, migration/snapshot/permanent invalidation gates.
- Effective successful native-HVF mapping/permission interval registry, no
  coarse fallback, exact HVA/GPA/flags and complete generation checks.
- Balloon pause before pop, referenced BH, saturating VM/queue/device generations,
  complete feature/status/poison/realization/topology revalidation, bounded drain
  under BQL, and preservation of the latest concurrent stop/resume intent.
- Darwin private allocations are constructed as independent guarded temporary
  objects remapped into the original continuous HVA, then receive an exact
  base/size/serial/granule receipt only after complete success. A whole backing
  object, current receipt, full unexposed bitmap and all original gates are
  required. Historical v6 uses 2 MiB objects and handles a 4 MiB SG as two
  transactions. V7 fixes the object size at 256 KiB on the main D9 layer, subject
  to the per-SG two-attempt/32-inspected-object caps above. Construction
  failures destroy the entire original reservation; no monolithic fallback is
  published. Temporary guards do not remain between every RAM object.
- A native fixed-address replacement transaction: retain old VM object, create
  fresh zero backing, preserve actual VM attributes and HVF flags, unmap/replace/
  remap, rollback on recoverable faults. Uncertain unmap or failed restoration is
  fatal before report acknowledgement or CPU resume. Cleanup leaks inhibit later
  replacements.
- Checked common RGBA8 renderer paths with actual GL-context pixel state restored.
  Unknown formats retain legacy behavior and exclude their intersecting RAMBlock.
- Media contexts and raw/uncertified external ASG mappings reversibly inhibit
  transactions while active. Destruction joins consumers and deletes decoder
  plugins before storage release and leave. Only an exact single-use producer
  certificate for a retained RingBlob's real map/size bypasses the ASG guard.

`INGRESS-REVIEW.md` explains the directed review, lock graph, source boundaries
and independent evidence. A registered capability alone is not a source proof.

## Isolated build

The build helper reads the original Ninja command/dependency database, compiles
all consumers of modified sources/headers into a cloned object tree and relinks there. It never runs
CMake/Ninja mutation or changes the original AOSP source/build. Run renderer
then headless **sequentially**, because both update shared cloned archives.

```sh
python3 -B incoming/macos-qemu/experimental/build-isolated-audit.py \
  --source .macos-work/qemu-patch-dev \
  --build .macos-work/reclaim-audit-objects \
  --original-source .macos-work/aosp/external/qemu \
  --original-build .macos-work/aosp/objs \
  --ninja .macos-work/aosp/prebuilts/cmake/darwin-x86/bin/ninja \
  --jobs 2 --target gfxstream_backend \
  --overrides incoming/macos-qemu/experimental/dma-overrides/manifest.json \
  --overrides incoming/macos-qemu/experimental/dma-qemu-overrides.json \
  --overrides incoming/macos-qemu/experimental/media-overrides/manifest.json
```

Repeat with `--target qemu-system-aarch64-headless`. The v4 renderer build compiled 17 affected units; its headless build compiled
577. The v6 renderer includes corrected native VT array owners (20 affected
units) and D8 dispatcher resets. The headless build compiles 580 affected units,
including the optional target/old-object/physical and eligibility diagnostics. Logs, exact
argv, patches and source/header hashes are frozen in the v4 build evidence.
Only a stopped private distribution may receive these artifacts; sign and
strictly verify the launcher, headless core and both modified dylibs before use.
The GUI core has not been rebuilt with this candidate and is not equivalent.

## Private runtime modes

`run-private-runtime.py` reuses the isolated runtime implementation while fixing
ADB 20150, console 20160 and gRPC 20162, including the storage watchdog. It never
connects to the main port's runtime. The QA AVD is Android34 ARM64 Google APIs;
requested 2048 MiB is raised by this image to 2560 MiB. At most one Android guest
may run on this 16 GiB host; preserve the runtime's 20 GiB disk floor.

Set the mode before process startup:

| Environment | Behavior |
| --- | --- |
| No variable | Original behavior; no new audit bitmap or replacement. |
| `MODAVD_HVF_RAM_AUDIT=1` | Metadata only; original Darwin discard path. |
| Audit + `MODAVD_HVF_RAM_PAUSE_AUDIT=1` | Real pause-before-pop, no replacement. |
| `MODAVD_HVF_RAM_RECLAIM=1` | Opt-in actual replacement, only after all gates. |
| Reclaim + `MODAVD_HVF_RAM_RECLAIM_FAULT=unmap\|map\|replace` | Private recoverable transaction fault injection. |
| Reclaim + `MODAVD_HVF_RAM_RECLAIM_RESIDENCY=1` | Read-only target-HVA, old-object metadata and direct system-counter observations around native cleanup. |

Unknown fault values inject nothing. The uncertain-unmap fatal injection is
native-probe-only and cannot be selected by the emulator environment.

```sh
MODAVD_HVF_RAM_RECLAIM=1 python3 -B \
  incoming/macos-qemu/experimental/run-private-runtime.py \
  --root .macos-work/reclaim-runtime start \
  --emulator /Users/azuki/ModAVD/.macos-work/reclaim-audit-objects/distribution/emulator/emulator \
  --headless --memory 2048
```

Only conforming free-page reporting establishes temporary guest ownership. The
Virtio 1.2 protocol and exact deployed kernel `7e35917775b8` permit replacement
with zero when PAGE_POISON is absent. The Linux guest clears that feature when
poisoning/init-on-free is disabled. Poison must still be zero, and the complete
negotiated features must remain unchanged across pause.

## Verification and interpretation

Native exact-component tests cover metadata/races, pause intent ownership,
2/4/8-vCPU HVF commit/rollback, full/interior ranges, original bytes, neighbor
canaries, all CPU reads, reused zero pages, VM attributes, permissions, uncertain
unmap fail-stop, real Apple GL pixel state, bounded rings, checked DMA, and media/
ASG lifetime accounting with sanitizers. Their limits are recorded with each test.

Real private guest evidence is separated by build and launch mode:

- `20261101-baseline-guest`: audit-only prototype; 3 unsized HVF lookups tainted RAM.
- `20261101-v2-sized-guest`: sized pingInfo lookup, no whole-block taint; no mutations.
- `20261101-v3-media-pause-guest`: boot/network passed, but PAGE_POISON gate prevented
  actual pause. Retained as a failed pause-coverage result, not counted as success.
- `20261101-v4-pause-guest` and `v4-pause-memory`: actual pause-before-pop,
  11.891s boot, 256 MiB churn x3 with 16 MiB live data and guards, 148 live scans,
  zero errors; 10 stop/start cycles and an 11.14s reboot.
- `20261101-v4-reclaim-guest` and `v4-reclaim-memory`: same signed v4 binary,
  actual successful backing replacements and the same 256 MiB x3 memory probe.
  Initial boot committed 131 ranges / 549,453,824 cumulative bytes, 11.189s boot.
  Probe passed with 148 live scans and zero errors. Network8 and a separate short
  probe passed; their timestamps did not overlap and they are not concurrent
  stress evidence. Snapshot save/load/delete passed, followed by an inhibited
  short probe with no new transactions.
- `20261101-v4-fault-unmap`, `fault-map`, `fault-replace`: respectively 403, 691,
  and 717 transactions, all rolled back, zero committed, no cleanup failure;
  each guest booted and passed a 128 MiB reuse/live-data probe before normal stop.
  The unmap injection is a deliberately invalid no-op; uncertain real unmap
  fail-stop remains covered by the separate native r2 test.
- `20261101-v5-reclaim-memory`: final VT-corrected/telemetry candidate, same
  256 MiB x3 probe passed in 31.1006s, 149 live scans and zero errors.
  `20261101-v5-memory-and-target-residency.json` records its exact transaction
  segment, target-residency observations and independent host sample bounds.

- `20261101-v6-reclaim-guest`: signed v6, 12.214s boot, 1,280 independent
  2 MiB main-RAM objects. 262 distinct commits / 549,453,824 cumulative bytes;
  old object refs were exactly 1, PRESENT 0, query errors 0. Both 256/512 MiB x3
  probes passed with 149 live checks and no errors, but produced no resident
  object release or repeated-offset second generation. Network8 overlapped the
  256 MiB probe for 11.119s and passed.
- `20261101-v6-simd-snapshot`: separate guest SIMD probe under 10 stop/start and 3
  snapshot save/load/delete cycles, 26 live boundaries, 2,126,178,560 checks,
  zero errors, cleanup passed. Q30 negative injection detects word 60; this is
  probe sensitivity, not an old-emulator regression control. Snapshot operation
  permanently inhibits later reclaim as intended.
- `20261101-v6-diagnostic-guest`: same gates plus complete SG/skip-reason logs;
  142 aligned 4 MiB SGs, 262 never-resident commits and 22 `chunk-exposure` skips.
  Neither SG size/alignment nor receipt/permissions blocked these reports.
  A short 256 MiB probe passed. Default page_reporting_order remained 10.
- `20261101-v6-fault-map`: final signed diagnostic candidate, 262 transactions
  all rolled back, zero commits/cleanup/query errors. A 128 MiB reuse/live-data
  probe passed 20 scans with zero errors; normal stop confirmed. Old refcount 3
  includes restored target and rollback references; old PRESENT remained0.
  System free deltas were 260 x 0 and 2 x +1, preserved as background observations.

- `20261101-v6-subobject-guest`: first short launch, two 256 MiB probes passed but
  only cold SGs arrived. Zero subobject lines is a coverage gap, not evidence of
  zero candidates. The launch was normally stopped and remains preserved.
- `20261101-v6-subobject-directed-guest`: same signed binary, explicit completed
  boot, original 256/512 MiB x3 x8s sequence passed (148/149 live checks, zero
  errors). 161 aligned 4 MiB SGs included 60 exposure-rejected parent observations;
  1,440 subobject records covered 44 distinct 2 MiB parents. 256 KiB: 6 whole
  observations / 5 distinct resident ranges, 1.25 MiB across observed distinct
  ranges. 128 KiB: 18 whole observations / 14 distinct resident ranges, 1.75 MiB.
  All queried pages were PRESENT; no query errors or budget skips. These sets
  overlap and are counterfactual resident-candidate bounds, not bytes released
  or future receipt/native-alias proof. All 262 actual 2 MiB commits remained
  never-resident objects. Guest order remained 10; REPORTING was negotiated and
  balloon status was 0xf. Normal stop confirmed.

Exact shared-helper/allocator/transaction native evidence is indexed by
`.macos-work/qa/exact-v6-native-suite-v2-20261101.json`: 4,558 correctness checks,
576 positive two-generation transactions, 288 monolithic controls, 8 fault modes
and 305 fault checks, plus 86 helper-fault checks. This exact native run's global
counter windows are noisy (`all_windows_exact=false`); 382/576 positive windows
were exactly+128 free pages and 276/288 controls exactly 0. Exact object identity,
refcount, residency, mapping and fault correctness pass independently. It is an
exact-component test with QEMU/GLib scaffolding, not full-QEMU integration.

Use `summarize-ram-audit.py` on a **single launch's** frozen log. Candidate upper
bounds are not replacements. Successful committed totals may repeatedly include
the same pages; they are not distinct pages or system physical bytes released.
RSS and footprint are independent observations. The matched workload captures
show RSS/footprint fluctuations and differing boot ages; no system physical-memory
benefit or performance improvement is established by these runs.

`run-guest-reclaim-probe.py` verifies the recorded host PID and private AVD,
pushes only into a fresh UUID guest directory, runs the bounded Android probe,
samples Darwin resident/physical-footprint/CPU identity independently, checks boot
identity, and cleans the exact guest paths. Evidence preserves source/binary
hashes, stage timestamps, host samples and the corresponding transaction segment.

The optional v5 `mincore` probe never reads or touches guest bytes. It samples
only already eligible report targets while the report owner, CPU pause and audit
lock remain active, then samples after native cleanup. Its exact-source native
sanitisers verify fresh, touched, protected and oversized ranges. Darwin can
report an unmapped range as zero resident without an error; this observation is
never used to prove mapping validity or authorize a transaction. Even a target
HVA becoming nonresident does not establish that the old VM object's pages or
system physical memory were freed. The native transaction semantics are unchanged.
