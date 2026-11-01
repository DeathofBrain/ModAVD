# Native FrameBuffer GPU readback

This verifier links the existing production `libgfxstream_backend.dylib` and calls its real FrameBuffer implementation with the host Vulkan GPU. It requires Apple Silicon macOS, Xcode and the matching configured AOSP/Ninja build tree. It does not build, sign or modify the backend or operate a guest.

## Preserved 16-frame verifier

```sh
python3 ports/macos/qa/framebuffer-readback/verify.py \
  --backend .macos-work/aosp/objs/distribution/emulator/lib64/libgfxstream_backend.dylib \
  --build-root .macos-work/aosp/objs \
  --source-root .macos-work/aosp \
  --output .macos-work/qa/framebuffer-new
```

All four paths are required. Output must be a new directory, including when retrying a failed run. Add `--expected-backend-sha256 HASH` to pin a sealed binary, or `--asan` to instrument only the new driver with ASan/UBSan. Sanitizer mode uses Xcode's host compiler/runtime with the inherited Ninja ABI/include/define flags; ordinary mode retains the actual build compiler. The linked backend and Apple frameworks retain their existing instrumentation.

The wrapper queries Ninja for the actual `frame_buffer.cpp.o` compile command and retains its defines, include directories and ABI options. It records the command and source/build/backend hashes, strips inherited `DYLD_*` and `__XPC_DYLD_*` variables, selects the explicit backend directory and requires the matching path in actual dyld loaded-image records for each mode. It explicitly selects that directory's `vulkan/libvulkan.dylib` and MoltenVK ICD instead of relying on the QA executable's location, and also requires their actual loader/driver image paths. It checks all tracked hashes, including the Vulkan loader, ICD and driver, again afterward. Matching source to the selected backend still requires external build provenance.

Each mode uploads and reads back 16 changing 64×48 RGBA frames on the real GPU, with separate RGBA/BGRA callback targets, varying alpha and pixel patterns that detect channel and row ordering errors. Both modes require a byte-exact source GPU readback and latest `getPixels` result. Vulkan-only mode additionally requires byte-exact direct callback pixels and an absent GL emulation object. Normal GL mode requires a present GL object; its asynchronous callbacks may legally contain prior-frame pixels or null, so only the latest `getPixels` result is compared against the posted frame.

This verifies native FrameBuffer GPU upload/readback, not Android guest or shared-memory transport integration. It does not cover other sizes/formats, resize, malformed calls, concurrent teardown, or visual game output. The preserved source is `../framebuffer_readback.cpp`; each run saves a source snapshot, executable, compile/query/link/run logs and `report.json`.

Fresh normal and ASan runs passed against sealed D8 backend SHA-256 `67812b82a0bbfcad9b6a65184b45d18b848ef66867b05bacb90ba26a29ec6d99`. Both modes confirmed the actual backend, Vulkan loader and MoltenVK driver paths through dyld. Reports are `.macos-work/qa/framebuffer-d8-normal-final-20261101/report.json` and `.macos-work/qa/framebuffer-d8-asan-sdk-20261101/report.json`, indexed with the build receipt in `.macos-work/qa/d8-native-gpu-video-suite-20261101.json`. Earlier harness loader and sanitizer-toolchain failures remain in separate directories. Their failed runs are not counted as successful D8 coverage.

## Extended format and cache verifier

`verify_extended.py` and its adjacent `framebuffer_readback_extended.cpp` are byte-identical exports of the executed v4 QA. The old `verify.py`, its source and historical reports remain separate. `extended-provenance.json` pins the exported files, tested backend identities, receipts and source-only generator reproduction checks.

```sh
python3 ports/macos/qa/framebuffer-readback/verify_extended.py \
  --backend .macos-work/aosp/objs/distribution/emulator/lib64/libgfxstream_backend.dylib \
  --expected-backend-sha256 35fbd4366185ea5985e41e495c73c910030e7522075ef23de7c6317ea6f60920 \
  --build-root .macos-work/aosp/objs \
  --source-root .macos-work/aosp \
  --cache-contract candidate \
  --output .macos-work/qa/framebuffer-extended-new
```

Use `--cache-contract candidate` for G2 or a later backend implementing its initialized-cache contract. Use `--cache-contract sealed` for the preceding D9 backend; this skips only the new initial-zero-cache check. The selected hash must identify the actual selected dylib. Both profiles run `vulkan-only` and `gl` in separate processes, require a fresh output directory, and inherit the matching Ninja ABI flags. Add `--asan` for driver-only ASan/UBSan. The backend, Vulkan loader/driver and Apple frameworks retain their existing instrumentation. The wrapper checks actual dyld backend/loader/driver paths and source/build/runtime-file hashes before and after execution. Its logs do not by themselves establish that a source tree produced a selected binary; retain the separate build receipt.

Each mode checks 24 changing 64×48 matrix frames: RGBA source to each RGBA/BGRA target for eight frames, and RGBX/BGRA sources to each target for two frames. It verifies the real GPU source and exact latest `getPixels` bytes, including channel order and every fourth byte. The RGBX expectations follow the existing implementations:

