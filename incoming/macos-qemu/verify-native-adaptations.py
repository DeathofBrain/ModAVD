#!/usr/bin/env python3
"""Compile and execute the patched production helpers against real Darwin APIs.

Usage: python3 verify-native-adaptations.py /path/to/patched/external/qemu
This complements emulator integration tests; it does not assert guest boot.
"""

import argparse
import os
from pathlib import Path
import platform
import subprocess
import tempfile


def function(text, signature, occurrence=0):
    start = -1
    for _ in range(occurrence + 1):
        start = text.index(signature, start + 1)
    opening = text.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("qemu", type=Path)
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("these checks require Apple Silicon macOS")
    qemu = args.qemu.resolve()
    tcp = (qemu / "slirp/tcp_subr.c").read_text()
    glue = (qemu / "android-qemu2-glue/main.cpp").read_text()
    with tempfile.TemporaryDirectory(prefix="modavd-darwin-check-") as temp:
        work = Path(temp)
        keepalive = work / "keepalive.c"
        keepalive.write_text(
            "#include <assert.h>\n#include <stdbool.h>\n#include <stdio.h>\n"
            "#include <stdlib.h>\n#include <string.h>\n#include <errno.h>\n"
            "#include <sys/socket.h>\n#include <netinet/in.h>\n"
            "#include <netinet/tcp.h>\n#include <unistd.h>\n"
            "#define qemu_setsockopt setsockopt\n"
            + function(tcp, "static void tcp_set_keepalive_option(")
            + "\n"
            + function(tcp, "static void tcp_set_keepalive(int s)")
            + r'''
int main(int argc, char **argv) {
    assert(argc == 5);
    int s = socket(AF_INET, SOCK_STREAM, 0);
    assert(s >= 0);
    tcp_set_keepalive(s);
    const int levels[] = {SOL_SOCKET, IPPROTO_TCP, IPPROTO_TCP, IPPROTO_TCP};
    const int options[] = {SO_KEEPALIVE, TCP_KEEPALIVE, TCP_KEEPINTVL, TCP_KEEPCNT};
    for (int i = 0; i < 4; ++i) {
        int expected = atoi(argv[i + 1]);
        if (expected < 0) continue;
        int actual = -1;
        socklen_t length = sizeof(actual);
        assert(getsockopt(s, levels[i], options[i], &actual, &length) == 0);
        if (i == 0) actual = !!actual;  // Darwin returns the enabled option bit.
        if (actual != expected) {
            fprintf(stderr, "option %d: expected %d, actual %d\n", options[i], expected, actual);
        }
        assert(actual == expected);
    }
    assert(close(s) == 0);
}
'''
        )
        subprocess.run(
            ["xcrun", "clang", "-std=c11", "-Wall", "-Wextra", "-Werror",
             "-mmacosx-version-min=11.0", str(keepalive), "-o", str(work / "keepalive")],
            check=True,
        )
        for env_value, expected in [
            (None, [1, 3, 1, 5]), ("0", [0, -1, -1, -1]),
            ("2,3,4", [1, 2, 3, 4]), ("invalid", [1, 3, 1, 5]),
        ]:
            env = os.environ.copy()
            env.pop("ANDROID_EMU_SLIRP_TCP_KEEPALIVE", None)
            if env_value is not None:
                env["ANDROID_EMU_SLIRP_TCP_KEEPALIVE"] = env_value
            subprocess.run([str(work / "keepalive"), *map(str, expected)], env=env, check=True)
        print("PASS: actual Darwin socket timers, custom/default/disabled/invalid settings")

        volume = work / "volume.cpp"
        volume.write_text(
            "#include <cassert>\n#include <cstdint>\n#include <string>\n"
            "#include <sys/attr.h>\n#include <sys/stat.h>\n"
            "#include <fcntl.h>\n#include <unistd.h>\n"
            + function(glue, "static bool volumeSupportsSparseFiles(", occurrence=1)
            + r'''
int main(int argc, char **argv) {
    assert(argc == 2);
    std::string dir = argv[1];
    assert(!volumeSupportsSparseFiles(dir + "/does-not-exist"));
    // Verify the positive capability against an actual sparse allocation.
    assert(volumeSupportsSparseFiles(dir));
    std::string file = dir + "/sparse.raw";
    int fd = open(file.c_str(), O_CREAT | O_EXCL | O_RDWR, 0600);
    assert(fd >= 0);
    constexpr off_t size = 64 * 1024 * 1024;
    assert(ftruncate(fd, size) == 0);
    assert(pwrite(fd, "x", 1, size - 1) == 1);
    struct stat st = {};
    assert(fstat(fd, &st) == 0);
    assert(st.st_size == size && st.st_blocks * 512 < size / 2);
    assert(close(fd) == 0);
    assert(unlink(file.c_str()) == 0);
}
'''
        )
        subprocess.run(
            ["xcrun", "clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
             "-mmacosx-version-min=11.0", str(volume), "-o", str(work / "volume")],
            check=True,
        )
        subprocess.run([str(work / "volume"), str(work)], check=True)
        print("PASS: Darwin volume capability, sparse allocation, unavailable-path fallback")

        cores = work / "cores.cpp"
        cores.write_text(
            "#include <algorithm>\n#include <cassert>\n#include <cstdint>\n"
            "#include <Hypervisor/hv_vm.h>\n"
            + function(glue, "static int appleArmMaxGuestCores(")
            + r'''
int main() {
    uint32_t hvfMax = 0;
    assert(hv_vm_get_max_vcpu_count(&hvfMax) == HV_SUCCESS && hvfMax > 0);
    for (int host : {0, 1, 4, 8, 16, 64}) {
        int actual = appleArmMaxGuestCores(host);
        assert(actual >= 1 && actual <= 8);
        assert(actual <= static_cast<int>(hvfMax));
        if (host > 0) assert(actual <= host);
    }
    assert(appleArmMaxGuestCores(1) == 1);
}
'''
        )
        subprocess.run(
            ["xcrun", "clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
             "-mmacosx-version-min=11.0", str(cores), "-framework", "Hypervisor",
             "-o", str(work / "cores")], check=True,
        )
        subprocess.run([str(work / "cores")], check=True)
        print("PASS: real HVF vCPU capability and host/GICv2 limits")


if __name__ == "__main__":
    main()
