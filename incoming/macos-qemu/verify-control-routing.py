#!/usr/bin/env python3
"""Check production console responses and compile Cocoa routing with SDK stubs.

The Cocoa check validates Objective-C++ API use, not a live Qt IME session.
"""
import argparse
from pathlib import Path
import runpy
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("qemu", type=Path)
    args = parser.parse_args()
    root = args.qemu.resolve()
    function = runpy.run_path(str(Path(__file__).with_name("verify-native-adaptations.py")))["function"]
    console = (root / "android/android-emu/android/console.cpp").read_text()
    qt = root / "android/android-ui/modules/aemu-ui-qt/src/android/skin/qt"
    with tempfile.TemporaryDirectory(prefix="modavd-control-check-") as temp:
        work = Path(temp)
        source = work / "console.cpp"
        source.write_text(r'''
#include <cassert>
#include <cstdarg>
#include <cstdio>
#include <string>
using ControlClient = void*;
static std::string output;
static bool headless = false;
static int backend = 0, calls = 0;
static bool requested = false;
struct Settings { bool host_emulator_is_headless() { return headless; } };
struct Agents { Settings* settings; };
static Agents* getConsoleAgents() {
    static Settings settings;
    static Agents agents = {&settings};
    return &agents;
}
static int skin_winsys_set_window_visible(bool visible) {
    ++calls;
    requested = visible;
    return backend;
}
static void control_write(ControlClient, const char* format, ...) {
    char buffer[1024];
    va_list args;
    va_start(args, format);
    vsnprintf(buffer, sizeof(buffer), format, args);
    va_end(args);
    output += buffer;
}
''' + function(console, "static int do_window_visibility(") + r'''
int main() {
    char extra[] = "extra";
    assert(do_window_visibility(nullptr, extra, true) == -1);
    assert(calls == 0 && output.find("KO:") == 0);
    output.clear();
    headless = true;
    assert(do_window_visibility(nullptr, nullptr, true) == -1);
    assert(calls == 0 && output.find("unsupported in headless") != std::string::npos);
    output.clear();
    headless = false;
    backend = -1;
    assert(do_window_visibility(nullptr, nullptr, false) == -1);
    assert(calls == 1 && output.find("not ready or has closed") != std::string::npos);
    output.clear();
    backend = 0;
    assert(do_window_visibility(nullptr, nullptr, true) == 0);
    assert(requested && output == "windows=1\r\n");
    output.clear();
    assert(do_window_visibility(nullptr, nullptr, false) == 0);
    assert(!requested && output == "windows=1\r\n");
}
''')
        subprocess.run(["xcrun", "clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        str(source), "-o", str(work / "console")], check=True)
        subprocess.run([str(work / "console")], check=True)
        print("PASS: console rejects invalid/headless/unavailable windows and reports real success")

        identity = work / "identity.cpp"
        identity.write_text(r'''
#include <cassert>
#include <cstdint>
using quint32 = uint32_t;
struct QKeyEvent {
    int qtKey;
    quint32 native;
    int key() const { return qtKey; }
    quint32 nativeVirtualKey() const { return native; }
};
''' + function((qt / "emulator-qt-window.cpp").read_text(), "static quint32 hostImeKeyId(") + r'''
int main() {
    assert(hostImeKeyId({'A', 0}) == 0);  // Cocoa's valid key code zero.
    assert(hostImeKeyId({'A', 0}) == hostImeKeyId({'a', 0}));
    assert(hostImeKeyId({'A', 0}) != hostImeKeyId({'S', 1}));
    assert(hostImeKeyId({0, 0}) != hostImeKeyId({'A', 0}));  // Synthetic text key.
}
''')
        subprocess.run(["xcrun", "clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        str(identity), "-o", str(work / "identity")], check=True)
        subprocess.run([str(work / "identity")], check=True)
        print("PASS: Cocoa physical key identity survives text/modifier changes and key code zero")

        stubs = work / "include"
        stubs.mkdir()
        (stubs / "routing-stubs.h").write_text(r'''
#pragma once
#include <cstdint>
#include <optional>
#include <utility>
using qintptr = long long;
using quint32 = uint32_t;
using WId = uintptr_t;
class QByteArray { public: int compare(const char*) const; };
class NativeEventFilter {
public: bool nativeEventFilter(const QByteArray&, void*, qintptr*);
};
struct SkinEvent {};
enum { kEventKeyDown };
class EmulatorQtWindow {
public:
    static EmulatorQtWindow* getInstance();
    WId hostImeKeyWindow() const;
    bool isActiveWindow() const;
    bool hostImeDefersNativeKey(quint32) const;
    void hostImeNativeKeyUp(quint32, bool);
    void hostImeNativeKeyDown(quint32, bool, bool, quint32, bool, bool, quint32 = 0);
    void queueSkinEvent(SkinEvent);
};
class NativeKeyboardEventHandler {
public:
    struct KeyEvent { int scancode, modifiers, eventType; };
    static NativeKeyboardEventHandler* getInstance();
    std::optional<SkinEvent> handleKeyEvent(const KeyEvent&);
};
struct Settings { bool use_keycode_forwarding(); };
struct Agents { Settings* settings; };
Agents* getConsoleAgents();
bool skin_keycode_native_is_keypad(int);
int skin_keycode_native_map_keypad(int);
''')
        for name in ["mac-native-event-filter.h", "emulator-qt-window.h", "native-keyboard-event-handler.h"]:
            target = stubs / "android/skin/qt" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('#include "routing-stubs.h"\n')
        for name in ["android/console.h", "android/skin/keycode.h"]:
            target = stubs / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('#include "routing-stubs.h"\n')
        subprocess.run(["xcrun", "clang++", "-std=c++17", "-x", "objective-c++",
                        "-fsyntax-only", "-Wall", "-Wextra", "-Werror",
                        "-mmacosx-version-min=11.0", "-I", str(stubs),
                        str(qt / "mac-native-event-filter.mm")], check=True)
        print("PASS: production Cocoa event filter compiles against the macOS SDK with Qt interface stubs")


if __name__ == "__main__":
    main()
