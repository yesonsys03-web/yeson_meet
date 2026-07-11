import Foundation

/// 볼래틸 EN 스냅샷 스트림을 **문장 단위**로 잘라 강제 파이널을 만드는 순수 로직.
///
/// 배경: SpeechTranscriber 자체의 발화 확정(`isFinal`)에만 의존하면 연속 회의 발화에서
/// 파이널이 몰렸다가(3s에 6개) 한 볼래틸 발화가 자라는 동안 15~25s씩 비는 문제가 있다.
/// 검증된 해법인 Gemini 경로의 `TranscriptAssembler` 의미론(문장경계 분할 + 소수점 가드,
/// min_final 병합, 길이/나이 강제 파이널, idle flush)을 **볼래틸 스냅샷 + consumedPrefix**
/// 모델로 이식한다. Gemini는 프래그먼트를 버퍼에 누적하지만, SpeechTranscriber는 현재
/// 발화의 **전체 텍스트 스냅샷**을 갱신해 준다 — 그래서 여기서는 이미 확정해 방출한
/// 접두사(`consumedPrefix`)를 추적하고 그 뒤의 미소비 suffix에 대해서만 판단한다.
///
/// 모든 임계값은 생성자 파라미터(테스트 용이성). 기본값은 설계 합의치.
/// 순수 struct — I/O·번역·시계 접근 없음(시계는 호출부가 `now`로 주입). 단위 테스트 대상.
public struct SentenceSegmenter {
    /// 한 번의 컷 결과. `isForced`=true는 세그멘터가 강제로 자른 문장,
    /// false는 실제 transcriber `isFinal`에서 flush된 잔여(진짜 파이널).
    public struct Segment: Equatable {
        public let text: String
        public let isForced: Bool
        public init(text: String, isForced: Bool) {
            self.text = text
            self.isForced = isForced
        }
    }

    private let minFinalChars: Int
    private let lengthBackstopChars: Int
    private let ageBackstopSec: Double
    private let idleFlushSec: Double
    private let sentenceEndGraceSec: Double

    /// 현재 발화에서 이미 강제 파이널로 방출·소비한 스냅샷 접두사.
    public private(set) var consumedPrefix: String = ""
    /// 현재 미소비 suffix(= 최신 스냅샷 − consumedPrefix). 파셜은 이것만 보여준다.
    public private(set) var currentSuffix: String = ""

    private var started = false
    private var lastSnapshot = ""
    private var suffixStartTime = 0.0   // 현재 미소비 텍스트 최초 등장 시각(나이 백스톱)
    private var lastGrowthTime = 0.0    // 스냅샷이 마지막으로 자란 시각(idle 판정)

    public init(
        minFinalChars: Int = 12,
        lengthBackstopChars: Int = 120,
        ageBackstopSec: Double = 12.0,
        idleFlushSec: Double = 2.0,
        sentenceEndGraceSec: Double = 0.8
    ) {
        self.minFinalChars = minFinalChars
        self.lengthBackstopChars = lengthBackstopChars
        self.ageBackstopSec = ageBackstopSec
        self.idleFlushSec = idleFlushSec
        self.sentenceEndGraceSec = sentenceEndGraceSec
    }

    /// 현재 발화의 새 볼래틸 스냅샷을 먹인다. 강제 컷들을 순서대로 반환(보통 0~1개,
    /// 스냅샷이 여러 문장을 한꺼번에 담으면 다수). 이후 `currentSuffix`가 파셜 대상.
    public mutating func onSnapshot(en: String, now: Double) -> [Segment] {
        if !started {
            started = true
            suffixStartTime = now
            lastGrowthTime = now
        } else if en != lastSnapshot {
            lastGrowthTime = now
        }
        let prevSuffixEmpty = currentSuffix.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        // 스냅샷은 보통 append지만 볼래틸은 앞부분을 수정하기도 한다 — 접두사가 더는
        // 맞지 않으면 공통 접두사까지 후퇴시켜 수정된 꼬리를 suffix로 다시 집는다.
        if !en.hasPrefix(consumedPrefix) {
            consumedPrefix = String(en.commonPrefix(with: consumedPrefix))
        }
        currentSuffix = String(en.dropFirst(consumedPrefix.count))
        let nowSuffixNonEmpty = !currentSuffix.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        if prevSuffixEmpty && nowSuffixNonEmpty {
            suffixStartTime = now
        }
        lastSnapshot = en
        return drain(now: now)
    }

