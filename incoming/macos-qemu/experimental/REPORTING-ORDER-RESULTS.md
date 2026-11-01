# Reporting-order A9/B6 results

One fresh-boot A9 then B6 pair completed on the same frozen f022 four-file private D9 set, with no compilation, TAIL_DIAG=0, fixed 256 KiB backing and unchanged gates. The normal production order remains 9. B6 expanded observed reporting/resident-object coverage and increased cumulative instrumented pause; this single pair does not establish general performance or net memory benefit.

| Strict comparison window | A: order 9 | B: order 6 |
|---|---:|---:|
| Window seconds | 86.593 | 87.304 |
| BH / input SG | 4 / 42 | 83 / 2298 |
| Report bytes, cumulative / address union (MiB) | 92 / 66 | 1019.75 / 324.25 |
| Native inspections / attempts | 343 / 8 | 4041 / 181 |
| Resident / cold commits | 6 / 2 | 173 / 8 |
| Resident cumulative object windows / old PRESENT (MiB) | 1.5 / 1.5 | 43.25 / 43.015625 |
| Resident address union (MiB) | 1 | 28.5 |
| Exact complete resident windows | 6 / 6 | 169 / 173 |
| Exact subset: address union / cumulative target / old PRESENT (MiB) | 1 / 1.5 / 1.5 | 27.75 / 42.25 / 42.25 |
| Nonexact resident / cold windows | 0 / 0 | 4 / 1 |
| request_to_resume_return_us: sum / max (us) | 14068 / 5889 | 136264 / 3723 |
| bh_to_before_final_log_us: sum / max (us) | 14910 / 6109 | 139924 / 4105 |
| Max inspections per BH | 194 | 251 |
| Before-setting / crossing / after-window BH | 5 / 0 / 0 | 5 / 0 / 0 |
| Probe live checks (256 / 512 MiB), errors | 148 / 149, 0 | 148 / 149, 0 |

A9 SG lengths were 38 x 2 MiB and 4 x 4 MiB. B6 retained all native sizes: 1299 x 256 KiB, 734 x 512 KiB, 210 x 1 MiB, 51 x 2 MiB and 4 x 4 MiB. Lower minimum order did not split larger free-list blocks. Cumulative report bytes and address unions are observed SG coverage and may repeat pages.

The exact B6 subset contains 169 complete resident object windows: old refcount 1, PRESENT 16/16 host pages, mincore 256 KiB to 0, global free +16 and speculative delta 0, with no query/cleanup errors. Its address union is 27.75 MiB; cumulative target objects and old PRESENT each total 42.25 MiB. These are finite object-window observations, not strict physical net savings.

Keep the other B6 observations separate: three partly resident objects had PRESENT/pagedout 8/8, 11/5 and 14/2, with free deltas +16, +16 and +17; a fourth full-resident object had free +19. All had mincore matching initial PRESENT and then 0. One cold object had PRESENT 0/pagedout 16 but global free +16. All five had speculative delta 0. The global-counter mismatch remains unresolved; it is not credited as additional resident release. All 173 resident old references were 1, and all observed native cleanup/mincore/page/counter query error counts were 0.

Both runs explicitly read initial order 10, wrote/read back the selected 9 or 6 under a same-boot guard, and retained that order after setting, after both probes and at final metadata. A9 PID 52491/boot 7868e8bc-ccd2-434d-9b68-a3d83632ecf7 and B6 PID 55557/boot 4ba7e58f-31f8-410e-a68e-0533d1887631 are recorded exactly in the JSON. Each window includes sequential metadata overhead; the six root reads are not an atomic snapshot. Buddy counts include already-reported/CMA blocks and do not prove host residency or benefit. Five complete pre-setting BHs per launch were excluded; no BH crossed either comparison boundary and no BH fell after the final window.

`request_to_resume_return_us` includes pause acquisition/drain/resume bookkeeping. `bh_to_before_final_log_us` starts after the initial snapshot/GArray and ends before the final log. Neither measures full BH body or each vCPU's exact off-run duration; observed sums/maxima are not bounds. B6's smaller single-window maximum accompanies a larger total and many more BHs/SGs. No shared BH 2-attempt/32-inspection budget is implemented; these caps remain per SG.

The first A9 attempt (PID 51018) failed the printf-to-sysfs write/readback before measurement and stopped normally; raw receipts remain in `evidence/20261101-v7-order-comparison`. The successful A9 stopped normally, but the immediate freezer still saw its dying QEMU in ps and refused. Its failure, later PID absence, original log, append-only check and successful offline retry are retained; its guest workload was not repeated. A9/B6 runner copies and a diff prove that the only intervening change was a bounded 5 s wait for the selected QEMU comm to disappear after normal stop. B6 stopped and froze normally. Final ps receipts show both PIDs absent and lsof shows no listener on private console 20160.

Raw and per-run frozen analysis are in `evidence/20261101-v7-order-comparison-retry1/order9` and `order6`. The companion JSON contains exact hashes, all nonexact physical rows, metadata boundaries and every validation check. Private opt-in only; strict global physical pass, net-memory saving, kernel metadata cost and full BH body remain unmeasured/unestablished. No further guest rounds were run.
