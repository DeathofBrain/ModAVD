# Media dispatcher and context lifetime QA

The D0008 lifecycle group consists of
`incoming/macos-qemu/0008-darwin-video-context-lifecycle.patch` and
`incoming/macos-aemu/0001-media-dispatcher-cleanup-declaration.patch`.
Build them together. The AEMU header declares the out-of-line H264 dispatcher
destructor implemented by the QEMU patch. Neither patch introduces RAM reclaim
hooks or changes class fields.

All three dispatcher destructors swap their raw plugin maps under `mMapLock`,
then delete remaining plugins outside that lock. The actual MediaContext
destroys its three dispatchers before removing/freeing its mapping, both at
destruction and before snapshot load. Snapshot save counts the HEVC decoder it
already serializes, preserving the existing record format.

`bootstrap.py patch` applies the AEMU overlay through the existing whole-tree
verification primitive and records `.repo/modavd-aemu-overlays.json` separately
from QEMU and gfxstream. Packaging includes that receipt, the AEMU patch hash
and AEMU Git source state. Source/patch receipts alone do not establish that a
particular binary was rebuilt from them.

Both verifiers require explicit patched and baseline QEMU/AEMU trees. Use a
baseline tree from before the lifecycle patches, not a path that has already
been updated. They read those trees and compile only new QA executables. Every
output path and companion build directory must be new; existing evidence is
refused.

```sh
python3 ports/macos/qa/media-lifecycle/verify-dispatchers.py \
  --qemu-source SOURCE_WITH_D0008/external/qemu \
  --aemu-source SOURCE_WITH_D0008/hardware/google/aemu \
  --baseline-qemu-source BASELINE_SOURCE/external/qemu \
  --baseline-aemu-source BASELINE_SOURCE/hardware/google/aemu \
  --output .macos-work/qa/lifecycle-dispatchers-new.json

python3 ports/macos/qa/media-lifecycle/verify-context.py \
  --qemu-source SOURCE_WITH_D0008/external/qemu \
  --aemu-source SOURCE_WITH_D0008/hardware/google/aemu \
  --baseline-qemu-source BASELINE_SOURCE/external/qemu \
  --baseline-aemu-source BASELINE_SOURCE/hardware/google/aemu \
  --gfxstream-source SOURCE_WITH_D0008/hardware/google/gfxstream \
  --output .macos-work/qa/lifecycle-context-new
```

`verify-dispatchers.py` reuses the earlier counted-codec driver without changing
its behavior. It compiles the three complete actual dispatchers and parsers
with the supplied actual public declarations. Counted codec, logging and
snapshot Stream implementations are shims. Under ASan/UBSan, 120 cycles cover
explicit destroy, H264/HEVC reset, remaining-plugin destruction, empty teardown,
snapshot save/reload, and plugin-destructor reentry that takes the map lock.
The baseline must fail the retained-plugin-count assertion.

`verify-context.py` compiles the complete actual `address_space_host_media.cpp`
and actual context, codec and control declarations. The factories/codecs,
aligned allocation, control operations and big-endian byte-oriented Stream
implementation are shims. Codec stubs retain the mapped buffer, read it during
destruction under ASan, and count ownership. The control shim checks that every
decoder is gone before removal. The positive run covers 20 destructor and
20 populated-to-empty load cycles, plus ten snapshot roundtrips for each of the
eight VPx/H264/HEVC presence combinations. It verifies exact snapshot bytes,
the next-record boundary, restored codec set and allocation/removal/free
pairing. Three separate baseline processes must detect destructor order, load
order and omitted HEVC count. A negative-control `pass` means the expected
assertion failed, not that the original implementation passed.

Initial independent evidence is indexed in
`.macos-work/qa/media-lifecycle-d0008-suite-20261101.json`. The tests used copies
of the main production sources, applied only the two lifecycle patches to those
copies, and left main AOSP, production binaries, packages and guests unchanged.
The dispatcher test created/destroyed 200 VPx, 240 H264 and 240 HEVC plugins;
the context test passed 3,424 checks, 80 snapshot roundtrips, 120/120 decoder
lifetimes per codec and 220/220/220 allocation/removal/free operations. The
bootstrap/package modules passed 23 tests, including the new AEMU routing,
independent-receipt, local-edit preservation and packaged provenance checks.

These are ownership/control-flow tests with codec and control shims. They do
not decode video, execute real renderer teardown, boot Android, validate live
snapshot use, prove concurrent teardown safety, or reclaim physical memory.
The separate native VT tests cover real helper decode in their recorded small
CPU-buffer scope. A fresh full production build/signature and guest regression
remain separate obligations for D0008. Previously written HEVC snapshots with
the erroneous decoder count may already have malformed framing; this patch
does not guess missing records or repair those old snapshots. General malformed
snapshot validation and propagation of codec-load failures remain unchanged.
