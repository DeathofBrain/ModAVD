# Darwin gfxstream additions

Apply both numbered diff files in order after the original 23 gfxstream
patches. The original series is unchanged. From original tree
`51288846bdcb9054d899ffc8458d6744d6206e32`, overlay 0001 produces
`ea665af11444f3a33c10d22896fa27e53b862a62`; adding overlay 0002 produces
the current tree `d14277bc86e27f74baa1a1716b1ee29f71587895`.

## 0001: native context binding

AppKit's native bind methods return void. The overlay makes the platform helper
return whether both `NSOpenGLContext.currentContext` and `CGLGetCurrentContext()`
identify the requested context on the calling thread. Unbind requires both to
be clear. A non-null context with a null native window fails; the existing null
pbuffer path remains valid for core-profile rendering. EGL propagates the
helper result to the existing common failure handling.

```sh
python3 incoming/macos-gfxstream/verify-native-context-bind.py \
  /path/to/gfxstream --output /path/to/new-report.json
```

The check compiles the full production native sources and uses private actual
NSOpenGL/CGL objects. Twenty-seven checks passed, including real bind/unbind,
the null core-profile pbuffer, rejected null windows and a subclass that leaves
the previous context current when its void bind method is invoked. No other
app/window, emulator, guest or ADB service is accessed. The existing video
IOSurface pointer-type warnings are retained in the report, outside this patch.

The incremental whole-core build and deep strict signature verification passed
in `.macos-work/build-egl-bind-check.log`; the update receipt is
[core-egl-bind-update-20261101.json](../../.macos-work/qa/core-egl-bind-update-20261101.json).
That historical backend is `lib64/libgfxstream_backend.dylib`, SHA-256
`97e60edf0cf1422159583bf1f05d2ffb982773fdb0bade53c219ae9aed7b315a`.
Current-context postconditions do not prove every draw attachment, context-loss,
renderer reset, OOM or pixel-output failure. Deprecated non-null native pbuffers
and full EGL/Android/game lifecycles still require separate acceptance.

Bootstrap maintains a separate
`.repo/modavd-gfxstream-overlays.json` receipt, independent of QEMU's overlay
tree. Nine bootstrap tests passed, including independent trees/receipts and
preservation of unrecognized local edits.

## 0002: publish only successful no-GL readback

Google's base already allocated an uninitialized cache and discarded reader
success through a void wrapper. Original ModAVD G0006's no-GL publication
branch depended on these behaviors. Overlay 0001 did not introduce them.
Overlay 0002 retains the existing void API/vtable, adds a nonvirtual checked
reader, initializes registered caches, and reuses one scratch buffer per
no-GL display under the existing cache lock. A failed or partially failed
read leaves the old cache and callback count unchanged; only a successful
read is converted and copied into the published cache. The patch propagates
the existing GL/Vulkan bool and does not detect every possible GPU error.
It costs an extra frame buffer and one copy per successful no-GL frame;
there is no per-frame allocation/clearing or measured performance claim.

The full build/install/strip and deep strict signature passed. Current backend
SHA-256 is `35fbd4366185ea5985e41e495c73c910030e7522075ef23de7c6317ea6f60920`.
Eight CPU baseline/candidate cases met their positive/negative expectations;
eight native GL/no-GL mode executions covered 192 format-matrix frames.
The independent package `ModAVD-gapfix-readback` verified 508 payload entries
and passed Qt/headless boot, data retention, SHM lifecycle and graceful synced
shutdown. The new backend has not repeated the old backend's video suite.
Exact identities, failure history, RGBX per-mode contracts and coverage limits
are in [G2 validation](../../ports/macos/qa/framebuffer-readback/G2-VALIDATION.md).
