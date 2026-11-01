# Private checked DMA renderer experiment

This directory contains six full-source overrides and additive internal headers.
It does not modify the main AOSP sources, normal bootstrap output, package,
running emulator or guest. `manifest.json` records each original absolute path,
original SHA-256, private override and private include directory. The isolated
build helper consumes it; it is not a bootstrap patch series.

The C registration API adds no fields or virtual methods to existing renderer
interfaces. The QEMU loader optionally registers its GPA/length callback, then
checks readiness. An absent callback, zero/overflowing request, or rejected range
returns null. Registration readiness alone is not an ingress, mapping-lifetime,
permissions or reclaim proof.

## Precise consumers

- GLES `s_glMapBufferRangeDMA` and `s_glUnmapBufferDMA` borrow the positive actual
  `GLsizeiptr` copy length before mapping or copying GL data. Failed borrows do
  not invoke the GL mapping. The existing GPA-zero unmap/flush behavior remains.
- RC update/read commands use a new private C implementation in
  `render_control.cpp`; the decoder retains its normal response/checksum/flush
  and DMA unlock behavior. The C implementation takes the same FrameBuffer
  global lock and color-buffer-map lookup as the original implementation, keeps
  an owning reference and transfers through that exact object. It does not
  perform a second handle lookup after validation.
- Only a target `R8G8B8A8_UNORM` and a request that the existing
  `GetGfxstreamFormat(format,type)` maps to that same format use precise pixels.
  These request formats are RGBA, RGBA8, RGB5_A1 and RGBA4; the existing conversion
  ignores the original protocol type. Positive dimensions, rectangle bounds and
  checked `width * height * 4 <= declared` are required. The callback receives
  that actual byte count, not the possibly larger declaration.
- Exact GL RC transfers enable a thread-local scope. After the actual helper
  context is bound, `ColorBufferGl::readPixels` / `subUpdate` save, set and query
  PACK/UNPACK alignment 1, row length 0, skip rows/pixels 0, and pixel-buffer
  binding 0. Unsupported or failed state changes reject the GL access. Scope
  exit restores all saved state on success and failure. IMAGE_HEIGHT and
  SKIP_IMAGES do not affect these two-dimensional operations. Other renderer
  calls leave this scope inactive.
- For target RGBA8, Vulkan's internal format remains RGBA8. Its transfer layout
  is one plane, horizontal alignment 1, four bytes per sample and no pack/unpack
  conversion. The backend rejects subrects before touching client pixels, so
  its accepted complete extent consumes exactly `width * height * 4` bytes.
  The input is copied to Vulkan staging memory before the callback returns;
  GPU submission does not retain the borrowed guest pointer.

Every other target or request format, including unknown formats and YUV, follows
its original unsized stream export and original RC callback. The private QEMU
legacy exports permanently exclude the entire intersecting RAMBlock. This
preserves existing compatibility and makes no precise footprint claim. It does
not validate arbitrary legacy protocol offsets or establish memory safety for
malformed legacy commands. A null fallback balances the update preparation lock
and returns failure. Precise-path rejection also balances that lock.

## External ASG provenance

`virtio_gpu_context.cpp` retains `resource.ShareRingBlob()` while calling
`create_instance`. Only a real owned RingBlob whose `map()` and actual `size()`
exactly match the mapped base and extent receives a positive provenance scope.
The optional `modavd_asg_external_owned_v1(handle, base, size)` query uses a
thread-local scope stack and consumes an exact match once. Wrong handles,
bases, sizes, missing/expired scopes, raw Vulkan mappings, zero or overflowing
extents cannot establish ownership. Nested calls and other threads cannot use
the enclosing scope's certificate.

The QEMU-private ASG constructor uses this query before allocating or publishing
workers. An external allocation without a positive certificate enters the same
balanced lifetime inhibitor as Media. The ASG destructor releases it only after
joining the consumer and freeing its storage. Missing query symbols and snapshot
reconstruction use the inhibited path. No shared create-info fields or virtual
interfaces change. Normal RingBlob detach retains the existing shared owner in
the asynchronous cleanup closure until ASG destruction finishes.

This inhibits reclaim while an opaque raw-mapping consumer exists; it does not
repair arbitrary guest blob sizes, Vulkan resource lifetime errors or malformed
legacy protocol accesses. A provenance certificate alone is not a proof of all
pointer ingress.

## Evidence and limits

`verify.py` compiles the full modified `dma_device.cpp` and extracts the exact
modified two GL functions, two decoder case bodies, RC implementation and old
RC callbacks into GL/resource/stream shims. It runs ASan and UBSan. Coverage
includes both copy directions, rejected/negative/overflow/zero extents, missing
callbacks, minimal RGBA8 borrowing, six invalid RGBA8 cases, four legacy format
cases, response flushing/unlock, the actual update failure unlock, exact-object
locking, thread-local isolation and pixel-store save/reset/query/restore/failure.
The report is created exclusively and refuses to overwrite historical evidence.

```sh
python3 incoming/macos-qemu/experimental/dma-overrides/verify.py \
  --output /absolute/new/report.json
```

The source proof for pixel conversion/layout is tied to these main-tree files:
`host/frame_buffer.cpp` (`GetGfxstreamFormat`), `host/color_buffer.cpp`
(`readToBytes` / `updateFromBytes`), `host/gl/color_buffer_gl.cpp`
(`GetFormatOpenglParameters`, read and sub-update), and
`host/vulkan/{color_buffer_vk.cpp,vk_common_operations.cpp,vk_format_utils.cpp,
vk_format_utils.h}`. Original and override hashes are captured in the evidence.
A separate native NSOpenGL pixel-store verifier checks real upload/readback and
state restoration; its report must be assessed separately from the shim tests.

`verify-asg-ownership.py` compiles the full modified common DMA source and
executes the actual producer, QEMU ASG constructor/destructor, query wrapper,
guard member initializer and lifetime registry with resource/consumer shims.
The native ASan/UBSan run covers exact and mismatched certificates, single use,
nested scopes, 8,000 parallel TLS scopes, raw and missing-callback fallbacks,
retained producer ownership and teardown ordering. Its consumer shim starts and
joins a real thread; it does not execute the actual renderer's join implementation.
The dated report is `.macos-work/qa/asg-ownership-lifecycle-20261101.json`.

```sh
python3 incoming/macos-qemu/experimental/dma-overrides/verify-asg-ownership.py \
  --output /absolute/new/report.json
```

The isolated six-translation-unit build proves compilation, not runtime symbol
selection or a completed QEMU reclaim transaction. Actual loader registration,
QEMU address-space and DmaMap checks, alias tainting, renderer/reset lifetimes,
HVF permissions, migration inhibition and guest workloads remain separate gates.
Unknown-source aliases and unsupported mappings must remain ineligible. No test
here authorizes `ingress_complete=1` by itself or establishes physical-memory
benefit. Measure the common RGBA8 path and remaining legacy whole-block events
in a disposable integrated emulator before claiming an advantage.