| Mode | Exact RGBX fourth-byte expectation | Callback check |
| --- | --- | --- |
| `vulkan-only` | 255, explicitly normalized by the no-GL helper | Exactly one synchronous current-frame callback; flush adds none; all bytes compared |
| `gl` | The same GPU source buffer's raw `readColorBuffer` X byte; RGB still matches an independent known pattern | Async callbacks may contain a prior frame or null; latest `getPixels` remains exact |

No fourth byte is ignored and no tolerance is used. Existing `ColorBufferGl` maps RGBX to `GL_RGBA8`/`GL_RGBA`/`GL_UNSIGNED_BYTE`; `readback` and `readbackAsync` select RGBA/BGRA without opaque-alpha normalization. The no-GL helper explicitly writes 255. Applying its opaque-alpha behavior to GL was a QA assumption, rather than a shared implementation contract.

The no-GL runs additionally create, upload and source-read valid R8, width-mismatched and height-mismatched GPU buffers. Each sequence verifies good seed → rejected publication with unchanged callback count/cache → successful fresh recovery. Post acceptance alone does not imply callback publication. Copy-bound checks cover zero, 17 and oversized `getPixels` requests with halo guards. Candidate profile also requires zero initial cache and zero callbacks before a successful post; a void `getPixels` result does not signal frame validity.

Four v4 wrappers passed against sealed D9 and G2, each in ordinary and driver-ASan modes: eight mode executions and 192 matrix frames, plus the no-GL rejection/recovery and cache checks. All selected dyld image paths were confirmed; no sanitizer diagnostics appeared, and tracked source/build/runtime identities remained unchanged. The master receipt is `.macos-work/qa/gfxstream-d9-readback-candidate-20261101/future-native-runs-v4/receipt.json` (SHA-256 `8efeef0b3a929cf8e1e99f96cdc6e878ac62e124e7866b5dca89cf10fad29d2d`).

Failure history is retained under the same candidate directory. `future-native-runs/` records v1's test-driver const-pointer compile error; no GPU mode ran. `future-native-runs-v2/` records sealed no-GL success and the first GL mismatch. `future-native-runs-v3/` records all four wrappers failing the old uniform-opaque assumption at the same first RGBX→RGBA frame: byte 3 was uploaded/raw X=31, expected 255. Original RGBA source cases had passed before that point. v4 changes the per-mode expectation using the source evidence above, and retains exact comparisons; the old failed runs remain failed and are not counted as coverage or G2 regressions. Production G2 does not modify GL readers or the GL worker.

Native coverage excludes injected real GPU failure, other sizes, resize, malformed/overflow calls, arbitrary concurrent callback/removal/finalize, guest behavior, codecs, shared-memory integration and visual game output. ASan driver success is not sanitizer coverage of backend internals.

## Verbatim failure contract generator

`prepare_failure_contract.py` copies the production Impl reader method, legacy/checked forwarding methods, no-GL helper and actual failed-publication branch into a small component with explicit reader, renderer and lock doubles. It generates source and a manifest only; it does not compile, execute or initialize a GPU.

```sh
python3 ports/macos/qa/framebuffer-readback/prepare_failure_contract.py \
  --source-root .macos-work/aosp/hardware/google/gfxstream \
  --output .macos-work/qa/readback-contract-prepared-new

DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcrun --sdk macosx clang++ \
  -std=c++17 -DGFXSTREAM_ENABLE_HOST_GLES=1 \
  .macos-work/qa/readback-contract-prepared-new/contract.cpp \
  -o .macos-work/qa/readback-contract-prepared-new/contract-gles1
```

Run the generated executable separately. Repeat with `GFXSTREAM_ENABLE_HOST_GLES=0`, and optionally `-fsanitize=address,undefined -fno-omit-frame-pointer` with the matching Xcode compiler/runtime. Use separate binary/log paths and capture exit status, JSON output, named `FAIL:` assertions and sanitizer diagnostics. To reproduce the negative control, generate from a frozen pre-G2 D9 gfxstream tree; do not apply or revert patches in an active build to obtain it.

The reader doubles return false before any write, or after writing seven bytes. A valid original-D9 negative control must compile successfully and then exit 1 with six named failures for GLES=1, or three for GLES=0: failure incorrectly invokes a callback, partial failure invokes a callback, and partial failure changes the cache, for each available GL/Vulkan reader. A compiler error, crash or sanitizer error is not a passing negative control. G2 must exit 0 with zero failures; supported-format conversion and rejection checks must also pass.

The executed baseline/candidate × GLES1/0 × ordinary/ASan matrix met these expectations in all eight cases, with no sanitizer diagnostics. Its receipt is `.macos-work/qa/gfxstream-d9-readback-candidate-20261101/future-contract-runs/receipt.json` (SHA-256 `b1e46f2961286870126f880a04a8eae8abcff0a3d160abcd500ccd8e417f8c8e`). The exported generator reproduced both executed generated sources/manifests byte for byte without compiling or executing them again. This component does not test real GPU failures, full translation units, ABI compatibility, locks, scheduling or concurrent teardown; retain the full backend build and native evidence separately.
