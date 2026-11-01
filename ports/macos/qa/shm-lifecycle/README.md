# SHM lifecycle host contract QA

`verify.py` reviews D0009 in a newly created private source tree. It never applies the patch to the configured emulator tree, runs guests, or loads an emulator/GPU backend binary.

```sh
python3 ports/macos/qa/shm-lifecycle/verify.py \
  --qemu .macos-work/aosp/external/qemu \
  --aemu .macos-work/aosp/hardware/google/aemu \
  --build .macos-work/aosp/objs \
  --patch incoming/macos-qemu/0009-shared-memory-recording-lifecycle.patch \
  --output .macos-work/qa/d0009-shm-lifecycle-NEW
```

The output must not exist. The runner snapshots the patch and relevant production files, validates and applies the patch only in its private Git directory, selects the matching Xcode compiler/linker/SDK from the configured Ninja SDK, builds baseline and patched drivers, and saves commands, stdout/stderr, hashes and bounded child results in `report.json`. Negative-control timeouts are deliberate failures of the original source. Their child-only POSIX SHM names are cleaned up after termination.

Compiled production TUs are `VideoFrameSharer.cpp`, `CallbackRegistry.cpp`, `MessageChannel.cpp` and QEMU's `SharedMemory_posix.cpp`. The real synchronization, message queue, callback registry and POSIX mapping implementations are exercised. Exported SHM start/stop and GPU register/unregister/set-record-mode functions are copied verbatim from their production source into generated includes. The driver supplies the surrounding Globals container, renderer/GpuFrameBridge/post controls, deterministic read-pixels endpoint, logging, wall clock and unused Producer type.

The cases cover idempotent start/stop; stable handle and real mapping across stop/restart; retained old header after stop; synchronous repost delivery through the real registry and Sharer callback; initialization failure and retry; never-started/running/stopped destruction; another recording owner; parallel exported starts/stops and independent Sharer starts/stops; stop/destructor waiting for an in-flight callback; same-key replacement and callback-internal registration becoming visible on the next invoke; the real 64-slot full queue and 128 no-frame start/stop cycles; and a producer overlapping external unregister without frame delivery. Baseline counter and queue cases must fail. Patched cases must pass normally and with every compiled TU instrumented by ASan/UBSan. This macOS runtime does not support LeakSanitizer, so leak detection is disabled.

This is function/TU contract verification, not wholeGPU, runtime ABI, packaging, guest or ADB/SHM pixel-parity validation. Callback-internal unregister/self-destruction remains unsupported, as in the existing registry. The patch retains the 24-byte SHM header and keeps the mapping plus its old header after stop; `shm_ready` continues to mean mapping existence, not active streaming. Runtime engine startup and SHM restart still require downstream validation after an authorized build.
