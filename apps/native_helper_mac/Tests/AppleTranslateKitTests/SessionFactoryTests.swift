import XCTest
@testable import AppleTranslateKit

final class SessionFactoryTests: XCTestCase {
    func testStrategyDefaultsToLowWhenUnset() {
        XCTAssertEqual(AppleMTStrategy.fromEnv([:]), .low)
    }

    func testStrategyLowExplicit() {
        XCTAssertEqual(AppleMTStrategy.fromEnv(["YESON_APPLE_MT_STRATEGY": "low"]), .low)
    }

    func testStrategyHighExplicit() {
        XCTAssertEqual(AppleMTStrategy.fromEnv(["YESON_APPLE_MT_STRATEGY": "high"]), .high)
    }

    func testStrategyIsCaseInsensitive() {
        XCTAssertEqual(AppleMTStrategy.fromEnv(["YESON_APPLE_MT_STRATEGY": "HIGH"]), .high)
        XCTAssertEqual(AppleMTStrategy.fromEnv(["YESON_APPLE_MT_STRATEGY": "Low"]), .low)
    }

    func testStrategyUnknownFallsBackToLow() {
        XCTAssertEqual(AppleMTStrategy.fromEnv(["YESON_APPLE_MT_STRATEGY": "medium"]), .low)
    }

    func testMissingAssetDescriptionCarriesPrefix() {
        let err = AppleMTMissingAsset("EN→KO 미설치")
        XCTAssertEqual("\(err)", "missing_mt_asset: EN→KO 미설치")
    }
}
