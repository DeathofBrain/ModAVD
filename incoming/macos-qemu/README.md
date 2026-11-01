# Darwin QEMU additions

Apply the nine ordinary diff files in numeric order after all 65 original
`mas36` QEMU patches. The original series is retained. There are no mail author
headers in these additional patches.

| File | Purpose |
| --- | --- |
| 0001 | Set Darwin TCP_KEEPALIVE idle time and the independent keepalive interval/count options. |
| 0002 | Recognize a destination volume's sparse-file capability before applying the sparse userdata free-space rule. |
| 0003 | Bound Apple Silicon guest CPU count by physical host cores, HVF's limit, and the current GICv2 limit of eight. |
| 0004 | Add synchronous `window show` / `window hide` console control for Qt instances. |
| 0005 | Route Cocoa keys and input-method text through the existing host IME channel. |
| 0006 | Correct the ARM64 HVF Q30 SIMD register mapping inherited from the pinned base. |
| 0007 | Match three VideoToolbox temporary array owners to their existing array allocations. |
| 0008 | Destroy remaining video-dispatcher plugins and preserve decoder/alias teardown ordering and HEVC snapshot framing. |
| 0009 | Make shared-memory recording start/stop idempotent and callback removal synchronous, including when no frames arrive. |

Patch application was checked against the original 65-patch tree
`5483e1bf05ebb3cd7549aed2adcde998bf1c4703`. Before 0007, the six functional additions reproduced tree
`b4d2ab46e9edf50ae500185698cb9c07870492b7`. With 0007 and the three separate
`macos-build` overlays, bootstrap verifies the complete tree
`ce93141c684b1cc23a2231fda48b4ccf3b70a795`. With 0008, the complete QEMU tree was `f0ad4acfabc8ed00711847b393cea5515fe10ca0`.
With 0009, the current complete QEMU tree is `3058e5f90f327708e08e4df6eff63b28362479b3`. Its companion
[`macos-aemu` declaration overlay](../macos-aemu/0001-media-dispatcher-cleanup-declaration.patch)
has its own bootstrap and package receipt; the AEMU overlay tree is
`04a678d0a729c6f9870e5f2c8149256294489298`. The original 89-patch series
is unchanged.

## Verification

On an Apple Silicon Mac, from this directory:

```sh
python3 verify-native-adaptations.py /path/to/patched/qemu
python3 verify-control-routing.py /path/to/patched/qemu
python3 verify-hvf-simd.py /path/to/patched/qemu --output /path/to/new-report.json
```

The native adaptation check compiles the actual production helper functions
against the macOS SDK and checks real socket options, volume capabilities,
sparse allocation, and HVF CPU limits. The control check verifies actual console
responses and physical-key identity with interface shims; the Cocoa event filter
is compiled against AppKit using Qt interface stubs. These checks passed on
Apple Silicon macOS 26.6.2. These helper checks alone do not establish a successful
whole-core build, live Qt window behavior, or a working Chinese/Japanese
input-method session.

The SIMD check extracts the production mapping verbatim, verifies every Q0–Q31
mapping independently, and proves that restoring the original Q30→Q20 defect
fails. A separately signed helper creates its own HVF VM, one vCPU and 32 KiB
of private memory. Sixteen rounds verify all 128 bits using canonical API
register names and AArch64 guest STR/LDR instructions. The check passed on this
Mac; the incremental core build and strict directory signature also passed in
`.macos-work/build-hvf-simd-fix.log`. This covers the mapping and real HVF
register transfers. A later full-QEMU Android worker held distinct Q0–Q31
values through ten pause/resume cycles and three snapshot save/load/delete
cycles, completing 2,135,867,594 checks without mismatch. That private v5 run
does not establish every-vCPU, host sleep, or game stability. Its immutable
evidence is `experimental/evidence/20261101-v5-simd-snapshot-r3/capture.json`.
The independent `ModAVD-gapfix-media` package includes the correction and has
booted through Qt; the original game package remains unchanged. See
[the update receipt](../../.macos-work/qa/core-hvf-simd-update-20261101.json).