    /// 시계 틱(새 스냅샷 없이 호출) — Gemini `poll`의 idle-finalize에 대응.
    /// 성장 정지가 idleFlushSec를 넘고 미소비 텍스트가 있으면 통째로 강제 컷.
    public mutating func onIdleCheck(now: Double) -> [Segment] {
        guard started else { return [] }
        guard !currentSuffix.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return [] }
        guard now - lastGrowthTime >= idleFlushSec else { return [] }
        return [cutAt(currentSuffix.count, now: now)]
    }

    /// 실제 transcriber `isFinal`: 잔여 suffix를 **비강제 파이널**로 flush하고 발화 종료 → reset.
    public mutating func onFinal(en: String, now: Double) -> [Segment] {
        if !en.hasPrefix(consumedPrefix) {
            consumedPrefix = String(en.commonPrefix(with: consumedPrefix))
        }
        let suffix = String(en.dropFirst(consumedPrefix.count))
            .trimmingCharacters(in: .whitespacesAndNewlines)
        var out: [Segment] = []
        if !suffix.isEmpty {
            out.append(Segment(text: suffix, isForced: false))
        }
        reset()
        return out
    }

    /// 새 발화 시작(결과 스트림 컨텍스트 리셋) → consumedPrefix/상태 초기화.
    public mutating func reset() {
        consumedPrefix = ""
        currentSuffix = ""
        lastSnapshot = ""
        started = false
        suffixStartTime = 0.0
        lastGrowthTime = 0.0
    }

    // MARK: - 내부 결정 로직

    private mutating func drain(now: Double) -> [Segment] {
        var out: [Segment] = []
        var guardCount = 0
        // 매 컷은 currentSuffix를 엄격히 줄이므로 자연 종료 — guard는 방어용 상한.
        while guardCount < 200 {
            guardCount += 1
            guard let cut = decideCut(now: now) else { break }
            out.append(cut)
        }
        return out
    }

    private mutating func decideCut(now: Double) -> Segment? {
        let chars = Array(currentSuffix)
        if String(chars).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return nil }

        // 1) 문장 경계(min_final 게이트). 마지막 완결 경계에서 컷 → 짧은 앞 문장은 병합됨.
        if let b = lastCompletedBoundary(chars: chars, now: now) {
            let headTrim = String(chars[0..<b]).trimmingCharacters(in: .whitespacesAndNewlines)
            if headTrim.count >= minFinalChars {
                return cutAt(b, now: now)
            }
            // 너무 짧으면 여기서 자르지 않고 백스톱으로 흘려보낸다(병합 대기).
        }
        // 2) 길이 백스톱: 마지막 단어 경계에서 컷(단어 중간 절단 회피).
        if chars.count > lengthBackstopChars {
            if let w = lastWhitespaceOffset(chars: chars),
               !String(chars[0..<w]).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                return cutAt(w, now: now)
            }
            return cutAt(chars.count, now: now)   // 공백 없는 단일 거대 토큰 → 통째로
        }
        // 3) 나이 백스톱: 최초 미소비 텍스트가 너무 오래됨.
        if now - suffixStartTime >= ageBackstopSec {
            return cutAt(chars.count, now: now)
        }
        // 4) idle: 성장 정지.
        if now - lastGrowthTime >= idleFlushSec {
            return cutAt(chars.count, now: now)
        }
        return nil
    }

    /// currentSuffix의 오프셋 `b`(문자 단위)에서 컷: [0..<b]를 확정, 뒤 공백은 소비,
    /// 나머지를 캐리로 남긴다. 항상 강제(isForced=true) 세그먼트를 반환.
    private mutating func cutAt(_ b: Int, now: Double) -> Segment {
        let chars = Array(currentSuffix)
        let clamped = min(max(b, 0), chars.count)
        let head = String(chars[0..<clamped])
        var restChars = Array(chars[clamped...])
        var wsCount = 0
        while wsCount < restChars.count, restChars[wsCount].isWhitespace { wsCount += 1 }
        let leadingWs = String(restChars[0..<wsCount])
        consumedPrefix += head + leadingWs
        restChars = Array(restChars[wsCount...])
        currentSuffix = String(restChars)
        if !currentSuffix.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            suffixStartTime = now
        }
        return Segment(text: head.trimmingCharacters(in: .whitespacesAndNewlines), isForced: true)
    }

    /// 마지막 **완결** 문장 경계의 오프셋(구두점 바로 다음)을 반환, 없으면 nil.
    /// - 소수점 가드: 숫자.숫자(예 "1.5")는 경계가 아니다(Gemini 규칙 미러).
    /// - 완결 판정: 구두점 뒤에 공백이 오거나(즉시), suffix 끝이며 성장 정지가
    ///   graceSec를 넘은 경우(안정화 확인). 구두점 뒤 비공백(약어/소수 유사)은 경계 아님.
    private func lastCompletedBoundary(chars: [Character], now: Double) -> Int? {
        let puncts: Set<Character> = [".", "?", "!", "\u{2026}"]  // . ? ! …
        var last = -1
        for i in 0..<chars.count where puncts.contains(chars[i]) {
            let prev: Character? = i > 0 ? chars[i - 1] : nil
            let next: Character? = i + 1 < chars.count ? chars[i + 1] : nil
            if let p = prev, let n = next, p.isNumber, n.isNumber { continue }  // 소수점
            if let n = next {
                if n.isWhitespace { last = i + 1 }           // ". " 형태 → 완결
                // 비공백이 뒤따르면 문장 경계로 보지 않음(약어/오인식).
            } else if now - lastGrowthTime >= sentenceEndGraceSec {
                last = i + 1                                  // 끝에 걸린 구두점 + 안정화
            }
        }
        return last >= 0 ? last : nil
    }

    /// 마지막 공백 문자의 오프셋(= 그 앞까지가 head, 뒤가 부분단어 캐리), 없으면 nil.
    private func lastWhitespaceOffset(chars: [Character]) -> Int? {
        var idx: Int? = nil
        for i in 0..<chars.count where chars[i].isWhitespace { idx = i }
        return idx
    }
}
