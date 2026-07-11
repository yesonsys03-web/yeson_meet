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

    // 라이브 경로 전략 파서 — 기본 high, "low"만 저지연으로 내린다.
    func testLiveStrategyDefaultsToHighWhenUnset() {
        XCTAssertEqual(AppleMTStrategy.from(nil, defaultTo: .high), .high)
    }

    func testLiveStrategyLowExplicit() {
        XCTAssertEqual(AppleMTStrategy.from("low", defaultTo: .high), .low)
    }

    func testLiveStrategyHighExplicit() {
        XCTAssertEqual(AppleMTStrategy.from("HIGH", defaultTo: .high), .high)
    }

    func testLiveStrategyUnknownUsesFallback() {
        XCTAssertEqual(AppleMTStrategy.from("medium", defaultTo: .high), .high)
        XCTAssertEqual(AppleMTStrategy.from("medium", defaultTo: .low), .low)
    }

    func testMissingAssetDescriptionCarriesPrefix() {
        let err = AppleMTMissingAsset("EN→KO 미설치")
        XCTAssertEqual("\(err)", "missing_mt_asset: EN→KO 미설치")
    }
}
