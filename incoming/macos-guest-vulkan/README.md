# ARM64 guest Vulkan additions

This directory ports the existing x86 guest `khrfix` and `bindlock2` behavior to
the exact API 34 Google APIs `arm64-v8a` revision 14 encoder. It does not replace
the SDK `vendor.img` or distribute a patched guest library. Manager can build
the library locally from the guest's original bytes.

| Identity | Value |
| --- | --- |
| Original file | `/vendor/lib64/libvulkan_enc.so`, ELF64 little endian AArch64, 4,409,384 bytes |
| Original SHA-256 | `d6188011ed2ed58a1d69adfb04246abaa2595c0f15565a22da83f7751a3d6828` |
| Original build ID | `e5dfdb0ada3f418d42db2660f8bc5bd5` |
| Candidate v2 SHA-256 | `73ddfe0935826feef1f1a78d5a70af3ee7fa83584d2efaf5bad6ee91f36eb613` |
| Architecture-specific cache | `libvulkan_enc.arm64-khrfix-bindlock2.so` |

All offsets and both complete hashes are fixed. An unrecognized image revision,
modified library or already fixed input is rejected. The original x86 profile
and the original 89 source patches remain independent.

## Mechanism

Both `vkUpdateDescriptorSetWithTemplateKHR` entries branch to the core entry,
which linearizes descriptor template data before calling the encoder. The
unmodified KHR entries call the encoder directly. The native GPU negative
control has reproduced their lost array elements with buffer, image and buffer
view descriptors.

The `vkBindImageMemory`, `vkBindImageMemory2` and KHR bind2 forwarders branch to
two AArch64 wrappers in the retired KHR dynCheck body. The wrappers preserve the
original arguments, lock `ResourceTracker::Impl::mLock` only around the original
map validators, unlock, then call the original encoder on success. Errors and
encoder return values are preserved. No host round trip occurs with this mutex
held. The wrappers use 64-byte aligned stack frames and total 276 bytes.

The patch also rebuilds three FDEs, relocates their PC-relative references and
the complete `.eh_frame_hdr` table, and keeps section sizes and all 9,563 FDEs.
The other 9,560 CFI programs are unchanged. Candidate v1 lacked this unwind
repair and is retained only as historical evidence; use v2.

## Build and local verification

From the repository root, use new output names for every evidence report:

```sh
python3 incoming/macos-guest-vulkan/build-arm64-candidate.py \
  /path/to/original.so --output /path/to/candidate-v2.so \
  --report /path/to/new-build-report.json

python3 incoming/macos-guest-vulkan/verify-native-arm64.py \
  /path/to/original.so /path/to/candidate-v2.so \
  --output /path/to/new-native-report.json

python3 incoming/macos-guest-vulkan/verify-arm64-unwind.py \
  /path/to/original.so /path/to/candidate-v2.so \
  --output /path/to/new-unwind-report.json

python3 incoming/macos-guest-vulkan/verify-manager-arm64.py \
  /path/to/original.so /path/to/candidate-v2.so \
  --manager-src .macos-work/manager --output /path/to/new-go-report.json
```

The candidate builder uses the pinned AOSP Clang/lld to assemble
`arm64-bindlock.S`; the pure-Go `manager-arm64-profile.go` needs no assembler at
runtime. The isolated Go 1.26.5 verifier copies the existing Manager `vkfix` and
`errs` source into a temporary module, adds the staged profile, and compares the
complete result byte for byte with the independently assembled candidate. It
also checks unchanged inputs, independent output, deterministic output, and
rejection of nil, empty, truncated, corrupted and already fixed inputs.

Recorded local evidence:

- [Native ARM execution](../../.macos-work/qa/arm64-guest-vulkan-native-v2-20261101.json):
  original validator instructions, both wrappers, four threads and 60,000 stress
  calls passed; 60,021 lock/unlock pairs, encoder calls outside the mutex, both
  KHR routes, and 36 actual registered-FDE unwind checks passed. The original
  unguarded validator fails the controlled valid-handle negative control.
