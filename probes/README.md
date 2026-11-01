# Official AVD baseline probes

These tools measure the official emulator as configured by the caller. They do
not start an emulator, edit its image, change its caching/reclamation settings,
or use ModAVD executables. The Android helper runs from `/data/local/tmp`.

## Build and smoke test

`python3 probes/build_probe.py` uses the installed Apple clang to generate an
AArch64 ELF object and a small, strict static linker to resolve its relocations.
No NDK or third-party binary is required. `probes/build/build_manifest.json`
records compiler flags, binary SHA-256, sections, and relocation counts. Unknown
relocation kinds or unresolved symbols are hard errors.

After the coordinating process has started the AVD:

```sh
python3 soak_tests.py --output results/probe_prepare prepare
```

The default ADB binary is `${WORKSPACE}/sdk/platform-tools/adb`, server
port `22150`, and serial `emulator-22160`. Override with `--adb`, `--adb-port`,
and `--serial` before the subcommand. Every invocation uses that explicit ADB
context. The `prepare` subcommand pushes the helper and verifies a 16 MiB
allocation, touch, and release.

## Memory reclamation

```sh
python3 soak_tests.py --emulator-pid PID --output results/memory memory
```

Default: 30-second baseline, then three rounds of allocate/touch 1 GiB, hold
60 seconds, `munmap`, and observe for 600 seconds. All guest pages are touched;
the default stride is 4096 bytes and can be set with `--guest-page-size`.
The 1 GiB is anonymous private memory in one standalone guest process, with no
guest cache drop, balloon control, or emulator memory policy modifications.

Every 5 seconds, `memory.csv` records guest `/proc/meminfo`, emulator process
RSS and physical footprint from Darwin `proc_pid_rusage`, whole-host compressor
usage from `vm_stat`, and whole-host swap from `sysctl vm.swapusage`. Compression
and swap are **system-wide**, so unrelated processes remain possible influences.
The emulator PID must identify the running qemu/emulator process itself.

Raw helper events and stderr are saved for each round. `memory_summary.json`
includes per-phase values, final-minute medians, and reclaimed footprint from
the end of the hold to the final observation minute. The overall linear slope
across deliberate allocations is descriptive and is not a memory leak test.

## Two-hour network and memory observation

```sh
python3 soak_tests.py --emulator-pid PID --output results/soak soak \
  --workload-label idle_non_game_control
```

The default TCP echo server binds a random loopback port. The guest connects
through emulator gateway `10.0.2.2`; the host control connects to `127.0.0.1`.
Each connection first exchanges a checked 32 MiB bulk transfer, then a checked
64 KiB heartbeat every second for the remainder of the 7200-second window.
Socket failures cause a reconnect with a one-second backoff. Payloads are
verified byte-for-byte. The echo service stops after the observation window.

This verifies the emulator NAT path and socket continuity. It does **not**
establish WAN reliability. Game resource download or a separately configured
external echo service is needed to assess WAN behavior. An external service
can be selected with `--no-local-echo --echo-port PORT --host-endpoint IPV4
--guest-endpoint IPV4`; it must implement a raw TCP byte echo protocol.

`host_network.jsonl` and `guest_network.jsonl` preserve events. CSV and JSON
summaries report checked ACK counts, disconnects, reconnects, recovery times,
RTT median/P95, bulk throughput, and achieved traffic during the whole window.
The offered heartbeat traffic is not a link-capacity measurement; bulk
throughput is reported separately. The memory monitor runs concurrently.

If game login is unavailable, use `idle_non_game_control` and explicitly mark
the planned game soak as blocked. A non-game observation cannot be relabeled
as a completed game soak.

## Recompute summaries

```sh
python3 soak_tests.py --output results/soak summarize
```

The coordinator may use shorter smoke durations without counting them toward
the 2-hour acceptance criterion. Original metadata preserves actual durations,
commands, workload labels, and endpoint scope.