The Q30-to-Q20 typo is present in Google's pinned base and also in the
examined `emu-main-dev` commit `cf0f40a0aad1aa3fca68f2f89372525c8d28ca68`
(2026-10-27). It was inherited, rather than introduced by this Darwin port
or Apple's HVF API. The read-only [upstream query receipt](../../.macos-work/qa/google-qemu-current-branches-hvf-map-20261101.json)
pins the URL and source hash; it does not establish every Google release or
the original introducing commit.

Separate integration runs have now built and signed the whole core and booted
the official API 34 ARM64 guest through both headless and Qt paths. The first Qt
show/hide/show run kept the guest boot ID and produced the expected visibility
responses. Patch 0004 subsequently gained the secondary-window visibility fix;
the incremental build completed successfully in
`.macos-work/build-window-fix.log`, while a fresh live check of that revision is
still pending. A real desktop PNG was read through POSIX SHM. This does not
establish dynamic-frame correctness, complete host IME behavior, or game
compatibility; see [the guest evidence record](../../ports/macos/qa/GUEST-CHECKS.md).

Qt visibility control returns `windows=1` followed by the console's `OK` on
success. A true headless binary returns `KO`; showing a window requires a GUI
instance with an initialized backing surface. Hiding leaves Android running.

## RAM reclamation remains experimental

The original free-page reporting handler is retained. Its Darwin backend still
uses the existing `MADV_DONTNEED` path. The generic reporting counter measures
bytes submitted to a successful API call; it does not measure physical bytes
actually released. See [darwin-memory-reclaim-design.md](darwin-memory-reclaim-design.md)
for the native measurements and the synchronization needed before adding a
production backend.

The optional probe uses 64 MiB of its own temporary process memory and a single
HVF vCPU running a small AArch64 loop. It exercises every 4 KiB guest page,
including the four guest pages inside a 16 KiB host page:

```sh
python3 run-experimental-hvf-probe.py --all-advice
```

The unmap/replace/remap cases test a full 64 MiB mapping and a 2 MiB interior
range. Assertions check that all sampled guest and host values agree, every
4 KiB boundary outside the replaced range retains its data, and guest writes
remain visible to the host after remapping. This experiment is not part of the
eight production patches.

A separate bounded experiment runs 2/4/8 vCPUs, waits for every vCPU to leave
HVF before changing mappings, retains old backing through a temporary Mach
alias, and exercises real HVF unmap/remap failures and restoration:

```sh
python3 run-experimental-hvf-transactions.py
```

Each subprocess maps at most 65.1 MiB into its guest. Full and 2 MiB interior
transactions passed for all three CPU counts. The test checks sampled words
at every 4 KiB boundary after rollback, committed zeros, unchanged neighbors,
and guest reuse. Its private mappings and thread handshake do not establish
QEMU's ownership, renderer/device synchronization, or Android behavior. See the
design for accounting limits and the prerequisites that still block a
production reclamation patch.

[darwin-reclaim-production-audit.md](darwin-reclaim-production-audit.md) traces
the current host-pointer/DMA/HVF entry points and report/reset lifetime. It
proposes a conservative first backend restricted to pages never exposed to
other host users, plus the concrete metadata and API hooks needed before any
production replacement can be enabled.

The separate [experimental audit prototype](experimental/README.md) records
host-exposure metadata and report-owner coverage without enabling replacement.
Its patch is outside the bootstrap overlay glob. Real metadata boundary and
concurrency tests, plus a read-only seven-translation-unit compile check, passed;
whole-emulator coverage and Android physical-memory benefit remain unverified.

## Independent VideoToolbox ownership correction

