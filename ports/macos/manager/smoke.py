#!/usr/bin/env python3
"""Exercise built CLI and the headless GUI HTTP server without starting an emulator."""

import argparse
import hashlib
import http.client
import json
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, default=ROOT / ".macos-work/manager-dist")
    args = parser.parse_args()
    cli = args.build / "tools/modavd"
    manager = args.build / "ModAVD.app/Contents/MacOS/ModAVD"
    result = subprocess.run([str(cli), "version", "--json"], check=True, capture_output=True, text=True)
    version = json.loads(result.stdout)
    check(version["ok"] and version["data"]["protocol"] == 1, "CLI protocol changed")
    result = subprocess.run([str(manager), "__version"], check=True, capture_output=True, text=True)
    check(json.loads(result.stdout)["data"]["protocol"] == 1, "GUI protocol changed")
    with tempfile.TemporaryDirectory(prefix="modavd-manager-smoke-") as directory:
        root = Path(directory).resolve()
        key = "ui-" + hashlib.sha256(str(root).encode()).digest()[:8].hex()
        state_dir = Path.home() / "Library/Application Support/ModAVD"
        state_path = state_dir / (key + ".json")
        lock_path = state_dir / (key + ".lock")
        (root / "mas-avd.json").write_text("{}")
        log = root / "server-output.log"
        with log.open("wb") as output:
            process = subprocess.Popen([str(manager), "--root", str(root), "--no-window", "--dev"],
                                       stdout=output, stderr=output)
        try:
            deadline = time.monotonic() + 15
            state = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Manager exited before startup: " + log.read_text())
                try:
                    state = json.loads(state_path.read_text())
                    break
                except (OSError, json.JSONDecodeError):
                    time.sleep(0.05)
            check(state is not None, "Manager did not publish its loopback address")
            check(state["pid"] == process.pid and state["root"] == str(root), "Wrong manager state")
            address = urlsplit(state["url"])

            def request(method, path, headers=None, body=None):
                connection = http.client.HTTPConnection(address.hostname, address.port, timeout=5)
                try:
                    connection.request(method, path, body=body, headers=headers or {})
                    response = connection.getresponse()
                    return response.status, dict(response.getheaders()), response.read()
                finally:
                    connection.close()

            status, _, _ = request("GET", "/api/info")
            check(status == 401, "Anonymous API request accepted")
            status, headers, _ = request("GET", "/?t=" + state["token"])
            check(status == 302 and "Set-Cookie" in headers, "Login did not establish a cookie")
            cookie = headers["Set-Cookie"].split(";", 1)[0]
            auth = {"Cookie": cookie}
            status, _, data = request("GET", "/api/info", auth)
            check(status == 200 and json.loads(data)["root"]["root"] == str(root), "Authenticated info failed")
            status, _, data = request("GET", "/api/instances", auth)
            check(status == 200 and json.loads(data)["instances"] == [], "Empty-root list failed")
            status, _, _ = request("GET", "/api/info", {**auth, "Host": "foreign.invalid"})
            check(status == 403, "Foreign Host request accepted")
            status, _, _ = request("POST", "/api/instances", {**auth, "Content-Type": "application/json"}, "{}")
            check(status == 403, "Mutation without Origin accepted")
            status, _, _ = request("POST", "/api/instances", {**auth, "Origin": "http://foreign.invalid",
                                                               "Content-Type": "application/json"}, "{}")
            check(status == 403, "Foreign Origin request accepted")
            second = subprocess.run([str(manager), "--root", str(root), "--no-window"], timeout=10, capture_output=True)
            check(second.returncode == 0 and process.poll() is None, "Single-instance reopen failed")
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            check(process.returncode == 0, "Manager did not shut down cleanly")
            check(not state_path.exists(), "Manager state file survived shutdown")
            lock_path.unlink(missing_ok=True)
            try:
                state_dir.rmdir()
            except OSError:
                pass
    print(json.dumps({"ok": True, "checks": ["cli_version", "gui_version", "loopback_startup", "login", "list",
                                               "host_rejection", "origin_rejection", "single_instance", "shutdown"]}))


if __name__ == "__main__":
    main()
