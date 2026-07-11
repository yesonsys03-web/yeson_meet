import XCTest
@testable import AppleTranslateKit

final class LiveEmitPolicyTests: XCTestCase {
    // policy.onVolatile(en:now:) -> en 스냅샷 번역이 허용되는 시점이면 그 텍스트, 아니면 nil
    // policy.onFinal(now:) -> 파이널 방출 후 seq 증가
    func testVolatileThrottledTo500ms() {
        var p = LiveEmitPolicy(throttleMs: 500, minDeltaChars: 4)
        XCTAssertEqual(p.onVolatile(en: "Hello there", now: 0.0), "Hello there")
        XCTAssertNil(p.onVolatile(en: "Hello there my", now: 0.3))       // 500ms 미경과
        XCTAssertEqual(p.onVolatile(en: "Hello there my friend", now: 0.6),
                       "Hello there my friend")
    }

    func testTinyDeltaIsSkipped() {
        var p = LiveEmitPolicy(throttleMs: 500, minDeltaChars: 4)
        _ = p.onVolatile(en: "Hello there", now: 0.0)
        XCTAssertNil(p.onVolatile(en: "Hello there!", now: 1.0))         // 델타 1자
    }

    func testSeqIncrementsOnFinal() {
        var p = LiveEmitPolicy(throttleMs: 500, minDeltaChars: 4)
        XCTAssertEqual(p.seq, 1)
        p.onFinal(now: 1.0)
        XCTAssertEqual(p.seq, 2)
        // 파이널 직후 볼래틸은 스로틀 리셋 — 새 발화 첫 파셜은 즉시 허용
        XCTAssertEqual(p.onVolatile(en: "Next one", now: 1.1), "Next one")
    }
}
