import Foundation

/// 볼래틸(파셜) 결과의 번역 요청 빈도를 제어하는 순수 정책.
/// 번역은 로컬이라 빠르지만, 볼래틸은 프레임마다 갱신되므로 무스로틀이면 낭비.
public struct LiveEmitPolicy {
    public private(set) var seq = 1
    private let throttleSec: Double
    private let minDeltaChars: Int
    private var lastEmitAt: Double = -1e9
    private var lastEmittedEn = ""

    public init(throttleMs: Int, minDeltaChars: Int) {
        self.throttleSec = Double(throttleMs) / 1000.0
        self.minDeltaChars = minDeltaChars
    }

    public mutating func onVolatile(en: String, now: Double) -> String? {
        guard now - lastEmitAt >= throttleSec else { return nil }
        guard abs(en.count - lastEmittedEn.count) >= minDeltaChars || lastEmittedEn.isEmpty
        else { return nil }
        lastEmitAt = now
        lastEmittedEn = en
        return en
    }

    public mutating func onFinal(now: Double) {
        seq += 1
        lastEmitAt = -1e9          // 다음 발화 첫 파셜은 즉시 허용
        lastEmittedEn = ""
    }
}
