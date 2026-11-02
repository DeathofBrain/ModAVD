# Onmyoji 2.8.89: installation succeeds, entry fails on the tested host renderer

Observation date: 2026-11-02. All observation times use Asia/Shanghai (UTC+08:00).

The APK installs and Android selects its ARM64 implementation, but the game repeatedly exits during native renderer initialization. **The tested `-gpu host` path exposes OpenGL ES 3.0; NeoX rejects that fallback after ES 3.2/3.1 context creation fails.** The same rendering thread then reports signal 11, its own crash handler exits, and Android records EXIT_SELF/status 1. This sequence appears in both saved observation windows.

This result applies to this configuration. Other graphics backends were not tested, and it does not establish that every emulator backend fails.

## Package, installation and selected ABI

The local APK is `onmyoji_netease_116_2.8.89_py3_small.apk`, 1,283,793,018 bytes, SHA256 `eecba6fd1f550263135c840b46a5e7fb3a8c324046fdfd3078454e95f92e7990`. Its package declaration is `com.netease.onmyoji`, version 2.8.89/code 261014. ZIP CRC checks passed. This export does not include the APK, and the local audit did not independently authenticate its publisher signature.

There are 56 libraries under each of `lib/arm64-v8a/` and `lib/armeabi-v7a/`, plus 8 asset ELFs containing ARM32, ARM64, x86 and x86-64. **The APK supports ARM64 but is not arm64-only.** Android installed it with `primaryCpuAbi=arm64-v8a` and `secondaryCpuAbi=null`; the guest advertises ARM64 only and `ro.dalvik.vm.native.bridge=0`. These facts support ARM64 selection; they do not transform a mixed-ABI APK into an arm64-only package. Full per-entry audit: [abi-receipt.json](abi-receipt.json).

The isolated AVD used API 34 Google APIs ARM64, 4096 MiB RAM, 4 cores, 32 GiB data capacity, a visible window and `-gpu host`. `adb install` returned 0 with `Success`; the launcher resolved to `.tag0`, and the Activity start returned `Status: ok` for ProtocolLauncher. Activity creation and a transient live PID did not establish successful game entry. Safe installation and guest facts are in [installation-summary.json](installation-summary.json).

## Failure evidence

The [initial excerpt](initial-log-excerpt.txt) and [later excerpt](recent-log-excerpt.txt) contain only the selected technical lines, each with its original full-log SHA256 and original LF line numbers. Full raw logcat, local paths, account data, credentials and unique account/device identifiers are not published.

| Observation | Initial window (PID 6917 / TID 7122) | Later window (PID 31031 / TID 31092) |
|---|---|---|
| ES 3.2 / ES 3.1 rejected |22:27:26.511 |22:29:41.582—.583 |
| Fallback actual context |ES 3.0, Apple M4 emulator OpenGL ES translator |Same ES 3.0 translator |
| Engine error |`GLESDevice only support version above OpenGLES 310 on Mobile`; `Failed to create NeoX Device` |Same errors |
| Signal 11 |22:27:26.677 |22:29:41.701 |
| Application `exitProcess` |22:27:26.983 |22:29:41.997 |
| Zygote exited with status 1 |22:27:27.026 |22:29:42.011 |

The engine wording is retained exactly; the logs establish that it rejects the actual ES 3.0 fallback. They do not resolve whether every ES 3.1 implementation would suffice. The precise faulting native function/instruction remains unknown because the application minidump was not retrieved or symbolicated.

[exit-info-summary.csv](exit-info-summary.csv) includes only the 8 main-process exit timestamps/PIDs/reasons/statuses in the saved exit-info snapshot. Every entry is EXIT_SELF/status 1; intervals range from 1.314 to 1.417 s (median 1.370 s). The empty system crash-buffer source has SHA256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`. A handler processing signal 11 and then calling exitProcess explains why an application native crash can coexist with EXIT_SELF and an empty system crash buffer.

The gray/white frame below was already captured during failure, and was visually checked to contain no account fields, credentials or identifying text. No other screenshots or post-login content are included.

![Observed gray/white entry failure](entry-failure.png)

SDK/LoadedApk warnings also occur, but execution continues into native game/EGL initialization. The two saved buffers contain no explicit `UnsatisfiedLinkError`, `dlopen failed`, unresolved-symbol or wrong-ELF-bitness error. A `try to dlopen(libclient.so), code:13` library message alone is not a dynamic-linker error or proof that 13 is errno EACCES. Security/probe messages do not establish an anti-emulator block. The renderer failure sequence is the strongest supported explanation; exact native fault attribution still requires a stack trace.

## Scope and shutdown

This test did not verify successful server login, a complete resource download, in-game gameplay or game automation. The runner did not accept agreements or enter accounts. Background network activity on launch was not measured as zero.

The test ended without trying another graphics backend. [shutdown-summary.json](shutdown-summary.json) records a synchronous stop with returncode 0, `still_alive=false`, and an idle exclusive slot; the AVD was retained. A candidate backend would need its actual required ES-context capability checked before any later comparison. No claim is made that switching to software rendering will fix the failure.

[manifest.json](manifest.json) maps each exported file to its original source SHA256(s) and exported SHA256, with redaction/selection notes. It contains source basenames rather than private machine paths. Original full logs are not included.
