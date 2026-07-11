import XCTest
@testable import AppleTranslateKit

/// SentenceSegmenter는 볼래틸 EN 스냅샷 스트림을 문장 단위 강제 파이널로 잘라낸다.
/// Gemini TranscriptAssembler 의미론(문장경계 분할·소수점 가드·min_final 병합·
/// 길이/나이 백스톱·idle flush)을 스냅샷/consumedPrefix 모델로 이식한 것.
final class SentenceSegmenterTests: XCTestCase {
    // 기본 임계값이 설계값(min12/len120/age12s/idle2s/grace0.8s)임을 전제로 한다.
    private func seg() -> SentenceSegmenter { SentenceSegmenter() }

    // 1) 문장경계 컷 + 잔여 캐리
    func testSentenceBoundaryCutAndCarry() {
        var s = seg()
        // "Hello everyone." (15자 ≥ min12) 뒤에 공백 → 완결 경계. "Today we"는 캐리.
        let out = s.onSnapshot(en: "Hello everyone. Today we", now: 0.0)
        XCTAssertEqual(out, [SentenceSegmenter.Segment(text: "Hello everyone.", isForced: true)])
        XCTAssertEqual(s.currentSuffix, "Today we")
        XCTAssertEqual(s.consumedPrefix, "Hello everyone. ")
    }

    // 2) 소수점은 경계가 아니다
    func testDecimalIsNotBoundary() {
        var s = seg()
        let out = s.onSnapshot(en: "The value is 1.5 and it grows", now: 0.0)
        XCTAssertEqual(out, [])                    // 컷 없음
        XCTAssertEqual(s.currentSuffix, "The value is 1.5 and it grows")
    }

    // 2b) 소수점은 무시하되 실제 문장 끝은 경계다
    func testDecimalIgnoredButRealEndCuts() {
        var s = seg()
        let out = s.onSnapshot(en: "Version 2.0 is done. Next", now: 0.0)
        XCTAssertEqual(out, [SentenceSegmenter.Segment(text: "Version 2.0 is done.", isForced: true)])
        XCTAssertEqual(s.currentSuffix, "Next")
    }

    // 3) min_final 미만 문장은 병합(즉시 컷하지 않음)
    func testShortSentenceRidesUntilMinFinal() {
        var s = seg()
        // "Hi. " 3자 < min12 → 컷 안 됨
        XCTAssertEqual(s.onSnapshot(en: "Hi. ", now: 0.0), [])
        // 뒤에 더 붙어 누적이 min을 넘으면 마지막 경계에서 병합 컷
        let out = s.onSnapshot(en: "Hi. Today we review. ", now: 0.1)
        XCTAssertEqual(out, [SentenceSegmenter.Segment(text: "Hi. Today we review.", isForced: true)])
        XCTAssertEqual(s.currentSuffix.trimmingCharacters(in: .whitespaces), "")
    }

    // 4) 길이 백스톱: 마지막 단어 경계에서 컷
    func testLengthBackstopCutsAtWordBoundary() {
        var s = seg()
        // 구두점 없는 130자 초과 문자열 (단어 5자 + 공백 = 6자 단위)
        var words: [String] = []
        for _ in 0..<24 { words.append("alpha") }   // 24*6-1 = 143자
        let head = words.joined(separator: " ")
        let text = head + " tailword"                // 마지막 부분단어
        let out = s.onSnapshot(en: text, now: 0.0)
        XCTAssertEqual(out.count, 1)
        XCTAssertTrue(out[0].isForced)
        // 마지막 공백 이전까지 컷 → "tailword"는 캐리
        XCTAssertEqual(s.currentSuffix, "tailword")
        XCTAssertFalse(out[0].text.contains("tailword"))
        XCTAssertTrue(out[0].text.hasPrefix("alpha"))
    }

    // 5) 나이 백스톱: 12s 초과 미소비 텍스트는 강제 컷 (성장 중이라 idle 아님)
    func testAgeBackstopCuts() {
        var s = seg()
        XCTAssertEqual(s.onSnapshot(en: "one", now: 0.0), [])
        XCTAssertEqual(s.onSnapshot(en: "one two three", now: 6.0), [])
        // 계속 성장(=idle 아님)했지만 최초 미소비 텍스트 나이 13s > 12s → 컷
        let out = s.onSnapshot(en: "one two three four five six", now: 13.0)
        XCTAssertEqual(out, [SentenceSegmenter.Segment(text: "one two three four five six", isForced: true)])
    }

