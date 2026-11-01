#!/usr/bin/env python3
"""Bounded slirp matrix for the owned 20150/20160 QA guest or native smoke."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime as dt
import errno
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import socketserver
import struct
import subprocess
import threading
import time
import uuid

BASE = Path(__file__).resolve().parents[3]
EXPECTED = {"many": (160, 8192), "single": (1, 32 * 1024**2),
            "parallel": (8, 4 * 1024**2), "half": (1, 4096),
            "reset": (1, 0), "recovery": (1, 8192)}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def servers(token):
    lock = threading.Condition()
    stats = {"accepted": 0, "active": 0, "high_water": 0, "completed": {},
             "requests": {}, "udp_echoes": 0, "errors": []}
    seen = {mode: set() for mode in EXPECTED}

    class TCP(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(20)
            mode = "unparsed"
            with lock:
                stats["accepted"] += 1
                stats["active"] += 1
                stats["high_water"] = max(stats["active"], stats["high_water"])
            try:
                with self.request.makefile("rb") as reader:
                    line = reader.readline(128)
                    parts = line.decode("ascii").strip().split()
                    if len(parts) != 6 or parts[:2] != ["MODNET", token]:
                        raise ValueError("invalid local request")
                    mode, index, count, size = parts[2], *map(int, parts[3:])
                    if mode not in EXPECTED or (count, size) != EXPECTED[mode] or not 0 <= index < count:
                        raise ValueError("request exceeds matrix bounds")
                    with lock:
                        if index in seen[mode]:
                            raise ValueError("duplicate stream index")
                        seen[mode].add(index)
                        stats["requests"][mode] = len(seen[mode])
                        lock.notify_all()
                        if mode in ("many", "parallel") and not lock.wait_for(
                                lambda: len(seen[mode]) == count, timeout=15):
                            raise TimeoutError("batch barrier did not fill")
                    if mode == "half" and reader.read(1) != b"":
                        raise ValueError("client did not half-close after request")
                    if mode == "reset":
                        self.request.sendall(b"R" * 64)
                        self.request.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                    else:
                        block = bytes((index * 17 + off * 29 + (off >> 8)) & 255 for off in range(65536))
                        for offset in range(0, size, len(block)):
                            self.request.sendall(block[:min(len(block), size - offset)])
                        self.request.shutdown(socket.SHUT_WR)
                    with lock:
                        stats["completed"][mode] = stats["completed"].get(mode, 0) + 1
            except Exception as error:
                with lock:
                    stats["errors"].append({"mode": mode, "error": str(error)[:200]})
            finally:
                with lock:
                    stats["active"] -= 1

    class TCPServer(socketserver.ThreadingTCPServer):
        request_queue_size = 256
        daemon_threads = True

    class UDP(socketserver.BaseRequestHandler):
        def handle(self):
            packet, sock = self.request
            valid = (len(packet) == 296 and packet[:32] == token.encode() and
                     packet[36:40] == b"UDP1")
            index = struct.unpack("!I", packet[32:36])[0] if valid else 160
            valid = valid and index < 160 and packet[40:] == bytes(
                (index * 17 + off * 29 + (off >> 8)) & 255 for off in range(256))
            if valid:
                sock.sendto(packet, self.client_address)
                with lock:
                    stats["udp_echoes"] += 1

    tcp = TCPServer(("127.0.0.1", 0), TCP)
    udp = socketserver.UDPServer(("127.0.0.1", 0), UDP)
    # Darwin can silently drop SYNs to a bound non-listening TCP socket. Keep
    # a UDP port allocated and prove TCP refusal on that number before/after;
    # a concurrent TCP listener makes the test fail without sending it data.
    def refusal_error(port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(2)
            return probe.connect_ex(("127.0.0.1", port))

    closed = None
    for _ in range(10):
        candidate = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        candidate.bind(("127.0.0.1", 0))
        if refusal_error(candidate.getsockname()[1]) == errno.ECONNREFUSED:
            closed = candidate
            break
        candidate.close()
    if closed is None:
        tcp.server_close()
        udp.server_close()
        raise RuntimeError("no verified closed TCP port available")
    stats["host_refusal_before"] = errno.ECONNREFUSED
    threads = [threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
               for s in (tcp, udp)]
    for thread in threads:
        thread.start()
    try:
        yield [tcp.server_address[1], udp.server_address[1], closed.getsockname()[1]], stats, lock
    finally:
        stats["host_refusal_after"] = refusal_error(closed.getsockname()[1])
        for server in (tcp, udp):
            server.shutdown()
            server.server_close()
        closed.close()
        for thread in threads:
            thread.join(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--local", action="store_true", help="Native host control only; no Android/ADB")
    parser.add_argument("--expected-pid", type=int, help="Required live disposable guest PID; no start/adopt")
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    output = args.output.resolve()
    if output.exists() or not output.parent.is_dir():
        parser.error("output must be new with an existing parent")
    token = uuid.uuid4().hex
    report = {"schema": 1, "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "scope": "native host control" if args.local else "Android ARM64 slirp to host loopback",
              "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "probe_source_sha256": hashlib.sha256(Path(__file__).with_name("network_probe.c").read_bytes()).hexdigest(),
              "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "limits": {"max_live_tcp": 160, "max_live_udp": 160, "tcp_payload_bytes": 68431872,
                         "external_services": False, "blackhole_or_keepalive_timeout_tested": False},
              "limitations": ["Loopback results do not establish CDN speed or a speed improvement over upstream.",
                              "No dead-peer TCP keepalive, ICMP fallback flood, or external blackhole evidence."]}
    guest = None
    directory = f"/data/local/tmp/modavd-netqa-{token}"
    created = False
    started = time.monotonic()
    try:
        if not args.local:
            if not args.expected_pid or args.expected_pid <= 1:
                parser.error("guest mode requires --expected-pid")
            raw = binary.read_bytes()[:20]
            if raw[:6] != b"\x7fELF\x02\x01" or int.from_bytes(raw[18:20], "little") != 183:
                raise ValueError("guest probe must be little-endian ARM64 ELF")
            checks = load("network_matrix_guest_checks", Path(__file__).with_name("guest_checks.py"))
            checks.runtime_module.ADB_PORT = 20150
            checks.runtime_module.CONSOLE_PORT = 20160
            root = BASE / ".macos-work/reclaim-runtime"
            profile = json.loads((root / "reclaim-profile.json").read_text())
            if profile != {"schema": 1, "adb": 20150, "console": 20160, "kind": "disposable-reclaim-audit"}:
                raise RuntimeError("unrecognized isolated guest profile")
            record = json.loads((root / "emulator.json").read_text())
            if record.get("pid") != args.expected_pid:
                raise RuntimeError("guest PID differs from explicitly selected PID")
            guest = checks.GuestChecks(checks.Runtime(root), timeout=180)
            serial = guest.owned()
            report["identity"] = {"private_adb": 20150, "serial": serial, "processes": guest.identity,
                                  "boot_id": guest.shell("cat /proc/sys/kernel/random/boot_id").strip()}
            usage = load("network_matrix_host_usage", Path(__file__).with_name("host_rusage.py"))
            report["host_before"] = usage.snapshot(args.expected_pid)
            guest.shell(f"umask 077; mkdir {directory} && printf '%s' '{token}' > {directory}/owner")
            created = True
            serial = guest.owned()
            guest.runtime.adb("-s", serial, "push", str(binary), directory + "/probe", timeout=30)
            guest.shell(f"chmod 700 {directory}/probe")
            remote_hash = guest.shell(f"sha256sum {directory}/probe").split()[0]
            if remote_hash != report["binary_sha256"]:
                raise RuntimeError("guest probe hash mismatch")
        with servers(token) as (ports, stats, stats_lock):
            report["host_ports"] = ports
            if args.local:
                completed = subprocess.run([str(binary), "127.0.0.1", *map(str, ports), token],
                                           capture_output=True, text=True, timeout=150)
                stdout, stderr, returncode = completed.stdout, completed.stderr, completed.returncode
            else:
                try:
                    stdout = guest.shell(f"{directory}/probe 10.0.2.2 {' '.join(map(str, ports))} {token}")
                    stderr, returncode = "", 0
                except subprocess.CalledProcessError as error:
                    stdout, stderr, returncode = error.stdout or "", error.stderr or "", error.returncode
            report["returncode"] = returncode
            report["stderr"] = stderr[:4000]
            report["cases"] = [json.loads(line) for line in stdout.splitlines() if line.startswith("{")]
            report["stdout"] = stdout[:16000]
            # Handler finally may trail the last received FIN by a scheduler tick.
            for _ in range(100):
                if not stats["active"]:
                    break
                time.sleep(0.01)
        with stats_lock:
            report["server"] = json.loads(json.dumps(stats))
        report["passed"] = (returncode == 0 and len(report["cases"]) == 8 and
                            all(case.get("passed") for case in report["cases"]) and
                            stats["high_water"] >= 160 and stats["udp_echoes"] == 160 and
                            not stats["active"] and not stats["errors"] and
                            stats["host_refusal_after"] == errno.ECONNREFUSED)
        if guest:
            guest.owned()
            report["host_after"] = usage.snapshot(args.expected_pid)
            report["boot_id_after"] = guest.shell("cat /proc/sys/kernel/random/boot_id").strip()
            report["passed"] = report["passed"] and report["boot_id_after"] == report["identity"]["boot_id"]
    except Exception as error:
        report["passed"] = False
        report["error"] = {"type": type(error).__name__, "detail": str(error)[:4000]}
    finally:
        if created:
            try:
                guest.shell(f"test \"$(cat {directory}/owner)\" = '{token}' && "
                            f"rm -- {directory}/probe {directory}/owner && rmdir {directory}")
                report["cleanup"] = "completed"
            except Exception as error:
                report["cleanup"] = {"error": str(error)[:2000], "path": directory}
        report["elapsed_seconds"] = time.monotonic() - started
        with output.open("x") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
    print(json.dumps({"output": str(output), "passed": report["passed"],
                      "cases": report.get("cases", []), "error": report.get("error")}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
