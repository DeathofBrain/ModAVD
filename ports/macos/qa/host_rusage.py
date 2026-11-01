#!/usr/bin/env python3
"""Read Darwin process usage for QA; footprint alone is not a reclaim proof."""
import argparse
import ctypes
import json
import sys
import time


class RusageInfoV0(ctypes.Structure):
    # sys/resource.h RUSAGE_INFO_V0: 16-byte UUID followed by ten uint64s.
    _fields_ = [("uuid", ctypes.c_ubyte * 16)] + [
        (name, ctypes.c_uint64) for name in (
            "user_time", "system_time", "pkg_idle_wakeups", "interrupt_wakeups",
            "pageins", "wired_size", "resident_size", "phys_footprint",
            "proc_start_abstime", "proc_exit_abstime")]


class MachTimebaseInfo(ctypes.Structure):
    _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]


def snapshot(pid):
    if sys.platform != "darwin" or type(pid) is not int or pid <= 1:
        raise ValueError("a live Darwin PID is required")
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    library.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int,
                                      ctypes.POINTER(RusageInfoV0)]
    library.proc_pid_rusage.restype = ctypes.c_int
    usage = RusageInfoV0()
    if library.proc_pid_rusage(pid, 0, ctypes.byref(usage)):
        raise OSError(ctypes.get_errno(), "proc_pid_rusage failed")
    if not usage.proc_start_abstime or usage.proc_exit_abstime:
        raise RuntimeError("process has no live start identity")
    system = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    system.mach_timebase_info.argtypes = [ctypes.POINTER(MachTimebaseInfo)]
    system.mach_timebase_info.restype = ctypes.c_int
    timebase = MachTimebaseInfo()
    if system.mach_timebase_info(ctypes.byref(timebase)) or not timebase.denom:
        raise RuntimeError("mach_timebase_info failed")
    # These CPU fields are Mach absolute ticks, unlike time.process_time_ns().
    # Keep the raw values and convert with the host's timebase, not a fixed scale.
    return {"pid": pid, "host_monotonic_ns": time.monotonic_ns(),
            "process_start_abstime": usage.proc_start_abstime,
            "resident_bytes": usage.resident_size,
            "physical_footprint_bytes": usage.phys_footprint,
            "wired_bytes": usage.wired_size,
            "user_cpu_abstime": usage.user_time,
            "system_cpu_abstime": usage.system_time,
            "mach_timebase_numer": timebase.numer,
            "mach_timebase_denom": timebase.denom,
            "user_cpu_ns": usage.user_time * timebase.numer // timebase.denom,
            "system_cpu_ns": usage.system_time * timebase.numer // timebase.denom,
            "pageins": usage.pageins}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pid", type=int)
    args = parser.parse_args()
    print(json.dumps(snapshot(args.pid), indent=2))