    // 6) idle flush: 성장 정지 2s 초과 → 컷 (min_final 무시)
    func testIdleFlushOverridesMinFinal() {
        var s = seg()
        XCTAssertEqual(s.onSnapshot(en: "hi", now: 0.0), [])   // 2자 < min
        XCTAssertEqual(s.onIdleCheck(now: 1.0), [])            // 2s 미만
        let out = s.onIdleCheck(now: 2.5)                      // 2.5s ≥ idle2s
        XCTAssertEqual(out, [SentenceSegmenter.Segment(text: "hi", isForced: true)])
        XCTAssertEqual(s.currentSuffix.trimmingCharacters(in: .whitespaces), "")
    }

    // 7) consumedPrefix가 성장 스냅샷에 걸쳐 유지 — 확정 문장 재방출 없음
    func testConsumedPrefixTrackingAcrossSnapshots() {
        var s = seg()
        _ = s.onSnapshot(en: "Hello everyone. Today", now: 0.0)     // "Hello everyone." 컷
        XCTAssertEqual(s.consumedPrefix, "Hello everyone. ")
        // 다음 스냅샷은 첫 문장을 다시 포함하지만 suffix엔 새 텍스트만
        let out = s.onSnapshot(en: "Hello everyone. Today we work", now: 0.2)
        XCTAssertEqual(out, [])                                     // 재방출 없음
        XCTAssertEqual(s.currentSuffix, "Today we work")
    }

    // 8) 새 발화에서 reset
    func testResetOnNewUtterance() {
        var s = seg()
        _ = s.onSnapshot(en: "Hello everyone. Today", now: 0.0)
        XCTAssertFalse(s.consumedPrefix.isEmpty)
        s.reset()
        XCTAssertEqual(s.consumedPrefix, "")
        XCTAssertEqual(s.currentSuffix, "")
        let out = s.onSnapshot(en: "Brand new", now: 1.0)
        XCTAssertEqual(out, [])
        XCTAssertEqual(s.currentSuffix, "Brand new")
    }

    // 9) 실제 isFinal → 잔여 suffix를 파이널(비강제)로 flush + reset
    func testFinalFlushEmitsRemainderNonForced() {
        var s = seg()
        _ = s.onSnapshot(en: "Hello everyone. Today we", now: 0.0)   // 첫 문장 컷, 캐리 "Today we"
        let out = s.onFinal(en: "Hello everyone. Today we meet", now: 1.0)
        XCTAssertEqual(out, [SentenceSegmenter.Segment(text: "Today we meet", isForced: false)])
        // final은 발화를 끝내므로 상태 리셋
        XCTAssertEqual(s.consumedPrefix, "")
    }

    // 9b) 남은 게 없으면 final flush는 빈 배열
    func testFinalFlushEmptyWhenNothingRemains() {
        var s = seg()
        _ = s.onSnapshot(en: "Hello everyone. ", now: 0.0)          // 전부 컷됨
        let out = s.onFinal(en: "Hello everyone.", now: 1.0)
        XCTAssertEqual(out, [])
    }

    // 10) 스냅샷 수축(shrink)/발산(divergence) 회귀: 볼래틸 결과가 접두사를 앞부분까지
    // 수정(짧아짐)해도 크래시 없이 commonPrefix로 후퇴해야 한다(onSnapshot 내부 가드).
    func testShrunkAndDivergedSnapshotReconcileViaCommonPrefix() {
        var s = seg()
        // "Hello everyone. " 컷 소비, 캐리 "Today"
        _ = s.onSnapshot(en: "Hello everyone. Today", now: 0.0)
        XCTAssertEqual(s.consumedPrefix, "Hello everyone. ")
        XCTAssertEqual(s.currentSuffix, "Today")

        // 스냅샷이 수축(더 짧아짐) — 크래시 없이 commonPrefix("Hello", consumedPrefix)로
        // 후퇴해야 한다. commonPrefix("Hello", "Hello everyone. ") == "Hello".
        let shrunkOut = s.onSnapshot(en: "Hello", now: 0.5)
        XCTAssertEqual(shrunkOut, [])
        XCTAssertEqual(s.consumedPrefix, "Hello")
        XCTAssertEqual(s.currentSuffix, "")

        // 완전히 발산한 스냅샷도 안전해야 한다: commonPrefix("Hi there friends.", "Hello") == "H"
        let divergedOut = s.onSnapshot(en: "Hi there friends.", now: 1.0)
        XCTAssertEqual(divergedOut, [])
        XCTAssertEqual(s.consumedPrefix, "H")
        XCTAssertEqual(s.currentSuffix, "i there friends.")
    }
}
