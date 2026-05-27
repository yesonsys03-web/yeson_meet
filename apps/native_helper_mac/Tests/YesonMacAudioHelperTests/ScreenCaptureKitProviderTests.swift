import XCTest
import YesonMacAudioHelperKit

final class ScreenCaptureKitProviderTests: XCTestCase {
    func testConformsToAudioCapture() {
        let provider: AudioCapture = ScreenCaptureKitProvider()
        XCTAssertGreaterThanOrEqual(provider.listTargets().count, 1) // .systemDefault at minimum
    }
}
