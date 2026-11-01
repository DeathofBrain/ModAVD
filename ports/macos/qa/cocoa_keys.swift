// Send bounded native key events to one explicitly selected QA process.
// Input sources and foreground application are restored before exit.
import AppKit
import ApplicationServices
import Carbon

func sourceID(_ source: TISInputSource) -> String {
    guard let value = TISGetInputSourceProperty(source, kTISPropertyInputSourceID) else { return "" }
    return Unmanaged<CFString>.fromOpaque(value).takeUnretainedValue() as String
}

let sources = TISCreateInputSourceList(nil, false).takeRetainedValue() as! [TISInputSource]
if CommandLine.arguments.count == 1 {
    let current = TISCopyCurrentKeyboardInputSource().takeRetainedValue()
    let result: [String: Any] = ["accessibilityTrusted": AXIsProcessTrusted(),
                               "currentSource": sourceID(current),
                               "sources": sources.map { sourceID($0) }]
    print(String(data: try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]), encoding: .utf8)!)
} else {
    func sendKeys() throws {
    guard CommandLine.arguments.count >= 4, let pid = Int32(CommandLine.arguments[1]),
          let application = NSRunningApplication(processIdentifier: pid),
          let source = sources.first(where: { sourceID($0) == CommandLine.arguments[2] }),
          AXIsProcessTrusted() else { fatalError("Explicit QA PID, enabled source and Accessibility access required") }
    let previousSource = TISCopyCurrentKeyboardInputSource().takeRetainedValue()
    let previousApp = NSWorkspace.shared.frontmostApplication
    let keyCodes = CommandLine.arguments.dropFirst(3).map { UInt16($0)! }
    guard keyCodes.count <= 64 else { fatalError("At most 64 key strokes") }
    defer {
        _ = TISSelectInputSource(previousSource)
        _ = previousApp?.activate(options: [])
    }
    guard TISSelectInputSource(source) == noErr, application.activate(options: []) else {
        throw NSError(domain: "ModAVD.QA", code: 1, userInfo: [NSLocalizedDescriptionKey: "Cannot select input source/focus QA application"])
    }
    usleep(300_000)
    for code in keyCodes {
        for down in [true, false] {
            guard let event = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: down) else {
                throw NSError(domain: "ModAVD.QA", code: 2, userInfo: [NSLocalizedDescriptionKey: "Cannot create key event"])
            }
            event.flags = []
            event.postToPid(pid)
            usleep(60_000)
        }
    }
    usleep(300_000)
    print("Sent \(keyCodes.count) native key strokes; input source restored")
    }
    try sendKeys()
}
