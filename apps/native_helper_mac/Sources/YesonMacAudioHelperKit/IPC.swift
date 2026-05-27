import Foundation

// === ANCHOR: IPC_START ===
public protocol ByteSink {
    func write(_ data: Data)
}

public struct FileHandleSink: ByteSink {
    let handle: FileHandle
    public init(handle: FileHandle) { self.handle = handle }
    public func write(_ data: Data) { handle.write(data) }
}

/// stdout = PCM binary stream. stderr = JSON line events.
public final class IPC {
    private let dataSink: ByteSink
    private let controlSink: ByteSink

    public init(dataSink: ByteSink, controlSink: ByteSink) {
        self.dataSink = dataSink
        self.controlSink = controlSink
    }

    public static func standard() -> IPC {
        IPC(
            dataSink: FileHandleSink(handle: FileHandle.standardOutput),
            controlSink: FileHandleSink(handle: FileHandle.standardError)
        )
    }

    public func emitChunk(_ data: Data) {
        dataSink.write(data)
    }

    public func emitEvent(name: String, payload: [String: Any] = [:]) {
        let obj: [String: Any] = ["event": name, "payload": payload]
        guard let data = try? JSONSerialization.data(withJSONObject: obj) else { return }
        var line = data
        line.append(0x0A) // \n
        controlSink.write(line)
    }
}
// === ANCHOR: IPC_END ===