Patch 0007 changes only three `unique_ptr<uint8_t>` declarations to
`unique_ptr<uint8_t[]>`, matching the existing `new uint8_t[...]` allocations
in H264, HEVC and the legacy H264 helper. The actual Google baseline
`f0c183f1cc7456ecd6f3607f2f47893768ae4334` already contains all three defects;
[the corrected source attribution](../../.macos-work/qa/native-vt-helper-20261101/allocator-origin-google-baseline.json)
records whole-file comparisons. Private actual-helper tests reproduced the
allocator mismatch and passed after correction. Main production backend and
both QEMU engines have now been built, installed and strictly signed;
[the production receipt](../../.macos-work/qa/core-videotoolbox-update-20261101.json)
records their identities. Reusable real-codec fixtures, helper and legacy
whole-translation-unit QA are in [native-vt](../../ports/macos/qa/native-vt/README.md).
This independent correction includes no experimental RAM reclamation code.

## Video lifecycle correction

Patch 0008 swaps each dispatcher's remaining plugins out under its mutex and
destroys them after releasing the mutex. Context teardown and snapshot load
destroy decoders before releasing their backing alias; save includes the HEVC
active count. This preserves the intended snapshot format. It does not repair
old snapshots whose HEVC framing was already malformed. The paired AEMU overlay
declares the H264 out-of-line destructor.

Actual dispatcher/parser and Context translation-unit checks with explicit
codec/control shims pass under ASan, including original-code negative controls.
See [the lifecycle suite](../../.macos-work/qa/media-lifecycle-d0008-suite-20261101.json)
and [build/signature receipt](../../.macos-work/qa/core-video-lifecycle-update-20261101.json).
The source-level shims are not whole-guest codec proof. Fresh hardware
VideoToolbox and real-GPU FrameBuffer checks use the sealed D0008 backend;
their independent outputs retain exact loaded library identities.

## Private physical-reclaim progress

The private v6 design constructs independent 2 MiB VM objects at a continuous
RAM virtual address, with exact allocation receipts and per-object rollback.
Native real-HVF positive/negative checks establish the object-release mechanism;
systemwide free-page counters can include background noise and failed strict
observations are retained. See [the mechanism assessment](../../ports/macos/qa/hvf-object-reclaim/mechanism-assessment.md).
This remains outside the production overlay glob. Android safety and physical
benefit must both close before production integration or a default change.

## Shared-memory recording lifecycle correction

Patch 0009 removes the duplicated recording references, makes start/stop and
sharer destruction idempotent, and unregisters callbacks synchronously. Removal
first drains queued registrations under the processing lock, so a stopped
renderer cannot fill the 64-entry queue with repeated add/remove pairs.
Callbacks do not take the sharer lifecycle mutex. The 24-byte SHM ABI is
unchanged; stop retains the mapping and its old header/pixels. Manager
`shm_ready` consequently means a mapping exists, not an active stream.

[Host contract QA](../../.macos-work/qa/d0009-shm-lifecycle-v4-20261101/report.json)
passes 12 normal and 12 ASan/UBSan cases using actual source/functions with an
explicit renderer boundary shim. All six original-code negative controls fail.
[Independent review](../../.macos-work/qa/d0009-independent-final-review-20261101/assessment.json)
passes 38 checks, and the existing [six CallbackRegistry tests](../../.macos-work/qa/d0009-upstream-registry-20261101/assessment.json)
pass in the rebuilt `android-emu_unittests` binary. The first command against
`android-emu-base_unittests` ran zero tests and is not counted as validation.
The [build/signature receipt](../../.macos-work/qa/core-shm-lifecycle-update-20261101.json)
records both new engines; the backend is byte-identical to the sealed D0008
backend, so that exact backend's GPU/video evidence remains applicable.

The independent `ModAVD-gapfix-shm` package has now booted the new Qt and
headless engines (34.8589 s and 12.2905 s). Each completed 80 repeated starts,
80 repeated stops, 40 start/stop cycles, frozen SHM while the guest display
changed, a new frame after restart, unchanged guest boot ID, and observed
stream/HOME restoration. Both guests stopped gracefully with sync. See
[Qt](../../.macos-work/qa/manager-shm-d9-qt-20261101/report.json) and
[headless](../../.macos-work/qa/manager-shm-d9-headless-20261101/report.json).
These public-operation observations do not expose internal reference counts,
prove an existing reader retains the same object, or establish pixel parity,
untorn frames or MAA consumption. The original game package is unchanged.
