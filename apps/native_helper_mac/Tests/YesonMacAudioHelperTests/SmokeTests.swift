import XCTest
import YesonMacAudioHelperKit

final class SmokeTests: XCTestCase {
    func testHelperVersion() {
        XCTAssertEqual(yesonHelperVersion, "0.1.0")
    }
}
