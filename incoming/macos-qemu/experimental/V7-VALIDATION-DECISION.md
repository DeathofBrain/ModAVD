# V7 private validation and production decision

Keep the 256 KiB backend private and opt-in. Actual Android object-page release
and bounded workload correctness are demonstrated, but overall net memory
saving, kernel metadata cost and a shared BH work bound are not established.
These results do not justify enabling this backend in the production path.

All runs used one isolated Android 34 ARM64 guest, the original reporting order,
fixed 256 KiB backing, and the original safety gates. No host pressure, kernel
knob, granule selector, or relaxed exposure/receipt/permission condition was used.
The last guest stopped normally and its quota was released to the main task.

## Separate versions and launches

| Version | Core SHA prefix | Launch | Result |
| --- | --- | --- | --- |
| Original v7 | `35dc1f3f` | PID 91872 | 262 cold commits; no resident replacement |
| V7 + balloon-only telemetry | `e84c349a` | PID 2276 | 266 commits: 262 cold + 4 resident |
| Same telemetry binary, map fault | `e84c349a` | PID 12525 | 271 rollbacks, including 9 resident windows |
| V7 + telemetry + read-only tail diagnostic | `f02215e0` | PID 20163 | 262 cold commits; tail observation only |

The original seven native-checkpoint files are unchanged in the telemetry-only
version. The last version adds a reviewed read-only block after the existing
`exec.c` transaction loop. Its loop, gates, offsets and budgets are unchanged;
the allocator and native transaction helper remain byte-identical. Source,
build, installed and launch receipts distinguish these versions. Complete
patches 0006, 0007 and 0008 are alternatives against the same private HEAD.

## What actual Android established

The positive telemetry launch contains four 256 KiB committed objects with
old refcount exactly 1, PRESENT 16/16 host pages, no paged-out/reusable pages,
and zero page/counter query errors. Each native cleanup completed, target HVA
residency changed 256 KiB to zero, the system free counter rose by exactly 16
host pages and the speculative counter did not change.

There are three distinct address ranges, totaling 768 KiB across observations.
Offset 280756224 was replaced in BH 11 and again in BH 14; old backing IDs
4205067072529385732 and 17321516412411955267 differ, with mapping generations
283 and 286. This is a resident second generation at the same offset. Four
windows cumulatively cover 1 MiB. Neither total is a steady-state net saving,
and repeated addresses must not be counted as distinct pages.

The full positive run has 10 non-exact cold windows out of 262: seven have
nonzero free deltas and three have speculative-only noise. The other 252 cold
windows have exact zero deltas. All four resident windows are exact, while
`all_guest_windows_exact`, the original native-global exact flag and the strict
global physical-benefit decision remain false. Zero tolerance was preserved.

The same telemetry binary's map-fault launch rolled back all 271 transactions.
Its nine resident windows retained PRESENT 16, target residency and the same
backing IDs on repeated offsets, with free/spec deltas both zero. Observed old
refcount 3 is at the rollback observation stage, with restored mapping and
retained rollback references; it is not the final post-cleanup refcount.
Cleanup, residency and metadata-query errors were zero. One cold window had
speculative-only noise, preserving the global false flag.

Positive live/zero/reuse probes at 256/512 MiB and the concurrent 256 MiB probe
passed with 148/150/149 live scans and zero errors. Eight network cases passed,
overlapping the last memory probe for 8.8741 seconds. The same launch passed
10 stop/start and three snapshot cycles, 26 live boundaries and 2,007,078,627
SIMD checks with zero errors, unchanged boot identity and exact-path cleanup.
The SIMD probe exercised one busy worker on guest CPU 0; it does not establish
all-vCPU, suspend/resume or game-workload coverage. The network overlap is with
a later memory-probe host interval; the four resident commits already appear
in the earlier preserved checkpoint.
Snapshots permanently inhibit subsequent reclaim as designed. Map-fault
256/512 MiB probes also passed; all guests were stopped before changing versions.

These are finite workload observations. They complement the exact native
component suite (172 external checks, 4,608 positive transactions, 2,304 controls,
helper and eight-mode fault validation); they do not turn that scaffolded suite
into full-QEMU coverage or erase its noisy global physical-counter windows.

## Pause and granule cost

Two attempts / 32 inspected objects are caps for one SG/range call. The caller
can process multiple SGs per element and up to 32 elements per paused BH. The
current implementation has no shared BH 2/32 cap or adaptive total-work budget.

