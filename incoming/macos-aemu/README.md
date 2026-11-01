# AEMU overlays for the macOS source build

`0001-media-dispatcher-cleanup-declaration.patch` is the declaration companion
to QEMU D0008 (`macos-qemu/0008-darwin-video-context-lifecycle.patch`). Apply and
rebuild both together: the H264 dispatcher destructor moves out of line to
release its remaining plugin map. It does not change the class layout.

`ports/macos/bootstrap.py patch` routes these patches to
`hardware/google/aemu` and records `.repo/modavd-aemu-overlays.json` using the
same source-tree verification and unknown-local-edit preservation as the other
overlays. Packaging records the patch hash, receipt and AEMU Git state.

Ownership/control-flow tests and their limits are described in
`ports/macos/qa/media-lifecycle/README.md`. No experimental RAM hook is included.
