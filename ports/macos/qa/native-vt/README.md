# Native VideoToolbox QA

These tools exercise real macOS H.264/HEVC decoding with tiny local fixtures. They require Apple Silicon macOS, Xcode command line tools, a matching AOSP source checkout, and an existing backend build. They never build or modify a backend, source checkout, package or guest. No downloads are required.

All output directories must be new. Each run preserves its executable, stdout/stderr, source and backend hashes, compile command and `report.json`. Existing evidence is refused, including a directory from a failed run. Use a different output path to retry. Both verifiers require explicit `--backend`, `--output`, `--fixtures` and `--source-root`; the fixture generator is independent of a backend and requires only `--output`.

## Generate fixtures and references

From the repository root:

```sh
python3 ports/macos/qa/native-vt/generate-fixtures.py \
  --output .macos-work/qa/vt-fixtures-new
```

`generate.mm` creates deterministic 128×96 I420 input, hardware encodes eight distinct frames per codec, then independently hardware decodes them to tight I420 references. Annex-B access units include parameter sets in the first frame and subsequent P slices without B-frame reordering. `manifest.json` records each access unit, PTS, NAL types, reference, input distortion and SHA-256. Encoded bytes may differ between macOS versions. The references share Apple's decoder backend; they do not reuse production helper parsing, copy or lifetime code.

## Test the actual current helper implementation

```sh
python3 ports/macos/qa/native-vt/verify-helper.py \
  --backend .macos-work/aosp/objs/distribution/emulator/lib64/libgfxstream_backend.dylib \
  --source-root .macos-work/aosp \
  --build-root .macos-work/aosp/objs \
  --fixtures .macos-work/qa/vt-fixtures-new/manifest.json \
  --output .macos-work/qa/vt-helper-new
```

`--qemu-source` defaults to `SOURCE_ROOT/external/qemu`; set it when testing a separately patched source tree. `--build-root` is optional for this verifier and records the backend's build metadata when present. `--expected-backend-sha256` can pin an expected binary identity; the actual hash is always recorded and checked again after the run. Source hashes identify the supplied checkout, while matching source to binary still requires build provenance.

The backend must resolve to a file named `libgfxstream_backend.dylib`. Before launching a verifier, the wrapper removes inherited `DYLD_*` and `__XPC_DYLD_*` variables, sets `DYLD_LIBRARY_PATH` to the selected backend directory and enables `DYLD_PRINT_LIBRARIES`. Dependencies must be available there or through the binary's existing rpaths. `report.json` records removed variable names, the explicit launch settings and the backend path observed in actual dyld loaded-image records. Every positive and expected-negative result requires that the only observed backend path resolves to the requested file; missing or different records fail verification. Disk hashes and link commands alone do not establish runtime library identity.

Add `--asan` and choose another output directory for ASan/UBSan mode. It instruments the QA executable and intercepts allocations; the linked backend and Apple frameworks retain their original instrumentation.

`helper.mm` links the actual exported helper methods and FFmpeg metadata probe. For each codec it runs 28 sessions: fresh init/decode/flush/drain/deInit, reuse of the same object, pending-output teardown and destructor-only teardown. It compares decoded dimensions, frame count, order, PTS and bytes with references, checks retained state, and marks each caller input page `PROT_NONE` after decode returns until helper teardown. Inputs include FFmpeg's 64 zero-padding bytes and guard pages. No decoder, session or codec is replaced by a shim.

## Test the complete legacy H264 translation unit

```sh
python3 ports/macos/qa/native-vt/verify-legacy.py \
  --backend .macos-work/aosp/objs/distribution/emulator/lib64/libgfxstream_backend.dylib \
  --source-root .macos-work/aosp \
  --build-root .macos-work/aosp/objs \
  --fixtures .macos-work/qa/vt-fixtures-new/manifest.json \
  --output .macos-work/qa/vt-legacy-new \
  --asan
```

This legacy class is absent from current backend exports and is not selected by the current default H264 factory. The verifier compiles its entire actual `.cpp` into the QA executable and links real parser/renderer dependencies from the supplied backend. It extracts the actual `Stream.cpp.o` from `BUILD_ROOT/archives/libandroid-emu-base.a` into the output directory to satisfy save/load link dependencies. It does not execute snapshot load.

The driver uses the version100 CPU-copy protocol and processes one NAL per call, as required by this class. It checks 30 hardware sessions, 180 decoded frames, 170 getImage reference comparisons, reset/flush, repeated explicit destroy and leftover-frame destruction. Caller request pages remain inaccessible after decode until destruction. In `--asan` mode both driver and complete legacy TU are instrumented; dependency objects and Apple frameworks remain unchanged.

To reproduce the original array-owner defect, add `--original-negative --asan` and use another output directory. By default the full TU is read with `git show` from the exact Google baseline `f0c183f1cc7456ecd6f3607f2f47893768ae4334` in `SOURCE_ROOT/external/qemu`. `--baseline-repository` and `--baseline-revision` can select another local Git snapshot. The verifier requires baseline-to-actual delta to be exactly the single `unique_ptr<uint8_t>` → `unique_ptr<uint8_t[]>` change. A successful negative-control report means the native process **failed** with `alloc-dealloc-mismatch`; it records `expected_negative=true`. This is separate from a passing fixed implementation.

## Evidence boundaries

These are small fixed-size CPU byte-buffer tests. They do not validate GPU texture output, Android codec service/MediaContext integration, damaged/large/reordered streams, guest workloads, physical memory reclaim or Apple internal allocation leak freedom. Private-field inspection changes access only, not layout or implementation. Testing a separately compiled legacy TU does not prove that a packaged executable selects that class.

Historical local evidence remains under `.macos-work/qa/native-vt-{fixtures,helper,legacy}-20261101/`; no dylib, executable, generated fixture or historical report is vendored in this directory. The three native driver/generator source files were copied without behavioral changes from those verified runs.

The repository wrappers were independently run against the signed production main backend, SHA-256 `1d1257c8358424558f5b751705878beab2304daac137253d5eaafb6dbfe42986`, using fresh fixtures. Helper normal/ASan and complete legacy-TU normal/ASan passed; the Google-baseline legacy negative control aborted with the expected allocation mismatch. All five runs confirmed the production backend through dyld and unchanged source/backend hashes. The normal helper run additionally inherited an incorrect library path and framework path, both removed before its native launch. Reports remain in `.macos-work/qa/native-vt-production-{helper-normal,helper-asan,legacy-normal,legacy-asan,legacy-negative}-20261101/report.json`, with an identity/build-receipt index in `.macos-work/qa/native-vt-production-suite-20261101.json`. These results retain the evidence boundaries above.

The sealed D8 backend, SHA-256 `67812b82a0bbfcad9b6a65184b45d18b848ef66867b05bacb90ba26a29ec6d99`, was separately tested with the existing verified fixtures. Fresh helper normal/ASan runs each passed 56 hardware sessions, 448 decoded frames and 5,898,240 byte comparisons, with dyld identity and unchanged source/backend hashes. Reports are `.macos-work/qa/native-vt-d8-helper-{normal,asan}-20261101/report.json`, indexed with the D8 build receipt in `.macos-work/qa/d8-native-gpu-video-suite-20261101.json`. Legacy tests were not repeated for D8; their D7 reports remain historical evidence for that binary and the separately compiled legacy source.