Actual telemetry observed up to 64 native attempts in a BH, and a different BH
with 32 SGs, 498 object inspections and three resident commits. That BH measured
14.068 ms from pause request to resume-call return, including 13.925 ms of
quiescent work. Its instrumented span was 15.144 ms. The instrumented span starts
after initial snapshot/GArray setup and excludes the final timing log. Resume
bookkeeping is not each vCPU's exact off-run duration. Existing audit/logging
work is included in the measured drain; these values are not latency bounds or
a comparison against production without diagnostic logging.

At 2.5 GiB, 256 KiB backing uses 10,240 independent objects/regions instead of
1,280 at 2 MiB. The untouched construction test observed 40.631416 ms to create
and 1.189625 ms to free at 256 KiB, versus 7.44075 / 0.145 ms at 2 MiB. The large
cases were single runs, not cost bounds. Task footprint does not measure all
kernel VM-object, map or HVF metadata. Those costs must be measured before
claiming that the small observed object-page release yields overall net savings.

## Cold-tail coverage and next candidate

The last independent read-only diagnostic inspected all 14 skipped objects
behind the first two attempted objects in each of 131 cold 4 MiB SGs. It covered
1,834 distinct 256 KiB ranges (458.5 MiB) and 3,668 overlapping 128 KiB groups.
All were whole unexposed and completely queried, with PRESENT/PAGEDOUT/REUSABLE,
query errors and budget skips all zero. Each SG used 224 of the original shared
256 Mach-query allowance, leaving 32; total calls were 29,344. The diagnostic
work summed to 28.028 ms across SGs, maximum 252 microseconds per SG; its timing
includes added diagnostic cost and is separate from the positive version.

No resident candidate was observed hidden behind a cold prefix. No cursor or
smaller-granule change is supported as a fix for observed starvation. The
candidate instruments only 128/256 KiB bitmap groups. Because every covered
tail host page was queried and nonresident, any 64/16 KiB subdivision within
those same tails also has zero resident bytes. This says nothing about rejected
hot SGs or other unqueried addresses. Bitmap/PRESENT observations are not native
permission or no-alias proofs.

If work continues, prioritize a caller-level shared work budget covering audit,
validation and native attempts, with an early bounded skip before expensive
quadratic SG validation or full-range bitmap scans. A conservative nonresident
hint could avoid useless cold transactions, but must never authorize a native
transaction, combine ownership across reports or weaken an existing gate.
Fairness changes and smaller-object allocation should wait for specific evidence
of missed resident candidates and a measured cost/benefit. No such policy was
implemented in this task. The private source is layered on D9; later main-tree
changes require a fresh rebase and validation before any promotion.

## Frozen evidence

- `evidence/20261101-v7-reclaim-guest/capture.json`: original v7 launch.
- `evidence/20261101-v7-pause-telemetry-guest/assessment.json`: positive object windows, second generation and correctness.
- The same directory's `timing-assessment.json` and `physical-noise-assessment.json`: exact BH and counter scopes.
- `evidence/20261101-v7-pause-telemetry-fault-map/assessment.json`: same-binary real-guest rollback controls.
- `evidence/20261101-v7-budget-tail-guest/tail-assessment.json`: bounded cold-tail metadata coverage.
- Fault/tail `stop-receipt.json` files capture actual subprocess output and ownership state. The positive launch's receipt transparently transcribes its successful session tool result; it was not replayed after the next guest started.
- `v7-handoff.json`: current identities, hashes, independent review links and quota status.

The independent frozen-evidence review passed 67 checks for the positive and
same-binary map-fault launches in
`.macos-work/qa/v7-timing-positive-independent-review-20261101/assessment.json`,
and 159 checks for the tail source/build/install/raw-log chain in
`.macos-work/qa/v7-cold-tail-independent-review-20261101/assessment.json`.
These workspace paths are relative to the repository root. Both reviews retain
the strict-global-false, no-net-saving and private-opt-in boundaries.

The tail source receipt's stage text was updated after the held-candidate review
while retaining its original `2026-10-31T21:53:19.805303+00:00` timestamp. Its
current SHA is `212a194fed5295d8f88b8aa92a8798164be4e5adb353f4d5fcd2be21d8d8f899`;
the earlier independent source review recorded
`e1e4b5da9b76400bbfed06e1e5b8ffbf5afc8bdfbc2bf67bee11759ef490918b`.
The before/after source and diagnostic patch hashes agree with the earlier
review. The separate build receipt at 22:11:48 UTC, install receipt at 22:13:49
UTC and launch receipt at 22:13:52 UTC establish actual later activation; the
earlier source timestamp does not date compilation or launch. Historical
receipts, initial review and frozen captures are preserved without rewriting.
