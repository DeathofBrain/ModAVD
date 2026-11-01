# Synthetic native HVF object reclaim

```sh
python3 ports/macos/qa/hvf-object-reclaim/verify.py \
  --output .macos-work/qa/hvf-object-reclaim-new
```

The only argument is a required new output directory. Existing paths are refused, including failed attempts. This requires Apple Silicon macOS, 16 KiB host pages and Xcode command line tools. It compiles the preserved `probe.mm`, ad-hoc signs only the new output executable with the supplied Hypervisor entitlement, and runs it with a 60-second timeout. No production executable or source, package, emulator or guest is modified or operated.

The native source is preserved byte-for-byte from the local `oldobject-hvf-release-sysctl-native-20261101` experiment. Three rounds pair 64/128 MiB targets with monolithic-object controls and independent 2 MiB backing objects. Each case begins with one whole-range HVF registration. A synthetic vCPU writes every 4 KiB target boundary, then remains stopped throughout replacement and cleanup. Independent cases repeat for a second generation, for 12 positive generations and 6 control generations. Peak touched synthetic RAM is 132 MiB; no pressure workload is used.

Each 2 MiB transaction retains an old-object rollback alias until all replacement mappings commit. The critical measurement window only destroys those existing aliases and reads kernel counters. It performs no user allocations, extra diagnostic aliases, target/neighbor data loads, `madvise` or vCPU execution. The test never discards or inspects Android guest data.

`assessment.json` separates mapping/ownership correctness from physical observation. Correctness requires all system calls to succeed, old independent objects to have one remaining reference, distinct nonzero object identities per chunk and 4096/8192 old resident pages, new targets to have zero resident/paged-out pages, and neighbor identity/residency/state metadata to remain unchanged. It verifies 12 positive generations and 6 controls. Physical observation separately requires each positive cleanup to increase direct `vm.page_free_count` and free-plus-speculative counters by exactly 4096/8192 host pages; both control deltas must have magnitude at most 2 host pages. System-wide counter noise can fail the physical observation while correctness passes.

`host_statistics64` is cached/rate-limited on current macOS and may show zero delta despite release. Process footprint can debit before the old object physically releases. Those values are retained only as background, not used as primary physical evidence. Metadata checks of neighbors do not establish content canaries. Outstanding kernel paging/I/O can defer object reaping, and this bounded native experiment does not measure production Android memory benefit or authorize default enablement.

Each run preserves source and entitlement snapshots/hashes, compile/sign/run commands and stdout/stderr, the signed executable hash, `native-report.json`, wrapper `report.json` and separate `assessment.json`. No historical reports, binaries or XNU source are vendored here.

The [mechanism assessment](mechanism-assessment.md) separates this physical mechanism from the production integration and access-safety requirements. The first exported run retained mapping/ownership correctness but failed exact physical observations in six positive windows due to system-wide counter variation. Its original evidence remains unchanged; `assessment-v2.json` assesses the same raw data with the strict two-page control bound and explicit independent object identities, without rerunning native code.
