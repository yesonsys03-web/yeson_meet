import Foundation
import YesonMacAudioHelperKit

// === ANCHOR: HELPER_MAIN_START ===
@main
struct YesonMacAudioHelperApp {
    static func main() async {
        let ipc = IPC.standard()
        ipc.emitEvent(name: "starting", payload: ["version": yesonHelperVersion])

        let provider: AudioCapture = ScreenCaptureKitProvider()

        if provider.permissionStatus != .granted {
            ipc.emitEvent(name: "permission_required",
                          payload: ["status": provider.permissionStatus.rawValue])
            let status = await provider.requestPermission()
            ipc.emitEvent(name: "permission_status", payload: ["status": status.rawValue])
            if status != .granted {
                ipc.emitEvent(name: "fatal", payload: ["reason": "permission_denied"])
                exit(3)
            }
        }

        do {
            try await provider.start { chunk in
                ipc.emitChunk(chunk)
            }
            ipc.emitEvent(name: "started", payload: [:])
        } catch {
            ipc.emitEvent(name: "fatal",
                          payload: ["reason": "start_failed", "detail": "\(error)"])
            exit(4)
        }

        signal(SIGINT, SIG_IGN)
        signal(SIGTERM, SIG_IGN)
        let sigSrc = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        sigSrc.setEventHandler {
            ipc.emitEvent(name: "stopping", payload: [:])
            provider.dispose()
            exit(0)
        }
        sigSrc.resume()

        // Block forever; signal handler triggers exit.
        try? await Task.sleep(nanoseconds: UInt64.max)
    }
}
// === ANCHOR: HELPER_MAIN_END ===
