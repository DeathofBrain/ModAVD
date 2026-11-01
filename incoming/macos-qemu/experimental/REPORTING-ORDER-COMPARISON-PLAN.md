# Prepared order 9 / order 6 comparison — not launched

Wait for the main task to finish G2 acceptance and explicitly release the single
guest quota. This file prepares two separate private launches only. No guest,
parameter write, binary installation, compilation or policy change has been
performed for this comparison.

## Fixed comparison conditions

Use one frozen signed four-file binary set for both launches, fixed 256 KiB
backing, all existing gates and per-SG limits, the same Android 34 image and
2,048 MiB request (image raises it to 2,560 MiB), same AVD, same probe binary,
same workload and observation durations. Verify the four hashes and strict
signatures before each start. Record source/build/install identities if any
pure observation instrumentation is added before both runs; do not mix its
results with the earlier `e84c349a` or `f02215e0` launch evidence.

Keep AUDIT, PAUSE_AUDIT, RECLAIM and RESIDENCY enabled, with recoverable fault
injection unset and `MODAVD_HVF_RAM_RECLAIM_TAIL_DIAG=0` explicitly set. Assert
that no tail-begin/end records appear. Use headless/HVF and the same rendering
mode in both runs. Keep guest poisoning, exposure, allocation receipts,
permissions, aliases, snapshots and native transaction logic unchanged. Do not
use memory pressure, drop-caches, compaction, defragmentation or kernel changes.

Run A uses explicit order 9; stop it completely before Run B starts with explicit
order 6. These fresh boots are separate observations, not one identical physical
memory state. Preserve each boot identity, boot age and initial buddy/PRESENT
conditions; one launch per setting cannot remove boot/order-of-run variance.
Matching order 9 reproduces only that reporting parameter, not Manager's full
boot tuning, scripts, storage, rendering or application environment.

## Identity, explicit setting and measurement windows

For each run:

1. Check single-guest ownership, the 20 GiB private-runtime disk floor and the
   expected four-file hashes. Use only private ADB 20150, console 20160, gRPC
   20162 and the isolated runtime/AVD. Start one guest only after quota release.
2. Verify host PID/start identity/executable/argv, console AVD identity, completed
   boot, guest boot UUID, actual guest page size and the expected kernel. Read
   the original reporting order, `pagetypeinfo`, `buddyinfo`, `zoneinfo`, limited
   meminfo/vmstat and reporting feature/status before the setting write.
3. With identity unchanged, issue one bounded root command that reads boot UUID,
   writes the run's literal desired value to the exact page-reporting parameter,
   reads it back and reads boot UUID again. Capture argv, times, exit code,
   stdout/stderr and the matching PID/AVD. Require successful write plus exact
   9/6 readback and identical UUID; a missing/denied/mismatched read fails the
   run. Do not treat a failed write as the intended setting.
4. Record the host log offset and a boundary timestamp after the verified
   write/readback. Keep initial boot/default-order transactions as a separate
   precondition dataset. A write does not kick the reporter or atomically
   replace an in-flight worker's order snapshot. Distinguish any BH crossing
   this boundary, preserve its data and exclude it from the wholly-after window.
5. Observe the same bounded 10-second idle interval, then run the same existing
   bounded live/zero/reuse probe: 256 MiB ×3 with 8-second release intervals,
   followed by 512 MiB ×3 with 8-second intervals, then a 10-second tail idle.
   The probe has 16 MiB protected live data and guards, no guest disk workload,
   and a 120-second per-probe watchdog. Use the frozen existing probe binary;
   its recorded SHA is `9ea596d0de18e350c5398719e96d870ebbd4ab23a8888766a96bfcbb8b880e04`.
6. Collect small read-only order/buddy/pagetype/zone samples before probes,
   after their final cleanup and at the final idle boundary. If phase sampling
   is added during release intervals, perform the same bounded commands and
   cadence in both runs, and record their overhead. Do not use `/proc/pagemap`
   bulk walks, forced compaction or host/guest memory touches for observation.
7. Require probe zero errors, successful live/zero/reuse checks, exact UUID-path
   cleanup, unchanged guest boot UUID and host process start identity. Read back
   order at each phase. Do not save/load snapshots before completing the
   comparison: the existing safety rule permanently inhibits later reclaim.
