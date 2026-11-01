# Reporting-order execution selection

This overlay selects the existing frozen f022 four-file private D9 build for A9 then B6, each a fresh guest. No compilation or QEMU source change is involved. TAIL_DIAG=0, 256 KiB backing, and all eligibility/per-SG budget gates remain unchanged. Main G2 is independent.

The earlier pinned assessment and comparison plan remain intact. This overlay supersedes only their unselected wrapper-instrumentation alternative. `request_to_resume_return_us` and `bh_to_before_final_log_us` retain their exact scope; full BH body remains unavailable.

Root transferred the sole guest quota after normal G2 shutdown. The first attempted A9 boot (PID 51018) stopped before the measurement window because Android printf-to-sysfs returned Invalid argument. It was synchronized and normally stopped. Those receipts remain in `evidence/20261101-v7-order-comparison`. Only the sysfs write was changed to the literal echo form already used by Manager, followed by explicit readback and same-boot checks.

The selected execution is `evidence/20261101-v7-order-comparison-retry1`. Both runs use explicit root setting/readback, 10 s idle, 256 MiB x 3 x 8 s, 512 MiB x 3 x 8 s, 10 s idle, metadata snapshots, and normal stop. Analyzer pairing now fails closed on a missing physical record; old positive/fault fixtures and deletion negative cases were checked. Exact identities are frozen in the companion JSON.

This is an experimental comparison, with no production/default promotion, strict global physical pass, full-BH timing bound, net-memory-saving claim, or kernel metadata cost measurement. Results are frozen separately after execution.
