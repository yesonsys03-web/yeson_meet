import XCTest
@testable import AppleTranslateKit

final class EventsTests: XCTestCase {
    func testPartialEncodesAsSingleJsonLine() throws {
        let line = OutEvent.partial(seq: 3, en: "Hello", ko: "안녕").jsonLine()
        XCTAssertFalse(line.contains("\n"))
        let obj = try JSONSerialization.jsonObject(
            with: line.data(using: .utf8)!) as! [String: Any]
        XCTAssertEqual(obj["type"] as? String, "partial")
        XCTAssertEqual(obj["seq"] as? Int, 3)
        XCTAssertEqual(obj["ko"] as? String, "안녕")
    }

    func testStatusErrorCarriesReason() throws {
        let line = OutEvent.status(state: "error", reason: "unsupported_os").jsonLine()
        let obj = try JSONSerialization.jsonObject(
            with: line.data(using: .utf8)!) as! [String: Any]
        XCTAssertEqual(obj["reason"] as? String, "unsupported_os")
    }

    func testFinalCarriesTimeRange() throws {
        let line = OutEvent.final(seq: 1, en: "Hi.", ko: "안녕.", t0: 1.25, t1: 2.5).jsonLine()
        let obj = try JSONSerialization.jsonObject(
            with: line.data(using: .utf8)!) as! [String: Any]
        XCTAssertEqual(obj["t0"] as? Double, 1.25)
    }
}
