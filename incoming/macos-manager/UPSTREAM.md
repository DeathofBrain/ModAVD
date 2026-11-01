# ModAVDManager Darwin ARM64 patch

- Upstream: https://github.com/ModAVD/ModAVDManager
- Base commit: `21c2a3ffa22eb37a5e39e61b1b53f2258d0f3e7a`
- Patch: `0001-darwin-arm64.patch`, ordinary `git diff --binary`, without author metadata.
- Apply to a clean checkout with `git apply 0001-darwin-arm64.patch`, or use `ports/macos/manager/build.sh` in the ModAVD repository.
- Source license: upstream AGPL-3.0; the build copies the upstream LICENSE into its output.

The patch renames the Win32-only host package to a platform-neutral host package, retains the Windows implementation, adds native Darwin services and an ARM64 SDK/AVD profile, and keeps the existing CLI/MCP/HTTP contracts. The native AppKit/WKWebView shell and reproducible build live in `ports/macos/manager` in the parent repository.

Darwin explicitly sets `ANDROID_EMU_VK_HIDE_REBAR=0` to preserve MoltenVK unified-memory visibility, while Windows retains its NVIDIA ReBAR and shader-cache policy. Runtime discovery follows the pinned core's ConfigDirs environment precedence; console authentication follows the platform's native home directory. Temporary-package contract tests cover the local Darwin SDK layout and `mas-36` version identity.

Validated locally on Apple Silicon with Go 1.26.5: `go test ./...`, `go vet ./...`, Windows amd64 `go build ./...`, and Windows test compilation using `go test -exec /usr/bin/true ./...` (compilation only). The patch has been replayed onto a clean pinned-base checkout and its Go/frontend sources compared byte-for-byte.

Shared update transaction tests now run on Darwin, including app resources and executable modes, interrupted swaps, conflict recovery and SDK/AVD preservation; the update package also passed Go's race detector. Darwin checks its 20 GiB reserve per MiB / file during downloads, extraction, incremental copies and installation backups; low space or a failed probe stops writes while retaining resumable downloads or recovery state. Windows retains its original budget and I/O behavior. These fixtures do not turn the local full SDK bundle into a supported online-update package or add rollback after a completed update.

The native WKWebView instance page and host diagnostics were opened against an isolated empty root. Real emulator/HVF boot, Qt window control, screenshot consumers, live Keychain authorization, broader GUI interaction, release signing/notarization and online package updates remain separate integration acceptance work. See `ports/macos/manager/README.md` for details.
