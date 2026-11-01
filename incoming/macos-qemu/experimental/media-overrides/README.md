# Media dispatcher teardown experiment

This experiment releases codec plugins still owned by a Media dispatcher when
its address-space context closes. The three dispatchers previously defaulted
their destructors while storing plugins as raw pointers. They now swap their
maps into local storage while holding `mMapLock`, then delete every plugin after
releasing that lock. Existing explicit DestroyContext and Reset behavior stays
responsible for the plugins those operations remove or replace.

`dispatcher-cleanup.patch` contains the four QEMU-private source/header changes.
The real files are already modified in `.macos-work/qemu-patch-dev`.
`manifest.json` provides public H264 and HEVC declaration overrides and the
private include root for the isolated build helper. The unchanged AOSP source
and normal build outputs are read-only inputs. Do not apply both the private
patch and the same change a second time.

The actual Apple helper uses `VTDecompressionSessionDecodeFrame` with flags=0.
The installed SDK's `VTDecompressionSession.h` says that with asynchronous and
temporal flags both clear, the output callback completes before the function
returns; on an error return there is no callback. The helper also waits for
asynchronous frames after success. Its destructor calls deInit, which performs
Invalidate then CFRelease of the session and releases its pixel buffer. The SDK
defines Invalidate as deterministic orderly teardown. No helper override is
needed for these current call modes. Input NAL bytes are copied before submission;
native callbacks consume decoder-owned pixel buffers, not the guest packet.

The owning Media context must destroy all three dispatchers before freeing its
buffer and releasing any context-lifetime reclaim inhibitor. That ordering and
the reclaim-counter implementation belong to the separate memory experiment.
This directory does not certify their behavior or complete pointer ingress.

Validation:

- All three modified complete translation units compiled for ARM64 with the
  actual production headers and build flags. Evidence:
  `.macos-work/qa/media-dispatcher-cleanup-build-20261101/real-header-object-build.json`.
- `verify-cleanup.py` runs the actual three dispatchers and parsers with counted
  codec shims under ASan/UBSan. It covers 120 cycles of create, explicit destroy,
  H264/HEVC reset, snapshot save/reload, empty teardown and dispatcher teardown
  with leftover plugins. A plugin-destructor reentry takes the map lock, checking
  that deletion happens outside it. Original sources are a failing negative
  control. Evidence: `.macos-work/qa/media-dispatcher-cleanup-final-20261101.json`.
- The patched run creates/destroys VPx 200/200, H264 240/240 and HEVC 240/240
  plugins; all three live counts finish at zero. This is an ownership test with
  codec shims, not a native decoder or guest workload test.

```sh
python3 incoming/macos-qemu/experimental/media-overrides/verify-cleanup.py \
  --output /absolute/new/evidence.json
```
