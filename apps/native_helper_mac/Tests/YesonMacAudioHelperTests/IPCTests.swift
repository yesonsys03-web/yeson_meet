import XCTest
import YesonMacAudioHelperKit

final class IPCTests: XCTestCase {
    func testWritePCMChunkToBuffer() {
        let buf = DataBufferSink()
        let ctrl = DataBufferSink()
        let ipc = IPC(dataSink: buf, controlSink: ctrl)
        let chunk = Data([0x00, 0x01, 0x02, 0x03])
        ipc.emitChunk(chunk)
        XCTAssertEqual(buf.collected, chunk)
        XCTAssertEqual(ctrl.collected.count, 0)
    }

    func testWriteControlEventAsJSONLine() throws {
        let data = DataBufferSink()
        let ctrl = DataBufferSink()
        let ipc = IPC(dataSink: data, controlSink: ctrl)
        ipc.emitEvent(name: "permission_denied", payload: ["code": "E_PERM"])
        let line = String(data: ctrl.collected, encoding: .utf8) ?? ""
        XCTAssertTrue(line.hasSuffix("\n"), "expected newline terminator")
        let json = try JSONSerialization.jsonObject(with: ctrl.collected.dropLast()) as? [String: Any]
        XCTAssertEqual(json?["event"] as? String, "permission_denied")
        XCTAssertEqual((json?["payload"] as? [String: String])?["code"], "E_PERM")
    }

    func testEmptyPayloadStillEmitsValidJSON() throws {
        let ctrl = DataBufferSink()
        let ipc = IPC(dataSink: DataBufferSink(), controlSink: ctrl)
        ipc.emitEvent(name: "started")
        let json = try JSONSerialization.jsonObject(with: ctrl.collected.dropLast()) as? [String: Any]
        XCTAssertEqual(json?["event"] as? String, "started")
        XCTAssertNotNil(json?["payload"] as? [String: Any])
    }
}

final class DataBufferSink: ByteSink {
    var collected = Data()
    func write(_ data: Data) { collected.append(data) }
}
