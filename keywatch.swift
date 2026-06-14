import Cocoa
import ApplicationServices

let vKeyCode: CGKeyCode = 9
let escapeKeyCode: CGKeyCode = 53
let source = CGEventSource(stateID: .hidSystemState)

let mask = (1 << CGEventType.keyDown.rawValue)
guard let eventTap = CGEvent.tapCreate(
    tap: .cgSessionEventTap,
    place: .headInsertEventTap,
    options: .listenOnly,
    eventsOfInterest: CGEventMask(mask),
    callback: { _, type, event, _ in
        if type == .keyDown {
            let keyCode = CGKeyCode(event.getIntegerValueField(.keyboardEventKeycode))
            let flags = event.flags
            if keyCode == vKeyCode && flags.contains(.maskCommand) {
                print("paste")
                fflush(stdout)
            } else if keyCode == escapeKeyCode {
                print("escape")
                fflush(stdout)
            }
        }
        return Unmanaged.passUnretained(event)
    },
    userInfo: nil
) else {
    fputs("Could not create keyboard event tap. Grant Accessibility permission to Terminal.\n", stderr)
    exit(1)
}

let runLoopSource = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, eventTap, 0)
CFRunLoopAddSource(CFRunLoopGetCurrent(), runLoopSource, .commonModes)
CGEvent.tapEnable(tap: eventTap, enable: true)
CFRunLoopRun()