- [Independent unwind inspection](../../.macos-work/qa/arm64-guest-vulkan-unwind-v2-20261101.json):
  all 9,563 header entries and 202 instruction PCs passed against independently
  decoded stack instruction semantics, including both bind2 exits. The original
  stale CFI fails the negative control.
- [Pure-Go output verification](../../.macos-work/qa/arm64-guest-vulkan-go-profile-20261101.json):
  all 4,409,384 output bytes equal candidate v2; all input and rejection checks
  passed under Go 1.26.5.

The native test runs private JIT mappings with synthetic tracker maps and
checked PLT callbacks. It proves native ARM argument, lock and unwind behavior;
its KHR check replaces the core entry and does not exercise Android TLS,
descriptor linearization or GPU output. It does not establish Android bionic
unwinder behavior or game compatibility.

The [real Android negative control](../../.macos-work/qa/guest-vulkan-original/receipt.json)
then reproduced all six KHR-update failures: both template creation routes for
each of the three descriptor types returned only the repeated first value,
while ordinary writes and core updates returned the expected distinct values.
The [candidate v2 positive run](../../.macos-work/qa/guest-vulkan-candidate-v2/receipt.json)
passed all twelve core/KHR template combinations using actual GPU dispatch and
readback on Apple M4. The ARM64 `size_t` pNext checks also passed. Four threads
completed 1,024 iterations each: 4,096 image/buffer pairs, 8,192 bind calls across
original, core2 and KHR2 routes, and zero Vulkan errors. The original driver also
passed its shorter concurrent bind control; the Android stress establishes a
bounded regression check, while the native controlled map test demonstrates
the lock defect and its repair.

The candidate was mounted only in the independent guest (private ADB server
20350, serial `emulator-20030`, QEMU PID 89203); QEMU identity and boot ID remained
unchanged. The temporary mount was rolled back to the original hash afterward.
This does not establish long-running game compatibility, arbitrary image
versions, OOM recovery or Android unwinding while inside the wrappers.

## Disposable Android GPU verification

See [GPU-PROBE.md](GPU-PROBE.md) for the real Android probe and its exact
coverage. Use only a named disposable guest with a private ADB server and a new
evidence directory. Never install into an existing game session.

`temporary-candidate-mount.sh` is a QA helper with `install` and `rollback`
actions. It checks the exact original/candidate hashes, refuses a preexisting
driver mount, copies the vendor SELinux label, and verifies the mount or
restoration. Push candidate v2 to
`/data/local/tmp/modavd-vulkan-arm64-candidate-v2.so` before running it as root.
Mount and rollback must run in the same mount namespace as the probe; compare
the ordinary and root shell's `/proc/self/ns/mnt` first. A fresh standalone
native probe loads the driver independently and needs no zygote restart. The
helper neither restarts zygote nor writes the vendor image.

Manager now selects the architecture's original hash, fixed hash, cache name
and patch function throughout checking, upload, mounting and reporting. The
guest script accepts only the two known architecture cache names. The existing
zygote restart for app processes, adopted-instance check-only behavior, and
foreground/install gates for deferred setup are retained. The staged profile
in this directory remains the independently verified reference.

[Manager validation](../../.macos-work/qa/manager-arm64-final-validation-20261101.json)
records full Darwin Go tests and vet, race checks for `emu` and `vkfix`, the exact
ARM64 fixture, Windows compilation/linking, deterministic pinned-source patch
verification, native builds and strict deep app signature verification. The
final Manager overlay SHA-256 is
`2182eac11f98e0261f758846f1d59a329b9de46d1a745f2a5333f454f23f0c68`.
Final combined-package acceptance still needs automatic ARM64 generation and
mounting, reboot/cache reuse, and no repeated zygote restart within one boot.