8. Stop normally with an actual subprocess receipt including before-state,
   expected PID, timestamps, command, output and exit code. Verify process exit,
   freeze the entire log and SHA, and release the quota before the next launch.

The writable change is temporary per-guest kernel state only. If a running
instance is retained for any reason, restore its captured original order and
read back before releasing ownership. Normal termination ends that boot's
runtime parameter state; no persistent image/config edit is needed.

## Metrics and complete BH measurement prerequisite

The existing telemetry times a useful interval, but not the full original
callback: its first timestamp follows initial snapshot/GArray allocation and
its final timestamp precedes its own timing log. Keep
`request_to_resume_return_us` and `bh_to_before_final_log_us` under their exact
existing meanings. Neither gives every vCPU's exact off-run duration.

To meet a complete callback-body measurement requirement, prepare and review
one **pure observation** wrapper before either run, then compile/sign/freeze
one common candidate once. The wrapper can time immediately before calling the
unchanged original BH body and immediately after it returns, thereby including
initial setup, all existing logs, validation, pause/drain/resume, cleanup and
rescheduling. Carry sequence and counters in local values before the body's
last object unref; access no device/CPU pointer afterwards. Emit the envelope
record after the end timestamp. Name it `callback_body_including_timing_log_us`:
it excludes the new wrapper's parameter lookup/timestamp overhead and envelope
emission, and excludes time waiting in the scheduler. It is still an observed
duration, not a worst-case bound or per-vCPU off-run measurement. This source
change/build has **not** been applied or authorized for execution by this plan.
If only the current binary is reused, report the existing narrower intervals
and mark complete-body measurement unavailable, instead of relabeling them.

For every wholly-after BH, preserve popped/acked element counts, actual input
SG count, reclaim-call count, all per-SG inspected/attempted objects and bytes,
skip reasons, native results/errors, candidate bytes and all phase durations.
Verify each BH's begin/end, equal pop/ack counts, exact phase sums and complete
SG-to-BH attribution. Report total/max and the full empirical distribution;
use small-sample percentiles only with the number of observed BHs. Do not assume
2/32 is shared across the BH. Attribute guest metadata collection overhead when
interpreting host timings.

Histogram actual mapped SG byte lengths and ranges, not the chosen minimum
order. Derive an observed order only when length is an exact power-of-two
multiple of the proven 4 KiB guest page size; keep other lengths and RAMBlock
boundary splits explicit. Cross-check event count with input-SG and cumulative
audit totals. Expect order 10 SGs to remain possible in both runs: the kernel
does not split them when the minimum is lowered.

Separate cold commits from resident commits. For each resident window preserve
old object identity/refcount/PRESENT/PAGEDOUT/REUSABLE/query errors, target HVA
before/after, cleanup outcome and exact free/spec deltas. Report cumulative
windows, unique address ranges and same-offset generations independently.
Keep all noisy windows and global strict false flags; do not infer net saving
from committed bytes, RSS, footprint or a partial set of exact object windows.

## Interpretation

Order 6 can demonstrate broader guest report coverage if actual order 6–8 SGs
arrive after readback, relative to order 9's minimum. Count their physical
address ranges, whole-unexposed candidate bytes and native results. More SGs
without resident commits indicates added traffic/work with no resident release
demonstrated in that window. Newly observed resident commits are still finite
object-page evidence, not total kernel-metadata/net-saving acceptance.

Buddy snapshots must be correlated to the same PID/boot/phase. If many smaller
free blocks exist but do not arrive, inspect the pending-worker/notify timing,
PageReported state, watermark, migration type, per-list work budget and descriptor
outcomes before attributing the gap to the host first-two policy. If no whole
order ≥6 blocks exist, lower-order free bytes cannot authorize 256 KiB native
replacement. Do not relax the whole-object gate or merge report lifetimes.

The comparison completes only after both frozen datasets and their identity,
setting, work/timing, resident-window and live-correctness analyses are available.
Absent that runtime evidence, this remains a prepared experiment and the backend
stays private opt-in.
